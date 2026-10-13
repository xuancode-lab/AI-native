"""C2/C3 探针：右键动作槽（颜色/hex/标签/箭头）+ 复制粘贴去重 + 宏级 undo。"""
import sys, os, tempfile
sys.path.insert(0, os.getcwd())
from pathlib import Path
tmp = Path(tempfile.mkdtemp(prefix="kms_c_menu_"))
import config.settings as settings
settings.DEFAULT_VAULT_PATH = tmp / "vault"

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
app = QApplication.instance() or QApplication([])
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault
from core.canvas import CanvasDocument
from app.ui.canvas import CanvasWorkspace

store = SQLiteStore(tmp / "t.db"); vault = Vault(tmp / "vault")
QMessageBox.warning = staticmethod(lambda *a, **k: None)   # offscreen 防模态阻塞
ws = CanvasWorkspace(store, vault)
ws.doc = CanvasDocument(rel_path="m.canvas")
a = ws.doc.add_text_card("甲", 0, 0)
b = ws.doc.add_text_card("乙", 300, 0)
e = ws.doc.add_edge(a.id, b.id, to_end="arrow")
ws.view.build(ws.doc)

# 卡色：色板 + undo/redo
ws._act_set_color([a.id], "4")
assert a.color == "#325bf3" or a.color == "4"
ws._undo.undo(); assert a.color is None
ws._undo.redo(); assert a.color == "4"
print("1 palette color + undo/redo ok")

# 自定义 hex：合法/非法
QInputDialog.getText = staticmethod(lambda *a_, **k: ("#123456", True))
ws._act_custom_color([b.id], "node")
assert b.color == "#123456"
QInputDialog.getText = staticmethod(lambda *a_, **k: ("红色", True))
ws._act_custom_color([b.id], "node")
assert b.color == "#123456"                      # 非法输入不改值
print("2 custom hex validated ok")

# 边标签 + 箭头方向切换
QInputDialog.getText = staticmethod(lambda *a_, **k: ("依赖", True))
ws._act_edge_label(e.id, "")
assert e.label == "依赖"
ws._act_toggle_arrow(e)
assert (e.from_end, e.to_end) == ("arrow", None)
ws._undo.undo()
assert (e.from_end, e.to_end) == (None, "arrow")
ws._undo.undo()
assert e.label is None
print("3 edge label + arrow toggle ok")

# 复制粘贴：两卡+中间边整组复制
for nid in (a.id, b.id):
    ws.view.item_of(nid).setSelected(True)
ws.copy_selection()
ws.paste()
assert len(ws.doc.nodes) == 4 and len(ws.doc.edges) == 2
new_cards = [n for n in ws.doc.nodes if n.id not in (a.id, b.id)]
new_edge = [x for x in ws.doc.edges if x.id != e.id][0]
assert {new_edge.from_node, new_edge.to_node} == {n.id for n in new_cards}
assert {c.x for c in new_cards} == {24.0, 324.0}   # 24px 偏移
print("4 paste remap ok")

# 一次 Ctrl+Z 撤掉整个粘贴宏
ws._undo.undo()
assert len(ws.doc.nodes) == 2 and len(ws.doc.edges) == 1
ws._undo.redo()
assert len(ws.doc.nodes) == 4
ws._undo.undo()
print("5 paste is single undo step ok")

# duplicate：只选 b
ws.view._scene.clearSelection()
ws.view.item_of(b.id).setSelected(True)
ws.duplicate_selection()
assert len(ws.doc.nodes) == 3
# 全选
ws.view.select_all_cards()
assert sum(1 for c in ws.view._cards.values() if c.isSelected()) == 3
# 渲染冒烟（含色/标签路径）
from PySide6.QtGui import QImage, QPainter
img = QImage(800, 600, QImage.Format.Format_ARGB32); img.fill(0)
ws.view.resize(800, 600)
with QPainter(img) as p_:
    ws.view.render(p_)
print("6 duplicate/select-all/render ok")

store.close(); import shutil; shutil.rmtree(tmp, ignore_errors=True)
print("PROBE MENU/CLIPBOARD ALL OK")
