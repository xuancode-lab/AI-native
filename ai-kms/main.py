#!/usr/bin/env python
"""AI-native KMS 入口（CLI）。

用法:
  python main.py scan                  # 扫描 Vault 重建索引
  python main.py ingest <file|text>    # 收录单个文件/文本
  python main.py drop <dir>            # 监听目录，丢素材自动入库
  python main.py watch                 # 监听默认 dropbox
  python main.py search <kw>           # 原子关键词检索（返回相关文件）
  python main.py dump <rel_path>       # 打印某文件的判断/原子/双链
  python main.py batch <dir>           # 批量收录目录下所有 .md/.txt
  python main.py reindex               # 从 Vault 现有文件重建索引（DB 丢失后恢复）
  python main.py fts-rebuild           # FTS 全文索引对账重建（分批直到补齐）
  python main.py export [dir]          # 导出结构层 JSONL+图谱（缺省 data/exports/<时间戳>）
  python main.py export-full [dir]     # 全库打包 zip（原文 .md + 结构 + manifest）
  python main.py curator "<子命令…>"    # 知识策展：dry/scan/list/reread/apply/approve-high
  python main.py mcp                   # 启动 MCP 只读服务（stdio，给外部 AI 客户端）
  python main.py ask "<问题>"           # AI 管家 RAG 问答
  python main.py gui                   # 启动桌面端（PySide6）
"""
from __future__ import annotations

import argparse
import sys

from config import settings
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault


def build() -> tuple[SQLiteStore, Vault]:
    store = SQLiteStore()
    vault = Vault()
    return store, vault


def cmd_scan(store, vault):
    from core.atoms.index import AtomIndex
    idx = AtomIndex(store)
    files = vault.all_markdown_files()
    print(f"Vault 下 {len(files)} 个 .md")
    for f in files:
        interp = "已索引" if store.file_exists(f) else "未入库"
        print(f"  [{interp}] {f}")
    isolated = idx.isolated_files(files, set())
    if isolated:
        print("孤立笔记(无链接):", isolated)


def cmd_ingest(store, vault, arg):
    from core.ingest.pipeline import IngestPipeline
    pipe = IngestPipeline(store, vault)
    from pathlib import Path
    if Path(arg).exists():
        res = pipe.ingest_file(arg)
    else:
        res = pipe.ingest_raw(arg)
    if res["ok"]:
        print(f"✅ 入库: {res['rel_path']} | {res['type']} | 主题={res['topic']} | 原子={res['atoms']}")
    else:
        print(f"⏭  {res.get('reason')}")
        if "dup_of" in res:
            print(f"   重复，已存在: {res['dup_of']}")


def cmd_drop(store, vault, dir_arg):
    from pathlib import Path
    from core.ingest.pipeline import IngestPipeline
    from core.ingest.watcher import DropWatcher
    d = Path(dir_arg)
    pipe = IngestPipeline(store, vault)
    w = DropWatcher(d, store, pipe)
    n = w.ingest_existing()
    print(f"启动收录已有 {n} 篇，监听中 {d}  (Ctrl+C 退出)")
    w.start()
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        w.stop()


def cmd_watch(store, vault):
    cmd_drop(store, vault, str(settings.DROPBOX_DIR))


def cmd_batch(store, vault, dir_arg):
    from core.ingest.pipeline import IngestPipeline
    pipe = IngestPipeline(store, vault)
    res = pipe.ingest_dir(dir_arg)
    if not res.get("ok"):
        print("❌", res.get("reason"))
        return
    print(f"批量收录完成：入库 {res['ingested']} · 重复 {res['duplicate']} "
          f"· 空 {res['empty']} · 错误 {res['errors']}")
    for d in res["details"]:
        print("  ", d)


def cmd_reindex(store, vault):
    from core.ingest.pipeline import IngestPipeline
    pipe = IngestPipeline(store, vault)
    stats = pipe.reindex()
    print(f"重建索引完成：扫描 {stats['scanned']} · 新建 {stats['indexed']} · 跳过 {stats['skipped']}")


