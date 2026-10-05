"""SQLiteStore 只读模式：ro 直连可读 + 写守卫 + 裸 SQL 契约（GraphEngine/KnowledgeSearch）。

这组测试锁死 MCP 只读服务的地基：外部进程以 mode=ro 打开主库后，
engine.py / index.py 里的 store.conn 裸读 SQL 必须照常工作（门面方案会翻车的点）。
"""
from __future__ import annotations

import unittest

from tests.conftest import TempEnv

from core.graph.engine import GraphEngine
from core.graph.search import KnowledgeSearch
from core.storage.sqlite_db import SQLiteStore


class TestReadonlyStore(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        # 播种：中文相对路径 + 多 kind 原子 + 双链 + 分类缓存
        self.store.upsert_file("技术/图谱.md", "图谱", "技术", dedup_key="d1")
        self.store.upsert_file("技术/检索.md", "检索", "技术", dedup_key="d2")
        self.store.add_atoms("技术/图谱.md", [
            {"kind": "keyword", "value": "节点"},
            {"kind": "keyword", "value": "边权"},
            {"kind": "claim", "value": "图谱由节点和边构成"},
            {"kind": "tag", "value": "图算法"},
            {"kind": "topic", "value": "技术"},
        ])
        self.store.add_atoms("技术/检索.md", [
            {"kind": "keyword", "value": "节点"},
            {"kind": "keyword", "value": "倒排"},
        ])
        self.store.add_link("技术/图谱.md", "检索")
        self.store.save_classification("技术/图谱.md", {
            "topic": "技术", "type": "note", "priority": 0,
            "verdict": {"rule": "kw"}})
        self.ro = SQLiteStore(self.env.db_path, read_only=True)

    def tearDown(self):
        self.ro.close()
        self.env.teardown()

    # ---- 读方法原样可用 ----
    def test_reads_work_on_ro(self):
        self.assertEqual(len(self.ro.all_files()), 2)
        self.assertTrue(self.ro.file_exists("技术/图谱.md"))
        self.assertEqual(len(self.ro.atoms_for("技术/图谱.md")), 5)
        row = self.ro.latest_classification("技术/图谱.md")
        self.assertEqual(row["topic"], "技术")
        self.assertEqual(self.ro.inlinks("检索"), ["技术/图谱.md"])

    def test_epoch_stays_zero_and_fts_probe(self):
        self.assertEqual(self.ro.epoch, 0)
        # 主库建表成功时 ro 侧应探测到 notes_fts（sqlite_master 探测路径）
        self.assertEqual(self.ro.fts_ok, self.store.fts_ok)

    def test_bare_sql_consumers_ro_compatible(self):
        # GraphEngine/AtomIndex 直接裸用 store.conn.execute——ro 连接 SELECT 必须通
        g = GraphEngine(self.ro, self.env.vault).build()
        ids = {n["id"] for n in g["nodes"]}
        self.assertEqual(ids, {"技术/图谱.md", "技术/检索.md"})
        # 同 store 实例 + epoch 恒 0 → 缓存常驻（只读进程语义正确）
        e2 = GraphEngine(self.ro, self.env.vault)
        self.assertIs(g, e2.build())
        # KnowledgeSearch 全链路（fts 未灌词会静默降级 legacy，这里只要求不炸且有结构）
        res = KnowledgeSearch(self.ro, self.env.vault).search("节点 图谱")
        self.assertIsInstance(res, list)
        if res:
            self.assertEqual(
                set(res[0]), {"path", "title", "topic", "score", "backlinks", "neighbors"})

    def test_dump_methods_readonly(self):
        self.assertEqual(len(self.ro.dump_links()), 1)
        cls = self.ro.dump_classifications()
        self.assertEqual(len(cls), 1)
        self.assertEqual(cls[0]["verdict"], {"rule": "kw"})   # JSON 已解析
        c = self.ro.table_counts()
        self.assertEqual((c["files"], c["atoms"], c["links"]), (2, 7, 1))
        self.assertEqual(len(list(self.ro.iter_atoms(batch=2))), 7)  # 小批量分页不丢不重

    # ---- 写守卫 ----
    def test_write_guards_raise(self):
        with self.assertRaises(RuntimeError):
            self.ro.upsert_file("x.md", "x")
        with self.assertRaises(RuntimeError):
            self.ro.add_atoms("技术/图谱.md", [{"kind": "keyword", "value": "新词"}])
        with self.assertRaises(RuntimeError):
            self.ro.log("info", "mcp", "不许写")
        with self.assertRaises(RuntimeError):
            self.ro.index_note_tokens("技术/图谱.md", "内容")

    # ---- 库文件不存在 ----
    def test_missing_db_raises_filenotfound(self):
        ghost = self.env.db_path.parent / "nope.db"
        with self.assertRaises(FileNotFoundError):
            SQLiteStore(ghost, read_only=True)

    # ---- 与写连接并发（WAL 读已提交快照）----
    def test_concurrent_with_open_writer(self):
        self.store.upsert_file("技术/新篇.md", "新篇", "技术")   # 写侧刚提交
        self.assertTrue(self.ro.file_exists("技术/新篇.md"))     # ro 立即可见


if __name__ == "__main__":
    unittest.main()
