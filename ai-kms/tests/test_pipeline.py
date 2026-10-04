"""清洗 / 分类 / 原子抽取 / 入库 / 去重 / 监听 的单元测试。

运行: python -m pytest tests -q   （或用 unittest: python -m unittest discover tests）
"""
from __future__ import annotations

import unittest
import time
import pathlib

from tests.conftest import TempEnv

from core.ingest.cleaner import Cleaner
from core.classify.rules import RuleClassifier
from core.ingest.pipeline import IngestPipeline
from core.ingest.watcher import DropWatcher
from core.atoms.extractor import AtomExtractor


class TestCleaner(unittest.TestCase):
    def setUp(self):
        self.c = Cleaner()

    def test_remove_html_and_normalize(self):
        out = self.c.clean("<script>bad()</script>你好<p>世界</p>")
        self.assertNotIn("bad", out)
        self.assertIn("你好", out)

    def test_multiple_blank_collapse(self):
        self.assertEqual(self.c.clean("a\n\n\n\nb"), "a\n\nb")

    def test_title_hint(self):
        self.assertEqual(self.c.extract_title_hint("# 我的标题\n正文"), "我的标题")


class TestClassifier(unittest.TestCase):
    def test_topic_tech(self):
        c = RuleClassifier().classify("python 算法 token 训练 架构")
        self.assertEqual(c["topic"], "技术")

    def test_topic_study(self):
        c = RuleClassifier().classify("学习笔记 课程 教程 方法")
        self.assertEqual(c["topic"], "学习")


class TestAtoms(unittest.TestCase):
    def test_extract_all_shape(self):
        atoms = AtomExtractor().extract_all("Python 生成器 yield 惰性求值。技术核心。")
        kinds = {a["kind"] for a in atoms}
        self.assertTrue({"keyword", "claim"}.issubset(kinds))


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.pipe = IngestPipeline(self.env.store, self.env.vault)

    def tearDown(self):
        self.env.teardown()

    def test_ingest_then_dedup(self):
        text = "# Python 生成器\n生成器是惰性求值机制，用 yield 实现，技术核心算法。"
        r1 = self.pipe.ingest_raw(text)
        self.assertTrue(r1["ok"])
        # 相同内容再次录入 => 判定重复
        r2 = self.pipe.ingest_raw(text)
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["reason"], "duplicate")
        self.assertEqual(r2["dup_of"], r1["rel_path"])

    def test_writes_native_md(self):
        r = self.pipe.ingest_raw("# 知识图谱\n用节点和边表示知识，技术研究。")
        full = self.env.vault.vault_path / r["rel_path"]
        self.assertTrue(full.exists())
        self.assertIn("知识图谱", full.read_text(encoding="utf-8"))

    def test_classify_cached_on_second_ingest(self):
        r = self.pipe.ingest_raw("# 番茄工作法\n时间管理方法，每25分钟专注，生活效率习惯。")
        cls2 = self.pipe.router.classify("whatever", rel_path=r["rel_path"])
        self.assertTrue(cls2.get("cached") is True)


class TestWatcher(unittest.TestCase):
    def test_live_drop_ingest(self):
        env = TempEnv()
        drop = env._tmp / "drop"
        drop.mkdir()
        pipe = IngestPipeline(env.store, env.vault)
        w = DropWatcher(drop, env.store, pipe)
        w.start()
        time.sleep(0.8)
        (drop / "素材.md").write_text("# 实时监听\n技术探针自动收录。", encoding="utf-8")
        target = env._tmp / "vault"
        deadline = time.time() + 6
        ingested = False
        while time.time() < deadline:
            if any(target.rglob("*.md")):
                ingested = True
                break
            time.sleep(0.3)
        w.stop()
        self.assertTrue(ingested, "监听未能在超时前收录素材")
        env.teardown()


class TestAIPilot(unittest.TestCase):
    """阶段3：AI 管家（MockProvider，离线也能跑）。"""

    def setUp(self):
        self.env = TempEnv()
        self.pipe = IngestPipeline(self.env.store, self.env.vault)
        self.pipe.ingest_raw("# Python 生成器\n生成器是惰性求值机制，用 yield 实现，技术核心算法。")
        self.pipe.ingest_raw("# 番茄工作法\n每25分钟专注一个任务，生活效率类方法。")
        from core.aipilot.manager import AIPilot
        self.pilot = AIPilot(self.env.store, self.env.vault)

    def tearDown(self):
        self.env.teardown()

    def test_qa_returns_offline_answer(self):
        a = self.pilot.ask("生成器是什么")
        self.assertIn("生成器", a)

    def test_summarize_uses_atoms(self):
        rel = self.env.store.all_files()[0]["path"]
        s = self.pilot.summarize_note(rel)
        self.assertTrue(len(s) > 10)

    def test_polish_preview_does_not_write(self):
        rel = self.env.store.all_files()[0]["path"]
        before = self.env.vault.read_note(rel)
        p = self.pilot.polish_preview(rel)
        self.assertIn("original", p)
        self.assertEqual(before, self.env.vault.read_note(rel))

    def test_write_with_confirmation(self):
        from core.aipilot.snapshot import ConfirmationNeeded
        rel = self.env.store.all_files()[0]["path"]
        new = "新的润色内容。"
        with self.assertRaises(ConfirmationNeeded):
            self.pilot.apply_change(rel, new, "test", require_confirm=True)
        # 确认后写回
        r = self.pilot.confirm_change(rel, new, "test")
        self.assertTrue(r["ok"])
        self.assertEqual(self.env.vault.read_note(rel), new)
        self.assertGreater(len(self.pilot.history(rel)), 0)

    def test_rollback(self):
        rel = self.env.store.all_files()[0]["path"]
        orig = self.env.vault.read_note(rel)
        self.pilot.apply_change(rel, "改过了", "t1")
        snaps = self.pilot.history(rel)
        self.pilot.rollback(snaps[0]["id"])
        self.assertEqual(self.env.vault.read_note(rel), orig)


if __name__ == "__main__":
    unittest.main()