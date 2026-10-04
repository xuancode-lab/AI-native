"""suggestions 表：批量插入 / UNIQUE 刷新语义 / 过滤 / resolve / obsolete / purge。"""
from __future__ import annotations

import unittest

from tests.conftest import TempEnv


def _row(path="a/x.md", target="y", kind="wikilink", conf=0.5, **payload):
    return {"kind": kind, "file_path": path, "target": target,
            "confidence": conf, "payload": payload}


class TestSuggestions(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store

    def tearDown(self):
        self.env.teardown()

    def test_add_and_unique(self):
        n = self.store.add_suggestions([_row(), _row(), _row(target="z")])
        self.assertEqual(self.store.count_pending(), 2)     # 重复行被 UNIQUE 吸收

    def test_rescan_refreshes_pending_only(self):
        self.store.add_suggestions([_row(conf=0.5, note="old")])
        sid = self.store.pending_suggestions()[0]["id"]
        self.store.resolve_suggestion(sid, "skipped")
        # 重扫：同一 (kind,path,target) 再来——skipped 行不动，不复活
        self.store.add_suggestions([_row(conf=0.9, note="new")])
        self.assertEqual(self.store.count_pending(), 0)
        rows = self.store.list_suggestions("skipped")
        self.assertEqual(rows[0]["confidence"], 0.5)         # 保持旧值
        # pending 行则刷新 payload/confidence
        self.store.add_suggestions([_row(path="b/y.md", conf=0.5)])
        self.store.add_suggestions([_row(path="b/y.md", conf=0.99, note="v2")])
        row = [r for r in self.store.pending_suggestions()
               if r["file_path"] == "b/y.md"][0]
        self.assertAlmostEqual(row["confidence"], 0.99)
        self.assertEqual(row["payload"].get("note"), "v2")

    def test_filters(self):
        self.store.add_suggestions([
            _row(path="a.md", target="t1", conf=0.9),
            _row(path="a.md", target="t2", kind="topic", conf=0.3),
            _row(path="b.md", target="t3", conf=0.6),
        ])
        self.assertEqual(len(self.store.pending_suggestions(kind="wikilink")), 2)
        self.assertEqual(len(self.store.pending_suggestions(min_conf=0.5)), 2)
        self.assertEqual(len(self.store.pending_suggestions(limit=1)), 1)
        # confidence 降序
        top = self.store.pending_suggestions()[0]
        self.assertEqual(top["target"], "t1")

    def test_resolve_and_count(self):
        self.store.add_suggestions([_row(), _row(target="z")])
        sid = self.store.pending_suggestions()[0]["id"]
        self.store.resolve_suggestion(sid, "applied")
        self.assertEqual(self.store.count_pending(), 1)
        got = self.store.get_suggestion(sid)
        self.assertEqual(got["status"], "applied")
        self.assertIsNotNone(got["resolved_at"])

    def test_mark_obsolete(self):
        self.store.add_suggestions([_row(path="a.md", target="t"),
                                    _row(path="b.md", target="t2")])
        self.store.mark_obsolete(file_path="a.md")
        self.assertEqual(self.store.count_pending(), 1)
        self.assertEqual(self.store.list_suggestions("obsolete")[0]["file_path"], "a.md")

    def test_purge_resolved(self):
        self.store.add_suggestions([_row(), _row(target="z")])
        sid = self.store.pending_suggestions()[0]["id"]
        self.store.resolve_suggestion(sid, "applied")
        n = self.store.purge_resolved()
        self.assertEqual(n, 1)
        self.assertIsNone(self.store.get_suggestion(sid))
        self.assertEqual(self.store.count_pending(), 1)


if __name__ == "__main__":
    unittest.main()
