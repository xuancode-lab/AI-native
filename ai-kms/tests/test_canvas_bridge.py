"""白板手绘边桥：字段/方向语义/去重刷新/already_linked/状态映射/删除语义/采纳全链。"""
from __future__ import annotations

import time
import unittest

from tests.conftest import TempEnv

from core.canvas import CanvasDocument
from core.curator import Curator, CANVAS_EDGE
from core.ingest.pipeline import IngestPipeline
from core.storage.vault import Vault

A = "技术/甲篇.md"
B = "学习/乙篇.md"


class TestCanvasBridge(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        self.vault = self.env.vault
        self.pipe = IngestPipeline(self.store, self.vault)
        self.cur = Curator(self.store, self.vault, self.pipe)

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

    def _rows(self):
        return self.store.conn.execute(
            "SELECT id, status FROM suggestions WHERE kind=?",
            (CANVAS_EDGE,)).fetchall()

    # ---------- 落库字段与方向 ----------
    def test_add_row_fields_and_direction(self):
        self._note(A)
        self._note(B, topic="学习")
        res = self.cur.canvas_edge_add("白板/计划.canvas", "e1", A, B)
        self.assertEqual(res["status"], "pending")
        row = self.store.get_suggestion(res["sid"])
        self.assertEqual(row["kind"], CANVAS_EDGE)
        self.assertEqual(row["file_path"], A)                  # 被改侧=尾侧
        self.assertEqual(row["target"], "乙篇")                 # stem
        self.assertEqual(row["confidence"], 1.0)
        pl = row["payload"]
        self.assertEqual(pl["source"], "canvas")
        self.assertEqual(pl["target_title"], "乙篇")
        self.assertEqual(pl["target_path"], B)
        self.assertEqual(pl["content_fp"],
                         Vault.make_dedup_key(self.vault.read_note(A)))
        self.assertIsNone(pl["insert_at"])
        self.assertFalse(pl["safe"])
        self.assertEqual((pl["canvas_rel"], pl["edge_id"]), ("白板/计划.canvas", "e1"))
        self.assertEqual((pl["from_rel"], pl["to_rel"]), (A, B))

    def test_human_direction_beats_deg(self):
        """deg 悬殊也不翻转：edges_scan 会选低度数侧，人画的必须原样。"""
        self._note(A, kws=["热词1", "热词2"])
        self._note(B, kws=["热词1", "热词2"])
        self._note("技术/丙.md", kws=["热词1"])
        self._note("技术/丁.md", kws=["热词2"])
        # 手工拉高 A 的度数（links 表）
        self.store.add_link(A, "丙")
        self.store.add_link(A, "丁")
        res = self.cur.canvas_edge_add("c.canvas", "e1", A, B)   # 人画 A→B
        self.assertEqual(self.store.get_suggestion(res["sid"])["file_path"], A)

    def test_parallel_pair_refresh_not_duplicated(self):
        self._note(A)
        self._note(B)
        r1 = self.cur.canvas_edge_add("c.canvas", "e1", A, B)
        r2 = self.cur.canvas_edge_add("c.canvas", "e2", A, B)   # 同有序对平行边
        self.assertEqual(r1["sid"], r2["sid"])
        self.assertEqual(len(self._rows()), 1)
        got = self.store.get_suggestion(r1["sid"])
        self.assertEqual(got["payload"]["edge_id"], "e2")       # pending 刷 payload

    def test_processed_not_revived(self):
        self._note(A)
        self._note(B)
        r = self.cur.canvas_edge_add("c.canvas", "e1", A, B)
        self.store.resolve_suggestion(r["sid"], "skipped")
        again = self.cur.canvas_edge_add("c.canvas", "e2", A, B)
        self.assertEqual(again["status"], "skipped")            # 不复活
        self.assertEqual(len(self._rows()), 1)
        self.assertEqual(self.store.get_suggestion(r["sid"])["payload"]["edge_id"], "e1")

    # ---------- 前置校验 ----------
    def test_already_linked_both_directions(self):
        self._note(A)
        self._note(B)
        self.store.add_link(A, "乙篇")
        self.assertEqual(self.cur.canvas_edge_add("c.canvas", "e1", A, B),
                         {"status": "already_linked"})
        self.assertEqual(self.cur.canvas_edge_add("c.canvas", "e1", B, A),
                         {"status": "already_linked"})          # 任一向实线都拦
        self.assertEqual(len(self._rows()), 0)

    def test_invalid_inputs_return_none(self):
        self._note(A)
        self._note(B)
        self.assertIsNone(self.cur.canvas_edge_add("c.canvas", "e1", A, A))       # 自环
        self.assertIsNone(self.cur.canvas_edge_add("c.canvas", "e1", A, "图.png"))  # 未入库
        self.assertIsNone(self.cur.canvas_edge_add("c.canvas", "e1", "缺.md", B))
        self.assertIsNone(self.cur.canvas_edge_add("", "e1", A, B))               # 缺关联信息

    def test_endpoint_blank_content_skipped(self):
        self._note(A)
        self._note(B)
        self.vault.write_note(A, "")
        self.assertIsNone(self.cur.canvas_edge_add("c.canvas", "e1", A, B))

    # ---------- 状态映射（边视觉态数据源） ----------
    def test_map_three_states_filtered_by_canvas(self):
        self._note(A)
        self._note(B)
        self._note("技术/丙.md")
        self._note("技术/丁.md")
        r_pend = self.cur.canvas_edge_add("c1.canvas", "ea", A, B)
        r_app = self.cur.canvas_edge_add("c1.canvas", "eb", A, "技术/丙.md")
        self.cur.apply_suggestion(r_app["sid"])
        r_skip = self.cur.canvas_edge_add("c1.canvas", "ec", B, A)
        self.store.resolve_suggestion(r_skip["sid"], "skipped")
        # 别的画布：用独立有序对（UNIQUE 按 pair，与画布无关）
        self.cur.canvas_edge_add("c2.canvas", "ex", A, "技术/丁.md")
        # AI edge kind 行不混入 map
        self.store.add_suggestions([{"kind": "edge", "file_path": B, "target": "丁",
                                     "payload": {"source": "edge_discovery"},
                                     "confidence": 0.7}])

        m = self.cur.canvas_edge_map("c1.canvas")
        self.assertEqual(set(m.keys()), {"ea", "eb", "ec"})
        self.assertEqual(m["ea"]["status"], "pending")
        self.assertEqual(m["eb"]["status"], "applied")
        self.assertEqual(m["ec"]["status"], "skipped")
        self.assertEqual(m["ea"]["sid"], r_pend["sid"])
        self.assertEqual(m["eb"]["from_rel"], A)

    def test_remove_pending_deletes_processed_keeps(self):
        self._note(A)
        self._note(B)
        self._note("技术/丙.md")
        r = self.cur.canvas_edge_add("c.canvas", "e1", A, B)
        self.assertEqual(self.cur.canvas_edge_remove(r["sid"]), "deleted")
        self.assertIsNone(self.store.get_suggestion(r["sid"]))
        # 已决策的行：删边保留建议（反向不动笔记里已生效的双链）
        r2 = self.cur.canvas_edge_add("c.canvas", "e2", A, "技术/丙.md")
        self.cur.apply_suggestion(r2["sid"])
        self.assertEqual(self.cur.canvas_edge_remove(r2["sid"]), "kept")
        self.assertEqual(self.store.get_suggestion(r2["sid"])["status"], "applied")
        self.assertEqual(self.cur.canvas_edge_remove(99999), "missing")

    def test_orphan_obsoletes_pending_of_that_canvas_only(self):
        self._note(A)
        self._note(B)
        self._note("技术/丙.md")
        self._note("技术/丁.md")
        p1 = self.cur.canvas_edge_add("旧.canvas", "e1", A, B)["sid"]
        p2 = self.cur.canvas_edge_add("旧.canvas", "e2", B, A)["sid"]
        other = self.cur.canvas_edge_add("留.canvas", "e3", A, "技术/丙.md")["sid"]
        done = self.cur.canvas_edge_add("旧.canvas", "e4", A, "技术/丁.md")["sid"]
        self.cur.apply_suggestion(done)
        self.assertEqual(self.cur.canvas_edge_orphan("旧.canvas"), 2)
        self.assertEqual(self.store.get_suggestion(p1)["status"], "obsolete")
        self.assertEqual(self.store.get_suggestion(p2)["status"], "obsolete")
        self.assertEqual(self.store.get_suggestion(other)["status"], "pending")
        self.assertEqual(self.store.get_suggestion(done)["status"], "applied")

    # ---------- 采纳全链（卖点：画线→审阅→图谱） ----------
    def test_apply_full_chain(self):
        self._note(A)
        self._note(B, topic="学习")
        res = self.cur.canvas_edge_add("c.canvas", "e1", A, B)
        out = self.cur.apply_suggestion(res["sid"])
        self.assertTrue(out["ok"], out)
        content = self.vault.read_note(A)
        self.assertIn("## 相关", content)
        self.assertIn("- [[乙篇]]", content)
        snaps = self.store.conn.execute(
            "SELECT * FROM snapshots WHERE file_path=?", (A,)).fetchall()
        self.assertEqual(snaps[0]["reason"], "画布关联确认")
        self.assertIn("乙篇", [l["to_target"] for l in self.store.links_for(A)])
        self.assertEqual(self.store.get_suggestion(res["sid"])["status"], "applied")
        # 图谱实线出现（reindex 后自然浮现）
        from core.graph.engine import GraphEngine
        g = GraphEngine(self.store).build()
        solid = [e for e in g["edges"] if e["kind"] == "link"
                 and {g["nodes"][e["source"]]["id"], g["nodes"][e["target"]]["id"]}
                 == {A, B}]
        self.assertEqual(len(solid), 1)

    def test_apply_stale_obsoletes(self):
        self._note(A)
        self._note(B)
        res = self.cur.canvas_edge_add("c.canvas", "e1", A, B)
        self.vault.write_note(A, "# 甲篇\n画完后笔记被改过。")
        out = self.cur.apply_suggestion(res["sid"])
        self.assertEqual(out["reason"], "stale")
        self.assertEqual(self.store.get_suggestion(res["sid"])["status"], "obsolete")

    def test_undo_chain_remove_then_readd(self):
        """删边(DELETE pending)→撤销(重新 add)：得到干净的行，支持 undo/redo 循环。"""
        self._note(A)
        self._note(B)
        r1 = self.cur.canvas_edge_add("c.canvas", "e1", A, B)
        self.cur.canvas_edge_remove(r1["sid"])
        r2 = self.cur.canvas_edge_add("c.canvas", "e1", A, B)
        self.assertEqual(r2["status"], "pending")
        self.assertEqual(len(self._rows()), 1)

    def test_model_bridge_integration_json(self):
        """模型生成的文档 id 直接喂桥：canvas_rel+edge_id 反查映射闭环。"""
        self._note(A)
        self._note(B)
        doc = CanvasDocument()
        a = doc.add_file_card(A, 0, 0)
        b = doc.add_file_card(B, 300, 0)
        e = doc.add_edge(a.id, b.id, to_end="arrow")
        res = self.cur.canvas_edge_add("c.canvas", e.id, A, B)
        m = self.cur.canvas_edge_map("c.canvas")
        self.assertIn(e.id, m)
        self.assertEqual(m[e.id]["sid"], res["sid"])


if __name__ == "__main__":
    unittest.main()
