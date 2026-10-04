"""知识图谱引擎（GBrain 思想，自研）。

从 vault_files + atoms + links 建图：
  - 节点 = 每篇笔记；属性：title/topic/type/usage_weight/isolated
  - 边 = 两类：
      * link    ：[[wikilink]] 解析出的双向链接（resolved 到已有标题）
      * semantic：两篇共享 >=2 个非停用关键词原子 → 语义关联（GBrain 自动补链）
  - isolated：没有任何入/出边 → 孤立点
 输出 JSON 友好的 dict，直接供 QGraphicsView 渲染。
"""
from __future__ import annotations

import os
from collections import Counter
from itertools import combinations
from weakref import WeakKeyDictionary

from core.storage.vault import Vault


def _title_of(rel_path: str) -> str:
    return os.path.splitext(os.path.basename(rel_path))[0]


# 按 store 实例缓存 build 结果：{store: {min_shared: (epoch, graph)}}。
# store 写路径自增 epoch → 自动失效；WeakKey 让 store 被回收时缓存同步消失。
# 各调用方（MainWindow/KnowledgeSearch/AITasks）各自 new GraphEngine 也共享。
_GRAPH_CACHE: WeakKeyDictionary = WeakKeyDictionary()
_HUB_BUCKET_CAP = 60      # 单个关键词的建边桶上限（防 hub 词 O(N²) 爆炸）


class GraphEngine:
    def __init__(self, store, vault: Vault | None = None):
        self.store = store
        self.vault = vault or Vault()

    def build(self, min_shared_keywords: int = 2) -> dict:
        epoch = getattr(self.store, "epoch", 0)
        cache = _GRAPH_CACHE.setdefault(self.store, {})
        hit = cache.get(min_shared_keywords)
        if hit and hit[0] == epoch:
            return hit[1]
        graph = self._build_uncached(min_shared_keywords)
        cache[min_shared_keywords] = (epoch, graph)
        return graph

    def _build_uncached(self, min_shared_keywords: int) -> dict:
        files = self.store.all_files()
        if not files:
            return {"nodes": [], "edges": [], "isolated": []}

        # 节点 + 索引（by title, by path）
        nodes = []
        path_index = {}   # rel_path -> node index
        title_index = {}  # title -> node index (注意标题可能重复，取首个)
        for i, f in enumerate(files):
            title = f["title"] or _title_of(f["path"])
            node = {
                "id": f["path"],
                "title": title,
                "topic": f["topic"],
                "type": f["type"],
                "weight": f["usage_count"] or 0,
            }
            path_index[f["path"]] = i
            title_index.setdefault(_title_of(f["path"]), i)
            title_index.setdefault(title, i)
            nodes.append(node)

        edges, edge_key = [], set()

        def _add_edge(a: int, b: int, kind: str):
            key = tuple(sorted((a, b))) + (kind,)
            if a == b or key in edge_key:
                return
            edge_key.add(key)
            edges.append({"source": a, "target": b, "kind": kind})

        # 批量拉取（万篇规模下 3 条 SQL 替代 2N 次查询）
        kw_atoms: dict[str, set] = {}
        for r in self.store.conn.execute(
                "SELECT file_path, value FROM atoms WHERE kind='keyword'"):
            kw_atoms.setdefault(r["file_path"], set()).add(r["value"])
        all_links = self.store.conn.execute(
            "SELECT from_path, to_target FROM links").fetchall()

        # link 边（wikilink → resolved title；未解析目标跳过，避免幻影节点）
        for r in all_links:
            a = path_index.get(r["from_path"])
            b = title_index.get(r["to_target"])
            if a is None or b is None:
                continue
            _add_edge(a, b, "link")

        # semantic 边：关键词倒排索引桶内配对计数（O(Σ桶²封顶) 替代 O(N²)）
        inv: dict[str, list[str]] = {}
        for path, kws in kw_atoms.items():
            for kw in kws:
                inv.setdefault(kw, []).append(path)
        usage = {f["path"]: f["usage_count"] or 0 for f in files}
        pair_count: Counter = Counter()
        for bucket in inv.values():
            if len(bucket) < 2:
                continue
            if len(bucket) > _HUB_BUCKET_CAP:      # hub 词：按记忆权重截断
                bucket = sorted(bucket, key=lambda p: -usage.get(p, 0))[:_HUB_BUCKET_CAP]
            pair_count.update(map(frozenset, combinations(sorted(bucket), 2)))
        for pair, cnt in pair_count.items():
            if cnt >= min_shared_keywords:
                pa, pb = tuple(pair)
                _add_edge(path_index[pa], path_index[pb], "semantic")

        # 孤立点检测
        linked_ids = {n for e in edges for n in (e["source"], e["target"])}
        isolated = [nodes[i]["id"] for i, n in enumerate(nodes) if i not in linked_ids]

        return {"nodes": nodes, "edges": edges, "isolated": isolated}

    def neighbors(self, rel_path: str) -> list[str]:
        """返回某文件在篇中的直接邻居（双向）。"""
        g = self.build()
        idx = next((i for i, n in enumerate(g["nodes"]) if n["id"] == rel_path), None)
        if idx is None:
            return []
        out = []
        for e in g["edges"]:
            if e["source"] == idx:
                out.append(g["nodes"][e["target"]]["id"])
            elif e["target"] == idx:
                out.append(g["nodes"][e["source"]]["id"])
        return out