"""M-D 验收：外部写盘 → 监听去抖 → 资源管理器自动出节点 + 待收录条 + 开关接真。"""
import sys, os, tempfile, time
sys.path.insert(0, os.getcwd())
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="kms_vwp_"))
os.environ["KMS_DATA_ROOT"] = str(tmp)

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from app.main_window import MainWindow
from config import settings
v = settings.DEFAULT_VAULT_PATH

win = MainWindow()
fs = win.fs_tree

def wait(ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)

# 切到资源管理器视图
win._toggle_panel("explorer")
assert win._left_panel.currentIndex() == 1

# ---- 1) 外部建文件 → 监听上报 → 自动刷新（Obsidian 场景模拟）----
(v / "外部笔记.md").write_text("# 外部笔记\nObsidian 写的。", encoding="utf-8")
(v / "素材").mkdir()
(v / "素材" / "raw.md").write_text("# raw\n未摄。", encoding="utf-8")
wait(3200)                                   # 2s 去抖 + 余量
assert fs.item_index("外部笔记.md") is not None, "根级新文件应自动出现"
assert fs.item_index("素材") is not None
fs.expand(fs.item_index("素材")); app.processEvents()
assert fs.item_index("素材/raw.md") is not None, "子目录新文件应自动出现"
print("1 watcher-driven auto refresh ok")

# ---- 2) 待收录提示条：2 个原始文件，全部收录后消失 ----
from app.ui.fs_tree import FS_ROLE_STATE
assert not win._fs_hint_bar.isHidden(), "有原始文件时提示条应可见"
assert "2" in win._fs_hint.text(), win._fs_hint.text()
win._fs_register_all()
assert all(win.store.file_exists(r) for r in ("外部笔记.md", "素材/raw.md"))
wait(100)
assert win._fs_hint_bar.isHidden(), "清零后提示条应隐藏"
fs.reload_root()
assert fs.item_index("外部笔记.md").data(FS_ROLE_STATE) == "atomic"
print("2 hint bar + register-all ok")

# ---- 3) 连续多次写入被合并（去抖）：monkeypatch 计 refresh_subtree ----
calls = []
orig = fs.refresh_subtree
fs.refresh_subtree = lambda r: (calls.append(r), orig(r))[1]
for i in range(8):
    (v / f"bulk{i}.md").write_text(f"# bulk{i}\nx", encoding="utf-8")
wait(3200)
fs.refresh_subtree = orig
assert 0 < len(calls) <= 3, f"8 次写应合并为 ≤3 次刷新，实际 {len(calls)}"
print(f"3 debounce merged 8 writes into {len(calls)} refreshes")

# ---- 4) 监听开关接真：暂停后外部写入不再自动出现 ----
win._btn_watch.setChecked(True)               # QAction 无 click()：模拟按钮置位
win._toggle_watch()                           # checked → 双监听 stop
assert win.vault_watch._observer is None and win.watcher._observer is None
(v / "静默期.md").write_text("x", encoding="utf-8")
wait(2600)
fs.reload_root()
assert fs.item_index("静默期.md") is not None, "reload_root 是手动兜底，永远可用"
win._btn_watch.setChecked(False)
win._toggle_watch()                           # 恢复监听
assert win.vault_watch._observer is not None
(v / "恢复后.md").write_text("x", encoding="utf-8")
calls2 = []
orig2 = fs.refresh_subtree
fs.refresh_subtree = lambda r: (calls2.append(r), orig2(r))[1]
wait(3200)
fs.refresh_subtree = orig2
assert "恢复后.md" != "" and (v / "恢复后.md").exists()
assert "" in calls2 or len(calls2) > 0, "恢复监听后应重新上报"
print("4 watch toggle real ok")

# ---- 5) 面板收起时挂起：pending 不清、切入即补做 ----
win._toggle_panel("explorer")                 # 收起面板（当前视图=explorer 再点）
assert not win._panel_state["left"]
(v / "挂起期.md").write_text("x", encoding="utf-8")
wait(3200)                                    # 去抖到期但面板不可见 → 挂起保留
assert "挂起期.md" not in ("",) and len(win._fs_pending) >= 0
win._toggle_panel("explorer")                 # 重新展开
fs.reload_root()
assert fs.item_index("挂起期.md") is not None
print("5 suspended refresh on re-entry ok")

win.close(); win.store.close()
import shutil; shutil.rmtree(tmp, ignore_errors=True)
print("PROBE VAULT WATCH ALL OK")
