"""设置双入口验收：_set_vault_env 写键语义 + 数据根迁移的 vault 独立跳过/撞名防嵌套。"""
import sys, os, tempfile
sys.path.insert(0, os.getcwd())
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="kms_setv_"))
cur_root = tmp / "curroot"
(cur_root / "vault" / "笔记").mkdir(parents=True)
(cur_root / "vault" / "笔记" / "a.md").write_text("# a\nx", encoding="utf-8")
(cur_root / "dropbox").mkdir()
(cur_root / "dropbox" / "in.md").write_text("# in\nx", encoding="utf-8")
os.environ["KMS_DATA_ROOT"] = str(cur_root)

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
app = QApplication.instance() or QApplication([])
from config import settings
settings.ENV_PATH = tmp / ".env"          # 写 .env 全部重定向到 temp，绝不碰真实
from app.main_window import MainWindow

win = MainWindow()
warnings_log = []
QMessageBox.warning = staticmethod(lambda *a, **k: warnings_log.append(
    " ".join(str(x) for x in a[1:])))   # warning(parent, title, text)：标题+正文都收
QMessageBox.information = staticmethod(lambda *a, **k: None)

# ---- 1) _set_vault_env 三态 ----
lib = tmp / "ObsidianLib"
lib.mkdir()
win._set_vault_env(lib)
txt = (tmp / ".env").read_text(encoding="utf-8")
assert "KMS_VAULT_PATH=" + lib.as_posix() in txt.splitlines()
win._set_vault_env(Path("D:/Other"))          # 原位替换不产生第二行
lines = (tmp / ".env").read_text(encoding="utf-8").splitlines()
bad = [l for l in lines if l.startswith("KMS_VAULT_PATH")]
assert bad == ["KMS_VAULT_PATH=D:/Other"], bad
win._set_vault_env(None)                       # 恢复默认=写空值保留键行
assert "KMS_VAULT_PATH=" in (tmp / ".env").read_text(encoding="utf-8").splitlines()
assert settings.resolve_vault_path({"KMS_VAULT_PATH": str(lib)}, cur_root) == lib
assert settings.resolve_vault_path({"KMS_VAULT_PATH": ""}, cur_root) == cur_root / "vault"
print("1 _set_vault_env write/replace/reset + resolve ok")

# ---- 2) _change_vault_path 对话框流（采纳） ----
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(lib))
QMessageBox.exec = lambda self, *a: QMessageBox.StandardButton.Yes
win._change_vault_path()
assert f"KMS_VAULT_PATH={lib.as_posix()}" in (tmp / ".env").read_text(encoding="utf-8").splitlines()
print("2 change_vault_path adopt-flow ok")

# ---- 3) 数据根迁移：vault 独立指位 → 只搬 dropbox，独立库与旧 数据根/vault 不动 ----
settings.DEFAULT_VAULT_PATH = lib          # 模拟独立指位
settings.DATA_ROOT = cur_root
(lib / "keep.md").write_text("k", encoding="utf-8")
new_root = tmp / "newroot"
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(new_root))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
win._change_data_root()
assert (new_root / "dropbox").exists(), "dropbox 应迁移"
assert not (new_root / "vault").exists(), "独立模式下不应搬 vault"
assert (cur_root / "vault" / "笔记" / "a.md").exists(), "旧 数据根/vault 原地未动"
assert (lib / "keep.md").exists(), "独立知识库原地未动"
env2 = (tmp / ".env").read_text(encoding="utf-8")
assert f"KMS_DATA_ROOT={new_root}" in env2
print("3 data-root migrate skips independent vault ok")

# ---- 4) 撞名防御：目标已有 vault/ → warning + 跳过（不嵌套），dropbox 照搬 ----
cur_root2 = tmp / "curroot2"
(cur_root2 / "vault").mkdir(parents=True)
(cur_root2 / "vault" / "b.md").write_text("b", encoding="utf-8")
settings.DATA_ROOT = cur_root2
settings.DEFAULT_VAULT_PATH = cur_root2 / "vault"     # 回到非独立形态
dst2 = tmp / "dst2"
(dst2 / "vault").mkdir(parents=True)
(dst2 / "vault" / "existing.md").write_text("e", encoding="utf-8")
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(dst2))
warnings_log.clear()
win._change_data_root()
assert any("嵌套" in w for w in warnings_log), warnings_log
assert (cur_root2 / "vault" / "b.md").exists(), "撞名时旧 vault 不得移动"
assert (dst2 / "vault" / "existing.md").exists() and not (dst2 / "vault" / "vault").exists(), "不得产生 vault/vault"
print("4 clash guard (no vault/vault nesting) ok")

win.close(); win.store.close()
import shutil; shutil.rmtree(tmp, ignore_errors=True)
print("PROBE SETTINGS VAULT ALL OK")
