"""M-C2 MainWindow 集成验收：环境变量隔离数据根，discover→审阅→apply 全链。"""
import sys, os, tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp(prefix="kms_m2_"))
os.environ["KMS_DATA_ROOT"] = str(tmp)          # settings 加载前注入——真隔离
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import time
from PySide6.QtWidgets import QApplication, QInputDialog
from PySide6.QtCore import QPointF
app = QApplication.instance() or QApplication([])
from app.main_window import MainWindow
from config import settings
assert str(settings.DB_PATH).startswith(str(tmp)), settings.DB_PATH
from core.storage.vault import Vault as _V

win = MainWindow()
for rel, t, kws in (("技术/甲篇.md", "甲篇", ["写作", "大纲"]),
                    ("学习/乙篇.md", "乙篇", ["写作", "大纲"])):
    body = f"# {t}\n{' '.join(kws)} 相关内容。"
    win.vault.write_note(rel, body)
    win.store.upsert_file(rel, t, "技术", dedup_key=_V.make_dedup_key(body), mtime=time.time())
    win.store.add_atoms(rel, [{"kind": "keyword", "value": k} for k in kws])

QInputDialog.getText = staticmethod(lambda *a, **k: ("集成.canvas", True))
win._new_canvas()
assert win.canvas_ws.add_note_card("技术/甲篇.md", QPointF(0, 0))
assert win.canvas_ws.add_note_card("学习/乙篇.md", QPointF(300, 0))
win.canvas_ws.do_discover()
rows = win.store.pending_suggestions(kind="canvas_edge")
assert len(rows) == 1 and rows[0]["payload"]["source"] == "canvas_ai", rows
win.open_review()
tb = win._rev_table
texts = [[tb.item(i, c).text() if tb.item(i, c) else "" for c in range(6)] for i in range(tb.rowCount())]
ai_rows = [t for t in texts if "AI 建议" in t[3]]
assert ai_rows and "共享" in ai_rows[0][3], ai_rows
win._review_apply_one(rows[0]["id"])
st = [it.status for it in win.canvas_ws.view._edges.values()]
assert "applied" in st, st
fp, tgt = rows[0]["file_path"], rows[0]["target"]
assert f"[[{tgt}]]" in win.vault.read_note(fp), (fp, tgt)
from PySide6.QtWidgets import QMessageBox
QMessageBox.exec = lambda self, *a: QMessageBox.StandardButton.Discard
print("M-C2 INTEGRATION OK:", rows[0]["payload"]["source"], "-> applied ->", fp, "追加 [[%s]]" % tgt)
win.close(); win.store.close()
import shutil; shutil.rmtree(tmp, ignore_errors=True)
