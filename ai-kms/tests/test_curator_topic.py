"""curator 分类校准：纯缓存判定 / needs_reread 三态 / 扫描零读盘 / apply 移动分类。"""
from __future__ import annotations

import json
import os
import time
import unittest

from tests.conftest import TempEnv

from core.curator import Curator
from core.ingest.pipeline import IngestPipeline
from core.storage.vault import Vault


class TestTopicCalibration(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        self.vault = self.env.vault
        self.pipe = IngestPipeline(self.store, self.vault)
        self.cur = Curator(self.store, self.vault, self.pipe)

    def tearDown(self):
        self.env.teardown()

    def _note(self, rel, body, topic, verdict_hits=None):
        self.vault.write_note(rel, body)
        self.store.upsert_file(rel, Vault.extract_title(body), topic,
                               dedup_key=Vault.make_dedup_key(body), mtime=time.time())
        if verdict_hits is not None:
            self.store.save_classification(rel, {
                "topic": topic, "type": "note", "priority": 0,
                "verdict": {"topic_hits": verdict_hits}})

    # ---------- 判定 ----------
    def test_suggestion_when_cache_disagrees(self):
        self._note("未分类/x.md", "# X\npython 算法 架构。", "未分类",
                   {"技术": 5, "学习": 1, "生活": 0, "创作": 0, "课题": 0})
        r = self.cur.recalibrate_scan(["未分类/x.md"], limit=10)
        self.assertEqual(r["added"], 1)
        s = self.store.pending_suggestions(kind="topic")[0]
        self.assertEqual(s["target"], "技术")
        self.assertAlmostEqual(s["confidence"], 0.8, places=2)
        self.assertFalse(s["payload"]["needs_reread"])

    def test_no_suggestion_when_agrees(self):
        self._note("技术/y.md", "# Y", "技术", {"技术": 9, "学习": 1})
        r = self.cur.recalibrate_scan(["技术/y.md"], limit=10)
        self.assertEqual(r["added"], 0)

    def test_needs_reread_states(self):
        # 三态：无分类行 / hits 全 0 / 打平
        self._note("未分类/n1.md", "# 一", "未分类", None)
        self._note("未分类/n2.md", "# 二", "未分类", {"技术": 0, "学习": 0})
        self._note("未分类/n3.md", "# 三", "未分类", {"技术": 3, "学习": 3})
        paths = ["未分类/n1.md", "未分类/n2.md", "未分类/n3.md"]
        dry = self.cur.recalibrate_dry_run({"type": "note", "paths": paths})
        self.assertEqual(dry["needs_reread"], 3)
        self.cur.recalibrate_scan(paths, limit=10)
        rows = self.store.pending_suggestions(kind="topic")
        self.assertEqual(len(rows), 3)
        for s in rows:
            self.assertTrue(s["payload"]["needs_reread"])
            self.assertEqual(s["confidence"], 0.0)

    def test_scan_reads_no_note_files(self):
        # 原则 4 硬验证：纯缓存扫描不得触碰 vault 文件（mtime 全不变）
        self._note("未分类/z.md", "# Z\n内容若干。", "未分类", {"技术": 4})
        stat_before = os.stat(self.vault.vault_path / "未分类/z.md")
        time.sleep(0.01)
        self.cur.recalibrate_scan(["未分类/z.md"], limit=10)
        stat_after = os.stat(self.vault.vault_path / "未分类/z.md")
        self.assertEqual(stat_before.st_mtime_ns, stat_after.st_mtime_ns)

    def test_reread_recalibrate(self):
        self._note("未分类/r.md", "# 未分类的Python笔记\npython 算法 架构 token 训练 代码。",
                   "未分类", None)
        self._note("未分类/keep.md", "# 保持\n随便。", "未分类", {"技术": 0})
        self.cur.recalibrate_scan(["未分类/r.md", "未分类/keep.md"], limit=10)
        rows = [s for s in self.store.pending_suggestions(kind="topic")
                if s["payload"].get("needs_reread")]
        self.assertEqual(len(rows), 2)
        out = self.cur.reread_recalibrate(["未分类/r.md", "未分类/keep.md"])
        self.assertEqual(out["changed"], 1)                     # r 判为技术；keep 正文无信息
        fresh = self.store.pending_suggestions(kind="topic")
        targets = {s["file_path"]: s["target"] for s in fresh}
        self.assertEqual(targets.get("未分类/r.md"), "技术")
        self.assertEqual(fresh[0]["payload"]["evidence"], "reread")
        # reread 后分类缓存即新值（router 不再回旧判）
        c = self.pipe.router.classify("x", title="t", rel_path="未分类/r.md")
        self.assertEqual(c["topic"], "技术")

    # ---------- 应用 ----------
    def test_apply_topic_moves_and_cascades(self):
        self._note("未分类/m.md", "# 移动\npython 技术。", "未分类", {"技术": 5})
        self.store.index_note_tokens("未分类/m.md", "# 移动\npython 技术。")
        self.store.add_link("未分类/m.md", "未分类")
        self.store.add_atoms("未分类/m.md",
                             [{"kind": "topic", "value": "未分类"}])
        self.store.add_suggestions([{
            "kind": "topic", "file_path": "未分类/m.md", "target": "技术",
            "confidence": 0.8, "payload": {"needs_reread": False}}])
        sid = self.store.pending_suggestions()[0]["id"]
        res = self.cur.apply_suggestion(sid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["new_rel"], "技术/m.md")
        self.assertFalse((self.vault.vault_path / "未分类/m.md").exists())
        self.assertTrue((self.vault.vault_path / "技术/m.md").exists())
        row = self.store.get_file("技术/m.md")
        self.assertEqual(row["topic"], "技术")
        self.assertIsNone(self.store.get_file("未分类/m.md"))
        # FTS/atoms/links/suggestions 级联到新 path
        self.assertIn("技术/m.md", self.store.fts_paths())
        lk = [l["to_target"] for l in self.store.links_for("技术/m.md")]
        self.assertIn("技术", lk)
        self.assertNotIn("未分类", lk)
        tp = [a["value"] for a in self.store.atoms_for("技术/m.md") if a["kind"] == "topic"]
        self.assertEqual(tp, ["技术"])
        self.assertEqual(self.store.get_suggestion(sid)["status"], "applied")
        cls = self.store.latest_classification("技术/m.md")
        self.assertEqual(json.loads(cls["verdict"])["source"], "curator_recalibration")

    def test_apply_topic_no_change_skips(self):
        self.store.add_suggestions([{"kind": "topic", "file_path": "技术/t.md",
                                     "target": "技术", "confidence": 0.5}])
        self.store.upsert_file("技术/t.md", "T", "技术")
        sid = self.store.pending_suggestions()[0]["id"]
        res = self.cur.apply_suggestion(sid)
        self.assertEqual(res["reason"], "no_change")
        self.assertEqual(self.store.get_suggestion(sid)["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
