"""画布内发现关联（C5）：canvas_suggest 过滤链/上限 + source/kws 载荷 + map 回 source。"""
from __future__ import annotations

import time
import unittest

from tests.conftest import TempEnv

from core.curator import Curator, CANVAS_EDGE
from core.ingest.pipeline import IngestPipeline
from core.storage.vault import Vault

A, B, C = "技术/甲篇.md", "技术/乙篇.md", "学习/丙篇.md"


class TestCanvasSuggest(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        self.vault = self.env.vault
        self.cur = Curator(self.store, self.vault, IngestPipeline(self.store, self.vault))

    def tearDown(self):
        self.env.teardown()

    def _note(self, rel, kws=(), topic="技术"):
        stem = rel.rsplit("/", 1)[-1][:-3]
        body = f"# {stem}\n正文 {' '.join(kws)}。"
        self.vault.write_note(rel, body)
        self.store.upsert_file(rel, stem, topic,
                               dedup_key=Vault.make_dedup_key(body), mtime=time.time())
        if kws:
            self.store.add_atoms(rel, [{"kind": "keyword", "value": k} for k in kws])

    def test_shared_pair_produces_suggestion(self):
        self._note(A, kws=["图谱", "节点"])
        self._note(B, kws=["图谱", "节点"])
        self._note(C, kws=["无关词"])
        out = self.cur.canvas_suggest([(A, ["ca"]), (B, ["cb"]), (C, ["cc"])], set())
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual({s["from_rel"], s["to_rel"]}, {A, B})
        self.assertEqual({s["from_node"], s["to_node"]}, {"ca", "cb"})
        self.assertGreaterEqual(s["conf"], 0.75)
        self.assertIn("图谱", s["kws"])

    def test_existing_pairs_filtered(self):
        self._note(A, kws=["图谱", "节点"])
        self._note(B, kws=["图谱", "节点"])
        out = self.cur.canvas_suggest([(A, ["ca"]), (B, ["cb"])],
                                      {frozenset((A, B))})
        self.assertEqual(out, [])

    def test_unregistered_card_side_dropped(self):
        self._note(A, kws=["图谱", "节点"])
        self._note(B, kws=["图谱", "节点"])
        out = self.cur.canvas_suggest([(A, ["ca"]), (B, ["cb"]), ("幽灵/未入库.md", ["cg"])],
                                      set())
        self.assertTrue(all("幽灵" not in (s["from_rel"], s["to_rel"]) for s in out))

    def test_first_card_id_used_and_limit_order(self):
        self._note(A, kws=["图谱", "节点", "白板"])
        self._note(B, kws=["图谱", "节点", "白板"])
        self._note(C, kws=["图谱", "白板"])
        out = self.cur.canvas_suggest([(A, ["ca1", "ca2"]), (B, ["cb"]), (C, ["cc"])],
                                      set(), limit=2)
        self.assertLessEqual(len(out), 2)
        confs = [s["conf"] for s in out]
        self.assertEqual(confs, sorted(confs, reverse=True))   # 降序
        card_of = {A: "ca1", B: "cb", C: "cc"}                 # 多卡取第一张
        for s in out:
            self.assertEqual(s["from_node"], card_of[s["from_rel"]])
            self.assertEqual(s["to_node"], card_of[s["to_rel"]])

    def test_too_few_cards(self):
        self._note(A, kws=["图谱", "节点"])
        self.assertEqual(self.cur.canvas_suggest([(A, ["ca"])], set()), [])
        self.assertEqual(self.cur.canvas_suggest([], set()), [])
        self.assertEqual(self.cur.canvas_suggest([(A, ["ca"]), (B, [])], set()), [])


class TestCanvasEdgeSource(unittest.TestCase):
    """canvas_edge_add 的 source/shared_keywords 形参（默认值向后兼容）。"""

    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        self.vault = self.env.vault
        self.cur = Curator(self.store, self.vault, IngestPipeline(self.store, self.vault))
        self._note(A, kws=["k1"])
        self._note(B, kws=["k1"])

    def tearDown(self):
        self.env.teardown()

    def _note(self, rel, kws=(), topic="技术"):
        stem = rel.rsplit("/", 1)[-1][:-3]
        body = f"# {stem}\n正文 {' '.join(kws)}。"
        self.vault.write_note(rel, body)
        self.store.upsert_file(rel, stem, topic,
                               dedup_key=Vault.make_dedup_key(body), mtime=time.time())
        if kws:
            self.store.add_atoms(rel, [{"kind": "keyword", "value": k} for k in kws])

    def test_default_source_canvas(self):
        r = self.cur.canvas_edge_add("c.canvas", "e1", A, B)
        row = self.store.get_suggestion(r["sid"])
        self.assertEqual(row["payload"]["source"], "canvas")
        self.assertNotIn("shared_keywords", row["payload"])
        self.assertEqual(row["confidence"], 1.0)

    def test_ai_source_payload_and_conf(self):
        r = self.cur.canvas_edge_add("c.canvas", "e1", A, B,
                                     source="canvas_ai", shared_keywords=["k1"])
        row = self.store.get_suggestion(r["sid"])
        self.assertEqual(row["payload"]["source"], "canvas_ai")
        self.assertEqual(row["payload"]["shared_keywords"], ["k1"])
        self.assertAlmostEqual(row["confidence"], 0.7)

    def test_map_returns_source_and_kws(self):
        self.cur.canvas_edge_add("c.canvas", "ea", A, B,
                                 source="canvas_ai", shared_keywords=["k1", "k2"])
        m = self.cur.canvas_edge_map("c.canvas")
        self.assertEqual(m["ea"]["source"], "canvas_ai")
        self.assertEqual(m["ea"]["kws"], ["k1", "k2"])
        self.assertIsInstance(m["ea"]["sid"], int)
        # 模拟历史行（payload 无 source 键）→ 回落默认 "canvas"
        self.store.conn.execute("UPDATE suggestions SET payload = "
                                "REPLACE(payload, '\"source\": \"canvas_ai\", ', '')"
                                " WHERE kind=?", (CANVAS_EDGE,))
        self.store.conn.commit()
        m2 = self.cur.canvas_edge_map("c.canvas")
        self.assertEqual(m2["ea"]["source"], "canvas")


if __name__ == "__main__":
    unittest.main()
