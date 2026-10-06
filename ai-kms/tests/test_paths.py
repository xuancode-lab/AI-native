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
        # 非法字符路径必然不可写 → 降级到 LOCALAPPDATA/AI-Native KMS
        bogus = Path("C:/nul_bad<>|chars")
        home = settings._app_home(bogus)
        # 平台无关：应落到各平台标准用户目录（Win=LOCALAPPDATA / Mac=AppSupport / Linux=xdg）
        self.assertEqual(home, settings._fallback_home())
        self.assertTrue(str(home).endswith("AI-Native KMS"))

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
