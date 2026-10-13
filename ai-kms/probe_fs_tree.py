"""M-B 验收：资源管理器树渲染/懒加载/徽标 + 活动栏互斥切换 + 双击分发 + mime。"""
import sys, os, tempfile
sys.path.insert(0, os.getcwd())
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="kms_fsprobe_"))
os.environ["KMS_DATA_ROOT"] = str(tmp)      # 真隔离：绝不开真实库

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

import time
from core.storage.vault import Vault
seed = tmp / "vault"
(seed / "技术").mkdir(parents=True)
(seed / "技术" / "已入库.md").write_text("# 已入库\n正文 图谱。", encoding="utf-8")
(seed / "技术" / "孤儿素材.md").write_text("# 孤儿素材\n未摄。", encoding="utf-8")
(seed / "白板区.canvas").write_text('{"nodes":[],"edges":[]}', encoding="utf-8")
(seed / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
(seed / "空夹").mkdir()
(seed / ".隐藏.md").write_text("x", encoding="utf-8")
(seed / "技术" / ".dot").mkdir()

from app.main_window import MainWindow
from app.ui.fs_tree import FS_ROLE_REL, FS_ROLE_STATE
from core.storage.fsindex import FileState

win = MainWindow()
w0 = win.store.upsert_file("技术/已入库.md", "已入库", "技术",
                           dedup_key=Vault.make_dedup_key("# 已入库\n正文 图谱。"),
                           mtime=time.time())

fs = win.fs_tree
fs.reload_root()

# ---- 1) 根枚举与徽标状态 ----
def rel_state(rel):
    idx = fs.item_index(rel)
    return None if idx is None else idx.data(FS_ROLE_STATE)

assert fs.item_index("技术") is not None
assert rel_state("白板区.canvas") == FileState.CANVAS
assert rel_state("pic.png") == FileState.IMAGE
assert rel_state("空夹") == FileState.FOLDER_EMPTY
assert rel_state(".隐藏.md") is None and fs.item_index("技术/.dot") is None, "隐藏项必须过滤"
# 子目录懒填前先判根层可见
assert rel_state("技术") == FileState.FOLDER
print("1 root enumeration + states ok")

# ---- 2) 懒加载展开 + ATOMIC/RAW ----
tidx = fs.item_index("技术")
it = fs._model.itemFromIndex(tidx)
first = it.child(0)
assert first is not None and first.data(Qt.ItemDataRole.UserRole + 3), "目录应带 dummy 子"
fs.expand(tidx)
app.processEvents()                    # 延迟填充在下一轮事件循环
idx_at = fs._model.indexFromItem(fs._item_for("技术/已入库.md"))
assert rel_state("技术/已入库.md") == FileState.ATOMIC
assert rel_state("技术/孤儿素材.md") == FileState.RAW
print("2 lazy populate + atomic/raw ok")

# ---- 3) indexed 快照 epoch 失效 ----
s0 = set(win._indexed_snapshot())
assert "技术/已入库.md" in s0 and "技术/孤儿素材.md" not in s0
r = win.pipe.register_existing("技术/孤儿素材.md")
assert r["status"] == "registered"
assert "技术/孤儿素材.md" in win._indexed_snapshot()   # epoch 变自动失效
fs.refresh_subtree("技术")
assert rel_state("技术/孤儿素材.md") == FileState.ATOMIC
print("3 epoch-driven refresh + register_existing ok")

# ---- 4) 活动栏互斥切换全序列 ----
def panel_sizes():
    return win._h_splitter.sizes()

win._toggle_panel("nav")
# 顶层窗口未 show：isVisible() 恒 False，改判显式隐藏位 isHidden()
assert win._panel_state["left"] and not win._left_panel.isHidden() and panel_sizes()[1] > 0
assert win._left_panel.currentIndex() == 0
assert win._activity_buttons["nav"].isChecked() and not win._activity_buttons["explorer"].isChecked()
win._toggle_panel("explorer")                      # 异视图：保持展开换页
assert win._panel_state["left"] and win._left_panel.currentIndex() == 1
assert win._activity_buttons["explorer"].isChecked() and not win._activity_buttons["nav"].isChecked()
win._toggle_panel("explorer")                      # 同视图再点：收起
assert not win._panel_state["left"] and panel_sizes()[1] == 0
assert not win._activity_buttons["explorer"].isChecked()
win._toggle_panel("explorer")                      # 收起态点=展开+切视图
assert win._panel_state["left"] and win._left_panel.currentIndex() == 1
print("4 activity-bar mutual exclusion ok")

# ---- 5) 双击分发 ----
# 段4切视图触发过 reload_root：懒加载子项需重新展开才有 index
tidx = fs.item_index("技术")
fs.expand(tidx)
app.processEvents()
opened = []
win._open_note = lambda rel: opened.append(("note", rel))
opened2 = []
win.canvas_ws.open_document = lambda rel: (opened2.append(rel), True)[1]
assert fs.item_index("技术/已入库.md") is not None, "展开后子项应可见"
win._on_fs_double_clicked(fs.item_index("技术/已入库.md"))
assert opened == [("note", "技术/已入库.md")]
win._on_fs_double_clicked(fs.item_index("白板区.canvas"))
assert opened2 == ["白板区.canvas"]
win._on_fs_double_clicked(fs.item_index("空夹"))    # 目录=展开切换不崩
print("5 double-click dispatch ok")

# ---- 6) mime_for 拖出规则 ----
root = win.vault.vault_path
assert fs.mime_for("技术/孤儿素材.md", FileState.ATOMIC, root) is not None
assert bytes(fs.mime_for("a.md", FileState.RAW, root).data("application/x-ai-kms-note")).decode() == "a.md"
assert fs.mime_for("x", FileState.CANVAS, root) is None
assert fs.mime_for("y", FileState.FOLDER, root) is None
assert fs.mime_for("p.png", FileState.IMAGE, root) is not None
assert fs.mime_for("z.pdf", FileState.OTHER, root) is None
print("6 drag mime policy ok")

# ---- 7) refresh_subtree 回落 ----
assert fs.refresh_subtree("不存在于树上的目录") is False
(seed / "技术" / "新文件.md").write_text("# 新文件\nx", encoding="utf-8")
assert fs.refresh_subtree("技术") is True
assert fs.item_index("技术/新文件.md") is not None
print("7 refresh_subtree + fallback ok")

win.store.close()
import shutil; shutil.rmtree(tmp, ignore_errors=True)
print("PROBE FS TREE ALL OK")