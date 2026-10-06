"""构建脚本：把 GUI 打包成 Windows 可执行。

用法（在本目录下）:
  python build.py pyinstaller            # 日常/内测：onedir，构建快、排错易
  python build.py nuitka                 # 商用发布：standalone 目录（编译成 C，代码保护）
  python build.py nuitka --onefile       # Nuitka 单文件 exe（分发更省事，首启解包略慢）

要点（均已内置）:
  - 入口 kms_entry.py，产物数据目录在 exe 同级（settings._app_root 冻结适配）
  - jieba 词典随包携带（package data）；app/icons/*.svg 显式打入
  - GUI 链路不需要 mcp/pydantic/WebEngine 等重型依赖 → 统一排除控体积
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# CI 的 Windows runner stdout 默认 cp1252，print ✅/中文会抛 UnicodeEncodeError；
# 统一强制 UTF-8（errors=replace 兜底任何终端），本地 GBK/UTF-8 环境同样安全。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
APP_NAME = "AI-Native-KMS"
ENTRY = "kms_entry.py"

# GUI 运行链路确定不需要的重型依赖（体积/复杂度双杀）
QT_TRIM = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtDesigner",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.Qt3DCore",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtSerialPort", "PySide6.QtRemoteObjects",
    "PySide6.QtPdf", "PySide6.QtPdfQuick", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
]
OTHER_TRIM = ["mcp", "pydantic", "pydantic_core", "tkinter", "matplotlib",
              "scipy", "numpy", "PIL", "IPython", "pytest"]


def _run(cmd: list[str]) -> int:
    print(">>", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(ROOT))


def build_pyinstaller() -> int:
    sep = ";" if sys.platform == "win32" else ":"   # --add-data 分隔符平台差异
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--windowed",                      # 无控制台窗口 / Mac 出 .app
        "--name", APP_NAME,
        "--collect-all", "jieba",          # 词典等 package data
        # icons 是纯数据文件，代码里 Path(__file__).parent/'icons' 定位 → 打进 app/icons
        "--add-data", f"app/icons{sep}app/icons",
    ]
    if sys.platform == "darwin":
        icns = ROOT / "resources" / "icons" / "appicon.icns"
        if icns.exists():
            args += ["--icon", str(icns)]  # 先跑 tools/make_icons.py 生成
        args += ["--osx-bundle-identifier", "com.xuancode.kms"]
    args.append(ENTRY)
    for m in QT_TRIM + OTHER_TRIM:
        args += ["--exclude-module", m]
    rc = _run(args)
    if rc == 0:
        hint = "（.app 实际位置以 dist 下 find 为准）" if sys.platform == "darwin" else ""
        print(f"\n✅ 构建完成，产物在 dist/{APP_NAME}/ {hint}"
              f"——onedir 即绿色便携包，数据生成在 exe/App 同级 data/")
    return rc


def build_nuitka(onefile: bool) -> int:
    cmd = [sys.executable, "-m", "nuitka", "--standalone"] + \
          (["--onefile"] if onefile else []) + [
        "--assume-yes-for-downloads",       # 自动拉取 C 编译器依赖（首次）
        "--windows-console-mode=disable",   # 无控制台
        "--enable-plugin=pyside6",
        "--include-package-data=jieba",
        "--include-data-dir=app/icons=app/icons",
        "--output-dir=dist",
        f"--output-filename={APP_NAME}.exe",
    ]
    for m in ["mcp", "pydantic", "tkinter"]:
        cmd += ["--nofollow-import-to=" + m]
    for m in QT_TRIM:
        cmd.append(f"--nofollow-import-to={m}")
    cmd.append(ENTRY)
    rc = _run(cmd)
    if rc == 0:
        out = f"dist/{APP_NAME}.exe" if onefile else f"dist/{ENTRY[:-3]}.dist/{APP_NAME}.exe"
        print(f"\n✅ Nuitka 产物: {out}")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", choices=["pyinstaller", "nuitka"])
    ap.add_argument("--onefile", action="store_true", help="仅 nuitka：单文件 exe")
    a = ap.parse_args()
    if a.target == "pyinstaller":
        return build_pyinstaller()
    return build_nuitka(a.onefile)


if __name__ == "__main__":
    raise SystemExit(main())
