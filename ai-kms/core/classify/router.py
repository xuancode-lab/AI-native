"""分类路由器：全系统的判断中枢。

核心是 "一次分类，终身复用"：
  - 每个入库文件只分类一次，结果(分类)先落缓存/DB(atom kind='topic')
  - 之后对同一文件的所有判断(dedup、补链、过时检测)只查缓存，不重读原文
  - 新增文件才走 provider；provider 不可用时降级规则集并记录日志
"""
from __future__ import annotations

from core.classify.providers import get_classifier
from core.storage.sqlite_db import SQLiteStore


class ClassificationRouter:
    def __init__(self, store: SQLiteStore):
        self.store = store
        self._provider = None  # 延迟实例化

    @property
    def provider(self):
        if self._provider is None:
            # 加载用户新增分类（categories 表）叠加到内置规则
            self._provider = get_classifier(
                extra_topics=self.store.custom_topic_rules())
        return self._provider

    def refresh_provider(self):
        """分类体系变化（新增/删除分类）后重建 provider。"""
        self._provider = None

    def classify(self, text: str, title: str = "", rel_path: str = "") -> dict:
        """对一份内容做单次判断。

        - 若该文件已有缓存分类，直接返回缓存（不重读、不重算）。
        - 否则调用判断层 provider，缓存后返回。
        """
        cached = None
        if rel_path:
            cached = self.store.latest_classification(rel_path)
        if cached is not None:
            import json
            try:
                verdict = json.loads(cached["verdict"] or "{}")
            except (json.JSONDecodeError, TypeError):
                verdict = {}
            return {
                "topic": cached["topic"],
                "type": cached["type"],
                "priority": cached["priority"],
                "cached": True,
                "verdict": verdict,          # 回填历史判断依据（供校准复查）
            }

        # 修复：原条件写反（有标题反而丢标题），标题必须参与判断
        c = self.provider.classify(f"{title}\n{text}" if title else text)
        c["cached"] = False
        if rel_path:
            self.store.save_classification(rel_path, c)
            # 同时把主题作为原子缓存，供后续判断复用
            self.store.add_atoms(rel_path, [{"kind": "topic", "value": c["topic"], "weight": 1.0}])
        return c

    def cached_topic(self, rel_path: str) -> str | None:
        """只查缓存的主题（不触发分类）——用于补链/去重等轻判断。"""
        row = self.store.latest_classification(rel_path)
        return row["topic"] if row else None