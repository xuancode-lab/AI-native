"""C1 探针：外部改盘 → 保存冲突（覆盖/重载/取消）→ 切页静默重载保视口。"""
import sys, os, tempfile
sys.path.insert(0, os.getcwd())
from pathlib import Path
tmp = Path(tempfile.mkdtemp(prefix="kms_c_conflict_"))
import config.settings as settings
settings.DEFAULT_VAULT_PATH = tmp / "vault"

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
app = QApplication.instance() or QApplication([])
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault
from app.ui.canvas import CanvasWorkspace
from core.canvas import content_fp

def wr(p, text):
    p.write_text(text, encoding="utf-8")

store = SQLiteStore(tmp / "t.db"); vault = Vault(tmp / "vault")
ws = CanvasWorkspace(store, vault)
QInputDialog.getText = staticmethod(lambda *a, **k: ("画布C", True))
assert ws.new_document_interactive()

p = vault.vault_path / "画布C.canvas"
ws.doc.add_text_card("本地", 0, 0)
wr(p, '{"nodes":[{"id":"x","type":"text","x":9,"y":9,"width":240,'
       '"height":120,"text":"外部改的"}],"edges":[]}')
assert ws.doc.stale_on_disk(content_fp(p.read_text(encoding="utf-8")))

# 冲突①：以本地覆盖
QMessageBox.exec = lambda self, *a: (
    QMessageBox.StandardButton.Save if self.windowTitle() == "磁盘冲突"
    else QMessageBox.StandardButton.Cancel)
assert ws.save_current() is True
assert "本地" in p.read_text(encoding="utf-8")
assert not ws.doc.dirty and ws.doc.disk_fp == content_fp(p.read_text(encoding="utf-8"))
print("1 overwrite branch ok")

# 冲突②：重载磁盘（本地脏 → question Yes 确认丢弃）
wr(p, '{"nodes":[{"id":"y","type":"text","x":1,"y":1,"width":240,'
       '"height":120,"text":"盘上的正版"}],"edges":[]}')
ws.doc.add_text_card("别丢", 500, 500)
seen = []
def fake_exec(self, *a):
    seen.append(self.windowTitle())
    return (QMessageBox.StandardButton.Discard
            if self.windowTitle() == "磁盘冲突" else QMessageBox.StandardButton.Cancel)
QMessageBox.exec = fake_exec
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
assert ws.save_current() is False
assert len(ws.doc.nodes) == 1 and ws.doc.nodes[0].text == "盘上的正版"
assert not ws.doc.dirty
print("2 reload branch ok, dialogs:", seen)

# 冲突③：取消 → 不动盘不丢改动
wr(p, '{"nodes":[],"edges":[]}')
ws.doc.add_text_card("脏", 0, 0)
QMessageBox.exec = lambda self, *a: QMessageBox.StandardButton.Cancel
assert ws.save_current() is False
assert ws.doc.dirty and len(ws.doc.nodes) == 2
print("3 cancel branch ok")

# 静默重载：干净态 + 外部改 → sync_disk_quiet 保缩放（不该弹任何框）
ws._undo.clear()
ws.doc = ws.doc   # noqa
ws.save_current.__func__  # touch
wr(p, '{"nodes":[{"id":"z","type":"text","x":5,"y":5,"width":240,'
       '"height":120,"text":"外部悄悄改"}],"edges":[]}')
doc_clean, _ = __import__("core.canvas", fromlist=["CanvasDocument"]).CanvasDocument.open(vault, "画布C.canvas")
ws.doc = doc_clean          # 模拟：上次保存后本地干净（disk_fp 已对齐盘上"空文档"态？不对——重开自盘）
# 上面 open 记录的 disk_fp == 现在盘上内容 → 不 stale。改为再外部改一次：
wr(p, '{"nodes":[{"id":"w","type":"text","x":5,"y":5,"width":240,'
       '"height":120,"text":"外部又改"}],"edges":[]}')
ws.view._scale_at(1.5, QPointF(100, 100))
z_before = ws.view.transform().m11()
def boom(self, *a):
    raise AssertionError("静默路径不该弹框")
QMessageBox.exec = boom
ws.sync_disk_quiet()
assert len(ws.doc.nodes) == 1 and ws.doc.nodes[0].text == "外部又改"
assert not ws.doc.dirty
assert abs(ws.view.transform().m11() - z_before) < 1e-6
print("4 sync_disk_quiet silent + viewport kept (zoom", round(z_before, 3), ")")

store.close(); import shutil; shutil.rmtree(tmp, ignore_errors=True)
print("PROBE CONFLICT ALL OK")
