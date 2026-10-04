"""生成层多模型 Provider。

统一接口 `generate(system, user, **opts) -> str`。
按优先级自动降级：Claude → OpenAI 兼容(任意网关) → Ollama(本地) → Mock(离线规则)，
保证无网/无密钥也能跑通界面与流程。
"""
from __future__ import annotations

import json
import logging
import time

from config import settings

_log = logging.getLogger("kms.provider")


class ProviderError(Exception):
    pass


class BaseProvider:
    """生成层抽象接口。"""
    name: str = "base"

    def available(self) -> bool:
        return True

    def generate(self, system: str, user: str, temperature: float = 0.7,
                 max_tokens: int = 1024) -> str:
        raise NotImplementedError


class ClaudeProvider(BaseProvider):
    name = "claude"

    def __init__(self):
        cfg = settings.GENERATIVE["providers"]["claude"]
        self.api_key = cfg.get("api_key") or ""
        self.model = cfg.get("model") or "claude-3-5-sonnet-20240620"
        self.api_url = cfg.get("api_url") or "https://api.anthropic.com/v1/messages"

    def available(self) -> bool:
        return bool(self.api_key)

    def generate(self, system, user, temperature=0.7, max_tokens=1024) -> str:
        import requests
        headers = {
            "x-api-key": self.api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        res = requests.post(self.api_url, headers=headers, json=payload, timeout=30)
        if res.status_code != 200:
            raise ProviderError(f"Claude API {res.status_code}: {res.text[:200]}")
        return res.json()["content"][0]["text"]


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self):
        cfg = settings.GENERATIVE["providers"].get("ollama", {})
        self.base_url = cfg.get("base_url") or "http://localhost:11434"
        self.model = cfg.get("model") or ""

    def available(self) -> bool:
        return bool(self.model)

    def generate(self, system, user, temperature=0.7, max_tokens=1024) -> str:
        import requests
        url = f"{self.base_url}/api/generate"
        res = requests.post(url, json={
            "model": self.model,
            "prompt": f"{system}\n\n{user}" if system else user,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }, timeout=45)
        if res.status_code != 200:
            raise ProviderError(f"Ollama {res.status_code}: {res.text[:200]}")
        return res.json().get("response", "").strip()


class OpenAIProvider(BaseProvider):
    """OpenAI 兼容接口（/chat/completions 标准协议）。

    一套代码通吃所有兼容网关：OpenAI 官方、DeepSeek、Kimi、智谱、
    vLLM / LM Studio 本地服务、one-api / new-api 聚合网关——甚至
    Ollama 的 /v1 兼容端点。base_url 留空走官方；本地服务无密钥时
    key 可空，启用条件是 model。
    """
    name = "openai"
    DEFAULT_BASE = "https://api.openai.com/v1"

    def __init__(self):
        cfg = settings.GENERATIVE["providers"].get("openai", {})
        self.api_key = cfg.get("api_key") or ""
        self.base_url = (cfg.get("base_url") or self.DEFAULT_BASE).rstrip("/")
        self.model = cfg.get("model") or ""

    def available(self) -> bool:
        return bool(self.model)

    def generate(self, system, user, temperature=0.7, max_tokens=1024) -> str:
        import requests
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        res = requests.post(f"{self.base_url}/chat/completions", headers=headers,
                            json={"model": self.model, "messages": messages,
                                  "temperature": temperature,
                                  "max_tokens": max_tokens}, timeout=60)
        if res.status_code != 200:
            raise ProviderError(f"OpenAI-compat {res.status_code}: {res.text[:200]}")
        data = res.json()
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            raise ProviderError(f"OpenAI-compat 响应结构异常: {str(data)[:200]}")


class MockProvider(BaseProvider):
    """离线规则式兜底：保证无网/无密钥时界面与流程仍可演示。

    行为：
      - 问答：从检索到的上下文里抽取匹配片段作答
      - 润色：保留原内容，加一段"【AI建议】"的提示
      - 矛盾：返回空列表（未检测到）
      - 摘要：取前两句作为摘要
    """
    name = "mock"

    def __init__(self):
        self._atoms_index = None

    def attach_atoms_index(self, idx) -> None:
        """让 MockProvider 能基于真实原子做离线检索，提升演示效果。"""
        self._atoms_index = idx

    def generate(self, system, user, temperature=0.7, max_tokens=1024) -> str:
        sys_lower = system.lower()
        if "问答" in sys_lower or "question" in sys_lower:
            return self._qa(user)
        if "润色" in sys_lower or "polish" in sys_lower or "rewrite" in sys_lower:
            return self._polish(user)
        if "矛盾" in sys_lower or "contradict" in sys_lower:
            return self._contradict(user)
        if "摘要" in sys_lower or "summarize" in sys_lower:
            return self._summarize(user)
        return f"[Mock] {user[:200]}"

    def _qa(self, user: str) -> str:
        import re
        toks = [t for t in re.split(r"\W+", user) if len(t) >= 2]
        if not toks or not self._atoms_index:
            return "[Mock] 未配置生成模型，暂无法在线作答。请连接 Anthropic API 或本地 Ollama。"
        hits = []
        for t in toks:
            for a in self._atoms_index.search_atoms(t):
                hits.append(f"{a['kind']}@{a['file_path']}: {a['value']}")
        hits = list(dict.fromkeys(hits))[:8]
        if not hits:
            return f"[Mock] 在知识库中未检索到与'{user}'相关的片段。"
        return "[Mock 离线回答] 以下片段与问题相关：\n" + "\n".join(f"- {h}" for h in hits)

    def _polish(self, user: str) -> str:
        return user + "\n\n【AI建议】（Mock 模式）：请接入真实 LLM 后获得深度润色。"

    def _contradict(self, user: str) -> str:
        return "【Mock】未检测到知识矛盾。接入真实 LLM 后会对两段笔记做对比推理。"

    def _summarize(self, user: str) -> str:
        lines = user.strip().splitlines()
        if not lines:
            return "（空）"
        return "摘要：" + " ".join(lines[:2])


def get_provider():
    """按 settings.GENERATIVE["mode"] 选择 Provider：

    auto   → Claude → OpenAI 兼容 → Ollama → Mock 依序探测
    claude / openai / ollama → 指定后端，不可用则记日志并降级 Mock
    mock   → 强制离线
    每次调用现取——GUI 设置面板保存后即时生效，无需重启。
    """
    mode = settings.GENERATIVE.get("mode", "auto")
    if mode == "mock":
        return MockProvider()
    chain = {"claude": [ClaudeProvider],
             "openai": [OpenAIProvider],
             "ollama": [OllamaProvider]}.get(
        mode) or [ClaudeProvider, OpenAIProvider, OllamaProvider]
    for factory in chain:
        try:
            p = factory()
            if p.available():
                _log.info("生成层 provider=%s (mode=%s)", p.name, mode)
                return p
            _log.info("provider %s 不可用（mode=%s），继续降级", factory.name, mode)
        except Exception as e:
            _log.warning("provider %s 构造失败: %s", factory.__name__, e)
    return MockProvider()