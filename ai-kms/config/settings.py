"""全局配置：路径、密钥、模型、开关。"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（config/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---- 路径 ----
DEFAULT_VAULT_PATH = PROJECT_ROOT / "data" / "vault"          # 原生 Markdown 知识库
DB_PATH = PROJECT_ROOT / "data" / "kms.db"                    # SQLite（原子/图谱/分类/日志/快照）
LOG_DIR = PROJECT_ROOT / "data" / "logs"
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "snapshots"            # AI 写库前的版本快照

for _d in (DEFAULT_VAULT_PATH, LOG_DIR, SNAPSHOT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_env() -> dict:
    """从 .env 读取密钥（不强制存在，缺省则走离线/规则判断）。"""
    env_path = PROJECT_ROOT / ".env"
    out = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    return out


_env = _load_env()

# ---- AI 两层模型配置 ----
# 判断层（分类路由）：MVP 走规则集；provider 接口预留 jesui-term / Claude 等多模型
CLASSIFY = {
    "mode": "rules",                    # rules(内置规则集) | api(外部分类器) —— 预留
    "api_url": _env.get("CLASSIFY_API_URL", ""),
    "api_key": _env.get("CLASSIFY_API_KEY", ""),
    "fallback_to_rules": True,          # 外部分类器不可用时自动降到规则集
}

# 生成层
GENERATIVE = {
    "disabled": False,                  # 无密钥时自动降级到 MockProvider
    "mode": _env.get("KMS_PROVIDER_MODE", "auto"),   # auto|claude|ollama|mock（GUI 设置面板可改）
    "providers": {
        "claude": {
            "api_key": _env.get("ANTHROPIC_API_KEY", ""),
            "model": _env.get("CLAUDE_MODEL", "claude-3-5-sonnet-20240620"),
            "api_url": "https://api.anthropic.com/v1/messages",
        },
        # 本地 Ollama / OpenAI 兼容网关（DeepSeek、Kimi、vLLM、one-api 等）
        "ollama": {"base_url": _env.get("OLLAMA_BASE_URL", "http://localhost:11434"),
                   "model": _env.get("OLLAMA_MODEL", "")},
        "openai": {"api_key": _env.get("OPENAI_API_KEY", ""),
                   "base_url": _env.get("OPENAI_BASE_URL", ""),   # 留空=官方 v1
                   "model": _env.get("OPENAI_MODEL", "")},        # 填了才启用
    },
}


def update_env_values(updates: dict[str, str], env_path: Path | None = None) -> None:
    """写回 .env：已有键原位替换，新键追加；保留注释与其他行。"""
    env_path = Path(env_path or (PROJECT_ROOT / ".env"))
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = env_path.read_text(encoding="utf-8").splitlines() \
        if env_path.exists() else []
    remaining = dict(updates)
    out = []
    for ln in lines:
        key = ln.split("=", 1)[0].strip() if "=" in ln and not ln.strip().startswith("#") else None
        if key and key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(ln)
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")

# ---- 行为开关 ----
WATCHER_ENABLED = True                  # 监听 Vault 新文件自动收录
HUMAN_CONFIRM_WRITES = False            # AI 写库前是否要人工确认（本批管线写库即认定用户已授权）
MIN_ATOM_LEN = 2                        # 原子关键片段最短长度（过滤噪音）

# 分类主题（未来数据驱动，先内置一组可扩展规则分类）
TOPICS = ["技术", "学习", "生活", "创作", "课题", "未分类"]