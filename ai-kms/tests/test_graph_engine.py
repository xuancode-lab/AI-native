"""GraphEngine 倒排建边：与旧 O(N²) 算法等价 / hub 桶封顶 / epoch 缓存。"""
from __future__ import annotations

import unittest

from tests.conftest import TempEnv

from core.graph.engine import GraphEngine


class TestGraphEngine(unittest.TestCase):
    def setUp(self):
        self.env = TempEnv()
        self.store = self.env.store
        self.engine = GraphEngine(self.store, self.env.vault)

    def tearDown(self):
        self.env.teardown()

    def _add(self, path, kws, usage=0):
        self.store.upsert_file(path, path.split("/")[-1][:-3], "技术")
        self.store.add_atoms(path, [{"kind": "keyword", "value": k} for k in kws])
        if usage:
            for _ in range(usage):
                self.store.conn.execute(
                    "UPDATE vault_files SET usage_count=usage_count+1 WHERE path=?",
                    (path,))
                self.store.conn.commit()

    def test_semantic_edges_equivalent_to_old(self):
        # A/B 共享 2 词；A/C 仅共享 1 词 → 只有 A-B 该有语义边
        self._add("技术/a.md", ["图谱", "节点", "alpha"])
        self._add("技术/b.md", ["图谱", "节点", "beta"])
        self._add("技术/c.md", ["图谱", "gamma"])
        g = self.engine.build()
        sem = {frozenset((g["nodes"][e["source"]]["id"], g["nodes"][e["target"]]["id"]))
               for e in g["edges"] if e["kind"] == "semantic"}
        self.assertEqual(sem, {frozenset({"技术/a.md", "技术/b.md"})})
        self.assertEqual(g["isolated"], ["技术/c.md"])

    def test_hub_bucket_cap(self):
        # 100 篇全共享 2 个 hub 词 → 旧算法 C(100,2)=4950 条边；封顶后应大幅收敛
        for i in range(100):
            self._add(f"技术/h{i}.md", ["hub1", "hub2", f"独有词{i}"])
        g = self.engine.build()
        sem = [e for e in g["edges"] if e["kind"] == "semantic"]
        self.assertGreater(len(sem), 1000)          # 边仍然建出来了
        self.assertLess(len(sem), 2000)             # 但被桶上限截断（< 4950）

    def test_epoch_cache(self):
        self._add("技术/a.md", ["x词", "y词"])
        g1 = self.engine.build()
        g2 = self.engine.build()
        self.assertIs(g1, g2)                        # 缓存命中，同一对象
        self._add("技术/b.md", ["x词", "y词"])        # 写路径 bump epoch
        g3 = self.engine.build()
        self.assertIsNot(g1, g3)
        self.assertEqual(len(g3["nodes"]), 2)

    def test_neighbors_shape(self):
        self._add("技术/a.md", ["共1", "共2"])
        self._add("技术/b.md", ["共1", "共2"])
        nb = self.engine.neighbors("技术/a.md")
        self.assertEqual(nb, ["技术/b.md"])


if __name__ == "__main__":
    unittest.main()
