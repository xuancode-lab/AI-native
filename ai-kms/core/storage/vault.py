"""原生 Markdown Vault：挂载、扫描、双链解析、标题/指纹提取。

对应 Obsidian 思想但自研实现：本地 .md 原生存储、[[wikilink]] 双向链接、标签。
"""
from __future__ import annotations

import re
from pathlib import Path

from config import settings

WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+?)(?:[#|][^\[\]]*)?\]\]")
TAG_RE = re.compile(r"(?<![\w])#([一-龥\w-]+)")


def slugify(text: str) -> str:
    """把标题/文件名转成安全文件名。"""
    text = re.sub(r"[\\/:*?\"<>|]", "-", text.strip())
    return text


class Vault:
    def __init__(self, vault_path: Path | None = None):
        self.vault_path = Path(vault_path) if vault_path else settings.DEFAULT_VAULT_PATH
        self.vault_path.mkdir(parents=True, exist_ok=True)

    # ---- 读写 ----
    def write_note(self, rel_path: str, content: str) -> Path:
        full = self.vault_path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return full

    def read_note(self, rel_path: str) -> str:
        full = self.vault_path / rel_path
        return full.read_text(encoding="utf-8") if full.exists() else ""

    def all_markdown_files(self):
        return [p.relative_to(self.vault_path).as_posix()
                for p in self.vault_path.rglob("*.md") if p.is_file()]

    def rel_path_for(self, full_path) -> str:
        full = Path(full_path).resolve()
        return full.relative_to(self.vault_path.resolve()).as_posix()

    # ---- 解析 ----
    @staticmethod
    def extract_title(content: str) -> str:
        m = re.search(r"^#\s+(.+)$", content, re.M)
        return m.group(1).strip() if m else "untitled"

    @staticmethod
    def extract_wikilinks(content: str) -> list[str]:
        return list(dict.fromkeys(m.group(1).strip() for m in WIKILINK_RE.finditer(content)))

    @staticmethod
    def extract_tags(content: str) -> list[str]:
        return list(dict.fromkeys(t for t in TAG_RE.findall(content)))

    @staticmethod
    def make_dedup_key(content: str) -> str:
        """内容指纹：压缩空白后取前 200 字符的稳定哈希，用于去重判断。"""
        norm = re.sub(r"\s+", "", content)
        norm = norm[:200]
        # 内置稳定 FNV-1a，避免引入 hashlib 输出平台差异（其实 hashlib 已稳定，这里仅示范）
        import hashlib
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def ensure_node_path(topic: str) -> str:
    """按主题归入目录，最先做简单两级：区域/主题/标题。"""
    t = slugify(topic)
    return f"{t}"


if __name__ == "__main__":
    v = Vault()
    print("vault:", v.vault_path)
    print("现有 .md:", v.all_markdown_files())