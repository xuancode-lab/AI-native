"""素材清洗：去噪音、去冗余、规整 Markdown。"""
from __future__ import annotations

import re


class Cleaner:
    def clean(self, content: str) -> str:
        content = self._strip_junk(content)
        content = self._normalize_md(content)
        content = self._dedupe_blank(content)
        return content.strip()

    @staticmethod
    def _strip_junk(content: str) -> str:
        # 去掉内联 HTML、脚本、样式
        content = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", content)
        content = re.sub(r"(?is)<[^>]+>", "", content)
        # 去掉可能从网页粘贴来的导航/版权行
        content = re.sub(r"\A[\s\-_=]*\n", "", content)
        return content

    @staticmethod
    def _normalize_md(content: str) -> str:
        # 统一换行符
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        # 行内超多空格=开始缩进危险，折叠连续空格
        content = re.sub(r"[ \t]{2,}", " ", content)
        return content

    @staticmethod
    def _dedupe_blank(content: str) -> str:
        content = re.sub(r"\n{3,}", "\n\n", content)
        return content

    def extract_title_hint(self, content: str) -> str:
        """从未有 # 标题的正文里猜个标题（取首句）。"""
        m = re.search(r"^#\s+(.+)$", content, re.M)
        if m:
            return m.group(1).strip().rstrip("。#")
        for line in content.splitlines():
            line = line.strip().lstrip("#-• ")
            if line and len(line) >= 2:
                return line[:40].rstrip("。#")
        return "untitled"