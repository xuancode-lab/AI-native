"""品牌图标生成管线：app.svg → PNG 全尺寸 / .iconset / .ico /（Mac 上）.icns。

用法（在 ai-kms 目录下）:
  python tools/make_icons.py
产出到 resources/icons/：
  appicon_<N>.png      16..1024 各尺寸
  appicon.iconset/     macOS iconutil 输入（含 @2x 命名）
  appicon.ico          Windows 图标（PNG 编码条目，Vista+ 兼容）
  appicon.icns         仅 macOS 运行本脚本时由 iconutil 生成

设计：所有尺寸由同一矢量源渲染 —— 改 app.svg 一条命令全平台重出。
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_SVG = ROOT / "app" / "icons" / "app.svg"
OUT = ROOT / "resources" / "icons"

PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
# iconset 命名规则：icon_大小[@2x].png
ICONSET = {16: "icon_16x16.png", 32: ["icon_16x16@2x.png", "icon_32x32.png"],
           64: "icon_32x32@2x.png", 128: "icon_128x128.png",
           256: ["icon_128x128@2x.png", "icon_256x256.png"],
           512: ["icon_256x256@2x.png", "icon_512x512.png"],
           1024: "icon_512x512@2x.png"}


def _render(size: int) -> bytes:
    """SVG → PNG bytes（QSvgRenderer + QPixmap，offscreen 可用）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication, QPixmap, QPainter
    from PySide6.QtCore import Qt, QByteArray, QBuffer, QIODevice
    from PySide6.QtSvg import QSvgRenderer
    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(SRC_SVG.read_bytes())
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pix.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_ico(entries: dict[int, bytes], path: Path) -> None:
    """手写 ICO 容器（每条目为 PNG 数据——Vista+ 原生支持）。"""
    hdr = struct.pack("<HHH", 0, 1, len(entries))
    images, offset = b"", 6 + 16 * len(entries)
    dir_entries = b""
    for size, data in sorted(entries.items()):
        w = 0 if size >= 256 else size
        dir_entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32,
                                   len(data), offset)
        images += data
        offset += len(data)
    path.write_bytes(hdr + dir_entries + images)


def main() -> int:
    if not SRC_SVG.exists():
        print(f"缺源图标: {SRC_SVG}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    pngs: dict[int, bytes] = {}
    for n in PNG_SIZES:
        data = _render(n)
        pngs[n] = data
        (OUT / f"appicon_{n}.png").write_bytes(data)
    print(f"PNG ×{len(PNG_SIZES)} → {OUT}")

    # iconset（macOS iconutil 用）
    iconset = OUT / "appicon.iconset"
    iconset.mkdir(exist_ok=True)
    for n, names in ICONSET.items():
        targets = names if isinstance(names, list) else [names]
        for t in targets:
            (iconset / t).write_bytes(pngs[n])
    print(f"iconset → {iconset}")

    write_ico({n: pngs[n] for n in ICO_SIZES}, OUT / "appicon.ico")
    print(f"ICO → {OUT / 'appicon.ico'}")

    if sys.platform == "darwin" and shutil.which("iconutil"):
        subprocess.call(["iconutil", "-c", "icns", str(iconset),
                         "-o", str(OUT / "appicon.icns")])
        print(f"ICNS → {OUT / 'appicon.icns'}")
    else:
        print("（.icns 需在 macOS 上运行本脚本或手动 "
              "`iconutil -c icns appicon.iconset`）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
