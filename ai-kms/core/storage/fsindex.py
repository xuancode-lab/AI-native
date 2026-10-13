"""文件系统 ↔ 知识库状态索引（纯函数：零 Qt、零 sqlite）。

资源管理器视图的"管道状态"判定与路径前缀操作的单一真源：
  vault_files 有行 = ATOMIC（已原子化），盘上有库里无 = RAW（原始待收录）
  ——状态是派生判定，不新增 schema 列。
目录改名/移动与单文件移动共用一套前缀语义（under_prefix 严格段匹配）；
store.rename_prefix 的 SQL 侧同样用这里的纯函数过滤——行为一致，
且刻意避开 SQL LIKE（SQLite LIKE 对 ASCII 大小写不敏感，"a/%" 会误伤 "A/x.md"）。
"""
from __future__ import annotations

from pathlib import PurePosixPath

from core.canvas import CANVAS_EXTS, FILE_EXTS, IMAGE_EXTS


class FileState:
    """vault 内文件的管道状态（徽标数据源）。"""
    ATOMIC = "atomic"          # 已入库：vault_files 有行
    RAW = "raw"                # 原始素材：盘上有、库里无，可收录
    CANVAS = "canvas"          # .canvas 白板
    IMAGE = "image"            # 图片附件
    OTHER = "other"            # 其它文件（.pdf 等：展示但无收录动作）
    FOLDER = "folder"
    FOLDER_EMPTY = "folder_empty"


STATE_CN = {"atomic": "已原子化", "raw": "原始文件（未收录）", "canvas": "白板",
            "image": "图片附件", "other": "文件",
            "folder": "文件夹", "folder_empty": "空文件夹"}


def normalize_rel(rel: str) -> str:
    """vault 相对路径校验归一：反斜杠→斜杠；拒空/绝对/盘符/"."/".."。违规 ValueError。"""
    r = str(rel).replace("\\", "/")
    if not r or r.startswith("/") or ":" in r.split("/", 1)[0]:
        raise ValueError(f"非法相对路径: {rel!r}")
    if any(p in ("", ".", "..") for p in r.split("/")):
        raise ValueError(f"非法相对路径: {rel!r}")
    return r


def parent_dir(rel: str) -> str:
    """'a/b/c.md'→'a/b'；'c.md'→''（根用空串约定，全模块一致）。"""
    p = str(PurePosixPath(normalize_rel(rel)).parent)
    return "" if p == "." else p


def under_prefix(rel: str, prefix: str) -> bool:
    """严格段匹配：prefix=''=全库；目录前缀不含 'ab/' 之于 'a/'；
    文件路径作前缀只匹自己（rename_prefix 因此单文件移动=前缀退化）。"""
    if prefix == "":
        return True
    return rel == prefix or rel.startswith(prefix + "/")


def apply_prefix_rename(rel: str, old: str, new: str) -> str | None:
    """rel 在 old 前缀下 → 换成 new 前缀的新路径；不在 → None。new='' 视为移出到根。"""
    if not under_prefix(rel, old):
        return None
    if old == "":
        return f"{new}/{rel}" if new else rel
    tail = rel[len(old):]                       # "" 或 "/xxx"
    return (new + tail) if tail else new


def file_state(rel: str, is_dir: bool, indexed, has_children: bool = True) -> str:
    """单条状态判定。indexed=已入库 rel 集合（调用方从 vault_files 快照）。"""
    if is_dir:
        return FileState.FOLDER if has_children else FileState.FOLDER_EMPTY
    low = rel.lower()
    if low.endswith(CANVAS_EXTS):
        return FileState.CANVAS
    if low.endswith(FILE_EXTS):
        return FileState.ATOMIC if rel in indexed else FileState.RAW
    if low.endswith(IMAGE_EXTS):
        return FileState.IMAGE
    return FileState.OTHER


def build_states(entries, indexed) -> dict[str, str]:
    """磁盘枚举 [(rel, is_dir), ...] → {rel: FileState}。
    两遍扫描：文件态先落，再据"谁的子项见过我"补目录空态（无需 has_children 预计算）。"""
    states: dict[str, str] = {}
    dirs_with_child: set[str] = set()
    for rel, is_dir in entries:
        if is_dir:
            states[rel] = FileState.FOLDER
            parent = parent_dir(rel)
            if parent:
                dirs_with_child.add(parent)
        else:
            states[rel] = file_state(rel, False, indexed)
            dirs_with_child.add(parent_dir(rel))
    for rel, is_dir in entries:
        if is_dir and rel not in dirs_with_child:
            states[rel] = FileState.FOLDER_EMPTY
    return states


def rewrite_doc_files(payload: dict, old_prefix: str, new_prefix: str
                      ) -> tuple[dict, int]:
    """.canvas 文档 JSON 的 node.file 引用级联（目录改名/移动三联之一）。

    payload=json.loads 后的文档对象；返回 (新对象, 改动数)，不改入参。
    只动 type=file 且 file 命中前缀的节点；group/边/其它字段原样。
    放在 fsindex 而非 core/canvas：canvas 是扩展名清单的单一真源，
    反向 import 会成环——前缀语义本就属于本模块。"""
    nodes = payload.get("nodes") or []
    changed = 0
    new_nodes = []
    for nd in nodes:
        if (isinstance(nd, dict) and nd.get("type") == "file"
                and isinstance(nd.get("file"), str)):
            nv = apply_prefix_rename(nd["file"], old_prefix, new_prefix)
            if nv is not None and nv != nd["file"]:
                nd = {**nd, "file": nv}
                changed += 1
        new_nodes.append(nd)
    if not changed:
        return payload, 0
    out = dict(payload)
    out["nodes"] = new_nodes
    return out, changed