def cmd_fts_rebuild(store, vault):
    from core.ingest.pipeline import IngestPipeline
    pipe = IngestPipeline(store, vault)
    if not store.fts_ok:
        print("❌ 本机 SQLite 不支持 FTS5，检索将一直走旧路径")
        return
    total = 0
    while True:
        r = pipe.fts_rebuild_chunk(limit=200)
        total += r["indexed"]
        print(f"  已重建 {r['indexed']} · 幽灵行清除 {r['removed_ghost']} · 剩余 {r['remaining']}")
        if not r["remaining"]:
            break
    n_fts, n_vault = store.fts_health()
    print(f"FTS 重建完成：{total} 篇 · 当前 fts={n_fts} / 登记={n_vault}")


def cmd_export(store, vault, arg, full: bool):
    """导出派生资产（core/export.py）：structure 只导结构，full 打全库 zip。"""
    from datetime import datetime
    from pathlib import Path
    from core.export import export_full, export_structure
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(arg) if arg else settings.EXPORT_DIR / stamp
    if full:
        zp = export_full(store, vault, out)
        print(f"全库打包完成 → {zp}（{zp.stat().st_size / 1024:.0f} KB）")
        return
    m = export_structure(store, vault, out)
    c = m["counts"]
    print(f"结构导出完成 → {out / 'structure'}")
    print(f"  {c['files']} 篇 · {c['atoms']} 原子 · {c['links']} 链 · "
          f"{c['classifications']} 分类 · {c['suggestions']} 建议 · "
          f"图谱 {c['graph_nodes']} 节点 / {c['graph_edges']} 边")


def cmd_curator(store, vault, arg):
    """知识策展 CLI（与 GUI 审阅队列共用 suggestions 表）。

    curator dry <wikilinks|topic>        预扫描报数（分类校准含 needs_reread 计数）
    curator scan <wikilinks|topic> [n]   生成建议进队列（n=每批，跑完为止）
    curator list [kind]                  列出 pending 建议
    curator reread [n]                   对 needs_reread 项显式重读校准（先报数）
    curator apply <id> [新目标]           采纳一条（可选覆盖目标）
    curator approve-high [阈值]          批量采纳 ≥阈值（默认 0.85）
    """
    from core.curator import Curator
    from core.ingest.pipeline import IngestPipeline
    parts = (arg or "help").split()
    action = parts[0]
    cur = Curator(store, vault, IngestPipeline(store, vault))
    scope_all = {"type": "all"}

    def _scan_loop(kind):
        paths = cur.resolve_scope(scope_all)
        while paths:
            fn = cur.wikilinks_scan if kind == "wikilinks" else cur.recalibrate_scan
            r = fn(paths, limit=20)
            paths = paths[r["scanned"]:]
            print(f"  scanned {r['scanned']} · added {r['added']} · 剩 {r['remaining']}")

    if action == "dry":
        kind = parts[1] if len(parts) > 1 else "wikilinks"
        r = (cur.wikilinks_dry_run(scope_all) if kind == "wikilinks"
             else cur.recalibrate_dry_run(scope_all))
        print(r)
    elif action == "scan":
        _scan_loop(parts[1] if len(parts) > 1 else "wikilinks")
        print(f"pending 建议共 {store.count_pending()} 条")
    elif action == "list":
        kind = parts[1] if len(parts) > 1 else None
        for s in store.pending_suggestions(kind=kind, limit=200):
            print(f"  #{s['id']} [{s['kind']}] conf={s['confidence']:.2f} "
                  f"{s['file_path']} → {s['target']}")
    elif action == "reread":
        rows = [s for s in store.pending_suggestions(limit=2000)
                if s["kind"] == "topic" and s["payload"].get("needs_reread")]
        rels = sorted({r["file_path"] for r in rows})[:int(parts[1]) if len(parts) > 1 else 50]
        if not rels:
            print("没有 needs_reread 项")
            return
        print(f"将重读 {len(rels)} 篇（用户已授权）…")
        print(cur.reread_recalibrate(rels))
    elif action == "apply":
        if len(parts) < 2:
            print("用法: curator apply <id> [新目标]")
            return
        sid = int(parts[1])
        print(cur.apply_suggestion(sid, parts[2] if len(parts) > 2 else None))
    elif action == "approve-high":
        th = float(parts[1]) if len(parts) > 1 else 0.85
        rows = store.pending_suggestions(min_conf=th, limit=1000)
        ok = sum(1 for s in rows if cur.apply_suggestion(s["id"]).get("ok"))
        print(f"阈值≥{th}: 采纳 {ok}/{len(rows)}（其余留 pending，见日志）")
    else:
        print(cmd_curator.__doc__)


