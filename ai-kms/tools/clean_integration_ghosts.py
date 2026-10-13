"""清理工具：删除集成调试脚本误写入真实 SQLite 库的幽灵笔记行。

背景：阶段 C 的 MainWindow 级联调用试脚本 monkey-patch 了 settings.DEFAULT_DB_PATH
（属性名不存在，真实为 DB_PATH），导致 upsert_file 等写进了用户真实库，留下
"磁盘无文件、库里挂名"的幽灵笔记。本脚本只清理测试白名单内的路径；
白名单外的缺失文件行一律不动，交人工判断。

用法：
    python tools/clean_integration_ghosts.py           # 只盘点
    python tools/clean_integration_ghosts.py --apply   # 实际删除（复用 SQLiteStore.delete_file 级联）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from core.storage.sqlite_db import SQLiteStore

TEST_WHITELIST = {
    "技术/某篇.md", "技术/乙篇.md", "技术/乙篇2.md", "技术/甲篇.md",
    "学习/乙篇.md", "学习/丙篇.md", "技术/丙篇.md", "技术/丁篇.md",
}
TEST_CANVAS_SNAPSHOTS = {          # 调试脚本在真实库 snapshots 表留下的画布快照 rel（磁盘无此文件才删）
    "季度计划.canvas", "改名计划.canvas", "t.canvas", "白板/计划.canvas",
    "集成.canvas", "钩子.canvas", "画布A.canvas", "画布B.canvas", "画布C.canvas",
    "d.canvas", "m.canvas", "画布X.canvas",
}


def main(apply: bool) -> None:
    vault = Path(settings.DEFAULT_VAULT_PATH)
    store = SQLiteStore()          # 真实库
    ghosts = [r["path"] for r in store.all_files()
              if r["path"] in TEST_WHITELIST and not (vault / r["path"]).exists()]
    other_missing = [r["path"] for r in store.all_files()
                     if r["path"] not in TEST_WHITELIST and not (vault / r["path"]).exists()]
    print("白名单幽灵（将清理）:", ghosts)
    print("白名单外缺失行（不动，请人工确认）:", other_missing)
    snap_rows = store.conn.execute(
        "SELECT id, file_path, reason FROM snapshots "
        "WHERE file_path LIKE '%.canvas'").fetchall()
    snap_kill = [dict(r) for r in snap_rows
                 if r["file_path"] in TEST_CANVAS_SNAPSHOTS
                 and not (vault / r["file_path"]).exists()]
    print("测试画布快照行（将清理）:", [(s["file_path"], s["reason"]) for s in snap_kill])
    if not apply:
        print("（dry-run：加 --apply 执行）")
        return
    for rel in ghosts:
        store.delete_file(rel)     # 级联：vault_files/atoms/links/classifications/notes_fts + pending→obsolete
        print("已清理笔记幽灵:", rel)
    for s in snap_kill:
        store.conn.execute("DELETE FROM snapshots WHERE id=?", (s["id"],))
    store.conn.commit()
    print("已清理画布快照行:", len(snap_kill))
    store.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
