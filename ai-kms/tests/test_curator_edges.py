"""发现关联（edge discovery）：候选分档/防噪门槛/cap/排除、扫描刷新与反向防重、应用链与级联。"""
from __future__ import annotations

import time
import unittest

from tests.conftest import TempEnv

from core.curator import Curator, MAX_EDGE_PER_NODE
from core.ingest.pipeline import IngestPipeline
from core.storage.vault import Vault


class TestEdges(unittest.TestCase):
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

    def _cand(self, all_pairs=False):
        return self.cur.edge_candidates({"type": "all"}, all_pairs)

    def _pair_of(self, cands, x, y):
        for d in cands:
            if {d["a"], d["b"]} == {x, y}:
                return d
        return None

    # ---------- 候选分档 ----------
    def test_tier1_dashed_default(self):
        self._note("技术/甲篇.md", kws=["图谱", "节点"])
        self._note("技术/乙篇.md", kws=["图谱", "节点"])
        d = self._pair_of(self._cand(), "技术/甲篇.md", "技术/乙篇.md")
        self.assertIsNotNone(d, "shared≥2 对必须默认产出（虚线升实线是核心来源）")
        self.assertEqual(d["tier"], "dashed")
        self.assertAlmostEqual(d["conf"], 0.75, places=2)     # .55+.10*2，ts=0
        self.assertEqual(d["min_deg"], 1)                     # 互相已有语义虚线

    def test_rescue_conf_and_ordering(self):
        self._note("技术/知识图谱入门.md", kws=["solo"])
        self._note("学习/知识图谱进阶.md", kws=["solo"], topic="学习")
        self._note("技术/甲篇.md", kws=["图谱", "节点"])
        self._note("技术/乙篇.md", kws=["图谱", "节点"])
        c = self._cand()
        self.assertEqual([d["min_deg"] for d in c],
                         sorted(d["min_deg"] for d in c))     # 孤立优先
        r = self._pair_of(c, "技术/知识图谱入门.md", "学习/知识图谱进阶.md")
        self.assertEqual(r["tier"], "rescue")
        self.assertGreaterEqual(r["ts"], 1)                   # "知识"或"图谱"交集
        self.assertAlmostEqual(r["conf"], 0.65, places=2)     # 救援封顶

    def test_single_word_without_backing_dropped(self):
        self._note("技术/甲.md", kws=["solo"])
        self._note("学习/乙.md", kws=["solo"], topic="学习")
        self.assertEqual(self._cand(), [])   # shared=1 + 无标题交集 + 异主题 → 噪音

    def test_single_word_same_topic_rescued(self):
        self._note("技术/甲.md", kws=["solo"])
        self._note("技术/乙.md", kws=["solo"])
        c = self._cand()
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["tier"], "rescue")

    def test_library_tier_needs_all_pairs(self):
        # 注意原子最短长度 2（MIN_ATOM_LEN），关键词不能用单字符
        self._note("技术/m.md", kws=["p1", "p2", "xx"])
        self._note("技术/p1f.md", kws=["p1", "p2"])
        self._note("技术/n.md", kws=["q1", "q2", "xx"])
        self._note("技术/q1f.md", kws=["q1", "q2"])
        self.assertIsNone(self._pair_of(self._cand(), "技术/m.md", "技术/n.md"))
        d = self._pair_of(self._cand(all_pairs=True), "技术/m.md", "技术/n.md")
        self.assertIsNotNone(d)
        self.assertEqual(d["tier"], "library")

    # ---------- 排除与防噪 ----------
    def test_exclusions(self):
        self._note("技术/a.md", kws=["k1", "k2"])
        self._note("技术/b.md", kws=["k1", "k2"])
        self.store.add_link("技术/a.md", "b")                 # 已有实线
        self._note("学习/dup.md", kws=["d1", "d2"])
        self._note("研究/dup.md", kws=["d1", "d2"])
        self._note("技术/丙篇.md", kws=["d1", "d2"])          # 两端撞 ambiguous
        self._note("学习/技术.md", kws=["t1", "t2"])          # stem=主题名
        self._note("学习/丁篇.md", kws=["t1", "t2"])
        self._note("技术/m1.md", kws=["s1", "s2"])
        self._note("技术/m2.md", kws=["s1", "s2"])
        self.store.add_suggestions([{"kind": "wikilink", "file_path": "技术/m1.md",
                                     "target": "m2", "confidence": 0.9, "payload": {}}])
        pairs = {frozenset((d["a"], d["b"])) for d in self._cand()}
        self.assertNotIn(frozenset(("技术/a.md", "技术/b.md")), pairs)
        self.assertNotIn(frozenset(("技术/丙篇.md", "学习/dup.md")), pairs)
        self.assertNotIn(frozenset(("技术/丙篇.md", "研究/dup.md")), pairs)
        self.assertNotIn(frozenset(("学习/技术.md", "学习/丁篇.md")), pairs)
        self.assertNotIn(frozenset(("技术/m1.md", "技术/m2.md")), pairs)

    def test_hub_bucket_not_counted(self):
        for i in range(65):
            self._note(f"技术/d{i}.md", kws=["hub"])
        self._note("技术/甲.md", kws=["hub", "privA"])
        self._note("技术/乙.md", kws=["hub", "privB"])
        self.assertIsNone(self._pair_of(self._cand(), "技术/甲.md", "技术/乙.md"))
        self.assertIsNone(self._pair_of(self._cand(all_pairs=True),
                                        "技术/甲.md", "技术/乙.md"))

    def test_per_node_cap(self):
        self._note("技术/z.md", kws=[f"k{i}{j}" for i in range(10) for j in "ab"])
        for i in range(10):
            self._note(f"技术/p{i}.md", kws=[f"k{i}a", f"k{i}b"])
        c = self._cand()
        zn = sum(1 for d in c if "技术/z.md" in (d["a"], d["b"]))
        self.assertEqual(zn, MAX_EDGE_PER_NODE)

    def test_dry_run_counts(self):
        self._note("技术/甲.md", kws=["solo"])
        self._note("技术/乙.md", kws=["solo"])
        r = self.cur.edges_dry_run({"type": "all"})
        self.assertEqual(r["will_pairs"], 1)
        self.assertEqual(r["isolated"], 2)
        self.assertEqual(r["pending"], 0)

    # ---------- 扫描提交 ----------
    def test_scan_idempotent_refresh_and_skip_persists(self):
        self._note("技术/甲.md", kws=["solo"])
        self._note("技术/乙.md", kws=["solo"])
        r1 = self.cur.edges_scan(self._cand(), limit=20)
        self.assertEqual(r1["added"], 1)
        self.cur.edges_scan(self._cand(), limit=20)          # 同向重扫
        self.assertEqual(self.store.count_pending("edge"), 1)  # 不产生第二行
        # 输入变化 → 同向 pending 被刷新为更高置信（dashed 档）
        for p in ("技术/甲.md", "技术/乙.md"):
            self.store.add_atoms(p, [{"kind": "keyword", "value": "solo2"}])
        before = self.store.pending_suggestions(kind="edge")[0]["confidence"]
        self.cur.edges_scan(self._cand(), limit=20)
        row = self.store.pending_suggestions(kind="edge")[0]
        self.assertGreater(row["confidence"], before)
        self.assertEqual(row["payload"]["tier"], "dashed")
        # skipped 行不被重扫复活
        self.store.resolve_suggestion(row["id"], "skipped")
        self.cur.edges_scan(self._cand(), limit=20)
        self.assertEqual(self.store.count_pending("edge"), 0)
        self.assertEqual(self.store.get_suggestion(row["id"])["status"], "skipped")

    def test_scan_direction_flip_not_duplicated(self):
        # 自然方向：deg 平手 → 路径小者乙为被改侧
        self._note("技术/甲.md", kws=["k1", "k2"])
        self._note("技术/乙.md", kws=["k1", "k2"])
        self.cur.edges_scan(self._cand(), limit=20)
        row = self.store.pending_suggestions(kind="edge")[0]
        self.assertEqual(row["file_path"], "技术/乙.md")      # 乙 < 甲 字典序平手规则
        # 抬高乙的连接数 → 候选方向应翻转为甲，但同对已有反向(乙为mod)pending → 跳过
        self._note("技术/丙.md", kws=["k3", "k4"])
        self.store.add_atoms("技术/乙.md", [{"kind": "keyword", "value": "k3"},
                                            {"kind": "keyword", "value": "k4"}])
        d = self._pair_of(self._cand(), "技术/甲.md", "技术/乙.md")
        self.assertIsNotNone(d)
        self.assertGreater(d["deg_a"], d["deg_b"])            # a=乙 现在deg更大 → 该改甲
        self.cur.edges_scan([d], limit=20)                    # 反向已 pending → 应跳过
        fps = [r["file_path"] for r in self.store.pending_suggestions(kind="edge")]
        self.assertNotIn("技术/甲.md", fps)                    # 不产翻转重复行
        self.assertIn("技术/乙.md", fps)                       # 原行保留

    def test_link_created_after_scan_excluded_on_rescan(self):
        self._note("技术/甲.md", kws=["k1", "k2"])
        self._note("技术/乙.md", kws=["k1", "k2"])
        self.cur.edges_scan(self._cand(), limit=20)
        row = self.store.pending_suggestions(kind="edge")[0]
        self.store.add_link(row["file_path"], row["target"])   # 手工连了实线
        self.assertEqual(self._cand(), [])

    # ---------- 应用链 ----------
    def test_apply_writes_real_link(self):
        self._note("技术/甲.md", kws=["solo"])
        self._note("技术/乙.md", kws=["solo"])
        self.cur.edges_scan(self._cand(), limit=20)
        row = self.store.pending_suggestions(kind="edge")[0]
        mod, tgt_stem = row["file_path"], row["target"]
        res = self.cur.apply_suggestion(row["id"])
        self.assertTrue(res["ok"], res)
        content = self.vault.read_note(mod)
        self.assertIn("## 相关", content)
        self.assertIn(f"- [[{tgt_stem}]]", content)
        snaps = self.store.conn.execute(
            "SELECT * FROM snapshots WHERE file_path=?", (mod,)).fetchall()
        self.assertEqual(snaps[0]["reason"], "关联确认")
        targets = [l["to_target"] for l in self.store.links_for(mod)]
        self.assertIn(tgt_stem, targets)                     # reindex 抽出真双链
        self.assertEqual(self.store.get_suggestion(row["id"])["status"], "applied")
        # 图谱实线出现（虚线升实线）
        from core.graph.engine import GraphEngine
        g = GraphEngine(self.store).build()
        ids = {n["id"] for n in g["nodes"]}
        solid = [e for e in g["edges"] if e["kind"] == "link"
                 and {g["nodes"][e["source"]]["id"], g["nodes"][e["target"]]["id"]}
                 == {"技术/甲.md", "技术/乙.md"}]
        self.assertEqual(len(solid), 1)

    def test_apply_stale(self):
        self._note("技术/甲.md", kws=["solo"])
        self._note("技术/乙.md", kws=["solo"])
        self.cur.edges_scan(self._cand(), limit=20)
        row = self.store.pending_suggestions(kind="edge")[0]
        self.vault.write_note(row["file_path"], "# 中途改过\n内容变了。")
        res = self.cur.apply_suggestion(row["id"])
        self.assertEqual(res["reason"], "stale")
        self.assertEqual(self.vault.read_note(row["file_path"]), "# 中途改过\n内容变了。")
        self.assertEqual(self.store.get_suggestion(row["id"])["status"], "obsolete")

    def test_cascade_rename_and_delete(self):
        self._note("技术/甲.md", kws=["solo"])
        self._note("技术/乙.md", kws=["solo"])
        self.cur.edges_scan(self._cand(), limit=20)
        row = self.store.pending_suggestions(kind="edge")[0]
        mod = row["file_path"]
        tpath = "技术/甲.md" if mod == "技术/乙.md" else "技术/乙.md"
        # 目标侧 DB 改名 → edge 建议 target 跟随 stem（级联扩 kind 的回归锚）
        self.store.rename_path(tpath, "技术/丙改名.md")
        got = self.store.get_suggestion(row["id"])
        self.assertEqual(got["target"], "丙改名")
        self.assertEqual(got["file_path"], mod)               # 被改侧不动
        # 目标被删 → obsolete → apply 拒
        self.store.delete_file("技术/丙改名.md")
        self.assertEqual(self.store.get_suggestion(row["id"])["status"], "obsolete")
        self.assertEqual(self.cur.apply_suggestion(row["id"])["reason"], "not_pending")

    def test_empty_scope(self):
        self.assertEqual(self.cur.edge_candidates(
            {"type": "topic", "topic": "不存在"}), [])
        self.assertEqual(
            self.cur.edges_dry_run({"type": "note", "paths": []})["will_pairs"], 0)


if __name__ == "__main__":
    unittest.main()
