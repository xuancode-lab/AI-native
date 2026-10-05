"""MCP 只读工具：KmsTools 纯函数本体测试（无需安装 mcp 包）+ build_server 冒烟（装了才跑）。"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import unittest

from tests.conftest import TempEnv

from core.mcp_server import KmsTools


class TestKmsTools(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        s = self.store = self.env.store
        s.upsert_file("技术/图谱.md", "图谱", "技术")
        s.upsert_file("技术/检索.md", "检索", "技术")
        s.upsert_file("读书/孤岛.md", "孤岛", "读书")
        s.add_atoms("技术/图谱.md", [
            {"kind": "keyword", "value": "节点"},
            {"kind": "keyword", "value": "图谱"},
            {"kind": "claim", "value": "图谱由节点和边构成"},
            {"kind": "tag", "value": "图算法"},
            {"kind": "topic", "value": "技术"},
        ])
        s.add_atoms("技术/检索.md", [
            {"kind": "keyword", "value": "节点"},
            {"kind": "keyword", "value": "检索"},
        ])
        s.save_classification("技术/图谱.md", {
            "topic": "技术", "type": "note", "priority": 0,
            "verdict": {"rule": "kw"}})
        s.add_link("技术/图谱.md", "检索")
        self.tools = KmsTools(s, self.env.vault)

    def tearDown(self):
        self.env.teardown()

    # ---- search ----
    def test_search_shape_and_fallback(self):
        r = self.tools.search("图谱", top_n=5)
        self.assertIn(r["engine"], ("fts", "legacy"))
        self.assertTrue(r["results"])
        first = r["results"][0]
        self.assertEqual(set(first),
                         {"path", "title", "topic", "score", "backlinks", "neighbors"})
        self.assertIn("query", r)

    def test_search_empty_query_ok(self):
        r = self.tools.search("的", top_n=3)     # 全是停用短词 → tokenize 后可能为空
        self.assertIsInstance(r["results"], list)   # 不抛即可

    def test_top_n_clamped(self):
        r = self.tools.search("节点", top_n=9999)
        self.assertLessEqual(len(r["results"]), 50)

    # ---- atoms ----
    def test_atoms_grouped_by_kind(self):
        r = self.tools.atoms("技术/图谱.md")
        self.assertEqual(set(r["atoms"]), {"keyword", "claim", "tag", "topic"})
        self.assertIn("节点", [x["value"] for x in r["atoms"]["keyword"]])
        self.assertIsInstance(r["atoms"]["keyword"][0]["weight"], float)

    def test_atoms_unknown_path_raises(self):
        with self.assertRaises(ValueError):
            self.tools.atoms("没/入库.md")

    # ---- classify_hint：纯缓存，绝不写库/绝不触发 provider ----
    def test_classify_hint_cached(self):
        r = self.tools.classify_hint("技术/图谱.md")
        self.assertTrue(r["cached"])
        self.assertEqual(r["topic"], "技术")
        self.assertEqual(r["verdict"], {"rule": "kw"})   # JSON 已解析

    def test_classify_hint_miss_does_not_write(self):
        n0 = self.store.conn.execute(
            "SELECT COUNT(*) FROM classifications").fetchone()[0]
        r = self.tools.classify_hint("读书/孤岛.md")
        self.assertFalse(r["cached"])
        self.assertIsNone(r["topic"])
        n1 = self.store.conn.execute(
            "SELECT COUNT(*) FROM classifications").fetchone()[0]
        self.assertEqual(n0, n1)                          # 没缓存也绝不补写

    def test_classify_hint_bad_verdict_json_kept_raw(self):
        self.store.conn.execute(
            "INSERT INTO classifications(file_path,topic,verdict) VALUES('读书/孤岛.md','读书','{坏')")
        self.store.conn.commit()
        r = self.tools.classify_hint("读书/孤岛.md")
        self.assertEqual(r["verdict"], {"verdict_raw": "{坏"})

    # ---- graph_neighbors ----
    def test_graph_neighbors_enriched(self):
        r = self.tools.graph_neighbors("技术/图谱.md")
        paths = {n["path"] for n in r["neighbors"]}
        self.assertIn("技术/检索.md", paths)
        nb = [n for n in r["neighbors"] if n["path"] == "技术/检索.md"][0]
        self.assertEqual(nb["title"], "检索")
        self.assertEqual(nb["topic"], "技术")

    def test_graph_neighbors_isolated(self):
        r = self.tools.graph_neighbors("读书/孤岛.md")
        self.assertEqual(r["neighbors"], [])
        self.assertIn("note", r)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "mcp 未安装，跳过薄壳冒烟")
class TestMcpServerShell(unittest.TestCase):
    def test_build_server_lists_four_tools(self):
        from core.mcp_server import build_server
        env = TempEnv()
        try:
            env.store.upsert_file("a.md", "a", "技术")   # 保证库非空
            server = build_server(env.db_path)           # 走 read_only 直连
            tools = asyncio.run(server.list_tools())
            names = sorted(t.name for t in tools)
            self.assertEqual(names, ["kms.atoms", "kms.classify_hint",
                                     "kms.graph_neighbors", "kms.search"])
            # schema 由类型标注自动生成：kms.search 应有 query/top_n 参数
            s = [t for t in tools if t.name == "kms.search"][0]
            self.assertIn("query", (s.input_schema if isinstance(s.input_schema, dict)
                                    else s.input_schema.model_dump())["properties"])
        finally:
            env.teardown()

    def test_build_server_missing_db_raises(self):
        from core.mcp_server import build_server
        from pathlib import Path
        with self.assertRaises(FileNotFoundError):
            build_server(Path("Z:/不存在/kms.db"))


if __name__ == "__main__":
    unittest.main()
