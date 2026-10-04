""".env 改写保行保注释 + get_provider mode 矩阵。不触碰项目真实 .env。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from config import settings
from core.generative import provider as provider_mod
from core.generative.provider import (ClaudeProvider, MockProvider,
                                       OllamaProvider, OpenAIProvider,
                                       get_provider)


class TestUpdateEnv(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.env = self.dir / ".env"
        self.env.write_text(
            "# 密钥配置\nANTHROPIC_API_KEY=sk-old\n\nFOO=bar  # 行内注释\n",
            encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_replace_keep_append(self):
        settings.update_env_values(
            {"ANTHROPIC_API_KEY": "sk-new", "NEW_KEY": "值"}, self.env)
        lines = self.env.read_text(encoding="utf-8").splitlines()
        self.assertIn("# 密钥配置", lines)                    # 注释保留
        self.assertIn("ANTHROPIC_API_KEY=sk-new", lines)      # 原位替换
        self.assertIn("FOO=bar  # 行内注释", lines)           # 无关行原样
        self.assertEqual(lines[-1], "NEW_KEY=值")             # 新键追加，中文值

    def test_create_when_missing(self):
        target = self.dir / "sub" / "n"
        target.mkdir(parents=True)
        settings.update_env_values({"KMS_PROVIDER_MODE": "mock"},
                                   target / ".env")
        self.assertEqual((target / ".env").read_text(encoding="utf-8").strip(),
                         "KMS_PROVIDER_MODE=mock")


class TestProviderMode(unittest.TestCase):
    def setUp(self):
        self._gen = dict(settings.GENERATIVE)
        self._gen["providers"] = {k: dict(v) for k, v in
                                  settings.GENERATIVE["providers"].items()}

    def tearDown(self):
        settings.GENERATIVE.clear()
        settings.GENERATIVE.update(self._gen)

    def _set(self, mode, claude_key="", ollama_model="",
             openai_model="", openai_key="", openai_base=""):
        settings.GENERATIVE["mode"] = mode
        settings.GENERATIVE["disabled"] = False
        settings.GENERATIVE["providers"]["claude"]["api_key"] = claude_key
        settings.GENERATIVE["providers"]["ollama"]["model"] = ollama_model
        oai = settings.GENERATIVE["providers"].setdefault("openai", {})
        oai["model"] = openai_model
        oai["api_key"] = openai_key
        oai["base_url"] = openai_base

    def test_mock_forced(self):
        self._set("mock", claude_key="sk-x", openai_model="gpt-4o-mini")
        self.assertIsInstance(get_provider(), MockProvider)

    def test_claude_selected_when_key(self):
        self._set("claude", claude_key="sk-x")
        self.assertIsInstance(get_provider(), ClaudeProvider)

    def test_claude_mode_no_key_degrades(self):
        self._set("claude")
        self.assertIsInstance(get_provider(), MockProvider)

    def test_openai_selected_when_model(self):
        self._set("openai", openai_model="deepseek-chat",
                  openai_base="https://api.deepseek.com/v1")
        p = get_provider()
        self.assertIsInstance(p, OpenAIProvider)
        self.assertEqual(p.base_url, "https://api.deepseek.com/v1")

    def test_openai_empty_base_defaults_official(self):
        p = OpenAIProvider()
        p.base_url = (p.base_url or OpenAIProvider.DEFAULT_BASE).rstrip("/")
        self.assertTrue(p.base_url.endswith("/v1"))

    def test_openai_mode_no_model_degrades(self):
        self._set("openai")
        self.assertIsInstance(get_provider(), MockProvider)

    def test_local_gateway_no_key_ok(self):
        # 本地网关：无 key、有 model → 可用
        self._set("openai", openai_model="qwen2.5",
                  openai_base="http://localhost:1234/v1")
        self.assertIsInstance(get_provider(), OpenAIProvider)

    def test_auto_order(self):
        self._set("auto", claude_key="sk-x", openai_model="gpt-4o-mini")
        self.assertIsInstance(get_provider(), ClaudeProvider)     # Claude 最优先
        self._set("auto", openai_model="gpt-4o-mini",
                  ollama_model="qwen2.5:14b")
        self.assertIsInstance(get_provider(), OpenAIProvider)     # 其次 OpenAI 兼容
        self._set("auto", ollama_model="qwen2.5:14b")
        self.assertIsInstance(get_provider(), OllamaProvider)     # 再次 Ollama
        self._set("auto")
        self.assertIsInstance(get_provider(), MockProvider)       # 都没有 → Mock

    def test_unknown_mode_falls_auto(self):
        self._set("nonsense")
        self.assertIsInstance(get_provider(), MockProvider)


class TestOpenAIResponse(unittest.TestCase):
    """协议细节：请求走 /chat/completions、响应取 choices[0].message.content。"""

    def setUp(self):
        self._gen = dict(settings.GENERATIVE)
        self._gen["providers"] = {k: dict(v) for k, v in
                                  settings.GENERATIVE["providers"].items()}

    def tearDown(self):
        settings.GENERATIVE.clear()
        settings.GENERATIVE.update(self._gen)

    def test_request_and_parse(self):
        from unittest.mock import patch
        captured = {}

        class Resp:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "  回答  "}}]}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return Resp()

        p = OpenAIProvider()
        p.api_key, p.base_url, p.model = "sk-x", "https://api.deepseek.com", "d"
        with patch("requests.post", fake_post):
            out = p.generate("系统提示", "用户问题")
        self.assertEqual(out, "回答")
        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(captured["headers"]["authorization"], "Bearer sk-x")
        self.assertEqual([m["role"] for m in captured["json"]["messages"]],
                         ["system", "user"])

    def test_bad_shape_raises(self):
        from unittest.mock import patch

        class Resp:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"error": "weird"}

        p = OpenAIProvider()
        p.model = "m"
        with patch("requests.post", lambda *a, **kw: Resp()):
            with self.assertRaises(provider_mod.ProviderError):
                p.generate("s", "u")


if __name__ == "__main__":
    unittest.main()
