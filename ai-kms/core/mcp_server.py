"""MCP 只读服务：把本地知识库变成任意 AI 客户端可低成本调用的"工具"。

分层：
  KmsTools —— 4 个只读工具的实现本体，零 mcp 依赖，单测直接调这里；
  build_server() —— FastMCP/MCPServer 薄壳（mcp 已安装时才 import）；
  run_stdio_server() —— 入口：只读直连主库，stdio 通道跑协议。

只读纪律（本期铁律，不开放任何写接口）：
  - SQLiteStore(read_only=True)：写方法一律 RuntimeError；
  - classify_hint 直接查缓存，绝不触发 AI provider/API 调用——
    "判断只查缓存原子、不重读原文"的核心信条对外同样成立；
  - stdout 是协议通道：全模块零 print，审计走 LOG_DIR/mcp.log 文件日志。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys

from config import settings
from core.graph.engine import GraphEngine
from core.graph.search import KnowledgeSearch
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault

_INSTRUCTIONS = (
    "本地个人知识库（ai-kms）只读查询服务。四个工具全部只读：判断类查询只命中"
    "入库时沉淀的缓存原子，绝不重读原始文档。score 是引擎相对分，仅在同一次调用内"
    "可排序比较，跨调用/跨引擎(fts|legacy)不可比。note_path 参数是 vault 内 POSIX "
    "相对路径（如 '技术/图谱.md'），可从 kms.search 返回的 path 字段获取。"
)


class KmsTools:
    """只读工具实现本体（不依赖 mcp 包）。"""

    def __init__(self, store: SQLiteStore, vault: Vault | None = None):
        self.store = store
        self.search_engine = KnowledgeSearch(store, vault)
        self.graph = GraphEngine(store, vault)

    def search(self, query: str, top_n: int = 8) -> dict:
        n = max(1, min(50, int(top_n)))
        res = self.search_engine.search(query, n)
        engine = "fts" if self.store.fts_ok else "legacy"
        if not res and engine == "fts":
            n_fts, _ = self.store.fts_health()
            if n_fts == 0:   # FTS 表已建但从未灌词（没跑 fts-rebuild）→ 显式降级
                res = self.search_engine._search_legacy(query, n)
                engine = "legacy"
        return {"engine": engine, "query": query,
                "results": res}   # 红线 6 字段原样透传：path/title/topic/score/backlinks/neighbors

    def atoms(self, note_path: str) -> dict:
        """缓存原子按 kind 分组返回；只读 SQLite，不碰原文。"""
        if self.store.get_file(note_path) is None:
            raise ValueError(f"笔记未入库：{note_path}")
        grouped: dict[str, list] = {}
        for r in self.store.atoms_for(note_path):
            grouped.setdefault(r["kind"], []).append(
                {"value": r["value"], "weight": r["weight"]})
        return {"path": note_path, "atoms": grouped}

    def classify_hint(self, note_path: str) -> dict:
        """纯分类缓存查询：无缓存返回 topic=None，绝不触发分类器/API。"""
        row = self.store.latest_classification(note_path)
        if row is None:
            return {"path": note_path, "cached": False, "topic": None}
        try:
            verdict = json.loads(row["verdict"] or "{}")
        except (json.JSONDecodeError, TypeError):
            verdict = {"verdict_raw": row["verdict"]}
        return {"path": note_path, "cached": True, "topic": row["topic"],
                "type": row["type"], "priority": row["priority"],
                "verdict": verdict}

    def graph_neighbors(self, note_path: str, limit: int = 20) -> dict:
        limit = max(1, min(100, int(limit)))
        items = []
        for p in self.graph.neighbors(note_path)[:limit]:
            f = self.store.get_file(p)
            items.append({"path": p,
                          "title": f["title"] if f is not None else p.rsplit("/", 1)[-1][:-3],
                          "topic": f["topic"] if f is not None else None})
        out = {"path": note_path, "neighbors": items}
        if not items:
            out["note"] = "笔记未入库或图谱中无关联"
        return out


def build_server(db_path=None):
    """薄壳：只读 store + KmsTools + 4 个 @tool 注册。返回 MCPServer。"""
    from mcp.server.mcpserver import MCPServer
    store = SQLiteStore(db_path, read_only=True)
    tools = KmsTools(store, Vault())
    log = logging.getLogger("kms.mcp")
    mcp = MCPServer(name="kms", instructions=_INSTRUCTIONS)

    @mcp.tool(name="kms.search",
              description="检索本地知识库笔记（FTS5/BM25，自动降级）。返回 "
                          "{engine, query, results:[{path,title,topic,score,backlinks,neighbors}]}。"
                          "score 仅同一次调用内可比。")
    def kms_search(query: str, top_n: int = 8) -> dict:
        log.info("tool=kms.search query=%r top_n=%s", query, top_n)
        return tools.search(query, top_n)

    @mcp.tool(name="kms.atoms",
              description="读取某笔记的缓存原子（关键词/命题/标签/主题，按 kind 分组，含权重）。"
                          "只查 SQLite 派生数据，不重读原文。笔记未入库时报错。")
    def kms_atoms(note_path: str) -> dict:
        log.info("tool=kms.atoms path=%r", note_path)
        return tools.atoms(note_path)

    @mcp.tool(name="kms.classify_hint",
              description="查询某笔记的分类缓存（topic/type/priority/verdict）。"
                          "纯缓存命中，绝不触发任何 AI 分类或 API 调用；无缓存时 topic=null。")
    def kms_classify_hint(note_path: str) -> dict:
        log.info("tool=kms.classify_hint path=%r", note_path)
        return tools.classify_hint(note_path)

    @mcp.tool(name="kms.graph_neighbors",
              description="知识图谱直接邻居（双向边，含对方 path/title/topic）。"
                          "用于发现关联笔记；无关联返回空列表。")
    def kms_graph_neighbors(note_path: str, limit: int = 20) -> dict:
        log.info("tool=kms.graph_neighbors path=%r limit=%s", note_path, limit)
        return tools.graph_neighbors(note_path, limit)

    return mcp


def run_stdio_server() -> None:
    """`python main.py mcp` 入口：只读直连 data/kms.db，stdio 跑协议。"""
    # 日志只进文件/stderr——stdout 是 stdio 协议通道，绝对不能碰。
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO,
                        filename=str(settings.LOG_DIR / "mcp.log"),
                        format="%(asctime)s %(message)s",
                        encoding="utf-8")   # Windows 缺省会落到 GBK，中文查询词会乱码
    try:
        server = build_server()   # 只读构造：库不存在/坏库此刻即暴露
    except FileNotFoundError as e:
        print(f"知识库打不开：{e}", file=sys.stderr)
        raise SystemExit(1)
    except sqlite3.Error as e:
        print(f"知识库不可读（坏库或 WAL 恢复失败）：{e}", file=sys.stderr)
        raise SystemExit(1)
    server.run(transport="stdio")
