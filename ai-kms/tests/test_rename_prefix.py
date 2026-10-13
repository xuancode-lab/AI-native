"""rename_prefix / delete_paths / register_existing：目录级联的 DB 侧全覆盖回归。"""
from __future__ import annotations

import time
import unittest

from tests.conftest import TempEnv

from core.ingest.pipeline import IngestPipeline
from core.storage.vault import Vault


class TestRenamePrefix(unittest.TestCase):
    """seed：a/ 下两篇入库笔记 + 一白板 + 一图片；A/ 大小写兄弟目录；
    一条 wikilink pending + 一条 canvas_edge applied。"""

    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        self.vault = self.env.vault
        for rel in ("a/甲.md", "a/乙.md", "A/x.md"):
            body = f"# {rel}\n正文 图谱 节点。"
            self.vault.write_note(rel, body)
            self.store.upsert_file(rel, rel.rsplit("/", 1)[-1][:-3], "技术",
                                   dedup_key=Vault.make_dedup_key(body),
                                   mtime=time.time())
            self.store.add_atoms(rel, [{"kind": "keyword", "value": "图谱"}])
            self.store.add_link(rel, "甲" if "乙" in rel else "主题词")
            self.store.index_note_tokens(rel, body)
        self.vault.write_note("a/丁.canvas", '{"nodes":[],"edges":[]}')
        (self.vault.vault_path / "a").mkdir(exist_ok=True)
        (self.vault.vault_path / "a" / "img.png").write_bytes(b"\x89PNG")
        self.store.add_suggestions([
            {"kind": "wikilink", "file_path": "a/甲.md", "target": "乙",
             "confidence": 0.9,
             "payload": {"source": "shared_keywords", "target_title": "乙",
                         "target_path": "a/乙.md", "content_fp": "x",
                         "insert_at": None, "anchor": None, "safe": False}},
            {"kind": "canvas_edge", "file_path": "a/甲.md", "target": "乙",
             "confidence": 1.0, "status": "applied",
             "payload": {"source": "canvas", "target_title": "乙",
                         "target_path": "a/乙.md", "content_fp": "x",
                         "insert_at": None, "anchor": None, "safe": False,
                         "canvas_rel": "a/丁.canvas", "edge_id": "e1",
                         "from_rel": "a/甲.md", "to_rel": "a/乙.md"}},
        ])
        # canvas_edge 直接置 applied（add_suggestions 默认 pending）
        self.store.conn.execute(
            "UPDATE suggestions SET status='applied' WHERE kind='canvas_edge'")
        self.store.conn.commit()

    def _paths(self, table="vault_files", col="path"):
        return {r[col] for r in self.store.conn.execute(
            f"SELECT {col} FROM {table}")}

    def test_dir_rename_full_cascade(self):
        res = self.store.rename_prefix("a", "收藏/a")
        self.assertEqual(res["files"], 2)                  # a/甲 a/乙；A/x 不算
        self.assertEqual(self._paths(),
                         {"收藏/a/甲.md", "收藏/a/乙.md", "A/x.md"})
        self.assertEqual(self._paths("atoms", "file_path"),
                         {"收藏/a/甲.md", "收藏/a/乙.md", "A/x.md"})
        self.assertEqual(self._paths("links", "from_path"),
                         {"收藏/a/甲.md", "收藏/a/乙.md", "A/x.md"})
        self.assertEqual(self._paths("suggestions", "file_path"),
                         {"收藏/a/甲.md"})
        if self.store.fts_ok:
            self.assertEqual(self._paths("notes_fts", "path"),
                             {"收藏/a/甲.md", "收藏/a/乙.md", "A/x.md"})
        self.assertEqual(res["payloads"], 2)               # wikilink + applied canvas_edge
        rows = {r["kind"]: r["payload"] for r in self.store.conn.execute(
            "SELECT kind, payload FROM suggestions")}
        import json
        wk = json.loads(rows["wikilink"])
        ce = json.loads(rows["canvas_edge"])
        self.assertEqual(wk["target_path"], "收藏/a/乙.md")
        self.assertEqual(ce["canvas_rel"], "收藏/a/丁.canvas")   # 未入库文件也在 payload 键里
        self.assertEqual((ce["from_rel"], ce["to_rel"]),
                         ("收藏/a/甲.md", "收藏/a/乙.md"))
        self.assertEqual(rows["canvas_edge"], rows["canvas_edge"])  # status 列不动
        st = self.store.conn.execute(
            "SELECT status FROM suggestions WHERE kind='canvas_edge'").fetchone()
        self.assertEqual(st["status"], "applied")           # applied 照改 payload 不改态

    def test_targets_not_touched(self):
        """stem 未变：links.to_target 与 suggestions.target 刻意不动（与 rename_path 差异）。"""
        before_links = [r["to_target"] for r in self.store.conn.execute(
            "SELECT to_target FROM links")]
        self.store.rename_prefix("a", "收藏/a")
        after_links = [r["to_target"] for r in self.store.conn.execute(
            "SELECT to_target FROM links")]
        self.assertEqual(sorted(before_links), sorted(after_links))
        for r in self.store.conn.execute("SELECT target FROM suggestions"):
            self.assertEqual(r["target"], "乙")

    def test_single_file_move_degenerates(self):
        res = self.store.rename_prefix("a/甲.md", "b/甲.md")
        self.assertEqual(res["files"], 1)
        self.assertIn("b/甲.md", self._paths())
        self.assertIn("a/乙.md", self._paths())             # 兄弟不动

    def test_clash_rejected_no_write(self):
        self.store.upsert_file("收藏/a/甲.md", "甲", "技术",
                               dedup_key="k", mtime=time.time())
        with self.assertRaises(ValueError):
            self.store.rename_prefix("a", "收藏/a")
        self.assertIn("a/甲.md", self._paths())             # 撞名预检在 UPDATE 前，无脏写

    def test_self_nest_rejected(self):
        with self.assertRaises(ValueError):
            self.store.rename_prefix("a", "a/b")

    def test_noop_same_prefix(self):
        self.assertEqual(self.store.rename_prefix("a", "a"),
                         {"files": 0, "payloads": 0})

    def test_epoch_bumped(self):
        e0 = self.store.epoch
        self.store.rename_prefix("a", "收藏/a")
        self.assertGreater(self.store.epoch, e0)

    def test_delete_paths_batch(self):
        n = self.store.delete_paths(["a/甲.md", "a/乙.md", "A/x.md"])
        self.assertEqual(n, 3)
        self.assertEqual(self._paths(), set())
        self.assertEqual(self._paths("atoms", "file_path"), set())
        pend = self.store.conn.execute(
            "SELECT status FROM suggestions WHERE kind='wikilink'").fetchone()
        self.assertEqual(pend["status"], "obsolete")        # pending→obsolete 同 delete_file
        self.assertEqual(self.store.conn.execute(
            "SELECT status FROM suggestions WHERE kind='canvas_edge'"
        ).fetchone()["status"], "applied")                  # 已决策不动

    def test_delete_paths_empty(self):
        self.assertEqual(self.store.delete_paths([]), 0)


