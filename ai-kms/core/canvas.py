"""白板画布文档模型（JSON Canvas 1.0）：宽容解析 / 透传序列化 / 编辑面。

设计纪律：
  - 无 Qt、无 DB——持久化只跟注入的 Vault 交互（write_note/read_note 纯文本读写）；
    "边→建议"的生命周期全在 Curator，本模块不 import。
  - 互通铁律（jsoncanvas 1.0）：读方必须忽略不认识的 node/edge 类型与字段。
    推论：我方写回必须**原样透传** Obsidian 可能写入的 group/link 节点与未知字段，
    否则"存一次盘毁掉对方白板"。混合模型 = 已知字段强类型 + extra dict 兜底。
  - 坐标为场景坐标（任意浮点，可负）；本应用卡片默认统一 240×120，
    width/height 照常写进 spec 字段，Obsidian 打开尺寸一致。

schema 速记（spec 键名 → 本模型属性）：
  node: {id, type, x, y, width, height, color?, file|text|url|group?, label?} + 未知字段透传
  edge: {id, fromNode, toNode, fromSide?, toSide?, fromEnd?, toEnd?, label?, color?} + 透传
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

CANVAS_EXTS = (".canvas",)
DEFAULT_W, DEFAULT_H = 240.0, 120.0
GROUP_PAD = 24.0                    # 成组包围盒外扩
FILE_EXTS = (".md", ".txt", ".rst")          # 可成笔记卡的文本类型（原在 UI 层，上移统一）
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")
ATTACH_DIR = "附件"                           # 外拖图片拷入 vault 的目标目录（Obsidian 兼容：file 字段只认相对路径）
COLOR_HEX = {"1": "#e93147", "2": "#ec0a8e", "3": "#a337ea",
             "4": "#325bf3", "5": "#00bf86", "6": "#e8e04c"}

_NODE_PROPS = frozenset({"color", "label", "text", "width", "height", "file"})
_EDGE_PROPS = frozenset({"color", "label", "from_side", "to_side", "from_end", "to_end"})


def color_hex(c: str | None) -> str | None:
    """spec 颜色值归一化：'#RRGGBB' 原样、色板号 1..6 → hex，其余 → None。"""
    if not c:
        return None
    if c.startswith("#"):
        return c
    return COLOR_HEX.get(c)


def content_fp(text: str) -> str | None:
    """冲突检测文档指纹：全文 sha1，空白/缩进敏感。
    ⚠ 不可复用 Vault.make_dedup_key——它压空白+截 200 字符，两份不同 JSON 可能同指纹。"""
    if not text:
        return None
    return hashlib.sha1(text.encode("utf-8")).hexdigest()

# spec 已定义键（to_json 固定输出顺序用）；其余键进 extra 原样回写
_NODE_KNOWN = ("id", "type", "x", "y", "width", "height",
               "color", "file", "text", "url", "group", "label")
_EDGE_KNOWN = ("id", "fromNode", "toNode", "fromSide", "toSide",
               "fromEnd", "toEnd", "label", "color")


class CanvasParseError(Exception):
    """整文件 JSON 层面不可解析（语法坏 / 顶层不是对象）。"""


def _new_id() -> str:
    return uuid4().hex[:16]


def _num(v) -> float | None:
    """spec 数值校验：int/float 收，bool/字符串拒（返回 None 让调用方判缺）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


