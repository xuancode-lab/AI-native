"""AI 管家：把任务/快照/检索串成对外能力。

对外只暴露一组"读-or-写"方法，写操作统一走 SnapshotManager 保证快照+确认。
"""
from __future__ import annotations

from core.generative.tasks import AITasks
from core.aipilot.snapshot import SnapshotManager, ConfirmationNeeded
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault


class AIPilot:
    def __init__(self, store: SQLiteStore, vault: Vault | None = None,
                 on_progress=None):
        self.store = store
        self.vault = vault or Vault()
        self.tasks = AITasks(store, self.vault, on_progress=on_progress)
        self.snapshots = SnapshotManager(store, self.vault)
        # MockProvider 的离线检索需要挂上原子索引
        from core.generative.provider import MockProvider, get_provider
        p = get_provider()
        if isinstance(p, MockProvider):
            from core.atoms.index import AtomIndex
            p.attach_atoms_index(AtomIndex(store))

    # ---------- 只读能力（不写库）----------
    def ask(self, question: str, extra_paths: list[str] | None = None) -> str:
        """基于知识库做 RAG 问答。extra_paths 为挂载笔记（上下文置顶）。"""
        return self.tasks.qa(question, extra_paths=extra_paths)

    def summarize_note(self, rel_path: str) -> str:
        return self.tasks.summarize(rel_path)

    def polish_preview(self, rel_path: str) -> dict:
        """润色预览：返回 {'original', 'polished'}，不写回。"""
        original = self.vault.read_note(rel_path)
        polished = self.tasks.polish(rel_path)
        return {"original": original, "polished": polished}

    def detect_contradicts(self, rel_path: str) -> list[dict]:
        """矛盾检测：返回 [{'against', 'issue', 'severity', 'suggestion'}, ...]。"""
        return self.tasks.contradict(rel_path)

    # ---------- 写回能力（强制快照 + 确认开关）----------
    def apply_change(self, rel_path: str, new_content: str, reason: str,
                     require_confirm: bool | None = None) -> dict:
        """把一段内容写回笔记（先快照）。

        若 settings.HUMAN_CONFIRM_WRITES=True，会抛 ConfirmationNeeded，
        由 GUI 弹确认框，用户点'确认'后再调 confirm_change()。
        """
        return self.snapshots.write(rel_path, new_content, reason,
                                    require_confirm=require_confirm)

    def confirm_change(self, rel_path: str, new_content: str, reason: str) -> dict:
        """用户点了确认框 → 真正写回。"""
        return self.snapshots.write(rel_path, new_content, reason,
                                    require_confirm=False)

    def polish_and_apply(self, rel_path: str) -> dict:
        """一键润色并写回（预览→写）。"""
        preview = self.polish_preview(rel_path)
        return self.apply_change(rel_path, preview["polished"], "AI润色")

    def history(self, rel_path: str) -> list[dict]:
        return self.snapshots.list_snapshots(rel_path)

    def rollback(self, snapshot_id: int) -> str | None:
        return self.snapshots.rollback(snapshot_id)