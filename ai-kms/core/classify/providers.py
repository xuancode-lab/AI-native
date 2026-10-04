"""分类器 Provider 接口：判断层的可插拔后端。

对应 "预留多模型，对分类器判断层做补充" 的决策：
  - 默认 provider = rules（内置规则集，离线、零成本）
  - 预留 api provider（对应 jesui-term/Claude 等外部"只判断"模型），本批先留接口
  - 外部分类器不可用时按配置自动降到规则集（fallback）
"""
from __future__ import annotations

from config import settings


class BaseClassifierProvider:
    name = "base"

    def classify(self, text: str) -> dict:
        raise NotImplementedError


class RulesProvider(BaseClassifierProvider):
    """离线规则分类器。"""
    name = "rules"

    def __init__(self, extra_topics: dict[str, list[str]] | None = None):
        from core.classify.rules import RuleClassifier
        self._impl = RuleClassifier(extra_topics)

    def classify(self, text: str) -> dict:
        return self._impl.classify(text)


class ApiProvider(BaseClassifierProvider):
    """外部"只判断不对话"的分类模型（预留）。

    当 config.CLASSIFY.mode == 'api' 且配好 api_url/api_key 时启用。
    本批仅保留调用骨架，真实实现由后续接入对应的分类模型。
    """
    name = "api"

    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url
        self.api_key = api_key

    def classify(self, text: str) -> dict:
        # TODO(阶段后): 调用外部分类模型，返回 topic/type/priority/verdict
        # 未实现则抛错，由 Router 触发 fallback 到规则集
        raise NotImplementedError("ApiProvider 未配置，请使用 rules 模式")


def get_classifier(extra_topics: dict[str, list[str]] | None = None) -> BaseClassifierProvider:
    """按配置选择判断层后端。extra_topics 透传给规则分类器（用户新增分类）。"""
    mode = settings.CLASSIFY["mode"]
    if mode == "api":
        prov = ApiProvider(settings.CLASSIFY["api_url"], settings.CLASSIFY["api_key"])
        # 仅当已配置密钥且开启时才真正启用 api，否则降级
        if settings.CLASSIFY["api_key"] and settings.CLASSIFY.get("fallback_to_rules", True):
            try:
                return prov
            except Exception:
                pass  # 走 rules
    return RulesProvider(extra_topics)