@dataclass
class CanvasNode:
    id: str
    type: str                    # 已知：file|text|link|group；未知类型照样建模透传
    x: float
    y: float
    width: float = DEFAULT_W
    height: float = DEFAULT_H
    color: str | None = None     # hex "#RRGGBB" 或色板 "1".."6"，原样透传
    file: str | None = None      # type=file：vault 根相对 posix 路径
    text: str | None = None      # type=text：markdown 字符串
    url: str | None = None       # type=link（本应用只透传不渲染）
    group: str | None = None     # type=group 嵌套父 id（1.1 前向兼容，只透传）
    label: str | None = None
    extra: dict = field(default_factory=dict)   # 未知字段：原样读、原样写

    @classmethod
    def from_json(cls, d: dict) -> "CanvasNode":
        nid, ntype = d.get("id"), d.get("type")
        if not isinstance(nid, str) or not nid or not isinstance(ntype, str):
            raise ValueError("node 缺 id/type")
        x, y = _num(d.get("x")), _num(d.get("y"))
        w = _num(d.get("width"))
        h = _num(d.get("height"))
        if None in (x, y, w, h):
            raise ValueError("node 缺数值几何字段")
        kw = {}
        for k in ("color", "file", "text", "url", "group", "label"):
            v = d.get(k)
            kw[k] = v if isinstance(v, str) else None
        return cls(id=nid, type=ntype, x=x, y=y, width=w, height=h,
                   extra={k: v for k, v in d.items() if k not in _NODE_KNOWN},
                   **kw)

    def to_json(self) -> dict:
        out = {"id": self.id, "type": self.type,
               "x": self.x, "y": self.y, "width": self.width, "height": self.height}
        for k in ("file", "text", "url", "group", "color", "label"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        out.update(self.extra)
        return out


@dataclass
class CanvasEdge:
    id: str
    from_node: str
    to_node: str
    from_side: str | None = None     # top|right|bottom|left；None=自动
    to_side: str | None = None
    from_end: str | None = None      # "none"|"arrow"
    to_end: str | None = None
    label: str | None = None
    color: str | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_json(cls, d: dict) -> "CanvasEdge":
        eid = d.get("id")
        f, t = d.get("fromNode"), d.get("toNode")
        if not isinstance(eid, str) or not eid:
            raise ValueError("edge 缺 id")
        if not isinstance(f, str) or not isinstance(t, str):
            raise ValueError("edge 缺端点")

        def _s(k):
            v = d.get(k)
            return v if isinstance(v, str) else None

        return cls(id=eid, from_node=f, to_node=t,
                   from_side=_s("fromSide"), to_side=_s("toSide"),
                   from_end=_s("fromEnd"), to_end=_s("toEnd"),
                   label=_s("label"), color=_s("color"),
                   extra={k: v for k, v in d.items() if k not in _EDGE_KNOWN})

    def to_json(self) -> dict:
        out = {"id": self.id, "fromNode": self.from_node, "toNode": self.to_node}
        for key, attr in (("fromSide", "from_side"), ("toSide", "to_side"),
                          ("fromEnd", "from_end"), ("toEnd", "to_end"),
                          ("label", "label"), ("color", "color")):
            v = getattr(self, attr)
            if v is not None:
                out[key] = v
        out.update(self.extra)
        return out


@dataclass
class CanvasDocument:
    rel_path: str | None = None
    nodes: list[CanvasNode] = field(default_factory=list)
    edges: list[CanvasEdge] = field(default_factory=list)
    dirty: bool = False
    disk_fp: str | None = None       # 磁盘基线指纹（C1 冲突检测；None=认为盘上无此文件）

    # ---------- 查找 ----------
    def node(self, node_id: str) -> CanvasNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def edge(self, edge_id: str) -> CanvasEdge | None:
        return next((e for e in self.edges if e.id == edge_id), None)

    def edges_of(self, node_id: str) -> list[CanvasEdge]:
        return [e for e in self.edges if node_id in (e.from_node, e.to_node)]

    @property
    def is_empty(self) -> bool:
        return not self.nodes and not self.edges

    # ---------- 解析 / 序列化 ----------
    @classmethod
    def loads(cls, rel_path: str | None, text: str) -> tuple["CanvasDocument", dict]:
        """宽容解析：整文件坏 → CanvasParseError；单条坏 → 跳过并计入 report，不连坐。"""
        try:
            data = json.loads(text.lstrip("﻿"))
        except (json.JSONDecodeError, TypeError) as exc:
            raise CanvasParseError(str(exc)) from exc
        if not isinstance(data, dict):
            raise CanvasParseError("顶层不是 JSON 对象")
        doc = cls(rel_path=rel_path)
        report = {"skipped_nodes": [], "skipped_edges": []}
        for i, nd in enumerate(data.get("nodes") or []):
            if isinstance(nd, dict):
                try:
                    doc.nodes.append(CanvasNode.from_json(nd))
                    continue
                except ValueError:
                    pass
            report["skipped_nodes"].append(i)
        for i, ed in enumerate(data.get("edges") or []):
            if isinstance(ed, dict):
                try:
                    doc.edges.append(CanvasEdge.from_json(ed))
                    continue
                except ValueError:
                    pass
            report["skipped_edges"].append(i)
        doc.disk_fp = content_fp(text)
        return doc, report

    def dumps(self) -> str:
        """UTF-8 可读 JSON：中文不转义；未知字段/未知类型条目已在模型里原样保留。"""
        payload = {"nodes": [n.to_json() for n in self.nodes],
                   "edges": [e.to_json() for e in self.edges]}
        return json.dumps(payload, ensure_ascii=False, indent=2)

    # ---------- 文件 IO（Vault 注入；快照/版本由上层走 SnapshotManager） ----------
    @classmethod
    def open(cls, vault, rel_path: str) -> tuple["CanvasDocument", dict]:
        text = vault.read_note(rel_path)
        if not text:                                   # 不存在/空文件 → 视为空文档
            return cls(rel_path=rel_path), {"skipped_nodes": [], "skipped_edges": []}
        return cls.loads(rel_path, text)

    def save(self, vault) -> str:
        if not self.rel_path:
            raise ValueError("文档无路径，无法保存")
        text = self.dumps()
        vault.write_note(self.rel_path, text)
        self.finalize_save(text)
        return text

    def new_document(self) -> None:
        """就地清为空文档（新建/重置用），保持对象引用不变。"""
        self.nodes, self.edges, self.dirty = [], [], True
        self.disk_fp = None

    # ---------- 磁盘基线（C1 冲突检测） ----------
    def finalize_save(self, text: str) -> None:
        """落盘成功后统一记账：不脏了，且磁盘指纹=刚写出去的文本。"""
        self.dirty = False
        self.disk_fp = content_fp(text)

    def stale_on_disk(self, cur_fp: str | None) -> bool:
        """盘上现文本指纹与内存基线不一致 → 外部改过。"""
        return cur_fp != self.disk_fp

    # ---------- 样式属性批量接口（M-C0 基建） ----------
    def set_node_props(self, ids: list[str], props: dict) -> dict[str, dict]:
        """按白名单统一改节点属性，返回旧值快照 {id: {k: old}}（撤销栈回放用）。"""
        bad = set(props) - _NODE_PROPS
        if bad:
            raise ValueError(f"不支持的节点属性: {sorted(bad)}")
        old: dict[str, dict] = {}
        for nid in ids:
            n = self.node(nid)
            if n is None:
                raise ValueError(f"节点不存在: {nid}")
            if "text" in props and n.type != "text":
                raise ValueError("text 仅 type=text 卡片可改")
            snap = {}
            for k, v in props.items():
                snap[k] = getattr(n, k)
                setattr(n, k, float(v) if k in ("width", "height") else v)
            old[nid] = snap
        if ids:
            self.dirty = True
        return old

    def set_edge_props(self, ids: list[str], props: dict) -> dict[str, dict]:
        bad = set(props) - _EDGE_PROPS
        if bad:
            raise ValueError(f"不支持的边属性: {sorted(bad)}")
        old: dict[str, dict] = {}
        for eid in ids:
            e = self.edge(eid)
            if e is None:
                raise ValueError(f"边不存在: {eid}")
            snap = {}
            for k, v in props.items():
                snap[k] = getattr(e, k)
                setattr(e, k, v)
            old[eid] = snap
        if ids:
            self.dirty = True
        return old

    # ---------- 编辑面（全部自动置 dirty） ----------
    def add_file_card(self, file_rel: str, x: float, y: float,
                      width: float = DEFAULT_W, height: float = DEFAULT_H,
                      color: str | None = None) -> CanvasNode:
        n = CanvasNode(id=_new_id(), type="file", x=float(x), y=float(y),
                       width=float(width), height=float(height), color=color,
                       file=str(file_rel).replace("\\", "/"))
        self.nodes.append(n)
        self.dirty = True
        return n

    def add_text_card(self, text: str, x: float, y: float,
                      width: float = DEFAULT_W, height: float = DEFAULT_H,
                      color: str | None = None) -> CanvasNode:
        n = CanvasNode(id=_new_id(), type="text", x=float(x), y=float(y),
                       width=float(width), height=float(height), color=color,
                       text=text)
        self.nodes.append(n)
        self.dirty = True
        return n

    def add_edge(self, from_id: str, to_id: str, *,
                 from_side: str | None = None, to_side: str | None = None,
                 from_end: str | None = None, to_end: str | None = None,
                 label: str | None = None, color: str | None = None) -> CanvasEdge:
        if from_id == to_id:
            raise ValueError("不允许自环边")
        for nid in (from_id, to_id):
            if self.node(nid) is None:
                raise ValueError(f"端点不存在: {nid}")
        e = CanvasEdge(id=_new_id(), from_node=from_id, to_node=to_id,
                       from_side=from_side, to_side=to_side,
                       from_end=from_end, to_end=to_end, label=label, color=color)
        self.edges.append(e)
        self.dirty = True
        return e

    def remove_node(self, node_id: str) -> list[CanvasEdge]:
        """删卡并级联删掉挂其上的边；返回被删边（撤销栈原样恢复用）。"""
        n = self.node(node_id)
        if n is None:
            raise ValueError(f"节点不存在: {node_id}")
        self.nodes.remove(n)
        gone = self.edges_of(node_id)
        self.edges = [e for e in self.edges if e not in gone]
        self.dirty = True
        return gone

    def remove_edge(self, edge_id: str) -> CanvasEdge | None:
        e = self.edge(edge_id)
        if e is None:
            return None
        self.edges.remove(e)
        self.dirty = True
        return e

    def restore_node(self, node: CanvasNode) -> None:
        """undo 专用：把之前 remove_node 摘下的对象原样放回（id 保序去重）。"""
        if self.node(node.id) is not None:
            raise ValueError(f"节点已存在: {node.id}")
        self.nodes.append(node)
        self.dirty = True

    def restore_edge(self, edge: CanvasEdge) -> None:
        if self.edge(edge.id) is not None:
            raise ValueError(f"边已存在: {edge.id}")
        if self.node(edge.from_node) is None or self.node(edge.to_node) is None:
            raise ValueError("端点缺失，无法还原边")
        self.edges.append(edge)
        self.dirty = True

    # ---------- group 容器（C6：spec 无 children 字段，成员=几何包含判定） ----------
    def create_group(self, node_ids: list[str], label: str = "",
                     color: str | None = None) -> CanvasNode:
        """以成员卡片矩形包围盒 + GROUP_PAD 外扩建组；成员不足抛 ValueError。"""
        members = []
        for nid in node_ids:
            n = self.node(nid)
            if n is None:
                raise ValueError(f"节点不存在: {nid}")
            if n.type == "group":
                raise ValueError("不能把组编进组（JSON Canvas 1.0 无嵌套组）")
            members.append(n)
        if len(members) < 2:
            raise ValueError("成组至少需要 2 个节点")
        x0 = min(n.x - n.width / 2 for n in members) - GROUP_PAD
        y0 = min(n.y - n.height / 2 for n in members) - GROUP_PAD
        x1 = max(n.x + n.width / 2 for n in members) + GROUP_PAD
        y1 = max(n.y + n.height / 2 for n in members) + GROUP_PAD
        g = CanvasNode(id=_new_id(), type="group",
                       x=(x0 + x1) / 2, y=(y0 + y1) / 2,
                       width=x1 - x0, height=y1 - y0,
                       color=color, label=label or None)
        self.nodes.append(g)
        self.dirty = True
        return g

    def group_members(self, group_id: str) -> list[str]:
        """成员=中心点落在组矩形内的卡片（边界值算内；恰被拖进的卡随组移动=规范语义）。"""
        g = self.node(group_id)
        if g is None or g.type != "group":
            return []
        out = []
        for n in self.nodes:
            if n is g or n.type == "group":
                continue
            if (abs(n.x - g.x) <= g.width / 2
                    and abs(n.y - g.y) <= g.height / 2):
                out.append(n.id)
        return out

    def move_nodes(self, deltas: dict[str, tuple[float, float]]) -> None:
        """批量位移 {node_id: (dx, dy)}；未知 id 静默跳过，应用过任何一条才置脏。"""
        applied = False
        for nid, (dx, dy) in deltas.items():
            n = self.node(nid)
            if n is not None:
                n.x += dx
                n.y += dy
                applied = True
        if applied:
            self.dirty = True

    def set_geometry(self, node_id: str, *, x: float | None = None,
                     y: float | None = None, width: float | None = None,
                     height: float | None = None) -> None:
        n = self.node(node_id)
        if n is None:
            raise ValueError(f"节点不存在: {node_id}")
        for attr, v in (("x", x), ("y", y), ("width", width), ("height", height)):
            if v is not None:
                setattr(n, attr, float(v))
        self.dirty = True

    def set_text(self, node_id: str, text: str) -> None:
        """仅文本卡可改（笔记卡正文属笔记工作区，画布不越权）。"""
        n = self.node(node_id)
        if n is None:
            raise ValueError(f"节点不存在: {node_id}")
        if n.type != "text":
            raise ValueError("只有 type=text 卡片支持 set_text")
        n.text = text
        self.dirty = True


# ---------- 复制粘贴纯函数（C3：deepcopy 隔离，原 doc 不受污染） ----------
def extract_subgraph(doc: CanvasDocument, node_ids: list[str]
                    ) -> tuple[list[CanvasNode], list[CanvasEdge]]:
    """取选中节点 + 两端都在集合内的全部边（深拷贝副本，按 doc 原序）。"""
    ids = set(node_ids)
    nodes = [copy.deepcopy(n) for n in doc.nodes if n.id in ids]
    edges = [copy.deepcopy(e) for e in doc.edges
             if e.from_node in ids and e.to_node in ids]
    return nodes, edges


def remap_copy(nodes: list[CanvasNode], edges: list[CanvasEdge],
               offset: tuple[float, float] = (24.0, 24.0)
               ) -> tuple[list[CanvasNode], list[CanvasEdge]]:
    """就地重造副本身份：新 id、位移 offset、边端点按旧→新映射；extra 原样。"""
    id_map: dict[str, str] = {}
    for n in nodes:
        new_id = _new_id()
        id_map[n.id] = new_id
        n.id = new_id
        n.x += offset[0]
        n.y += offset[1]
    for e in edges:
        e.id = _new_id()
        e.from_node = id_map.get(e.from_node, e.from_node)
        e.to_node = id_map.get(e.to_node, e.to_node)
    return nodes, edges


# ---------- 附件与批量排布纯函数（C7/C8） ----------
def unique_attach_rel(orig_name: str, existing: set[str]) -> str:
    """外图入 vault 的附件命名：附件/<名><ext>，重名追加 -1/-2…直到唯一。"""
    p = Path(orig_name)
    stem = p.stem or "file"
    ext = (p.suffix or "").lower()
    cand = f"{ATTACH_DIR}/{stem}{ext}"
    i = 1
    while cand in existing:
        cand = f"{ATTACH_DIR}/{stem}-{i}{ext}"
        i += 1
    return cand


def grid_positions(n: int, start: tuple[float, float], sx: float = 280.0,
                   sy: float = 160.0,
                   cols: int | None = None) -> list[tuple[float, float]]:
    """n 个卡片的网格中心点（默认近方形列数）；图谱批量导入排布用。"""
    import math
    if n <= 0:
        return []
    cols = cols or max(1, math.ceil(math.sqrt(n)))
    return [(start[0] + (i % cols) * sx, start[1] + (i // cols) * sy)
            for i in range(n)]