class TestRegisterExisting(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        self.vault = self.env.vault
        self.pipe = IngestPipeline(self.store, self.vault)

    def tearDown(self):
        self.env.teardown()

    def _write(self, rel, body):
        self.vault.write_note(rel, body)

    def test_orphan_registered_in_place(self):
        self._write("学习/笔记 乙.md", "# 笔记 乙\n正文 图谱 节点。")
        res = self.pipe.register_existing("学习/笔记 乙.md")
        self.assertEqual(res["status"], "registered")
        self.assertTrue(self.store.file_exists("学习/笔记 乙.md"))
        self.assertTrue(self.vault.vault_path.joinpath("学习/笔记 乙.md").exists())
        # 幂等：二次 already；不产生第二份
        again = self.pipe.register_existing("学习/笔记 乙.md")
        self.assertEqual(again["status"], "already")
        self.assertEqual(len(self.vault.all_markdown_files()), 1)

    def test_duplicate_and_ghost_and_empty(self):
        body = "# 甲\n内容甲。"
        self._write("技术/甲.md", body)
        self.pipe.register_existing("技术/甲.md")
        self._write("学习/副本.md", body)                   # 同内容异路径
        self.assertEqual(self.pipe.register_existing("学习/副本.md")["status"],
                         "duplicate")
        self.assertEqual(self.pipe.register_existing("缺/无.md")["status"], "ghost")
        self._write("技术/空.md", "   ")
        self.assertEqual(self.pipe.register_existing("技术/空.md")["status"], "empty")

    def test_reindex_counts_compat(self):
        self._write("技术/一.md", "# 一\nA。")
        self._write("技术/二.md", "# 二\nB。")
        st = self.pipe.reindex()
        self.assertEqual((st["scanned"], st["indexed"], st["skipped"]), (2, 2, 0))
        st2 = self.pipe.reindex()
        self.assertEqual((st2["indexed"], st2["skipped"]), (0, 2))


if __name__ == "__main__":
    unittest.main()
