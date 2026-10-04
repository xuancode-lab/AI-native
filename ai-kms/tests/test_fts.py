"""FTS5 存储层：索引幂等 / rename·delete 级联 / 对账重建 / 降级 / tokenize 一致性。"""
from __future__ import annotations

import unittest

from tests.conftest import TempEnv

from core.ingest.pipeline import IngestPipeline
from core.storage.fts import tokenize


class TestFts(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        self.vault = self.env.vault
        self.pipe = IngestPipeline(self.store, self.vault)

    def tearDown(self):
        self.env.teardown()

    def skipUnlessFts(self):
        if not self.store.fts_ok:
            self.skipTest("本机 SQLite 无 FTS5")

    def test_ingest_indexes_tokens(self):
        self.skipUnlessFts()
        res = self.pipe.ingest_raw("# Python 生成器\n迭代器 yield 协程 技术。")
        self.assertTrue(res["ok"])
        hits = self.store.fts_search(tokenize("生成器"), 10)
        self.assertIn(res["rel_path"], [h["path"] for h in hits])
        self.assertGreater(hits[0]["score"], 0)

    def test_reindex_idempotent(self):
        self.skipUnlessFts()
        rel = "技术/a.md"
        self.store.upsert_file(rel, "a")
        self.store.index_note_tokens(rel, "知识 图谱 基础")
        n1 = self.store.fts_health()[0]
        self.store.index_note_tokens(rel, "笔记 二次 编辑")   # 重索引同一 path
        self.assertEqual(self.store.fts_health()[0], n1)     # 行数不变
        self.assertIn(rel, [h["path"] for h in
                            self.store.fts_search(["二次"], 5)])
        self.assertNotIn(rel, [h["path"] for h in
                               self.store.fts_search(["图谱"], 5)])  # 旧词消失

    def test_rename_cascades_fts_and_suggestions(self):
        self.skipUnlessFts()
        res = self.pipe.ingest_raw("# 旧标题\n架构 算法 设计。")
        rel = res["rel_path"]
        new = "技术/新标题.md"
        self.store.add_suggestions([{"kind": "wikilink", "file_path": "x/y.md",
                                     "target": "旧标题", "confidence": 0.9}])
        self.store.rename_path(rel, new)
        self.assertIn(new, self.store.fts_paths())
        self.assertNotIn(rel, self.store.fts_paths())
        self.assertIn(new, [h["path"] for h in
                            self.store.fts_search(["架构"], 5)])
        rows = self.store.pending_suggestions()
        self.assertEqual([r["target"] for r in rows if r["file_path"] == "x/y.md"],
                         ["新标题"])                          # 建议 target 跟随改名

    def test_delete_cascades(self):
        self.skipUnlessFts()
        res = self.pipe.ingest_raw("# 将被删除\n删除 测试 原子。")
        rel = res["rel_path"]
        self.store.add_suggestions([
            {"kind": "wikilink", "file_path": "other.md", "target": "将被删除",
             "confidence": 0.9},
            {"kind": "topic", "file_path": rel, "target": "技术", "confidence": 0.5},
        ])
        self.store.delete_file(rel)
        self.assertNotIn(rel, self.store.fts_paths())
        statuses = {(s["file_path"], s["target"]): s["status"]
                    for s in self.store.list_suggestions("obsolete")}
        self.assertEqual(statuses.get((rel, "技术")), "obsolete")
        self.assertEqual(statuses.get(("other.md", "将被删除")), "obsolete")

    def test_rebuild_missing_and_ghost(self):
        self.skipUnlessFts()
        r1 = self.pipe.ingest_raw("# 一\n技术 算法。")
        r2 = self.pipe.ingest_raw("# 二\n学习 方法。")
        self.store.delete_note_tokens(r2["rel_path"])         # 模拟漏索引
        missing, ghost = self.pipe.fts_missing()
        self.assertEqual(missing, [r2["rel_path"]])
        out = self.pipe.fts_rebuild_chunk(limit=10)
        self.assertEqual(out["indexed"], 1)
        self.assertEqual(out["remaining"], 0)
        self.assertEqual(self.store.fts_health()[0], self.store.fts_health()[1])

    def test_disabled_store_noop(self):
        self.store.fts_ok = False
        self.store.index_note_tokens("z/a.md", "任何 内容")    # 不应抛
        self.assertEqual(self.store.fts_search(["任何"], 5), [])
        self.assertEqual(self.store.fts_paths(), set())
        self.store.fts_ok = True

    def test_tokenize_consistency(self):
        self.skipUnlessFts()
        s = "知识管理系统：AI 时代个人笔记的复利效应。"
        rel = "学习/consistency.md"
        self.store.upsert_file(rel, "consistency")
        self.store.index_note_tokens(rel, s)
        for tok in tokenize(s):
            hits = [h["path"] for h in self.store.fts_search([tok], 5)]
            self.assertIn(rel, hits, f"token {tok} 查不中")


if __name__ == "__main__":
    unittest.main()