def cmd_ask(store, vault, question):
    from core.aipilot.manager import AIPilot
    pilot = AIPilot(store, vault, on_progress=lambda m: print(m, file=sys.stderr))
    print(pilot.ask(question))


def cmd_search(store, vault, kw):
    from core.atoms.index import AtomIndex
    idx = AtomIndex(store)
    rows = store.search_atoms(kw)
    if not rows:
        print("无匹配原子")
        return
    seen = {}
    for r in rows:
        seen.setdefault(r["file_path"], []).append(f"{r['kind']}:{r['value']}")
    for path, atoms in seen.items():
        print(f"  {path}  <- {atoms[:4]}")


def cmd_dump(store, vault, rel_path):
    nfile = store.get_file(rel_path)
    if not nfile:
        print("未入库:", rel_path)
        return
    print("文件:", rel_path)
    print(" 主题/类型/优先级:", nfile["topic"], nfile["type"], nfile["priority"])
    ats = store.atoms_for(rel_path)
    print(" 原子数:", len(ats), [a["kind"] for a in ats][:10])
    links = store.links_for(rel_path)
    print(" 双链→:", [l["to_target"] for l in links])


def _launch_gui():
    from app.main_window import run
    raise SystemExit(run())


def main():
    ap = argparse.ArgumentParser(prog="kms", description="AI-native KMS")
    ap.add_argument("cmd", nargs="?", default="scan")
    ap.add_argument("arg", nargs="?")
    a = ap.parse_args()

    if a.cmd == "mcp":
        # 只读 MCP：必须早退在 build() 之前——否则可写 store 会静默建空库 schema，
        # 把"库不存在"的错误吞掉。字典分发里不放 mcp。
        from core.mcp_server import run_stdio_server
        run_stdio_server()
        return
    store, vault = build()
    fn = {
        "scan": lambda: cmd_scan(store, vault),
        "ingest": lambda: cmd_ingest(store, vault, a.arg or ""),
        "drop": lambda: cmd_drop(store, vault, a.arg or str(settings.DROPBOX_DIR)),
        "watch": lambda: cmd_watch(store, vault),
        "search": lambda: cmd_search(store, vault, a.arg or ""),
        "dump": lambda: cmd_dump(store, vault, a.arg or ""),
        "batch": lambda: cmd_batch(store, vault, a.arg or str(settings.DROPBOX_DIR)),
        "reindex": lambda: cmd_reindex(store, vault),
        "fts-rebuild": lambda: cmd_fts_rebuild(store, vault),
        "export": lambda: cmd_export(store, vault, a.arg, full=False),
        "export-full": lambda: cmd_export(store, vault, a.arg, full=True),
        "curator": lambda: cmd_curator(store, vault, a.arg or ""),
        "ask": lambda: cmd_ask(store, vault, a.arg or ""),
        "gui": lambda: _launch_gui(),
    }.get(a.cmd, lambda: (print("未知命令"), sys.exit(1)))
    fn()


if __name__ == "__main__":
    main()