"""fsindex 纯函数：rel 校验 / 前缀语义 / 六态判定 / canvas file 级联。"""
from __future__ import annotations

import unittest

from core.storage.fsindex import (FileState, apply_prefix_rename, build_states,
                                  file_state, normalize_rel, parent_dir,
                                  under_prefix, rewrite_doc_files)


class TestRelUtils(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_rel(r"技术\甲篇.md"), "技术/甲篇.md")
        for bad in ("", "/", "/abs.md", "../out.md", "a/../b.md", "a//b.md",
                    "C:/x.md", "a/./b.md"):
            with self.assertRaises(ValueError):
                normalize_rel(bad)
        # 隐藏文件（.hidden/x）normalize 放行——过滤属枚举层职责
        self.assertEqual(normalize_rel(".hidden/x.md"), ".hidden/x.md")

    def test_parent_dir(self):
        self.assertEqual(parent_dir("a/b/c.md"), "a/b")
        self.assertEqual(parent_dir("c.md"), "")

    def test_under_prefix_strict_segment(self):
        self.assertTrue(under_prefix("a", "a"))
        self.assertTrue(under_prefix("a/b.md", "a"))
        self.assertTrue(under_prefix("a/b/c.md", "a"))
        self.assertFalse(under_prefix("ab/c.md", "a"))     # 段边界：ab 不在 a 下
        self.assertFalse(under_prefix("b/a.md", "a"))
        self.assertTrue(under_prefix("anything", ""))      # 空前缀=全库
        # 文件路径作前缀只匹自己（单文件移动=前缀退化）
        self.assertTrue(under_prefix("a/b.md", "a/b.md"))
        self.assertFalse(under_prefix("a/b.md.x", "a/b.md"))


class TestApplyPrefixRename(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(apply_prefix_rename("a/b.md", "a", "收藏/a"), "收藏/a/b.md")
        self.assertIsNone(apply_prefix_rename("other/b.md", "a", "x"))
        # 目录本身改名
        self.assertEqual(apply_prefix_rename("a", "a", "b"), "b")
        # 单文件移动
        self.assertEqual(apply_prefix_rename("a/b.md", "a/b.md", "c/b.md"), "c/b.md")

    def test_from_root_prefix(self):
        self.assertEqual(apply_prefix_rename("a.md", "", "归档"), "归档/a.md")
        self.assertEqual(apply_prefix_rename("a.md", "", ""), "a.md")


class TestFileState(unittest.TestCase):
    IDX = frozenset({"技术/已入库.md"})

    def test_six_states(self):
        self.assertEqual(file_state("技术/已入库.md", False, self.IDX), FileState.ATOMIC)
        self.assertEqual(file_state("技术/孤儿.md", False, self.IDX), FileState.RAW)
        self.assertEqual(file_state("a/x.CANVAS", False, self.IDX), FileState.CANVAS)
        self.assertEqual(file_state("附件/图.PNG", False, self.IDX), FileState.IMAGE)
        self.assertEqual(file_state("a/b.pdf", False, self.IDX), FileState.OTHER)
        self.assertEqual(file_state("a", True, self.IDX), FileState.FOLDER)
        self.assertEqual(file_state("a", True, self.IDX, has_children=False),
                         FileState.FOLDER_EMPTY)


class TestBuildStates(unittest.TestCase):
    def test_two_pass_and_nested(self):
        entries = [("技术", True), ("技术/甲.md", False), ("空夹", True),
                   ("外", True), ("外/深", True), ("外/深/乙.txt", False)]
        st = build_states(entries, {"技术/甲.md"})
        self.assertEqual(st["技术/甲.md"], FileState.ATOMIC)
        self.assertEqual(st["外/深/乙.txt"], FileState.RAW)
        self.assertEqual(st["技术"], FileState.FOLDER)
        self.assertEqual(st["空夹"], FileState.FOLDER_EMPTY)      # 没有任何子项见过它
        self.assertEqual(st["外/深"], FileState.FOLDER)
        self.assertEqual(st["外"], FileState.FOLDER)              # 子目录也算孩子

    def test_root_entries(self):
        st = build_states([("c.md", False)], set())
        self.assertEqual(st["c.md"], FileState.RAW)


class TestRewriteDocFiles(unittest.TestCase):
    PAYLOAD = {"nodes": [
        {"id": "n1", "type": "file", "x": 0, "y": 0, "width": 240, "height": 120,
         "file": "旧目录/甲.md"},
        {"id": "n2", "type": "file", "x": 0, "y": 0, "width": 240, "height": 120,
         "file": "别的/乙.md"},
        {"id": "t1", "type": "text", "x": 0, "y": 0, "width": 240, "height": 120,
         "text": "hi"},
        {"id": "g1", "type": "group", "x": 0, "y": 0, "width": 400, "height": 400},
    ], "edges": [{"id": "e1", "fromNode": "n1", "toNode": "n2"}]}

    def test_hit_only_file_nodes(self):
        out, n = rewrite_doc_files(self.PAYLOAD, "旧目录", "新目录")
        self.assertEqual(n, 1)
        self.assertEqual(out["nodes"][0]["file"], "新目录/甲.md")
        self.assertEqual(out["nodes"][1]["file"], "别的/乙.md")       # 不命中不动
        self.assertEqual(self.PAYLOAD["nodes"][0]["file"], "旧目录/甲.md")  # 入参不污染
        self.assertEqual(out["edges"], self.PAYLOAD["edges"])

    def test_no_hit_returns_same(self):
        out, n = rewrite_doc_files(self.PAYLOAD, "不存在", "x")
        self.assertEqual((out, n), (self.PAYLOAD, 0))

    def test_move_to_deeper(self):
        out, n = rewrite_doc_files(self.PAYLOAD, "旧目录", "a/旧目录")
        self.assertEqual((n, out["nodes"][0]["file"]),
                         (1, "a/旧目录/甲.md"))


if __name__ == "__main__":
    unittest.main()
