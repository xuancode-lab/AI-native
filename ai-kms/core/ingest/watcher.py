"""文件监听：watchdog 监听 Vault，新 .md/掉落素材自动入库。"""
from __future__ import annotations

import os
import time
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from core.ingest.pipeline import IngestPipeline
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault

# 监听目录下这些文件视为"待收录素材"，入库后不再监听事件噪音
RAWMARKER = ".raw"          # 放在 dropbox 目录内：任何文件落进来都入一篇
COOLDOWN = 1.5              # 防双击抖动


class _Handler(FileSystemEventHandler):
    def __init__(self, pipeline: IngestPipeline, drop_dir: Path, store: SQLiteStore):
        self.pipeline = pipeline
        self.drop_dir = drop_dir
        self.store = store
        self._last = {}

    def _trigger(self, path: str):
        path = os.path.abspath(path)
        now = time.time()
        if path in self._last and now - self._last[path] < COOLDOWN:
            return
        self._last[path] = now

        p = Path(path)
        if not p.is_file():
            return

        try:
            suffix = p.suffix.lower()
            if suffix in (".md", ".txt", ".rst"):
                res = self.pipeline.ingest_file(p)
                self.store.log("info", "watcher", f"auto-ingest {p.name} → {res}")
                # 收录完成的素材原文件可移至 done（可选保留策略）
            elif suffix == ".canvas":
                pass    # 白板文档只列不摄（编辑器直接读写文件，监听器完全忽略）
            else:
                # 非 md：作为附件收集到同名 .md 引用（MVP 先仅记录）
                self.store.log("info", "watcher", f"non-md dropped: {p.name}")
        except Exception as e:  # pragma: no cover
            self.store.log("error", "watcher", f"ingest failed {p.name}: {e}")

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._trigger(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory and event.src_path.endswith(".md"):
            self._trigger(event.src_path)


class DropWatcher:
    """监听 dropDir：把素材丢进去即自动入库。start/stop 可反复（监听开关接真）。"""

    def __init__(self, drop_dir: Path, store: SQLiteStore, pipeline: IngestPipeline):
        self.drop_dir = Path(drop_dir)
        self.drop_dir.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.pipeline = pipeline
        self._observer = None

    def start(self) -> None:
        if self._observer is not None and self._observer.is_alive():
            return
        self._observer = Observer()
        self._observer.schedule(_Handler(self.pipeline, self.drop_dir, self.store),
                                str(self.drop_dir), recursive=False)
        self._observer.daemon = True
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None

    def ingest_existing(self) -> int:
        """启动时把 dropbox 里已存在的素材一次性入库。"""
        n = 0
        for p in sorted(self.drop_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in (".md", ".txt", ".rst"):
                r = self.pipeline.ingest_file(p)
                self.store.log("info", "drop", f"boot ingest {p.name} → {r}")
                n += 1
        return n


# ---------- Vault 磁盘真相监听（资源管理器 M-D） ----------
def _dirty_parent(rel_of, event):
    """纯函数（可零 Qt 单测）：fs 事件 → 需刷新的父目录 rel（相对监听根，''=根）。
    盘外路径 / 隐藏项（.xx、~$xx）→ None；目录事件 → 自身；文件 → 父目录。"""
    src = rel_of(event.src_path)
    if src is None:
        return None
    if src == ".":
        src = ""
    if not src:                                   # 根目录自身
        return "" if event.is_directory else None
    name = PurePosixPath(src).name
    if name.startswith(".") or name.startswith("~$"):
        return None
    if event.is_directory:
        return src
    parent = str(PurePosixPath(src).parent)
    return "" if parent == "." else parent


class _VaultHandler(FileSystemEventHandler):
    """回调发生在 observer 线程——UI 侧经 Qt signal queued 编组，这里绝不留 Qt 引用。"""

    def __init__(self, vault_path: Path, on_dirty_dir):
        self.vault_path = Path(vault_path).resolve()
        self.on_dirty_dir = on_dirty_dir          # callable(rel_dir: str)

    def _rel(self, path):
        try:
            return Path(path).resolve().relative_to(self.vault_path).as_posix()
        except (ValueError, OSError):
            return None

    def _emit(self, path, is_dir):
        d = _dirty_parent(self._rel,
                          SimpleNamespace(src_path=path, is_directory=is_dir))
        if d is not None:
            self.on_dirty_dir(d)

    def on_created(self, event):
        self._emit(event.src_path, event.is_directory)

    def on_deleted(self, event):
        self._emit(event.src_path, event.is_directory)

    def on_modified(self, event):
        self._emit(event.src_path, event.is_directory)

    def on_moved(self, event):
        self._emit(event.src_path, event.is_directory)
        dest = getattr(event, "dest_path", None)
        if dest:
            self._emit(dest, event.is_directory)


class VaultScanWatcher:
    """递归监听 Vault 只做「脏目录上报」：不入库、不写日志、不抑制自写——
    合并去抖与幂等刷新全在 UI 槽侧（M-D 拍板：监听器做哑巴，策略归界面）。"""

    def __init__(self, vault_path: Path, on_dirty_dir):
        self.vault_path = Path(vault_path)
        self.on_dirty_dir = on_dirty_dir
        self._observer = None

    def start(self) -> None:
        if self._observer is not None:
            return
        self._observer = Observer()
        self._observer.schedule(
            _VaultHandler(self.vault_path, self.on_dirty_dir),
            str(self.vault_path), recursive=True)
        self._observer.daemon = True
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None