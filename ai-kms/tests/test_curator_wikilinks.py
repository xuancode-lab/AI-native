"""curator 双链生成：候选两档 / 排除项 / 围栏安全 / 扫描幂等 / apply 与 stale 保护。"""
from __future__ import annotations

import time
import unittest

from tests.conftest import TempEnv

from core.curator import Curator
from core.ingest.pipeline import IngestPipeline
from core.storage.vault import Vault


class TestWikilinks(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        self.vault = self.env.vault
        self.cur = Curator(self.store, self.vault,
                           IngestPipeline(self.store, self.vault))

    def tearDown(self):
        self.env.teardown()

    def _note(self, rel, body, kws=(), topic="技术"):
        self.vault.write_note(rel, body)
        self.store.upsert_file(rel, Vault.extract_title(body), topic,
                               dedup_key=Vault.make_dedup_key(body),
                               mtime=time.time())
        if kws:
            self.store.add_atoms(rel, [{"kind": "keyword", "value": k} for k in kws])

    def _ctx(self):
        return self.cur._scan_ctx()

    # ---------- 候选生成 ----------
    def test_title_mention(self):
        self._note("技术/GraphDB.md", "# GraphDB\n图数据库的存储引擎。")
        self._note("技术/a.md", "# 甲\n这里引用了 GraphDB 的概念，很好。")
        rows = self.cur.wikilinks_for("技术/a.md", self._ctx())
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["target"], "GraphDB")     # target=文件 stem
        self.assertAlmostEqual(r["confidence"], 0.9)
        pl = r["payload"]
        content = self.vault.read_note("技术/a.md")
        self.assertEqual(content[pl["insert_at"]:pl["insert_at"] + len("GraphDB")],
                         "GraphDB")
        self.assertTrue(pl["safe"])
        self.assertEqual(pl["content_fp"], Vault.make_dedup_key(content))

    def test_exclusions(self):
        self._note("技术/GraphDB.md", "# GraphDB\n存储。")
        self._note("技术/a.md", "# 甲\n引用 GraphDB 与 技术。")   # 技术=主题名
        self.store.add_link("技术/a.md", "GraphDB")              # 已链过
        self.store.index_note_tokens("技术/a.md", "引用 GraphDB 与 技术")
        self.store.index_note_tokens("技术/GraphDB.md", "存储。")
        rows = self.cur.wikilinks_for("技术/a.md", self._ctx())
        self.assertEqual(rows, [])

    def test_ambiguous_titles_skipped(self):
        self._note("技术/x/GraphDB.md", "# 一\n存储。")
        self._note("学习/y/GraphDB.md", "# 二\n存储。")
        self._note("技术/a.md", "# 甲\n引用 GraphDB 一次。")
        self.store.index_note_tokens("技术/a.md", "引用 GraphDB 一次。")
        ctx = self._ctx()
        self.assertIn("GraphDB", ctx.ambiguous)
        self.assertEqual(self.cur.wikilinks_for("技术/a.md", ctx), [])

    def test_mention_in_fence_not_safe(self):
        self._note("技术/GraphDB.md", "# GraphDB\n存储。")
        self._note("技术/a.md", "# 甲\n代码里 ```\nGraphDB\n``` 出现不算。\n后面还有 GraphDB。")
        rows = self.cur.wikilinks_for("技术/a.md", self._ctx())
        self.assertEqual(len(rows), 1)
        pl = rows[0]["payload"]
        self.assertFalse(pl["safe"])          # 首次出现在围栏内 → 不可原位插入
        self.assertIsNone(pl["insert_at"])

    def test_shared_keywords_section_mode(self):
        self._note("技术/b.md", "# 乙\n共享词很多。", kws=["索引", "存储", "查询"])
        self._note("技术/a.md", "# 甲\n也共享。", kws=["索引", "存储"])
        self._note("技术/d.md", "# 丁\n只共享一个词。", kws=["索引"])   # 1 词 < 阈值
        rows = self.cur.wikilinks_for("技术/a.md", self._ctx())
        r = [x for x in rows if x["payload"]["source"] == "shared_keywords"]
        self.assertEqual(len(r), 1)
        self.assertAlmostEqual(r[0]["confidence"], 0.6)
        self.assertIsNone(r[0]["payload"]["insert_at"])
        self.assertEqual(sorted(r[0]["payload"]["shared_keywords"]), ["存储", "索引"])

    # ---------- 扫描 ----------
    def test_scan_idempotent(self):
        self._note("技术/GraphDB.md", "# GraphDB\n存储。")
        self._note("技术/a.md", "# 甲\n引用 GraphDB。")
        paths = self.cur.resolve_scope({"type": "all"})
        r1 = self.cur.wikilinks_scan(paths, limit=10)
        n1 = self.store.count_pending("wikilink")
        self.cur.wikilinks_scan(paths, limit=10)               # 重扫
        self.assertEqual(self.store.count_pending("wikilink"), n1)

    # ---------- 应用 ----------
    def test_apply_inline(self):
        self._note("技术/GraphDB.md", "# GraphDB\n存储。")
        self._note("技术/a.md", "# 甲\n这里引用了 GraphDB 的概念。")
        self.store.index_note_tokens("技术/a.md", "这里引用了 GraphDB 的概念。")
        rows = self.cur.wikilinks_for("技术/a.md", self._ctx())
        self.store.add_suggestions(rows)
        sid = self.store.pending_suggestions()[0]["id"]
        res = self.cur.apply_suggestion(sid)
        self.assertTrue(res["ok"], res)
        content = self.vault.read_note("技术/a.md")
        self.assertIn("[[GraphDB]]", content)                  # 插入用标题原文文本
        snaps = self.store.conn.execute(
            "SELECT * FROM snapshots WHERE file_path='技术/a.md'").fetchall()
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["reason"], "AI 双链")
        self.assertEqual(self.store.get_suggestion(sid)["status"], "applied")
        targets = [l["to_target"] for l in self.store.links_for("技术/a.md")]
        self.assertIn("GraphDB", targets)                       # reindex 后双链入图

    def test_apply_section_append(self):
        # 关键词写进正文本体：apply 后 reindex 会按正文重抽原子，悬空关键词会消失
        self._note("技术/b.md", "# 乙\n索引 存储 索引 存储 索引。",
                   kws=["索引", "存储"])
        self._note("技术/a.md", "# 甲\n索引 存储 索引 存储 存储。",
                   kws=["索引", "存储"])
        self.store.index_note_tokens("技术/a.md", "索引 存储 索引 存储 存储。")
        rows = [r for r in self.cur.wikilinks_for("技术/a.md", self._ctx())
                if r["payload"]["source"] == "shared_keywords"]
        self.assertTrue(rows)
        self.store.add_suggestions(rows)
        res = self.cur.apply_suggestion(self.store.pending_suggestions()[0]["id"])
        self.assertTrue(res["ok"], res)
        content = self.vault.read_note("技术/a.md")
        self.assertIn("## 相关", content)
        self.assertIn("[[b]]", content)
        # 再次相关段追加不重复建段
        self._note("技术/c.md", "# 丙\n索引 存储 索引 索引。", kws=["索引", "存储"])
        self.store.index_note_tokens("技术/c.md", "索引 存储 索引 索引。")
        rows = [r for r in self.cur.wikilinks_for("技术/a.md", self._ctx())
                if r["payload"]["source"] == "shared_keywords"]
        self.store.add_suggestions(rows)
        sid = [s for s in self.store.pending_suggestions()
               if s["target"] == "c"][0]["id"]
        self.cur.apply_suggestion(sid)
        content = self.vault.read_note("技术/a.md")
        self.assertEqual(content.count("## 相关"), 1)
        self.assertIn("[[c]]", content)

    def test_apply_stale_protection(self):
        self._note("技术/GraphDB.md", "# GraphDB\n存储。")
        self._note("技术/a.md", "# 甲\n这里引用了 GraphDB 的概念。")
        self.store.index_note_tokens("技术/a.md", "这里引用了 GraphDB 的概念。")
        self.store.add_suggestions(self.cur.wikilinks_for("技术/a.md", self._ctx()))
        sid = self.store.pending_suggestions()[0]["id"]
        # 扫描后用户改了笔记 → 指纹不符，不盲写
        self.vault.write_note("技术/a.md", "# 甲\n完全改掉了，GraphDB 也不提了。")
        before = self.vault.read_note("技术/a.md")
        res = self.cur.apply_suggestion(sid)
        self.assertEqual(res, {"ok": False, "reason": "stale"})
        self.assertEqual(self.vault.read_note("技术/a.md"), before)  # 未被写
        self.assertEqual(self.store.get_suggestion(sid)["status"], "obsolete")


if __name__ == "__main__":
    unittest.main()
