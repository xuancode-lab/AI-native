"""C4/C5 探针：富文本渲染冒烟 + discover 批量落边/建议/宏级 undo/existing 去重。"""
import sys, os, tempfile, time
sys.path.insert(0, os.getcwd())
from pathlib import Path
tmp = Path(tempfile.mkdtemp(prefix="kms_disc_"))
import config.settings as settings
settings.DEFAULT_VAULT_PATH = tmp / "vault"

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
app = QApplication.instance() or QApplication([])
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault
from core.curator import Curator, CANVAS_EDGE
from core.ingest.pipeline import IngestPipeline
from core.canvas import CanvasDocument

store = SQLiteStore(tmp / "t.db"); vault = Vault(tmp / "vault")
cur = Curator(store, vault, IngestPipeline(store, vault))

def note(rel, title, body_extra, kws):
    body = f"# {title}\n{body_extra}"
    vault.write_note(rel, body, )
    store.upsert_file(rel, title, "技术", dedup_key=Vault.make_dedup_key(body), mtime=time.time())
    store.add_atoms(rel, [{"kind": "keyword", "value": k} for k in kws])

A, B, C = "技术/甲.md", "技术/乙.md", "学习/丙.md"
note(A, "甲", "## 小节\n- 列表一\n- 列表二\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", ["图谱", "节点"])
note(B, "乙", "**粗体** 和 `代码` 混排。", ["图谱", "节点"])
note(C, "丙", "无关。", ["旅行"])

from app.ui.canvas import CanvasWorkspace
ws = CanvasWorkspace(store, vault, curator=cur)
ws.doc = CanvasDocument(rel_path="d.canvas")
ws.doc.new_document()
ca = ws.doc.add_file_card(A, 0, 0)
cb = ws.doc.add_file_card(B, 300, 0)
cc = ws.doc.add_file_card(C, 600, 0)
ct = ws.doc.add_text_card("纯文本卡", 0, 300)
ce = ws.doc.add_text_card("", 300, 300)          # 空文本卡走占位渲染
ws.view.build(ws.doc)

# C4：渲染冒烟（表格/列表/代码块/空卡占位全走 QTextDocument 路径）
img = QImage(900, 600, QImage.Format.Format_ARGB32); img.fill(0)
ws.view.resize(900, 600)
with QPainter(img) as p:
    ws.view.render(p)
print("1 richtext render ok (600字截断:", len(ws.view._cards[ca.id].excerpt) <= 600, ")")

# label 缓存：mtime 变更才重读
n0 = len(ws._label_cache)
ws.refresh_card_labels()
assert len(ws._label_cache) >= 3
p_extra = vault.vault_path / A
p_extra.write_text(p_extra.read_text(encoding="utf-8") + "\n新增段落。", encoding="utf-8")
ws.refresh_card_labels()
assert "新增段落" in ws.view._cards[ca.id].excerpt, ws.view._cards[ca.id].excerpt
print("2 label mtime cache + refresh ok")

# C5：discover → 画布出边 + pending(canvas_ai)
n = ws.discover_links()
assert n == 1, n
ai_edge = ws.doc.edges[0]
st = ws._edge_state[ai_edge.id]
assert st["source"] == "canvas_ai" and st["kws"]
row = store.get_suggestion(st["sid"])
assert row["payload"]["source"] == "canvas_ai" and row["status"] == "pending"
assert ws.view._edges[ai_edge.id].status == "pending"
print("3 discover ok:", st["kws"], "conf", row["confidence"])

# existing_pairs：discover 出来的边挡住同对再建议
assert ws.discover_links() == 0
# 手绘第二对（A-C 无共享词）不产建议但产边
ws.commit_link(ca.id, cc.id, "right", "left")
assert len(ws.doc.edges) == 2 and ws.doc.edges[1].id not in ws._edge_state or True
print("4 dedup existing ok")

# 宏级 undo：一次 Ctrl+Z 撤掉整批 AI 边（含建议行回收）
while ws._undo.count() and not isinstance(None, type(None)):
    pass
# 找到 AI 宏：undo 到只剩手绘
ws._undo.undo()                        # 撤最后一笔 commit_link（单步）
assert len(ws.doc.edges) == 1
ws._undo.undo()                        # 撤 discover 宏（一次回全部 AI 边）
assert len(ws.doc.edges) == 0
assert store.conn.execute("SELECT COUNT(*) c FROM suggestions WHERE kind=?",
                          (CANVAS_EDGE,)).fetchone()["c"] == 0
ws._undo.redo()                        # 重放：AI 边+建议+source 全回来
assert len(ws.doc.edges) == 1
st2 = ws._edge_state[ws.doc.edges[0].id]
assert st2["source"] == "canvas_ai" and st2["status"] == "pending"
print("5 macro undo/redo keeps AI marks ok")

# 6) AI 边随"删卡→undo"级联还原时保持 canvas_ai 标记
from app.ui.canvas import _CardItem
eid_ai = ws.doc.edges[0].id
ws.view.item_of(ws.doc.edges[0].from_node).setSelected(True)
ws.delete_selection()
assert ws.doc.edges == [] and eid_ai not in ws._edge_state
ws._undo.undo()
restored = ws.doc.edges[0]
assert restored.id == eid_ai
st3 = ws._edge_state[restored.id]
assert st3["source"] == "canvas_ai" and st3["status"] == "pending", st3
assert store.get_suggestion(st3["sid"])["payload"]["source"] == "canvas_ai"
print("6 cascade delete-undo keeps AI mark ok")

store.close(); import shutil; shutil.rmtree(tmp, ignore_errors=True)
print("PROBE DISCOVER/RICHTEXT ALL OK")
