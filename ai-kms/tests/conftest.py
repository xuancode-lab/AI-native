"""共享测试夹具：独立的临时 Vault + SQLite。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault


class TempEnv:
    def __init__(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="kms_test_"))
        self.db_path = self._tmp / "t.db"
        self.vault_path = self._tmp / "vault"
        self.vault_path.mkdir()
        self.store = SQLiteStore(self.db_path)
        self.vault = Vault(self.vault_path)

    def setup(self):
        return self

    def teardown(self):
        self.store.close()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)