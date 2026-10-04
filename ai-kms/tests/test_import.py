"""批量导入：枚举白名单 / 路径归一 / 去重幂等 / ingest_dir 回归。非 GUI。

运行: python -m pytest tests -q
"""
from __future__ import annotations

import unittest

from tests.conftest import TempEnv

from core.ingest.pipeline import (
    IngestPipeline, SUPPORTED_EXTS, iter_supported_files, collect_importable,
)


class TestEnumerate(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        src = self.env._tmp / "src"
        (src / "sub").mkdir(parents=True)
        (src / "a.md").write_text("# A\n技术 python 架构 算法。", encoding="utf-8")
        (src / "b.txt").write_text("B 学习笔记 课程 方法。", encoding="utf-8")
        (src / "c.rst").write_text("C 生活 番茄工作法 效率。", encoding="utf-8")
        (src / "d.png").write_bytes(b"\x89PNG")
        (src / "e.md.tmp").write_text("x", encoding="utf-8")        # 临时 → 排除
        (src / "~$f.md").write_text("x", encoding="utf-8")          # Office 临时 → 排除
        (src / ".hidden.md").write_text("x", encoding="utf-8")      # 隐藏 → 排除
        (src / "sub" / "g.md").write_text("# G\n创作 写作 灵感。", encoding="utf-8")
        self.src = src

    def tearDown(self):
        self.env.teardown()

    def test_ext_constant(self):
        self.assertEqual(SUPPORTED_EXTS, (".md", ".txt", ".rst"))

    def test_iter_supported_recurses_and_excludes(self):
        names = {p.name for p in iter_supported_files(self.src)}
        self.assertEqual(names, {"a.md", "b.txt", "c.rst", "g.md"})
        self.assertEqual(iter_supported_files(self.src / "d.png"), [])  # 非目录 → 空

    def test_collect_importable_mixed(self):
        missing = str(self.src / "missing.md")
        files, skipped = collect_importable(
            [str(self.src), str(self.src / "d.png"), missing, str(self.src / "a.md")])
        self.assertIn("d.png", skipped)
        self.assertIn(missing, skipped)
        names = {p.name for p in files}
        self.assertGreaterEqual(names, {"a.md", "b.txt", "c.rst", "g.md"})

    def test_collect_importable_dedups_paths(self):
        f2, _ = collect_importable([str(self.src), str(self.src)])
        keys = [str(p.resolve()) for p in f2]
        self.assertEqual(len(keys), len(set(keys)))


class TestIngestDirRegression(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.pipe = IngestPipeline(self.env.store, self.env.vault)

    def tearDown(self):
        self.env.teardown()

    def test_ingest_dir_shape_unchanged(self):
        src = self.env._tmp / "s"
        src.mkdir()
        (src / "x.md").write_text("# X\n技术 代码 算法 架构。", encoding="utf-8")
        (src / "y.png").write_bytes(b"1")
        r = self.pipe.ingest_dir(src)
        self.assertTrue(r["ok"])
        self.assertEqual(r["ingested"], 1)
        self.assertEqual(r["errors"], 0)
        self.assertGreaterEqual(
            set(r), {"ok", "ingested", "duplicate", "empty", "errors", "details"})

    def test_reimport_all_duplicate(self):
        src = self.env._tmp / "s"
        src.mkdir()
        (src / "x.md").write_text("# Z\n技术 python。", encoding="utf-8")
        self.pipe.ingest_dir(src)
        r2 = self.pipe.ingest_dir(src)          # 第二次全重复
        self.assertEqual(r2["ingested"], 0)
        self.assertEqual(r2["duplicate"], 1)

    def test_per_file_branch_counts(self):
        """GUI tick 循环的逐文件语义等价校验：ok / duplicate / empty 各归各类。"""
        src = self.env._tmp / "s"
        src.mkdir()
        (src / "note.md").write_text("# 笔记\n学习 方法 总结。", encoding="utf-8")
        (src / "blank.md").write_text("   \n  ", encoding="utf-8")   # clean 后为空
        res = {p.name: self.pipe.ingest_file(p)
               for p in iter_supported_files(src)}
        self.assertTrue(res["note.md"]["ok"])
        self.assertFalse(res["blank.md"]["ok"])
        self.assertEqual(res["blank.md"]["reason"], "empty")
        dup = self.pipe.ingest_file(src / "note.md")
        self.assertEqual(dup["reason"], "duplicate")


if __name__ == "__main__":
    unittest.main()
