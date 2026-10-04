"""M2 修复回归：router 标题条件 / verdict 回填 / recommend_links 精确化 / search 结构。"""
from __future__ import annotations

import unittest

from tests.conftest import TempEnv

from core.atoms.index import AtomIndex
from core.classify.router import ClassificationRouter
from core.graph.search import KnowledgeSearch
from core.ingest.pipeline import IngestPipeline


class TestRouterFixes(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.router = ClassificationRouter(self.env.store)

    def tearDown(self):
        self.env.teardown()

    def test_title_participates_in_classify(self):
        # 正文无信息量、标题含强技术词：修复前（条件写反）这里会得到 未分类
        c = self.router.classify("随便写点什么", title="Python 架构 算法 训练 token")
        self.assertEqual(c["topic"], "技术")
        self.assertFalse(c["cached"])

    def test_cached_verdict_backfilled(self):
        c = self.router.classify("python 代码 算法 架构", title="技术笔记",
                                 rel_path="技术/x.md")
        self.assertTrue(c["verdict"].get("topic_hits"))
        again = self.router.classify("whatever", title="t", rel_path="技术/x.md")
        self.assertTrue(again["cached"])
        self.assertEqual(again["verdict"], c["verdict"])   # 回填而非空 dict


class TestRecommendLinks(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.idx = AtomIndex(self.env.store)

    def tearDown(self):
        self.env.teardown()

    def test_exact_not_substring(self):
        s = self.env.store
        s.upsert_file("学习/a.md", "a", "学习")
        s.add_atoms("学习/a.md", [{"kind": "keyword", "value": "学习方法论大全"}])
        # LIKE 时代 "学习" 会误伤它；精确倒排不该推荐
        self.assertEqual(self.idx.recommend_links(["学习"], "学习/b.md"), [])
        s.upsert_file("学习/c.md", "c", "学习")
        s.add_atoms("学习/c.md", [{"kind": "keyword", "value": "学习"}])
        self.assertEqual(self.idx.recommend_links(["学习"], "学习/b.md"), ["学习/c.md"])

    def test_rank_by_shared_count(self):
        s = self.env.store
        s.upsert_file("x/p.md", "p", "技术")
        s.upsert_file("x/q.md", "q", "技术")
        s.add_atoms("x/p.md", [{"kind": "keyword", "value": k} for k in ["k1", "k2", "k3"]])
        s.add_atoms("x/q.md", [{"kind": "keyword", "value": k} for k in ["k1"]])
        rec = self.idx.recommend_links(["k1", "k2", "k3"], "x/anchor.md")
        self.assertEqual(rec, ["x/p.md", "x/q.md"])


class TestSearchInterface(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.pipe = IngestPipeline(self.env.store, self.env.vault)

    def tearDown(self):
        self.env.teardown()

    def test_search_shape_and_hit(self):
        res = self.pipe.ingest_raw("# 知识图谱\n图谱 节点 边 检索。")
        self.assertTrue(res["ok"])
        ks = KnowledgeSearch(self.env.store, self.env.vault)
        out = ks.search("图谱")
        self.assertTrue(out)
        self.assertEqual(set(out[0]),
                         {"path", "title", "topic", "score", "backlinks", "neighbors"})
        self.assertEqual(out[0]["path"], res["rel_path"])

    def test_legacy_path_when_fts_disabled(self):
        res = self.pipe.ingest_raw("# 降级检索\n检索 测试 旧路径。")
        self.assertTrue(res["ok"])
        ks = KnowledgeSearch(self.env.store, self.env.vault)
        self.env.store.fts_ok = False
        try:
            out = ks.search("检索")
        finally:
            self.env.store.fts_ok = True
        self.assertIn(res["rel_path"], [r["path"] for r in out])

    def test_backlinks_via_inlinks(self):
        self.pipe.ingest_raw("# 目标笔记\n图谱 基础 概念。")
        s = self.env.store
        s.upsert_file("技术/anchor.md", "锚点", "技术")
        s.add_link("技术/anchor.md", "目标笔记")
        s.add_link("技术/anchor.md", "目标笔记的别名不算")
        ks = KnowledgeSearch(s, self.env.vault)
        target = next(f["path"] for f in s.all_files() if f["title"] == "目标笔记")
        self.assertEqual(ks.backlinks(target), ["技术/anchor.md"])


if __name__ == "__main__":
    unittest.main()
