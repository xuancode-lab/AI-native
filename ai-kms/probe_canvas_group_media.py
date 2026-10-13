"""M-C3 探针：group 成组/拖组/删组 + 媒体卡（附件/缩略/外图）+ 图谱批量导入。"""
import sys, os, tempfile, time
sys.path.insert(0, os.getcwd())
from pathlib import Path
tmp = Path(tempfile.mkdtemp(prefix="kms_c3_"))
import config.settings as settings
settings.DEFAULT_VAULT_PATH = tmp / "vault"

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication.instance() or QApplication([])
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault
from core.curator import Curator
from core.ingest.pipeline import IngestPipeline
from core.canvas import CanvasDocument

store = SQLiteStore(tmp / "t.db"); vault = Vault(tmp / "vault")
cur = Curator(store, vault, IngestPipeline(store, vault))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QMessageBox.exec = lambda self, *a: QMessageBox.StandardButton.Cancel

from app.ui.canvas import CanvasWorkspace, _GroupItem
ws = CanvasWorkspace(store, vault, curator=cur)
ws.doc = CanvasDocument(rel_path="g.canvas")
ws.doc.new_document()
a = ws.doc.add_file_card("技术/甲.md", 0, 0)
b = ws.doc.add_text_card("乙", 400, 300)
c = ws.doc.add_text_card("丙", 2000, 2000)
ws.view.build(ws.doc)

# ---- C6 group ----
ws._act_group([a.id, b.id])
gitems = list(ws.view._groups.values())
assert len(ws.doc.nodes) == 4 and len(gitems) == 1
g = ws.doc.nodes[3]
assert g.type == "group"
assert set(ws.doc.group_members(g.id)) == {a.id, b.id}
# 拖组（模拟 view 扩展后的 commit_move：组+成员同 delta）
ws.commit_move({g.id: (g.x, g.y, g.x + 100, g.y), a.id: (a.x, a.y, a.x + 100, a.y),
                b.id: (b.x, b.y, b.x + 100, b.y)})
assert ws.doc.node(a.id).x == 100.0
ws._undo.undo()
assert ws.doc.node(a.id).x == 0.0 and ws.doc.node(g.id).x != 100.0
# 删组不删成员
ws.view._scene.clearSelection()
gitems[0].setSelected(True)
ws.delete_selection()
assert ws.doc.node(g.id) is None
assert ws.doc.node(a.id) is not None and ws.doc.node(b.id) is not None
ws._undo.undo()
assert ws.doc.node(g.id) is not None
# 透传组可见（Obsidian 画的组）
raw = ('{"nodes":[{"id":"p1","type":"file","x":0,"y":0,"width":240,"height":120,'
       '"file":"技术/甲.md"},{"id":"pg","type":"group","x":0,"y":0,"width":500,'
       '"height":400,"label":"外部组"}],"edges":[]}')
doc2, _ = CanvasDocument.loads("t.canvas", raw)
ws.view.build(doc2)
assert list(ws.view._groups) == ["pg"]
from PySide6.QtGui import QImage as QI
img = QI(900, 600, QI.Format.Format_ARGB32); img.fill(0)
ws.view.resize(900, 600)
with QPainter(img) as p:
    ws.view.render(p)
print("1 group ok")

# ---- C7 媒体卡 ----
png = QPixmap(60, 40); png.fill(Qt.GlobalColor.red)
assert png.save(str(vault.vault_path / "红块.png"))
assert ws.add_card_by_rel("红块.png", QPointF(0, 0))
card = ws.view._cards[ws.doc.nodes[-1].id]
assert card._is_image and ws.pixmap_for("红块.png", 200, 100) is not None
with QPainter(img) as p:
    ws.view.render(p)                       # 缩略渲染不炸
(vault.vault_path / "红块.png").rename(vault.vault_path / "改名.png")
assert ws.pixmap_for("红块.png", 200, 100) is None    # mtime 失效→文件没了→None 破图
# 外图 → 询问 → 附件/（重名 -1）
ext = tmp / "照片.png"
QPixmap(20, 20).fill(Qt.GlobalColor.blue)
QPixmap(20, 20).save(str(ext))
rels = ws.import_external_images([ext, ext])
assert rels == ["附件/照片.png", "附件/照片-1.png"], rels
assert ws.add_externals_with_ask([tmp / "不存在.png"], QPointF(0, 0)) == 0
print("2 media ok")

# ---- C8 图谱→画布导入 ----
ws.view.build(None); ws.doc = None
ws2 = CanvasWorkspace(store, vault, curator=cur)
ws2.doc = CanvasDocument(rel_path="i.canvas")
ws2.doc.new_document()
ws2.view.build(ws2.doc)
def note(rel, title, kws):
    body = f"# {title}\n正文 {' '.join(kws)}。"
    vault.write_note(rel, body)
    store.upsert_file(rel, title, "技术", dedup_key=Vault.make_dedup_key(body), mtime=time.time())
    store.add_atoms(rel, [{"kind": "keyword", "value": k} for k in kws])
for i, t in enumerate(["甲", "乙", "丙", "丁"]):
    note(f"技术/{t}.md", t, [f"k{i}"])
store.add_link("技术/甲.md", "乙")            # 既有双链
n = ws2.import_from_graph(["技术/甲.md", "技术/乙.md", "技术/丙.md", "技术/甲.md"])
assert n == 3
assert len(ws2.doc.edges) == 1                 # 甲↔乙 一条边
eids = set(ws2._edge_state)
row = store.pending_suggestions(kind="canvas_edge")
assert row == []                               # already_linked → 零队列污染
it = ws2.view._edges[ws2.doc.edges[0].id]
assert it.status == ""                          # 中性实线
# 对话框（不 exec）：过滤+多选通道
from app.ui.canvas import _ImportDialog
dlg = _ImportDialog(store.all_files)
dlg._search.setText("丙")
hidden_c = [i for i in range(dlg._list.count()) if dlg._list.item(i).isHidden()]
assert "技术/丙.md" not in {dlg._list.item(i).data(Qt.ItemDataRole.UserRole) for i in hidden_c}
dlg._select_visible()
assert "技术/丙.md" in dlg.selected_rels()
# 宏级 undo 一步清空（卡+边全回）
ws2._undo.undo()
assert len(ws2.doc.nodes) == 0 and len(ws2.doc.edges) == 0
ws2._undo.redo()
assert len(ws2.doc.nodes) == 3 and len(ws2.doc.edges) == 1
print("3 import + dialog + macro undo/redo ok")

store.close(); import shutil; shutil.rmtree(tmp, ignore_errors=True)
print("PROBE GROUP/MEDIA/IMPORT ALL OK")
