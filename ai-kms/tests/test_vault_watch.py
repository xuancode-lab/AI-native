"""VaultScanWatcher：_dirty_parent 纯函数矩阵 + observer 线程集成回调。"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.ingest.watcher import VaultScanWatcher, _dirty_parent


def _rel_of(root: Path):
    def f(path):
        try:
            return Path(path).resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            return None
    return f


class TestDirtyParent(unittest.TestCase):
    def setUp(self):
        import tempfile as tf
        self.dir = Path(tf.mkdtemp(prefix="kms_dw_"))
        (self.dir / "技术").mkdir()
        self.rf = _rel_of(self.dir)

    def ev(self, path, is_dir=False):
        return SimpleNamespace(src_path=str(path), is_directory=is_dir)

    def test_matrix(self):
        self.assertEqual(_dirty_parent(self.rf, self.ev(self.dir / "技术" / "a.md")), "技术")
        self.assertEqual(_dirty_parent(self.rf, self.ev(self.dir / "x.md")), "")
        self.assertEqual(_dirty_parent(self.rf, self.ev(self.dir / "技术", True)), "技术")
        self.assertEqual(_dirty_parent(self.rf, self.ev(self.dir, True)), "")
        self.assertEqual(_dirty_parent(self.rf, self.ev(self.dir / ".隐藏.md")), None)
        self.assertEqual(_dirty_parent(self.rf, self.ev(self.dir / "~$doc.docx")), None)
        self.assertEqual(_dirty_parent(self.rf, self.ev(self.dir / ".git", True)), None)
        self.assertEqual(_dirty_parent(self.rf, self.ev(Path("/totally/elsewhere/a.md"))), None)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class TestVaultScanWatcherIntegration(unittest.TestCase):
    """observer 真实线程集成：轮询放宽等待，防 CI 抖动。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="kms_dw2_"))
        self.got: list[str] = []
        self.lock = threading.Lock()

        def cb(rel_dir: str):
            with self.lock:
                self.got.append(rel_dir)
        self.w = VaultScanWatcher(self.dir, cb)
        self.w.start()
        time.sleep(0.3)                     # 让 observer 线程先就位

    def tearDown(self):
        self.w.stop()
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _wait(self, pred, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if pred(list(self.got)):
                    return True
            time.sleep(0.1)
        return False

    def test_file_event_reports_parent(self):
        (self.dir / "n.md").write_text("内容", encoding="utf-8")
        self.assertTrue(self._wait(lambda g: "" in g), f"got={self.got}")

    def test_subdir_file_reports_subdir(self):
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "m.md").write_text("x", encoding="utf-8")
        self.assertTrue(self._wait(lambda g: "sub" in g), f"got={self.got}")

    def test_hidden_ignored(self):
        (self.dir / ".secret.md").write_text("x", encoding="utf-8")
        (self.dir / "~$temp.md").write_text("x", encoding="utf-8")
        time.sleep(1.2)
        with self.lock:
            self.assertEqual(self.got, [])      # 隐藏事件连父目录都不该报

    def test_double_start_stop_idempotent(self):
        self.w.start()                      # 已活着：跳过
        self.w.stop()
        self.w.stop()                       # 未跑：不炸
        self.w.start()                      # 重启可再来
        (self.dir / "after-restart.md").write_text("x", encoding="utf-8")
        self.assertTrue(self._wait(lambda g: "" in g), f"got={self.got}")


if __name__ == "__main__":
    unittest.main()
