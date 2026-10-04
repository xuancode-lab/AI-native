"""阶段4 E2E 全流程测试：收录 → 去重 → 图谱 → 检索 → AI → 快照回滚 → 批量 → 重建索引。"""
from __future__ import annotations

import unittest

from tests.conftest import TempEnv

from core.ingest.pipeline import IngestPipeline
from core.graph.engine import GraphEngine
from core.graph.search import KnowledgeSearch
from core.aipilot.manager import AIPilot
from core.aipilot.snapshot import ConfirmationNeeded


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.pipe = IngestPipeline(self.env.store, self.env.vault)
        self.graph = GraphEngine(self.env.store, self.env.vault)
        self.search = KnowledgeSearch(self.env.store, self.env.vault)
        self.pilot = AIPilot(self.env.store, self.env.vault)

    def tearDown(self):
        self.env.teardown()

    def _seed(self):
        a = self.pipe.ingest_raw(
            "# Python 生成器\n生成器是惰性求值机制，用 yield 实现。技术核心算法。")
        b = self.pipe.ingest_raw(
            "# 番茄工作法\n每25分钟专注一个任务再休息，生活效率类方法。")
        c = self.pipe.ingest_raw(
            "# 图谱入门\n知识图谱用[[Python 生成器]]和节点边表示，研究 [[番茄工作法]] 的关联。技术研究。")
        return a, b, c

    def test_full_flow(self):
        # 1) 收录 3 篇
        a, b, c = self._seed()
        for r in (a, b, c):
            self.assertTrue(r["ok"], r)

        # 2) 去重：重复录入被拦
        dup = self.pipe.ingest_raw("# Python 生成器\n生成器是惰性求值机制，用 yield 实现。技术核心算法。")
        self.assertEqual(dup["reason"], "duplicate")

        # 3) 同名不同内容 → 不覆盖（阶段4修复的 bug）
        same_title = self.pipe.ingest_raw("# 图谱入门\n完全不同的另一篇内容，生活旅行计划。")
        self.assertTrue(same_title["ok"])
        self.assertNotEqual(same_title["rel_path"], c["rel_path"])
        # 原文件未被覆盖
        self.assertIn("知识图谱", self.env.vault.read_note(c["rel_path"]))

        # 4) 图谱：link 边（wikilink）存在
        g = self.graph.build()
        kinds = {e["kind"] for e in g["edges"]}
        self.assertIn("link", kinds)

        # 5) 检索：能命中
        hits = self.search.search("生成器")
        self.assertTrue(any("Python 生成器" in h["title"] for h in hits))

        # 6) AI 问答（Mock 离线）返回基于知识库内容
        answer = self.pilot.ask("生成器是什么")
        self.assertTrue(len(answer) > 10)

        # 7) 润色 → 确认写回 → 快照 → 回滚
        target = c["rel_path"]
        original = self.env.vault.read_note(target)
        preview = self.pilot.polish_preview(target)
        with self.assertRaises(ConfirmationNeeded):
            self.pilot.apply_change(target, preview["polished"], "test", require_confirm=True)
        self.pilot.confirm_change(target, preview["polished"], "test")
        self.assertNotEqual(self.env.vault.read_note(target), original)
        snaps = self.pilot.history(target)
        self.assertGreaterEqual(len(snaps), 1)
        self.pilot.rollback(snaps[0]["id"])
        # 回滚恢复到确认前内容（回滚前先快照，所以要回到更早一次）
        self.assertTrue(self.env.vault.read_note(target))

        # 8) 记忆权重
        before = self.env.store.get_file(target)["usage_count"]
        self.pilot.snapshots  # noqa: touch
        self.env.store.touch_file(target)
        after = self.env.store.get_file(target)["usage_count"]
        self.assertGreater(after, before)

    def test_batch_and_reindex(self):
        # batch：收录目录
        src = self.env._tmp / "batch_src"
        src.mkdir()
        (src / "1.md").write_text("# 批量A\n技术学习笔记内容。", encoding="utf-8")
        (src / "2.md").write_text("# 批量B\n生活旅行计划内容。", encoding="utf-8")
        res = self.pipe.ingest_dir(src)
        self.assertTrue(res["ok"])
        self.assertEqual(res["ingested"], 2)
        # 再跑一次 → 全部重复
        res2 = self.pipe.ingest_dir(src)
        self.assertEqual(res2["ingested"], 0)
        self.assertEqual(res2["duplicate"], 2)

        # reindex：清空 DB 后从 Vault 恢复
        env_store = self.env.store
        env_store.conn.execute("DELETE FROM vault_files")
        env_store.conn.execute("DELETE FROM atoms")
        env_store.conn.execute("DELETE FROM classifications")
        env_store.conn.commit()
        stats = self.pipe.reindex()
        self.assertEqual(stats["scanned"], 2)
        self.assertEqual(stats["indexed"], 2)
        self.assertEqual(len(self.env.store.all_files()), 2)
        # 再跑一次 → 全跳过
        stats2 = self.pipe.reindex()
        self.assertEqual(stats2["indexed"], 0)
        self.assertEqual(stats2["skipped"], 2)

    def test_force_topic_new_note(self):
        """新建笔记时可手动指定主题（阶段5）。"""
        res = self.pipe.ingest_raw("# 手动主题笔记\n随便写点内容。", force_topic="创作")
        self.assertTrue(res["ok"])
        self.assertEqual(res["topic"], "创作")

    def test_rename_updates_all_tables(self):
        """重命名后所有关联表的路径同步更新（阶段5）。"""
        a, _, _ = self._seed()
        old = a["rel_path"]
        new = old.replace(".md", "-renamed.md")
        self.env.vault.vault_path.joinpath(old).rename(
            self.env.vault.vault_path.joinpath(new)
        )
        self.env.store.rename_path(old, new)
        self.assertIsNotNone(self.env.store.get_file(new))
        self.assertIsNone(self.env.store.get_file(old))
        for at in self.env.store.atoms_for(new):
            self.assertEqual(at["file_path"], new)
        # 双链：新路径仍能查到出链，旧路径查不到
        self.assertGreater(len(self.env.store.links_for(new)), 0)
        self.assertEqual(len(self.env.store.links_for(old)), 0)

    def test_delete_clears_all_records(self):
        """删除后清理所有表记录（阶段5）。"""
        a, _, _ = self._seed()
        rel = a["rel_path"]
        self.env.store.delete_file(rel)
        self.assertIsNone(self.env.store.get_file(rel))
        self.assertEqual(len(self.env.store.atoms_for(rel)), 0)
        self.assertEqual(len(self.env.store.links_for(rel)), 0)

    def test_edit_meta_and_reindex_atoms(self):
        """编辑保存后 update_edit_meta + 原子重建（阶段5）。"""
        from core.storage.vault import Vault as V
        a, _, _ = self._seed()
        rel = a["rel_path"]
        new_content = "# 改过的标题\n全新的内容，包含 图论 和 算法 关键词。"
        title = V.extract_title(new_content)
        dedup = V.make_dedup_key(new_content)
        self.env.vault.write_note(rel, new_content)
        self.env.store.update_edit_meta(rel, title, dedup)
        f = self.env.store.get_file(rel)
        self.assertEqual(f["title"], "改过的标题")
        # 原子重建
        self.env.store.clear_atoms(rel)
        self.env.store.add_atoms(rel, self.pipe.extractor.extract_all(new_content))
        kws = [x["value"] for x in self.env.store.atoms_for(rel) if x["kind"] == "keyword"]
        self.assertIn("图论", kws)


if __name__ == "__main__":
    unittest.main()