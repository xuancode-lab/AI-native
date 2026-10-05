"""结构层导出 / 全库打包：行数口径、JSON 解析容错、graph 边转 path、zip 布局与内容一致性。"""
from __future__ import annotations

import json
import unittest
import zipfile

from tests.conftest import TempEnv

from core.export import EXPORT_FORMAT_VERSION, export_full, export_structure


def _read_jsonl(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line]


class TestExport(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        s = self.store = self.env.store
        v = self.vault = self.env.vault
        v.write_note("技术/图谱.md", "# 图谱\n知识图谱由节点和边构成。#图算法")
        v.write_note("技术/检索.md", "# 检索\nFTS5 倒排索引很快。")
        v.write_note("读书/笔记.md", "# 笔记\n复利思想。")
        s.upsert_file("技术/图谱.md", "图谱", "技术", dedup_key="d1")
        s.upsert_file("技术/检索.md", "检索", "技术", dedup_key="d2")
        s.upsert_file("读书/笔记.md", "笔记", "读书", dedup_key="d3")
        s.add_atoms("技术/图谱.md", [
            {"kind": "keyword", "value": "节点"},
            {"kind": "keyword", "value": "图谱"},
            {"kind": "claim", "value": "知识图谱由节点和边构成"},
            {"kind": "tag", "value": "图算法"},
            {"kind": "topic", "value": "技术"},
        ])
        s.add_atoms("技术/检索.md", [{"kind": "keyword", "value": "倒排"}])
        s.add_link("技术/图谱.md", "检索")
        s.add_link("技术/图谱.md", "不存在的幻影目标")   # 幻影链原样导出
        s.save_classification("技术/图谱.md", {"topic": "旧主题", "verdict": {"r": 1}})
        s.save_classification("技术/图谱.md", {"topic": "技术", "verdict": {"r": 2}})
        s.add_category("技术", ["图谱", "检索"])
        s.add_suggestions([
            {"kind": "wikilink", "file_path": "技术/检索.md", "target": "图谱",
             "payload": {"shared": ["倒排"]}, "confidence": 0.9},
            {"kind": "topic", "file_path": "读书/笔记.md", "target": "技术",
             "payload": {}, "confidence": 0.6},
        ])
        # 注入坏 payload（模拟历史脏数据）：导出应保 raw 不抛
        s.conn.execute("UPDATE suggestions SET payload='{坏json' WHERE confidence=0.6")
        s.conn.commit()

    def tearDown(self):
        self.env.teardown()

    def _n(self, sql):
        return self.store.conn.execute(sql).fetchone()[0]

    # ---- 结构导出 ----
    def test_structure_files_and_counts(self):
        out = self.env.db_path.parent / "ex"
        manifest = export_structure(self.store, self.vault, out)
        sdir = out / "structure"
        self.assertEqual(len(_read_jsonl(sdir / "files.jsonl")), 3)
        self.assertEqual(len(_read_jsonl(sdir / "atoms.jsonl")), self._n("SELECT COUNT(*) FROM atoms"))
        self.assertEqual(len(_read_jsonl(sdir / "links.jsonl")), self._n("SELECT COUNT(*) FROM links"))
        self.assertEqual(len(_read_jsonl(sdir / "suggestions.jsonl")), 2)
        self.assertEqual(manifest["counts"]["files"], 3)
        self.assertEqual(manifest["export_format"], EXPORT_FORMAT_VERSION)
        self.assertFalse(manifest["includes_vault_md"])
        self.assertFalse((out / "vault").exists())   # structure 模式不含原文

    def test_atoms_no_id_and_sorted(self):
        rows = _read_jsonl(self._export_once() / "structure" / "atoms.jsonl")
        self.assertEqual(set(rows[0]), {"file_path", "kind", "value", "weight"})
        keys = [(r["file_path"], r["kind"], r["value"]) for r in rows]
        self.assertEqual(keys, sorted(keys))          # 确定性排序

    def test_classifications_latest_only_with_parsed_verdict(self):
        rows = _read_jsonl(self._export_once() / "structure" / "classifications.jsonl")
        self.assertEqual(len(rows), self._n(
            "SELECT COUNT(DISTINCT file_path) FROM classifications"))  # 每文件仅最新
        mine = [r for r in rows if r["file_path"] == "技术/图谱.md"]
        self.assertEqual(len(mine), 1)                # 同文件两条 → 只导最新一条
        self.assertEqual(mine[0]["topic"], "技术")
        self.assertEqual(mine[0]["verdict"], {"r": 2})  # JSON 已解析

    def test_bad_payload_kept_raw(self):
        rows = _read_jsonl(self._export_once() / "structure" / "suggestions.jsonl")
        bad = [r for r in rows if r["payload"] == {"payload_raw": "{坏json"}]
        self.assertEqual(len(bad), 1)
        good = [r for r in rows if r["kind"] == "wikilink"]
        self.assertEqual(good[0]["payload"], {"shared": ["倒排"]})

    def test_graph_edges_are_paths_not_indices(self):
        g = json.loads((self._export_once() / "structure" / "graph.json")
                       .read_text(encoding="utf-8"))
        self.assertTrue(all(isinstance(e["source"], str) and isinstance(e["target"], str)
                            for e in g["edges"]))
        self.assertIn("读书/笔记.md", {n["id"] for n in g["nodes"]})
        self.assertEqual(g["params"], {"min_shared_keywords": 2})

    def test_export_deterministic(self):
        a = (self._export_once(1) / "structure" / "atoms.jsonl").read_text(encoding="utf-8")
        b = (self._export_once(2) / "structure" / "atoms.jsonl").read_text(encoding="utf-8")
        self.assertEqual(a, b)                        # 同库两次导出逐字一致

    def test_manifest_counts_match_db(self):
        manifest = export_structure(self.store, self.vault, self.env.db_path.parent / "ex")
        db = self.store.table_counts()
        for k in db:   # manifest 在 table_counts 之上另有 graph_* 两键
            self.assertEqual(manifest["counts"][k], db[k], k)

    # ---- 全库 zip ----
    def test_full_zip_layout_and_content(self):
        zp = export_full(self.store, self.vault, self.env.db_path.parent / "exz")
        self.assertTrue(zp.exists() and zp.suffix == ".zip")
        with zipfile.ZipFile(zp) as zf:
            names = zf.namelist()
            self.assertIn("manifest.json", names)
            self.assertIn("structure/atoms.jsonl", names)
            self.assertIn("vault/技术/图谱.md", names)          # 中文 arcname 可用
            self.assertEqual(zf.read("vault/技术/图谱.md").decode("utf-8"),
                             self.vault.read_note("技术/图谱.md"))
            m = json.loads(zf.read("manifest.json").decode("utf-8"))
            self.assertEqual(m["mode"], "full")
            self.assertTrue(m["includes_vault_md"])
            self.assertEqual(m["files"].count("vault/技术/图谱.md"), 1)

    def _export_once(self, tag=0):
        out = self.env.db_path.parent / f"exp{tag}"
        export_structure(self.store, self.vault, out)
        return out


if __name__ == "__main__":
    unittest.main()
