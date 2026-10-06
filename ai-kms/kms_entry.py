"""打包入口：直接启动桌面 GUI（等价 `python main.py gui`）。

PyInstaller / Nuitka 以本文件为 entry——不带 argv 分支，
打包产物双击即进入界面。源码态同样可运行：python kms_entry.py
"""
import sys
from pathlib import Path

# 源码直接运行时保证项目根在 sys.path（打包态由冻结导入器处理，无副作用）
_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.main_window import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())
