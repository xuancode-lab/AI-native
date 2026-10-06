"""全局配置：路径、密钥、模型、开关。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _app_root() -> Path:
    """数据根目录定位——开发态=源码目录；打包态=exe 同级目录（便携可迁移）。

    不处理的话，PyInstaller/Nuitka 冻结后 __file__ 指向临时解包目录，
    vault/db/.env 会被写进 temp 随退出丢失。规则：
      - Nuitka onefile：__nuitka_binary_entry__ = 原始 exe 路径
      - PyInstaller（onefile/onedir）与 Nuitka standalone：sys.frozen
      - 开发态：沿用源码位置
    """
    # Nuitka 约定：onefile 下 __main__ 带 __nuitka_binary_entry__（原始 exe 绝对路径）
    main = sys.modules.get("__main__")
    entry = getattr(main, "__nuitka_binary_entry__", "") or os.environ.get(
        "KMS_APP_ROOT", "")
    if entry:
        return Path(entry).resolve().parent
    if getattr(sys, "frozen", False):          # PyInstaller；Nuitka standalone 也置 frozen
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# ---- 路径体系 ----
# APP_HOME：.env 与默认数据的基地。exe(或源码)目录可写 → 便携模式原地；
# 不可写（如装进 Program Files）→ 降级平台标准用户目录（Win=LOCALAPPDATA /
# Mac=Application Support / Linux=~/.local/share），避开 UAC VirtualStore 黑洞。
def _is_writable(d: Path) -> bool:
    """目录可写探测（Program Files 等受保护位置返回 False）。"""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".kms_write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _fallback_home() -> "Path | None":
    """不可写基地时的平台标准用户数据目录（纯函数，便于单测）。

    Windows: %LOCALAPPDATA%（Program Files 装机正规退路，避 VirtualStore）
    macOS:   ~/Library/Application Support —— 不在 iCloud 同步范围，
             天然避开 SQLite-WAL × iCloud 驱逐文件的经典坑
    Linux:   $XDG_DATA_HOME 或 ~/.local/share
    """
    import platform
    sysname = platform.system()
    if sysname == "Windows":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        return Path(base) / "AI-Native KMS" if base else None
    if sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "AI-Native KMS"
    xdg = os.getenv("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "AI-Native KMS"


def _app_home(app_root: Path) -> Path:
    if _is_writable(app_root):
        return app_root
    fb = _fallback_home()
    return fb if fb else app_root


def resolve_data_root(env_map: dict, app_home: Path) -> Path:
    """数据根解析（纯函数，便于单测）：KMS_DATA_ROOT > APP_HOME/data。"""
    raw = str(env_map.get("KMS_DATA_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return app_home / "data"


APP_HOME = _app_home(_app_root())
PROJECT_ROOT = APP_HOME          # 兼容旧引用


def _load_env() -> dict:
    """从 .env 读取密钥/路径覆盖（不强制存在；os.environ 同名键优先）。"""
    out = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    for k in ("KMS_DATA_ROOT", "KMS_VAULT_PATH"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


ENV_PATH = APP_HOME / ".env"
_env = _load_env()

DATA_ROOT = resolve_data_root(_env, APP_HOME)


def resolve_vault_path(env_map: dict, data_root: Path) -> Path:
    """vault 路径（纯函数）：KMS_VAULT_PATH 独立覆盖 > DATA_ROOT/vault。"""
    raw = str(env_map.get("KMS_VAULT_PATH") or "").strip()
    return Path(raw).expanduser() if raw else data_root / "vault"


# 知识本体（Markdown vault）可独立指到任意目录（如"文档"）；缺省在 DATA_ROOT 下
DEFAULT_VAULT_PATH = resolve_vault_path(_env, DATA_ROOT)
DB_PATH = DATA_ROOT / "kms.db"                    # SQLite（原子/图谱/分类/日志/快照）
LOG_DIR = DATA_ROOT / "logs"
SNAPSHOT_DIR = DATA_ROOT / "snapshots"            # AI 写库前的版本快照
DROPBOX_DIR = DATA_ROOT / "dropbox"               # 丢素材自动入库目录
EXPORT_DIR = DATA_ROOT / "exports"                # 结构导出资产

for _d in (DEFAULT_VAULT_PATH, LOG_DIR, SNAPSHOT_DIR, DROPBOX_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

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
    env_path = Path(env_path or ENV_PATH)
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