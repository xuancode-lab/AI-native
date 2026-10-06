"""SQLite 存储层：原子索引 / 文件登记 / 分类缓存 / 双链边 / 版本快照 / 日志。

设计要点（对应"一次解析终身复用"）：
  - atoms 存入库时抽取的原子片段+分类，后续判断只查这里，不重读原文。
  - vault_files 登记每个入库文件的指纹(dedup_key)，用于去重。
"""
from __future__ import annotations

import sqlite3
import json
from pathlib import Path

from config import settings
from core.storage.fts import tokenize


class SQLiteStore:
    def __init__(self, db_path: Path | None = None, read_only: bool = False):
        self.db_path = Path(db_path) if db_path else settings.DB_PATH
        self._read_only = read_only
        self._epoch = 0          # 写路径计数器：graph 缓存失效的唯一依据（GIL 下 int 自增原子）
        if read_only:
            # 只读连接（MCP/导出场景）：绝不跑 _init_schema——mode=ro 下 CREATE TABLE 必报错。
            if not self.db_path.exists():
                raise FileNotFoundError(
                    f"知识库数据库不存在：{self.db_path}（请先在 GUI 入库或运行 python main.py reindex）")
            # 路径可能含空格：as_uri() 自带百分号编码，不能裸拼 file: + 原路径。
            self.conn = sqlite3.connect(
                self.db_path.resolve().as_uri() + "?mode=ro",
                uri=True, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            # 不设 journal_mode PRAGMA（该动作需写权限；主库已是 WAL，只读方直接读已提交快照）。
            self.fts_ok = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notes_fts'"
            ).fetchone() is not None
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _require_write(self) -> None:
        """全部公开写方法首行守卫：只读模式显式报错（比 sqlite OperationalError 可读、可单测）。"""
        if self._read_only:
            raise RuntimeError("只读模式禁止写入知识库")

    @property
    def epoch(self) -> int:
        return self._epoch

    def _bump(self) -> None:
        self._epoch += 1

    def _init_schema(self) -> None:
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS vault_files (
                path       TEXT PRIMARY KEY,
                title      TEXT,
                topic      TEXT,
                type       TEXT,
                priority   INTEGER DEFAULT 0,
                dedup_key  TEXT,
                mtime      REAL,
                usage_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS atoms (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                kind      TEXT,             -- topic|keyword|claim|entity|tag
                value     TEXT NOT NULL,
                weight    REAL DEFAULT 1.0,
                UNIQUE(file_path, kind, value)
            );
            CREATE INDEX IF NOT EXISTS idx_atoms_value ON atoms(value);
            CREATE TABLE IF NOT EXISTS links (
                from_path TEXT NOT NULL,
                to_target TEXT NOT NULL,     -- 双链目标（标题）
                kind      TEXT DEFAULT 'wikilink',
                UNIQUE(from_path, to_target)
            );
            CREATE TABLE IF NOT EXISTS classifications (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                topic    TEXT, type TEXT, priority INTEGER,
                verdict  TEXT,                -- 分类依据 JSON
                classified_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                content   TEXT,               -- 旧内容备份
                reason    TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS logs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                level     TEXT, module TEXT, message TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS categories (
                name       TEXT PRIMARY KEY,           -- 分类名 = topic（文件夹）
                keywords   TEXT DEFAULT '',            -- 逗号分隔触发关键词（供规则分类器）
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS suggestions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                kind       TEXT NOT NULL,              -- 'wikilink' | 'topic'
                file_path  TEXT NOT NULL,
                target     TEXT NOT NULL,              -- wikilink=目标标题 / topic=新分类名
                payload    TEXT DEFAULT '{}',          -- JSON，schema 见 core/curator.py
                confidence REAL DEFAULT 0,
                status     TEXT DEFAULT 'pending',     -- pending|applied|skipped|obsolete
                created_at TEXT DEFAULT (datetime('now','localtime')),
                resolved_at TEXT,
                UNIQUE(kind, file_path, target)
            );
            CREATE INDEX IF NOT EXISTS idx_sugg_status ON suggestions(status, kind);
            CREATE INDEX IF NOT EXISTS idx_links_target ON links(to_target);
            """
        )
        # 轻量迁移：老库补 usage_count 列（新库已包含，CREATE IF NOT EXISTS 不触发）
        cols = {r[1] for r in c.execute("PRAGMA table_info(vault_files)")}
        if "usage_count" not in cols:
            c.execute("ALTER TABLE vault_files ADD COLUMN usage_count INTEGER DEFAULT 0")
        # FTS5 单独容错建（精简版 sqlite 可能没编译；失败则全程走旧检索路径）
        self.fts_ok = False
        try:
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
                      "USING fts5(path UNINDEXED, tokens)")
            self.fts_ok = True
        except sqlite3.OperationalError:
            self.log("warn", "fts", "FTS5 不可用，检索走旧路径")
        self.conn.commit()

    # ---- vault_files ----
    def upsert_file(self, path: str, title: str, topic: str = "未分类",
                    type_: str = "note", priority: int = 0,
                    dedup_key: str | None = None, mtime: float = 0.0) -> None:
        self._require_write()
        self.conn.execute(
            """INSERT INTO vault_files(path,title,topic,type,priority,dedup_key,mtime)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET
                 title=excluded.title, topic=excluded.topic, type=excluded.type,
                 priority=excluded.priority, dedup_key=excluded.dedup_key, mtime=excluded.mtime""",
            (path, title, topic, type_, priority, dedup_key, mtime),
        )
        self.conn.commit()
        self._bump()

    def file_exists(self, path: str) -> bool:
        return self.get_file(path) is not None

    def get_file(self, path: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM vault_files WHERE path=?", (path,))
        return cur.fetchone()

    def find_by_dedup(self, dedup_key: str) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM vault_files WHERE dedup_key=?", (dedup_key,))
        return cur.fetchone()

    def all_files(self):
        return self.conn.execute("SELECT * FROM vault_files").fetchall()

    # ---- 分类（分类层 taxonomy 的持久化扩展，文件夹=分类）----
    def add_category(self, name: str, keywords: list[str] | None = None) -> None:
        """新增自定义分类；keywords = 触发词（命中即归入该 topic）。"""
        self._require_write()
        self.conn.execute(
            "INSERT INTO categories(name, keywords) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET keywords=excluded.keywords",
            (name, ",".join(keywords or [])),
        )
        self.conn.commit()

    def all_categories(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name, keywords FROM categories ORDER BY name").fetchall()
        return [{"name": r["name"],
                 "keywords": [k for k in (r["keywords"] or "").split(",") if k]}
                for r in rows]

    def drop_category(self, name: str) -> None:
        self._require_write()
        self.conn.execute("DELETE FROM categories WHERE name=?", (name,))
        self.conn.commit()

    def custom_topic_rules(self) -> dict[str, list[str]]:
        """供规则分类器加载：{分类名: [触发关键词]}。"""
        return {c["name"]: c["keywords"] for c in self.all_categories()}

    # ---- 记忆权重（GBrain 思想：按使用/浏览频次迭代权重）----
    def touch_file(self, path: str) -> None:
        """记录一次访问，提升该文件与其原子的记忆权重。"""
        self._require_write()
        self.conn.execute(
            "UPDATE vault_files SET usage_count = usage_count + 1 WHERE path=?",
            (path,),
        )
        self.conn.execute(
            "UPDATE atoms SET weight = weight * 1.1 WHERE file_path=?",
            (path,),
        )
        self.conn.commit()
        self._bump()      # usage_count 影响 graph 的节点权重与桶排序

    # ---- 阶段5：编辑/重命名/删除 的索引维护 ----
    def update_edit_meta(self, path: str, title: str, dedup_key: str) -> None:
        """人工编辑保存后：更新标题、内容指纹、mtime。"""
        self._require_write()
        import time as _time
        self.conn.execute(
            "UPDATE vault_files SET title=?, dedup_key=?, mtime=? WHERE path=?",
            (title, dedup_key, _time.time(), path),
        )
        self.conn.commit()
        self._bump()

    def clear_atoms(self, path: str) -> None:
        self._require_write()
        self.conn.execute("DELETE FROM atoms WHERE file_path=?", (path,))
        self.conn.commit()
        self._bump()

    def clear_links_from(self, path: str) -> None:
        self._require_write()
        self.conn.execute("DELETE FROM links WHERE from_path=?", (path,))
        self.conn.commit()
        self._bump()

    def rename_path(self, old: str, new: str) -> None:
        """重命名后同步所有关联表的路径引用。"""
        self._require_write()
        tables = [("vault_files", "path"), ("atoms", "file_path"),
                  ("links", "from_path"), ("classifications", "file_path"),
                  ("snapshots", "file_path"), ("suggestions", "file_path")]
        if self.fts_ok:
            tables.append(("notes_fts", "path"))
        for table, col in tables:
            self.conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new, old))
        # 双链目标若指向旧标题，也一并更新（notes_fts tokens 不随改名变，无需重分词）
        import os
        old_t = os.path.splitext(os.path.basename(old))[0]
        new_t = os.path.splitext(os.path.basename(new))[0]
        self.conn.execute("UPDATE links SET to_target=? WHERE to_target=?",
                          (new_t, old_t))
        # 未处理的链接/关联建议跟随改名（edge 与 wikilink 的 target 都是 stem）
        self.conn.execute(
            "UPDATE suggestions SET target=? WHERE kind IN ('wikilink','edge') "
            "AND target=? AND status='pending'", (new_t, old_t))
        self.conn.commit()
        self._bump()

    def delete_file(self, path: str) -> None:
        """删除笔记：清理其在所有表中的记录，失效其相关建议。"""
        self._require_write()
        tables = [("vault_files", "path"), ("atoms", "file_path"),
                  ("links", "from_path"), ("classifications", "file_path"),
                  ("snapshots", "file_path")]
        if self.fts_ok:
            tables.append(("notes_fts", "path"))
        for table, col in tables:
            self.conn.execute(f"DELETE FROM {table} WHERE {col}=?", (path,))
        # 该笔记的 pending 建议 → obsolete；别人链向它的也失效（标题=文件名 stem）
        import os
        stem = os.path.splitext(os.path.basename(path))[0]
        self.conn.execute(
            "UPDATE suggestions SET status='obsolete', "
            "resolved_at=datetime('now','localtime') "
            "WHERE status='pending' AND (file_path=? OR (kind IN ('wikilink','edge') AND target=?))",
            (path, stem))
        self.conn.commit()
        self._bump()

    def top_used(self, limit: int = 10):
        return self.conn.execute(
            "SELECT * FROM vault_files ORDER BY usage_count DESC LIMIT ?", (limit,)
        ).fetchall()

    # ---- atoms ----
    def add_atoms(self, file_path: str, atoms: list[dict]) -> int:
        self._require_write()
        added = 0
        for a in atoms:
            kind, value, weight = a["kind"], a["value"], a.get("weight", 1.0)
            if len(str(value)) < settings.MIN_ATOM_LEN:
                continue
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO atoms(file_path,kind,value,weight)
                   VALUES(?,?,?,?)""",
                (file_path, kind, str(value), float(weight)),
            )
            added += cur.rowcount
        self.conn.commit()
        if added:
            self._bump()
        return added

    def atoms_for(self, file_path: str):
        return self.conn.execute(
            "SELECT * FROM atoms WHERE file_path=?", (file_path,)
        ).fetchall()

    def search_atoms(self, value_like: str):
        return self.conn.execute(
            "SELECT * FROM atoms WHERE value LIKE ?", (f"%{value_like}%",)
        ).fetchall()

    # ---- links ----
    def add_link(self, from_path: str, to_target: str,
                 kind: str = "wikilink") -> None:
        self._require_write()
        self.conn.execute(
            "INSERT OR IGNORE INTO links(from_path,to_target,kind) VALUES(?,?,?)",
            (from_path, to_target, kind),
        )
        self.conn.commit()
        self._bump()

    def delete_link(self, from_path: str, to_target: str) -> None:
        self._require_write()
        self.conn.execute(
            "DELETE FROM links WHERE from_path=? AND to_target=?",
            (from_path, to_target),
        )
        self.conn.commit()
        self._bump()

    def links_for(self, from_path: str):
        return self.conn.execute(
            "SELECT to_target FROM links WHERE from_path=?", (from_path,)
        ).fetchall()

    def inlinks(self, target: str) -> list[str]:
        """指入某标题的出链方（谁链接到了它）。"""
        return [r["from_path"] for r in self.conn.execute(
            "SELECT from_path FROM links WHERE to_target=?", (target,)).fetchall()]

    def remove_atoms_kind(self, file_path: str, kind: str) -> None:
        """删除某笔记指定类型的原子（分类校准时换 topic 原子用）。"""
        self._require_write()
        self.conn.execute(
            "DELETE FROM atoms WHERE file_path=? AND kind=?", (file_path, kind))
        self.conn.commit()
        self._bump()

    # ---- FTS5（notes_fts：path 不索引，tokens=分词后空格文本）----
    def index_note_tokens(self, rel: str, text: str) -> None:
        """delete-then-insert，幂等；fts_ok=False 时空操作。"""
        self._require_write()
        if not self.fts_ok:
            return
        self.conn.execute("DELETE FROM notes_fts WHERE path=?", (rel,))
        self.conn.execute("INSERT INTO notes_fts(path,tokens) VALUES(?,?)",
                          (rel, " ".join(tokenize(text))))
        self.conn.commit()

    def delete_note_tokens(self, rel: str) -> None:
        self._require_write()
        if not self.fts_ok:
            return
        self.conn.execute("DELETE FROM notes_fts WHERE path=?", (rel,))
        self.conn.commit()

    def fts_search(self, tokens: list[str], limit: int) -> list[dict]:
        """BM25 检索。tokens 需来自 fts.tokenize（防御性剔除引号/通配符）。"""
        if not self.fts_ok or not tokens:
            return []
        safe = [t for t in tokens if t and not set(t) & set('"\'*-^')]
        if not safe:
            return []
        match = " OR ".join(f'"{t}"' for t in safe)
        rows = self.conn.execute(
            "SELECT path, -rank AS score FROM notes_fts "
            "WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, limit)).fetchall()
        return [{"path": r["path"], "score": float(r["score"])} for r in rows]

    def fts_paths(self) -> set[str]:
        if not self.fts_ok:
            return set()
        return {r["path"] for r in self.conn.execute("SELECT path FROM notes_fts")}

    def fts_health(self) -> tuple[int, int]:
        """(fts 行数, vault_files 登记数)——不等即需要重建对账。"""
        n_vault = self.conn.execute(
            "SELECT COUNT(*) c FROM vault_files").fetchone()["c"]
        if not self.fts_ok:
            return 0, n_vault
        n_fts = self.conn.execute(
            "SELECT COUNT(*) c FROM notes_fts").fetchone()["c"]
        return n_fts, n_vault

    # ---- suggestions（AI 建议审阅队列）----
    def add_suggestions(self, rows: list[dict]) -> int:
        """批量插入。冲突(kind,file_path,target)时：pending 行刷新 payload/confidence，
        已处理(applied/skipped/obsolete)行不动——重扫不会复活用户已跳过的建议。"""
        self._require_write()
        n = 0
        for r in rows:
            cur = self.conn.execute(
                """INSERT INTO suggestions(kind,file_path,target,payload,confidence)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(kind,file_path,target) DO UPDATE SET
                     payload=excluded.payload, confidence=excluded.confidence
                   WHERE suggestions.status='pending'""",
                (r["kind"], r["file_path"], r["target"],
                 json.dumps(r.get("payload", {}), ensure_ascii=False),
                 float(r.get("confidence", 0))))
            n += cur.rowcount
        self.conn.commit()
        return n

    def pending_suggestions(self, kind: str | None = None,
                            min_conf: float | None = None,
                            limit: int = 500) -> list[dict]:
        q = "SELECT * FROM suggestions WHERE status='pending'"
        args: list = []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if min_conf is not None:
            q += " AND confidence>=?"
            args.append(min_conf)
        q += " ORDER BY confidence DESC, id LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(q, args).fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except json.JSONDecodeError:
                d["payload"] = {}
            out.append(d)
        return out

    def list_suggestions(self, status: str, kind: str | None = None,
                         limit: int = 500) -> list[dict]:
        q = "SELECT * FROM suggestions WHERE status=?"
        args: list = [status]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn.execute(q, args).fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except json.JSONDecodeError:
                d["payload"] = {}
            out.append(d)
        return out

    def get_suggestion(self, sid: int) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM suggestions WHERE id=?", (sid,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except json.JSONDecodeError:
            d["payload"] = {}
        return d

    def resolve_suggestion(self, sid: int, status: str) -> None:
        self._require_write()
        self.conn.execute(
            "UPDATE suggestions SET status=?, resolved_at=datetime('now','localtime') "
            "WHERE id=?", (status, sid))
        self.conn.commit()

    def count_pending(self, kind: str | None = None) -> int:
        q = "SELECT COUNT(*) c FROM suggestions WHERE status='pending'"
        if kind:
            q += " AND kind=?"
            return self.conn.execute(q, (kind,)).fetchone()["c"]
        return self.conn.execute(q).fetchone()["c"]

    def mark_obsolete(self, file_path: str | None = None,
                      target: str | None = None) -> None:
        """按路径/目标失效 pending 建议（apply 指纹过期、重读校准后清理用）。"""
        self._require_write()
        conds, args = [], []
        if file_path:
            conds.append("file_path=?")
            args.append(file_path)
        if target:
            conds.append("target=?")
            args.append(target)
        if not conds:
            return
        self.conn.execute(
            "UPDATE suggestions SET status='obsolete', "
            "resolved_at=datetime('now','localtime') "
            f"WHERE status='pending' AND ({' OR '.join(conds)})", args)
        self.conn.commit()

    def purge_resolved(self) -> int:
        self._require_write()
        cur = self.conn.execute(
            "DELETE FROM suggestions WHERE status IN('applied','skipped','obsolete')")
        self.conn.commit()
        return cur.rowcount

    # ---- classifications ----
    def save_classification(self, file_path: str, c: dict) -> None:
        self._require_write()
        self.conn.execute(
            """INSERT INTO classifications(file_path,topic,type,priority,verdict)
               VALUES(?,?,?,?,?)""",
            (file_path, c.get("topic"), c.get("type"),
             c.get("priority", 0), json.dumps(c.get("verdict", {}), ensure_ascii=False)),
        )
        self.conn.commit()

    def latest_classification(self, file_path: str) -> sqlite3.Row | None:
        cur = self.conn.execute(
            "SELECT * FROM classifications WHERE file_path=? ORDER BY id DESC LIMIT 1",
            (file_path,),
        )
        return cur.fetchone()

    # ---- snapshots ----
    def snapshot(self, file_path: str, content: str, reason: str) -> None:
        self._require_write()
        self.conn.execute(
            "INSERT INTO snapshots(file_path,content,reason) VALUES(?,?,?)",
            (file_path, content, reason),
        )
        self.conn.commit()

    # ---- logs ----
    def log(self, level: str, module: str, message: str) -> None:
        self._require_write()
        try:
            self.conn.execute(
                "INSERT INTO logs(level,module,message) VALUES(?,?,?)",
                (level, module, str(message)[:2000]),
            )
            self.conn.commit()
        except Exception:
            pass

    # ---- 导出专用只读查询（供 core/export.py；path 均为 vault 相对 POSIX 路径）----
    def iter_atoms(self, batch: int = 2000):
        """按 (file_path,kind,value) 键集分页流式导出全部原子：内存恒定 + 顺序确定。"""
        last: tuple[str, str, str] | None = None
        while True:
            if last is None:
                rows = self.conn.execute(
                    "SELECT file_path,kind,value,weight FROM atoms "
                    "ORDER BY file_path,kind,value LIMIT ?", (batch,)).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT file_path,kind,value,weight FROM atoms "
                    "WHERE (file_path,kind,value) > (?,?,?) "
                    "ORDER BY file_path,kind,value LIMIT ?",
                    (*last, batch)).fetchall()
            if not rows:
                return
            for r in rows:
                yield dict(r)
            last = (rows[-1]["file_path"], rows[-1]["kind"], rows[-1]["value"])

    def dump_links(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT from_path,to_target,kind FROM links "
            "ORDER BY from_path,to_target").fetchall()]

    def dump_classifications(self) -> list[dict]:
        """每文件只导最新一条（id 最大）；verdict 坏 JSON 保留 raw，绝不抛。"""
        rows = self.conn.execute(
            """SELECT c.file_path,c.topic,c.type,c.priority,c.verdict,c.classified_at
               FROM classifications c
               JOIN (SELECT file_path, MAX(id) mid FROM classifications
                     GROUP BY file_path) t ON c.id = t.mid
               ORDER BY c.file_path""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["verdict"] = json.loads(d.get("verdict") or "{}")
            except json.JSONDecodeError:
                d["verdict"] = {"verdict_raw": d.get("verdict")}
            out.append(d)
        return out

    def dump_suggestions(self) -> list[dict]:
        """全部状态（pending/applied/skipped/obsolete）——审阅队列全貌；id 不导出（跨库无意义）。"""
        rows = self.conn.execute(
            """SELECT kind,file_path,target,payload,confidence,status,
                      created_at,resolved_at
               FROM suggestions ORDER BY kind,file_path,target""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except json.JSONDecodeError:
                d["payload"] = {"payload_raw": d.get("payload")}
            out.append(d)
        return out

    def table_counts(self) -> dict[str, int]:
        """manifest 用的自然计数（各表全量行数；classifications 是全历史，
        导出文件"每文件最新一条"的行数 ≤ 此值属预期，见 core/export.py）。"""
        def _n(sql: str) -> int:
            return self.conn.execute(sql).fetchone()[0]
        counts = {
            "files": _n("SELECT COUNT(*) FROM vault_files"),
            "atoms": _n("SELECT COUNT(*) FROM atoms"),
            "links": _n("SELECT COUNT(*) FROM links"),
            "classifications": _n("SELECT COUNT(*) FROM classifications"),
            "categories": _n("SELECT COUNT(*) FROM categories"),
            "suggestions": _n("SELECT COUNT(*) FROM suggestions"),
            "fts": _n("SELECT COUNT(*) FROM notes_fts") if self.fts_ok else 0,
        }
        return counts

    def close(self) -> None:
        self.conn.close()