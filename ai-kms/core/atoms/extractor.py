"""原子抽取：把一篇内容拆成可复用的原子片段。

"一次解析，原子沉淀" —— 入库时抽取 keyword/claim/entity/tag，落库缓存；
后续判断(去重/补链/检索/过时)只消费这些原子，不再重读原文。
"""
from __future__ import annotations

import jieba
from core.storage.vault import Vault

# 编辑时剔除的噪音（停用词、结构词）
_STOP_WORDS = {
    "的", "了", "是", "在", "和", "与", "及", "也", "就", "都", "而", "被", "把",
    "我", "你", "他", "她", "它", "我们", "你们", "一个", "这个", "那个", "就是",
    "可以", "进行", "以及", "并且", "或者", "因此", "因为", "所以", "但是",
}


class AtomExtractor:
    def __init__(self, min_len: int = 2):
        self.min_len = min_len

    def extract_topics(self, text: str, top_n: int = 8) -> list[str]:
        """基于词频提关键词，作为轻量主题原子。"""
        import re
        text = re.sub(r"[#`*_>\[\]()（）]|\[\[.*?\]\]", " ", text)
        words = jieba.lcut(text)
        counts: dict[str, int] = {}
        for w in words:
            w = w.strip()
            if len(w) < self.min_len or w in _STOP_WORDS or not w.isalnum():
                continue
            counts[w] = counts.get(w, 0) + 1
        ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [w for w, _ in ranked]

    def extract_claims(self, text: str, max_n: int = 6) -> list[str]:
        """粗提取"命题"式原子：以句号/分号/问号切分出的非空短句。"""
        import re
        parts = re.split(r"[。；;！？\n]", text)
        out = []
        for p in parts:
            p = p.strip()
            p = re.sub(r"^#{1,6}\s*", "", p)        # 去标题 #
            p = p.strip("-•* ")
            if len(p) >= 8 and len(p) <= 120:
                out.append(p)
            if len(out) >= max_n:
                break
        return out

    def extract_tags(self, text: str) -> list[str]:
        return Vault.extract_tags(text) or self.extract_topics(text, 3)

    def extract_all(self, text: str) -> list[dict]:
        """输出统一原子列表 [{kind, value, weight}]。"""
        atoms: list[dict] = []
        for v in self.extract_topics(text, 8):
            atoms.append({"kind": "keyword", "value": v, "weight": 1.0})
        for v in self.extract_claims(text, 6):
            atoms.append({"kind": "claim", "value": v, "weight": 0.8})
        for v in self.extract_tags(text):
            atoms.append({"kind": "tag", "value": v, "weight": 1.2})
        return atoms