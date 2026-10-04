"""知识检索（阶段2）：FTS5/BM25 检索 + 相关推荐。

优先走 notes_fts（jieba 预分词写入 + bm25 排序，万篇规模可用）；
fts_ok=False 或异常时回退旧全表扫路径（_search_legacy）。
接口层继续预留语义向量替换位。
"""
from __future__ import annotations

from core.graph.engine import GraphEngine, _title_of
from core.storage.fts import tokenize


class KnowledgeSearch:
    def __init__(self, store, vault=None):
        self.store = store
        self.vault = vault
        self.graph = GraphEngine(store, vault)

    def _tokens(self, text: str) -> list[str]:
        return tokenize(text)

    def search(self, query: str, top_n: int = 10) -> list[dict]:
        """返回 [{path, title, topic, score, backlinks, neighbors}] 按分降序。"""
        toks = tokenize(query)
        if not toks:
            return []
        if self.store.fts_ok:
            try:
                rows = self.store.fts_search(toks, top_n * 3)
                return self._assemble(rows, top_n)
            except Exception:
                pass        # FTS 异常（索引损坏/词表漂移）→ 降级旧路径
        return self._search_legacy(query, top_n)

    def _assemble(self, fts_rows: list[dict], top_n: int) -> list[dict]:
        """把 FTS 命中行组装成与旧接口一致的返回结构（keys 不变是 GUI 红线）。"""
        g = self.graph.build()               # epoch 缓存后一次构建
        idx = {n["id"]: i for i, n in enumerate(g["nodes"])}
        out = []
        for r in fts_rows[:top_n]:
            f = self.store.get_file(r["path"])
            if f is None:                    # 幽灵行防御
                continue
            out.append({
                "path": r["path"],
                "title": f["title"],
                "topic": f["topic"],
                "score": r["score"],
                "backlinks": self.backlinks(r["path"]),
                "neighbors": self._neighbors_of(idx.get(r["path"]), g),
            })
        return out

    @staticmethod
    def _neighbors_of(i, g: dict) -> list[str]:
        if i is None:
            return []
        out = []
        for e in g["edges"]:
            if e["source"] == i:
                out.append(g["nodes"][e["target"]]["id"])
            elif e["target"] == i:
                out.append(g["nodes"][e["source"]]["id"])
        return list(dict.fromkeys(out))

    def _search_legacy(self, query: str, top_n: int = 10) -> list[dict]:
        """旧路径：全表扫 + 关键词计数（仅 fts_ok=False/异常时使用）。"""
        toks = tokenize(query)
        if not toks:
            return []
        results = []
        for f in self.store.all_files():
            kw = [a["value"] for a in self.store.atoms_for(f["path"]) if a["kind"] == "keyword"]
            title_toks = self._tokens(f["title"]) + [t for t in self._tokens(_title_of(f["path"]))]
            corpus = (f["title"] + " " + " ".join(kw)) * 3   # 标题/关键词加权
            for t in toks:
                if t in f["title"]:
                    corpus += (" " + f["title"] * 5)
            score = sum(corpus.count(t) for t in toks)
            if score <= 0:
                continue
            results.append({
                "path": f["path"],
                "title": f["title"],
                "topic": f["topic"],
                "score": score,
                "backlinks": self.backlinks(f["path"]),
                "neighbors": self.graph.neighbors(f["path"]),
            })
        results.sort(key=lambda r: -r["score"])
        return results[:top_n]

    def backlinks(self, rel_path: str) -> list[str]:
        """指入该文件的双链来源。to_target 可能存文件名 stem 或笔记标题，两者都查。"""
        row = self.store.get_file(rel_path)
        targets = {_title_of(rel_path)}
        if row and row["title"]:
            targets.add(row["title"])
        out = set()
        for t in targets:
            for src in self.store.inlinks(t):
                if src != rel_path:
                    out.add(src)
        return sorted(out)

    def related(self, rel_path: str, top_n: int = 5) -> list[dict]:
        """相关推荐：直接邻居优先，其次共享关键词。"""
        g = self.graph.build()
        idx = next((i for i, n in enumerate(g["nodes"]) if n["id"] == rel_path), None)
        if idx is None:
            return []
        neigh = [g["nodes"][i] for i in set(
            (e["target"] if e["source"] == idx else e["source"] if e["target"] == idx else None)
            for e in g["edges"]) if i is not None]
        # 归一化去重 by id
        seen, ranked = set(), []
        for n in neigh:
            if n["id"] not in seen:
                seen.add(n["id"]); ranked.append(n)
        return ranked[:top_n]