"""C9 探针：文本卡原地编辑——进入/打字/Esc 提交/FocusOut 提交/重放/编辑态互斥。"""
import sys, os, tempfile
sys.path.insert(0, os.getcwd())
from pathlib import Path
tmp = Path(tempfile.mkdtemp(prefix="kms_c9_"))
import config.settings as settings
settings.DEFAULT_VAULT_PATH = tmp / "vault"
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault
from core.canvas import CanvasDocument
from app.ui.canvas import CanvasWorkspace

store = SQLiteStore(tmp / "t.db"); vault = Vault(tmp / "vault")
ws = CanvasWorkspace(store, vault)
ws.doc = CanvasDocument(rel_path="c9.canvas")
ws.doc.new_document()
t1 = ws.doc.add_text_card("初稿", 0, 0)
t2 = ws.doc.add_text_card("第二张", 300, 0)
ws.view.build(ws.doc)
ws.view.resize(800, 600); ws.view.show()
app.processEvents()

i1 = ws.view._cards[t1.id]
i1.begin_inline_edit()
assert i1._proxy is not None and ws._editing is i1
assert not i1.flags() & __import__("PySide6.QtWidgets", fromlist=["QGraphicsItem"]).QGraphicsItem.GraphicsItemFlag.ItemIsMovable
assert all(not sc.isEnabled() for sc in ws._scs)          # 快捷键已让位
ed = i1._proxy[1]
ed.setPlainText("改过的内容")

# 另一张卡 begin → 全局互斥：旧卡先提交
i2 = ws.view._cards[t2.id]
i2.begin_inline_edit()
assert t1.text == "改过的内容" and i1._proxy is None
assert ws._editing is i2

# Esc 提交（走 eventFilter）
ed2 = i2._proxy[1]
ed2.setPlainText("Esc 也提交")
ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
assert i2.filter_edit_event(ed2, ev) is True
assert t2.text == "Esc 也提交" and i2._proxy is None and ws._editing is None
assert all(sc.isEnabled() for sc in ws._scs)               # 快捷键恢复
assert i2.flags() & __import__("PySide6.QtWidgets", fromlist=["QGraphicsItem"]).QGraphicsItem.GraphicsItemFlag.ItemIsMovable

# FocusOut 提交路径
i1.begin_inline_edit()
i1._proxy[1].setPlainText("失焦提交")
fev = QEvent(QEvent.Type.FocusOut)
i1.filter_edit_event(i1._proxy[1], fev)
assert t1.text == "失焦提交" and i1._proxy is None

# 撤销链：三次编辑=三条命令（c1:t1 c2:t2 c3:t1），LIFO 逐步回退
assert t1.text == "失焦提交"
ws._undo.undo(); assert t1.text == "改过的内容"      # c3 撤
ws._undo.undo(); assert t2.text == "第二张"           # c2 撤（t2 的提交）
ws._undo.undo(); assert t1.text == "初稿"             # c1 撤
ws._undo.redo(); ws._undo.redo(); ws._undo.redo()
assert t1.text == "失焦提交" and t2.text == "Esc 也提交"
# 空提交不产生命令
n_cmd = ws._undo.count()
i2.begin_inline_edit(); i2._proxy[1].setPlainText("Esc 也提交"); i2._end_inline_edit(True)
assert ws._undo.count() == n_cmd
# 编辑中保存：先提交再落盘
i1.begin_inline_edit(); i1._proxy[1].setPlainText("保存时还在编辑")
assert ws.save_current()
doc2, _ = CanvasDocument.open(vault, "c9.canvas")
assert doc2.node(t1.id).text == "保存时还在编辑"
# 关文档时编辑态被 build 回收
i1.begin_inline_edit()
ws.close_document.__func__                # 不真关，直接 build 验证
ws.view.build(doc2)
assert ws._editing is None
store.close(); import shutil; shutil.rmtree(tmp, ignore_errors=True)
print("PROBE INLINE EDIT ALL OK")
