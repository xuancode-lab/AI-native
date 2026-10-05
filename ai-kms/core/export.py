"""结构层导出：把 SQLite 派生数据沉淀为可移植资产文件（"终身复用"的资产化出口）。

两种模式：
  export_structure —— 只导结构（JSONL + graph.json + manifest.json）到目录
  export_full      —— 全库打包 zip（vault 全部 .md + 结构目录 + manifest）

约定（对应"数据不锁定"承诺）：
  - path 一律是 vault 相对 POSIX 路径，与全系统 path key 一致；
  - 自增 id 不进资产（本机实现细节，跨库无意义）；
  - 输出顺序确定（按 path/kind/value 排序），同一库两次导出内容一致；
  - zip 内布局与目录模式逐字一致（manifest.json + structure/ + vault/），
    解压即结构导出目录，消费方一套解析代码。
"""
from __future__ import annotations

import json
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from core.graph.engine import GraphEngine

EXPORT_FORMAT_VERSION = 1          # 资产格式版本，消费方按此判别 schema


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _write_jsonl(path: Path, rows) -> int:
    """逐行写 JSONL，返回行数。"""
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def graph_payload(store) -> dict:
    """GraphEngine.build() 的 edges 是节点下标（GUI 渲染内部格式），资产里必须自包含：
    转成 source/target = path 字符串。"""
    g = GraphEngine(store).build()
    nodes = g["nodes"]
    edges = [{"source": nodes[e["source"]]["id"],
              "target": nodes[e["target"]]["id"],
              "kind": e["kind"]} for e in g["edges"]]
    return {"generated_at": _now(),
            "params": {"min_shared_keywords": 2},
            "nodes": nodes, "edges": edges, "isolated": g["isolated"]}


def _export_structure_into(store, vault, staging: Path) -> dict:
    """把结构文件写进 staging 目录（manifest.json + structure/*），返回 manifest。"""
    sdir = staging / "structure"
    sdir.mkdir(parents=True, exist_ok=True)

    files_rows = [dict(r) for r in store.all_files()]
    _write_jsonl(sdir / "files.jsonl", files_rows)
    _write_jsonl(sdir / "atoms.jsonl", store.iter_atoms())
    _write_jsonl(sdir / "links.jsonl", store.dump_links())
    _write_jsonl(sdir / "classifications.jsonl", store.dump_classifications())
    _write_jsonl(sdir / "categories.jsonl", store.all_categories())
    _write_jsonl(sdir / "suggestions.jsonl", store.dump_suggestions())

    graph = graph_payload(store)
    (sdir / "graph.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = store.table_counts()          # 口径与上面各文件行数一致
    counts["graph_nodes"] = len(graph["nodes"])
    counts["graph_edges"] = len(graph["edges"])
    manifest = {
        "app": "ai-kms",
        "export_format": EXPORT_FORMAT_VERSION,
        "mode": "structure",
        "exported_at": _now(),
        "sqlite_fts_ok": bool(store.fts_ok),
        "vault_root_name": Path(vault.vault_path).name,
        "includes_vault_md": False,
        "counts": counts,
        "files": ["manifest.json"] + [f"structure/{p.name}" for p in sorted(sdir.iterdir())],
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def export_structure(store, vault, out_dir: Path) -> dict:
    """只导结构：out_dir 下生成 manifest.json + structure/。返回 manifest。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return _export_structure_into(store, vault, out_dir)


def export_full(store, vault, out_dir: Path) -> Path:
    """全库打包：zip = manifest.json + structure/ + vault/（全部 .md 原文）。返回 zip 路径。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = out_dir / f"kms-vault-{stamp}.zip"
    with tempfile.TemporaryDirectory(prefix="kms_export_") as td:
        staging = Path(td)
        manifest = _export_structure_into(store, vault, staging)
        manifest["mode"] = "full"
        manifest["includes_vault_md"] = True
        rels = sorted(vault.all_markdown_files())
        manifest["files"] += [f"vault/{r}" for r in rels]
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(staging / "manifest.json", "manifest.json")
            for p in sorted((staging / "structure").iterdir()):
                zf.write(p, f"structure/{p.name}")
            # 中文/正斜杠 arcname：Python zipfile 对非 ASCII 名自动 UTF-8 标志位
            for rel in rels:
                zf.writestr(f"vault/{rel}", vault.read_note(rel))
    return zip_path
