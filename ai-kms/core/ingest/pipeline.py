"""入库管线编排：清洗 → 分类路由 → 原子抽取 → 去重/增量合并 → 原生MD落盘 + 双链/标签 + 索引。

对应 "一次解析，原子沉淀，终身复用"：
  入库时把所有判断做一遍并缓存；之后不再重读原文。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from core.ingest.cleaner import Cleaner
from core.classify.router import ClassificationRouter
from core.atoms.extractor import AtomExtractor
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault, slugify

# GUI"导入文件夹/拖拽入库"与 ingest_dir 共用的扩展名白名单
SUPPORTED_EXTS = (".md", ".txt", ".rst")


def _is_supported_file(p: Path) -> bool:
    """白名单扩展名，且排除隐藏 / Office 临时（~$）/ .tmp 文件。"""
    if p.suffix.lower() not in SUPPORTED_EXTS:
        return False
    n = p.name
    if n.startswith(".") or n.startswith("~$") or n.lower().endswith(".tmp"):
        return False
    return True


def iter_supported_files(dir_path) -> list:
    """目录 → 递归枚举受支持文件（排序，去隐藏/临时）。非目录返回 []。"""
    d = Path(dir_path)
    if not d.is_dir():
        return []
    return sorted(p for p in d.rglob("*") if p.is_file() and _is_supported_file(p))


# NOTE: 去重靠 find_by_dedup 查询而非 DB UNIQUE 约束，与 watcher 线程并发
# 存在理论竞态（触发条件极苛刻：正常导入目录 ≠ data/dropbox）。批量导入
# 走主线程分批、不新增线程；若日后分类升级为 LLM（换 worker 线程），需同步
# 给 dedup_key 建 UNIQUE 索引并把 find+insert 包进同一事务。
def collect_importable(paths) -> tuple:
    """把文件/目录混排的路径归一为 (待入库文件列表, 跳过项列表)。

    目录递归枚举，单文件按白名单过滤；结果按 resolve 去重。
    GUI 走这里（过滤隐藏/临时）；CLI batch 直调 ingest_dir，行为保持旧样，
    这处分歧是刻意的——批量导入面向任意用户目录，需要更保守的过滤。
    """
    files, skipped = [], []
    for raw in paths:
        q = Path(raw)
        if q.is_dir():
            files.extend(iter_supported_files(q))
        elif q.is_file():
            if _is_supported_file(q):
                files.append(q)
            else:
                skipped.append(q.name)
        else:
            skipped.append(str(raw))
    seen, uniq = set(), []
    for f in files:
        k = str(f.resolve())
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq, skipped


class IngestPipeline:
    def __init__(self, store: SQLiteStore, vault: Vault):
        self.store = store
        self.vault = vault
        self.cleaner = Cleaner()
        self.router = ClassificationRouter(store)
        self.extractor = AtomExtractor()

    # ---------- 对外 ----------
    def ingest_raw(self, raw: str, source_label: str = "manual",
                   force_topic: str | None = None) -> dict:
        """录入一段原始素材（剪贴板/文件内容），走完整管线。

        force_topic: 新建笔记时手动指定主题，跳过规则判断结果。
        """
        cleaned = self.cleaner.clean(raw)
        if not cleaned:
            return {"ok": False, "reason": "empty"}

        title = self.cleaner.extract_title_hint(cleaned)
        # 双链目标：从正文里解析 [[wikilink]]（对应 Obsidian 思想）
        wikilinks = [t for t in Vault.extract_wikilinks(cleaned) if t not in (title,)]
        dedup_key = Vault.make_dedup_key(cleaned)

        # 1) 去重：按内容指纹查库
        existing = self.store.find_by_dedup(dedup_key)
        if existing:
            return {"ok": False, "reason": "duplicate", "dup_of": existing["path"]}

        # 2) 分类路由（单次判断）；force_topic 覆盖判断结果
        cls = self.router.classify(cleaned, title)
        if force_topic:
            cls = dict(cls)
            cls["topic"] = force_topic

        # 3) 原子抽取 + 缓存
        atoms = self.extractor.extract_all(cleaned)

        # 4) 原生 Markdown 落盘（文件名冲突时自动加序号，防止不同笔记互相覆盖）
        filename = self._unique_filename(slugify(cls["topic"]), title)
        rel_path = self.vault.rel_path_for(self.vault.write_note(filename, cleaned))

        # 5) 登记 + 索引
        self.store.upsert_file(rel_path, title, cls["topic"], cls["type"],
                               cls["priority"], dedup_key, os.path.getmtime(
                                   str(self.vault.vault_path / rel_path)))
        self.store.add_atoms(rel_path, atoms)
        self.store.add_link(rel_path, cls["topic"])  # 主题作为软链接
        for target in wikilinks:                       # 真正的 [[双链]]
            self.store.add_link(rel_path, target)
        # 落缓存：分类由判断中枢持久化，供后续"一次判断终身复用"（不再重读原文）
        self.store.save_classification(rel_path, cls)
        self.store.add_atoms(rel_path, [{"kind": "topic", "value": cls["topic"], "weight": 1.0}])
        self.store.index_note_tokens(rel_path, cleaned)      # FTS 索引（fts_ok=False 时空操作）

        return {
            "ok": True,
            "rel_path": rel_path,
            "title": title,
            "topic": cls["topic"],
            "type": cls["type"],
            "priority": cls["priority"],
            "atoms": len(atoms),
            "cached_classify": cls["cached"],
        }

    def ingest_file(self, file_path) -> dict:
        """把一个文件内容整篇收录。"""
        p = Path(file_path)
        raw = p.read_text(encoding="utf-8", errors="ignore")
        return self.ingest_raw(raw, str(p))

    # ---------- 阶段4：批量 / 重建 ----------
    def ingest_dir(self, dir_path) -> dict:
        """批量收录一个目录下的所有 .md/.txt 文件。"""
        d = Path(dir_path)
        if not d.is_dir():
            return {"ok": False, "reason": "not_a_directory"}
        stats = {"ingested": 0, "duplicate": 0, "empty": 0, "errors": 0}
        details = []
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTS:
                continue
            try:
                res = self.ingest_file(p)
                if res["ok"]:
                    stats["ingested"] += 1
                    details.append(res["rel_path"])
                elif res["reason"] == "duplicate":
                    stats["duplicate"] += 1
                else:
                    stats["empty"] += 1
            except Exception as e:
                stats["errors"] += 1
                details.append(f"ERR {p.name}: {e}")
                self.store.log("error", "batch", f"{p.name}: {e}")
        return {"ok": True, **stats, "details": details}

    def reindex(self) -> dict:
        """从 Vault 现有 .md 文件重建索引（DB 丢失/损坏后恢复用）。

        已有分类缓存的跳过判断（不重读上下文），只补登记与原子。
        """
        stats = {"scanned": 0, "indexed": 0, "skipped": 0}
        for rel in self.vault.all_markdown_files():
            stats["scanned"] += 1
            if self.store.file_exists(rel):
                stats["skipped"] += 1
                continue
            content = self.vault.read_note(rel)
            title = Vault.extract_title(content) or self.cleaner.extract_title_hint(content)
            dedup_key = Vault.make_dedup_key(content)
            # 已有该指纹的登记 → 只跳过（不同路径同内容不重复建原子）
            if self.store.find_by_dedup(dedup_key):
                stats["skipped"] += 1
                continue
            # 分类：优先按路径已有缓存，否则现场判断一次并缓存
            cls = self.router.classify(content, title, rel_path=rel)
            atoms = self.extractor.extract_all(content)
            full = self.vault.vault_path / rel
            self.store.upsert_file(rel, title, cls["topic"], cls["type"],
                                   cls["priority"], dedup_key, os.path.getmtime(str(full)))
            self.store.add_atoms(rel, atoms)
            self.store.add_link(rel, cls["topic"])
            for target in Vault.extract_wikilinks(content):
                self.store.add_link(rel, target)
            self.store.save_classification(rel, cls)
            self.store.add_atoms(rel, [{"kind": "topic", "value": cls["topic"], "weight": 1.0}])
            self.store.index_note_tokens(rel, content)
            stats["indexed"] += 1
        self.store.log("info", "reindex", str(stats))
        return stats

    # ---------- 编辑/写回后的统一重建 ----------
    def reindex_note(self, rel: str, content: str | None = None) -> dict:
        """meta + 原子 + 双链 + FTS 一次重建（原 GUI _reindex_note 下沉为权威版）。"""
        content = self.vault.read_note(rel) if content is None else content
        title = Vault.extract_title(content) or self.cleaner.extract_title_hint(content)
        dedup = Vault.make_dedup_key(content)
        self.store.update_edit_meta(rel, title, dedup)
        self.store.clear_atoms(rel)
        atoms = self.extractor.extract_all(content)
        self.store.add_atoms(rel, atoms)
        self.store.clear_links_from(rel)
        row = self.store.get_file(rel)
        cls_topic = row["topic"] if row else "未分类"
        self.store.add_link(rel, cls_topic)                      # 保留主题软链
        for t in Vault.extract_wikilinks(content):
            self.store.add_link(rel, t)
        self.store.add_atoms(rel, [{"kind": "topic", "value": cls_topic, "weight": 1.0}])
        self.store.index_note_tokens(rel, content)
        return {"ok": True, "title": title, "atoms": len(atoms)}

    # ---------- FTS 对账与分批重建 ----------
    def fts_missing(self) -> list[str]:
        """双向差集：待补索引的 path + 多余的幽灵行。返回 (missing, ghost)。"""
        registered = {f["path"] for f in self.store.all_files()}
        indexed = self.store.fts_paths()
        return sorted(registered - indexed), sorted(indexed - registered)

    def fts_rebuild_chunk(self, limit: int = 50) -> dict:
        """重建一批：先删幽灵行，再补缺失（每批 limit 篇）。返回进度。"""
        if not self.store.fts_ok:
            return {"ok": False, "reason": "fts_unavailable"}
        missing, ghost = self.fts_missing()
        for g in ghost[:limit]:
            self.store.delete_note_tokens(g)
        batch = missing[:limit]
        for rel in batch:
            content = self.vault.read_note(rel)
            if content:
                self.store.index_note_tokens(rel, content)
        still = len(missing) - len(batch)
        return {"ok": True, "indexed": len(batch), "remaining": still,
                "removed_ghost": min(len(ghost), limit)}

    # ---------- 内部 ----------
    @staticmethod
    def _plain_filename(title: str, topic: str) -> str:
        """生成稳定文件名，避免撞名；含主题前缀便于浏览。"""
        base = slugify(title or "untitled")
        base = re.sub(r"[#{}]|\[|\]", "", base)[:60]
        return f"{base}"

    def _unique_filename(self, topic_dir: str, title: str) -> str:
        """保证落盘路径唯一：同名不同内容 → 自动追加 -2、-3…（阶段4 修复覆盖 bug）。"""
        base = self._plain_filename(title, topic_dir)
        rel = f"{topic_dir}/{base}.md"
        # 路径不存在，或存在但内容恰好相同（重复入库已在前面拦截）→ 直接用
        if not (self.vault.vault_path / rel).exists():
            return rel
        n = 2
        while (self.vault.vault_path / f"{topic_dir}/{base}-{n}.md").exists():
            n += 1
        return f"{topic_dir}/{base}-{n}.md"