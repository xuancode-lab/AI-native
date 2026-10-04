"""原子索引：判断层的只查缓存查询入口。

供去重、孤立点检测、补链、BM25 检索复用，全部不触发重读原文。
"""
from __future__ import annotations

from core.storage.sqlite_db import SQLiteStore


class AtomIndex:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def keywords_of(self, rel_path: str) -> list[str]:
        return [r["value"] for r in self.store.atoms_for(rel_path) if r["kind"] == "keyword"]

    def search_atoms(self, value_like: str):
        """委托 store（MockProvider 的离线检索依赖此入口，此前是断链）。"""
        return self.store.search_atoms(value_like)

    def topic_of(self, rel_path: str) -> str | None:
        row = self.store.latest_classification(rel_path)
        return row["topic"] if row else None

    def recommend_links(self, keywords: list[str], exclude_path: str, top_n: int = 5) -> list[str]:
        """基于关键词原子的精确倒排匹配（走 idx_atoms_value），按共享数聚排。

        修复：原 LIKE '%kw%' 会把子串误伤当关联（"学习"命中"学习方法论"原子）；
        原子 value 本身就是离散关键词，精确等值匹配才是正确的倒排语义。
        """
        if not keywords:
            return []
        hits: dict[str, int] = {}
        for kw in keywords:
            rows = self.store.conn.execute(
                "SELECT file_path FROM atoms WHERE kind='keyword' AND value=?",
                (str(kw),)).fetchall()
            for r in rows:
                if r["file_path"] == exclude_path:
                    continue
                hits[r["file_path"]] = hits.get(r["file_path"], 0) + 1
        ranked = sorted(hits.items(), key=lambda x: -x[1])
        return [p for p, _ in ranked[:top_n]]

    def isolated_files(self, all_files: list[str], linked_targets: set[str]) -> list[str]:
        """孤立点检测：没有任何入链/出链的文件。"""
        return [f for f in all_files
                if not self.store.links_for(f)
                and VaultTitle(f) not in linked_targets]

    def bm25_score(self, text: str, query_tokens: list[str]) -> float:
        """简易 BM25 打分（无库 BM25，供关键词检索排级）。"""
        toks = text.split()
        if not toks:
            return 0.0
        return sum(toks.count(t) for t in query_tokens) / len(toks)


def VaultTitle(rel_path: str) -> str:
    """从 rel_path 取标题（不带后缀文件名）。"""
    import os
    return os.path.splitext(os.path.basename(rel_path))[0]