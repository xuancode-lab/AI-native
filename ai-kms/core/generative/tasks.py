"""生成层任务：摘要 / 润色 / 问答(RAG) / 矛盾检测。

所有任务只消费缓存的原子与标题，不重读整篇原文，符合判断层"一次解析终身复用"原则。
"""
from __future__ import annotations

import time
from typing import Callable

from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault
from core.atoms.index import AtomIndex
from core.graph.search import KnowledgeSearch
from core.generative.provider import get_provider


class AITasks:
    def __init__(self, store: SQLiteStore, vault: Vault | None = None,
                 on_progress: Callable[[str], None] | None = None):
        self.store = store
        self.vault = vault or Vault()
        self.idx = AtomIndex(store)
        self.search = KnowledgeSearch(store, self.vault)
        self._on_progress = on_progress or (lambda _: None)

    def _call(self, system: str, user: str, **opts) -> str:
        provider = get_provider()
        self._on_progress(f"〔AI〕调用 provider={provider.name}")
        start = time.time()
        try:
            out = provider.generate(system, user, **opts)
            elapsed = int((time.time() - start) * 1000)
            self._on_progress(f"〔AI〕返回 {len(out)} 字符 · {elapsed}ms · {provider.name}")
            return out
        except Exception as e:
            self._on_progress(f"〔AI〕失败: {e}")
            raise

    # ---------- 任务 ----------
    def summarize(self, rel_path: str) -> str:
        """对一篇笔记做摘要（用已缓存的原子，不重读全文）。"""
        atoms = [f"[{a['kind']}] {a['value']}" for a in self.store.atoms_for(rel_path)]
        row = self.store.get_file(rel_path)
        title = row["title"] if row else rel_path
        ctx = f"标题：{title}\n原子片段：\n" + "\n".join(atoms[:20])
        return self._call(
            "你是知识管理助手，用 3 句话概括下面的知识片段，保留核心观点与事实，不虚构。",
            ctx,
        )

    def polish(self, rel_path: str) -> str:
        """润色/重构一篇笔记，返回建议稿（不自动覆盖，由调用方决定是否写回）。"""
        content = self.vault.read_note(rel_path)
        return self._call(
            "你是专业编辑，请对下面的 Markdown 笔记做润色：保留事实与结构，修复语病、统一用词、"
            "让逻辑更清晰。返回润色后的完整 Markdown，不要加解释。",
            content,
        )

    def qa(self, question: str, top_n: int = 6,
           extra_paths: list[str] | None = None) -> str:
        """RAG 问答：检索相关原子 → 喂 LLM 作答，严格基于检索上下文。

        extra_paths: 用户挂载的笔记——其原子直接置顶入上下文（"【挂载笔记】"标记）。
        """
        ctx_chunks = []
        used = set()
        for p in extra_paths or []:
            f = self.store.get_file(p)
            if not f:
                continue
            atoms = [a["value"] for a in self.store.atoms_for(p)
                     if a["kind"] in ("keyword", "claim", "topic")]
            if atoms:
                ctx_chunks.append(f"## 【挂载笔记】{f['title']}\n"
                                  + "；".join(atoms[:8]))
                used.add(p)
        hits = self.search.search(question, top_n=top_n + len(used))
        if not hits and not ctx_chunks:
            return f"知识库中未找到与'{question}'相关的笔记。"
        for h in hits:
            if h["path"] in used:
                continue
            atoms = [a["value"] for a in self.store.atoms_for(h["path"])
                     if a["kind"] in ("keyword", "claim", "topic")]
            ctx_chunks.append(f"## {h['title']}\n" + "；".join(atoms[:6]))
        ctx = "\n\n---\n".join(ctx_chunks)
        return self._call(
            "你是个人知识库助手。严格根据下面的检索上下文回答用户问题；"
            "如果上下文不足以回答，就说'根据当前知识库无法回答'，不要编造。"
            "引用时带上笔记标题。",
            f"上下文：\n{ctx}\n\n问题：{question}",
        )

    def contradict(self, rel_path: str) -> list[dict]:
        """矛盾检测：把给定笔记与语义相关笔记两两对比，让 LLM 找出冲突。"""
        related = self.search.related(rel_path, top_n=4)
        if not related:
            return []
        target = self.vault.read_note(rel_path)[:2500]
        issues = []
        for r in related:
            other = self.vault.read_note(r["id"])[:2500]
            if not other:
                continue
            out = self._call(
                "你是知识审核员。对比两段知识，判断是否有事实、观点或逻辑上的矛盾。"
                "输出 JSON 数组，每项含 issue(一句话描述矛盾)、severity(high|medium|low)、suggestion(修正建议)。"
                "若无矛盾输出 []。只输出 JSON，不要其他文字。",
                f"笔记A({r['title']}):\n{other}\n\n笔记B:\n{target}",
            )
            parsed = self._parse_issues(out)
            for p in parsed:
                p["against"] = r["title"]
            issues.extend(parsed)
        return issues

    @staticmethod
    def _parse_issues(text: str) -> list[dict]:
        import json, re
        text = text.strip()
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []