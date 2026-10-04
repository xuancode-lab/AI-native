"""文件监听：watchdog 监听 Vault，新 .md/掉落素材自动入库。"""
from __future__ import annotations

import os
import time
from pathlib import Path
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
            if p.suffix.lower() in (".md", ".txt", ".rst"):
                res = self.pipeline.ingest_file(p)
                self.store.log("info", "watcher", f"auto-ingest {p.name} → {res}")
                # 收录完成的素材原文件可移至 done（可选保留策略）
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
    """监听 dropDir：把素材丢进去即自动入库。"""

    def __init__(self, drop_dir: Path, store: SQLiteStore, pipeline: IngestPipeline):
        self.drop_dir = Path(drop_dir)
        self.drop_dir.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.pipeline = pipeline
        self._observer = Observer()

    def start(self) -> None:
        handler = _Handler(self.pipeline, self.drop_dir, self.store)
        self._observer.schedule(handler, str(self.drop_dir), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

    def ingest_existing(self) -> int:
        """启动时把 dropbox 里已存在的素材一次性入库。"""
        n = 0
        for p in sorted(self.drop_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in (".md", ".txt", ".rst"):
                r = self.pipeline.ingest_file(p)
                self.store.log("info", "drop", f"boot ingest {p.name} → {r}")
                n += 1
        return n