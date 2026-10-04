"""版本快照 + 人工确认开关。

AI 对笔记的每次写回操作：
  1) 强制做版本快照（写入快照表，不碰 Vault 原始文件）
  2) 若 settings.HUMAN_CONFIRM_WRITES=True，抛 ConfirmationNeeded 供 UI 弹确认框
  3) 确认后写回；否则直接覆盖
可追溯：list_snapshots / rollback。
"""
from __future__ import annotations

from config import settings
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault


class ConfirmationNeeded(Exception):
    """AI 写回前需人工确认时抛出，UI 捕获后弹确认框。"""

    def __init__(self, rel_path: str, new_content: str, reason: str, preview: str = ""):
        super().__init__(f"待确认：{rel_path}")
        self.rel_path = rel_path
        self.new_content = new_content
        self.reason = reason
        self.preview = preview or new_content[:500]


class SnapshotManager:
    def __init__(self, store: SQLiteStore, vault: Vault | None = None):
        self.store = store
        self.vault = vault or Vault()

    def snapshot(self, rel_path: str, reason: str) -> int:
        """为当前文件做快照（返回 snapshot id）。"""
        content = self.vault.read_note(rel_path)
        if not content:
            return -1
        self.store.snapshot(rel_path, content, reason)
        row = self.store.conn.execute(
            "SELECT MAX(id) AS id FROM snapshots WHERE file_path=?", (rel_path,)
        ).fetchone()
        return int(row["id"]) if row else -1

    def write(self, rel_path: str, new_content: str, reason: str,
              require_confirm: bool | None = None) -> dict:
        """写回笔记（先快照，再按开关决定是否弹确认）。

        返回 {"ok": bool, "status": str, "snapshot_id": int,
              "confirm_pending": bool}。
        """
        snap_id = self.snapshot(rel_path, reason)
        need_confirm = (
            require_confirm
            if require_confirm is not None
            else settings.HUMAN_CONFIRM_WRITES
        )
        if need_confirm:
            raise ConfirmationNeeded(rel_path, new_content, reason)

        self.vault.write_note(rel_path, new_content)
        self.store.log("info", "aipilot",
                       f"write_back {rel_path} snapshot={snap_id} reason={reason}")
        return {"ok": True, "status": "written", "snapshot_id": snap_id,
                "confirm_pending": False}

    def list_snapshots(self, rel_path: str) -> list[dict]:
        rows = self.store.conn.execute(
            "SELECT id, created_at, reason, length(content) AS size FROM snapshots "
            "WHERE file_path=? ORDER BY id DESC",
            (rel_path,),
        ).fetchall()
        return [dict(r) for r in rows]

    def rollback(self, snapshot_id: int) -> str | None:
        """回滚到指定快照，返回恢复的快照原因。"""
        row = self.store.conn.execute(
            "SELECT * FROM snapshots WHERE id=?", (snapshot_id,)
        ).fetchone()
        if not row:
            return None
        # 先快照当前状态，避免覆盖丢失
        self.snapshot(row["file_path"], f"rollback to {snapshot_id}")
        self.vault.write_note(row["file_path"], row["content"])
        self.store.log("info", "aipilot",
                       f"rollback {row['file_path']} -> snapshot {snapshot_id}")
        return row["reason"]