"""数据路径解析单测：便携默认 / env 覆盖 / 不可写降级 AppData。"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from config import settings


class TestPathResolution(unittest.TestCase):
    def test_default_is_app_home_data(self):
        home = Path("D:/app")
        self.assertEqual(settings.resolve_data_root({}, home), home / "data")

    def test_env_override_wins(self):
        home = Path("D:/app")
        got = settings.resolve_data_root({"KMS_DATA_ROOT": "D:/MyKnowledge"}, home)
        self.assertEqual(got, Path("D:/MyKnowledge").resolve())

    def test_override_accepts_path_object(self):
        home = Path("D:/app")
        got = settings.resolve_data_root({"KMS_DATA_ROOT": Path.home() / "km"}, home)
        self.assertTrue(str(got).endswith("km"))

    def test_app_home_falls_back_when_not_writable(self):
        # 不依赖路径字法的平台差异（"C:/nul<>|" 在 mac 上是合法相对路径，
        # 非法字符探测只在 Windows 成立）→ 直接 monkeypatch 可写探测，三平台同一语义
        orig = settings._is_writable
        settings._is_writable = lambda d: False
        try:
            home = settings._app_home(Path("X:/somewhere"))
            self.assertEqual(home, settings._fallback_home())
            self.assertTrue(str(home).endswith("AI-Native KMS"))
        finally:
            settings._is_writable = orig

    def test_fallback_home_platform_branch(self):
        """各 runner 上验证自己平台的分支正确（Mac=AppSupport / Win=AppData）。"""
        import platform
        fb = str(settings._fallback_home())
        sysname = platform.system()
        if sysname == "Darwin":
            self.assertIn("Application Support", fb)
        elif sysname == "Windows":
            self.assertIn("AppData", fb)
        self.assertTrue(fb.endswith("AI-Native KMS"))

    def test_app_home_stays_when_writable(self, ):
        tmp = Path(os.environ.get("TEMP", ".")) / "kms_writable_probe_dir"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            self.assertEqual(settings._app_home(tmp), tmp)
        finally:
            tmp.rmdir()

    def test_settings_dev_paths_consistent(self):
        """开发态：DATA_ROOT 在源码 data/ 下，派生目录齐全且已创建。"""
        self.assertEqual(settings.DB_PATH, settings.DATA_ROOT / "kms.db")
        for d in (settings.LOG_DIR, settings.SNAPSHOT_DIR,
                  settings.DROPBOX_DIR, settings.EXPORT_DIR):
            self.assertTrue(d.exists())


class TestVaultPathResolution(unittest.TestCase):
    def test_default_under_data_root(self):
        dr = Path("D:/app/data")
        self.assertEqual(settings.resolve_vault_path({}, dr), dr / "vault")

    def test_env_override_independent(self):
        dr = Path("D:/app/data")
        got = settings.resolve_vault_path({"KMS_VAULT_PATH": "D:/Docs/Vault"}, dr)
        self.assertEqual(got, Path("D:/Docs/Vault"))


if __name__ == "__main__":
    unittest.main()
