"""FTS 分词：索引侧与查询侧共用的唯一事实源。

notes_fts 表存的是这里 tokenize() 后空格连接的文本（unicode61 按空格切），
查询同样先 tokenize 再拼 MATCH——两侧词表必须一致，否则升级 jieba 后查不中。
"""
from __future__ import annotations

import re
import jieba

_TOKEN_RE = re.compile(r"[^一-龥a-zA-Z0-9]")


def tokenize(text: str) -> list[str]:
    """去符号 → jieba 分词 → 剔除单字噪音。与 KnowledgeSearch._tokens 逐字符一致。"""
    text = _TOKEN_RE.sub(" ", text or "")
    return [t for t in jieba.lcut(text) if len(t) >= 2]


def join_tokens(tokens: list[str]) -> str:
    return " ".join(tokens)
