"""M-C 验收：资源管理器写操作端到端。重点=目录改名「三联」（磁盘+DB+payload+canvas JSON）
与收录防双份（register_existing 就地登记）。"""
import sys, os, tempfile, time
sys.path.insert(0, os.getcwd())
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="kms_fsact_"))
os.environ["KMS_DATA_ROOT"] = str(tmp)

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
app = QApplication.instance() or QApplication([])
from core.storage.vault import Vault
from config import settings

v = settings.DEFAULT_VAULT_PATH
(v / "x").mkdir(parents=True)
(v / "x" / "y.md").write_text("# Y篇\n正文 图谱 节点。", encoding="utf-8")
(v / "x" / "z.md").write_text("# Z篇\n原始。", encoding="utf-8")
(v / "x" / "b.canvas").write_text(
    '{"nodes":[{"id":"n1","type":"file","x":0,"y":0,"width":240,"height":120,'
    '"file":"x/y.md"},{"id":"t","type":"text","x":1,"y":1,"width":10,"height":10,'
    '"text":"hi"}],"edges":[]}', encoding="utf-8")

from app.main_window import MainWindow
import app.ui.fs_tree as fst
win = MainWindow()
store = win.store

store.upsert_file("x/y.md", "Y篇", "技术",
                  dedup_key=Vault.make_dedup_key("# Y篇\n正文 图谱 节点。"), mtime=time.time())
store.add_atoms("x/y.md", [{"kind": "keyword", "value": "图谱"},
                           {"kind": "keyword", "value": "节点"}])
store.index_note_tokens("x/y.md", "# Y篇\n正文 图谱 节点。")
store.add_suggestions([
    {"kind": "canvas_edge", "file_path": "x/y.md", "target": "z", "confidence": 1.0,
     "payload": {"source": "canvas", "target_title": "z", "target_path": "x/z.md",
                 "content_fp": "x", "insert_at": None, "anchor": None, "safe": False,
                 "canvas_rel": "x/b.canvas", "edge_id": "e1",
                 "from_rel": "x/y.md", "to_rel": "x/z.md"}},
])
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
warnings = []
QMessageBox.warning = staticmethod(lambda *a, **k: warnings.append(a[2] if len(a) > 2 else ""))
QMessageBox.exec = lambda self, *a: QMessageBox.StandardButton.Cancel

# ---- 1) 收录 RAW 就地登记：盘上路径不变、无第二份 ----
fs = win.fs_tree
fs.reload_root()
fs.expand(fs.item_index("x"))          # 懒加载：展开子层
app.processEvents()
assert fs.item_index("x/z.md").data(fst.FS_ROLE_STATE) == "raw"
win._fs_register(["x/z.md"])
assert store.file_exists("x/z.md"), "收录后应入库"
assert (v / "x" / "z.md").exists() and len(list(v.rglob("z.md"))) == 1, "不允许第二份副本"
# _fs_register 内部已 refresh_subtree("x")：徽标应就地变 atomic（顺带验证增量刷新）
assert fs.item_index("x/z.md").data(fst.FS_ROLE_STATE) == "atomic"
print("1 register in-place + incremental badge ok")

# ---- 2) 目录改名三联 ----
class FakePicker:
    result = None
    def __init__(self, *a, **k): pass
    def exec(self): return 1          # Accepted（QDialog.Accepted=1）
    def selected_rel(self): return FakePicker.result
QInputDialog.getText = staticmethod(lambda *a, **k: ("归档", True))
win._fs_rename_dir("x")
assert (v / "归档" / "y.md").exists() and not (v / "x").exists(), "磁盘已迁移"
paths = {r["path"] for r in store.conn.execute("SELECT path FROM vault_files")}
assert paths == {"归档/y.md", "归档/z.md"}, paths
import json
row = store.conn.execute(
    "SELECT payload FROM suggestions WHERE kind='canvas_edge'").fetchone()
pl = json.loads(row["payload"])
assert pl["canvas_rel"] == "归档/b.canvas", pl          # 未入库的画布文件也级联
assert (pl["from_rel"], pl["target_path"]) == ("归档/y.md", "归档/z.md"), pl
canvas_json = json.loads((v / "归档" / "b.canvas").read_text(encoding="utf-8"))
assert canvas_json["nodes"][0]["file"] == "归档/y.md", "canvas JSON 引用三联"
assert canvas_json["nodes"][1]["text"] == "hi", "非 file 节点原样"
stt = store.conn.execute("SELECT status FROM suggestions").fetchone()["status"]
assert stt == "pending"
print("2 dir-rename triple cascade ok")

# ---- 3) 撞名防御：目标已存在 → 拒绝且磁盘不动 ----
# 先造根级同名文件作撞名目标
v.joinpath("z.md").write_text("# 根z\n撞名占位。", encoding="utf-8")
QInputDialog.getText = staticmethod(lambda *a, **k: ("z.md", True))
before_tree = sorted(p.as_posix() for p in v.rglob("*"))
win._fs_rename_dir("归档")
assert sorted(p.as_posix() for p in v.rglob("*")) == before_tree, "撞名不应发生任何迁移"
assert (v / "归档" / "y.md").exists()
print("3 clash guarded ok")

# ---- 4) 移动到文件夹（FakePicker）----
(v / "目标").mkdir()
FakePicker.result = "目标"
fst.VaultDirPicker = FakePicker
win._fs_move(["归档/z.md"])
assert (v / "目标" / "z.md").exists() and not (v / "归档" / "z.md").exists()
assert store.file_exists("目标/z.md") and not store.file_exists("归档/z.md")
n = win._rewrite_canvas_node_files("目标/z.md", "目标/z.md")   # 自映射应零改动
assert n == 0
print("4 move + prefix-degenerate ok")

# ---- 5) 删除（文件夹含入库项）----
before = {r["path"] for r in store.conn.execute("SELECT path FROM vault_files")}
win._fs_delete(["归档"])
assert not (v / "归档").exists()
after = {r["path"] for r in store.conn.execute("SELECT path FROM vault_files")}
assert after == before - {"归档/y.md"}, after
cand = store.conn.execute(
    "SELECT status FROM suggestions WHERE kind='canvas_edge'").fetchone()
assert cand["status"] in ("obsolete", None), cand  # 相关建议失效或画布删除链
print("5 delete dir cascade ok")

win.close(); store.close()
import shutil; shutil.rmtree(tmp, ignore_errors=True)
print("PROBE FS ACTIONS ALL OK")
