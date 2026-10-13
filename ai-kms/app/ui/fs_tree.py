"""资源管理器：Vault 磁盘真相树（M-B）。

与"项目导航"（知识树，数据源 vault_files）互补：本树以磁盘枚举为源、
懒加载展开，每个节点带管道状态（fsindex.FileState）——
已原子化=实心知识图标、原始文件=黄点角标、白板/图片/文件夹各有 base 图标。
状态判定与前缀语义一律走 core.storage.fsindex 纯函数（本文件零判定逻辑）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QUrl, QMimeData, QByteArray, QTimer
from PySide6.QtGui import (QBrush, QColor, QDrag, QIcon, QPainter, QPen,
                           QPixmap, QStandardItem, QStandardItemModel)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox,
                               QTreeView, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from core.canvas import FILE_EXTS, IMAGE_EXTS
from core.storage.fsindex import FileState, file_state, parent_dir

# 行角色（QTreeView 层与主窗口分发共用，避免魔法数）
FS_ROLE_REL = Qt.ItemDataRole.UserRole          # str: vault 相对路径（posix）
FS_ROLE_STATE = Qt.ItemDataRole.UserRole + 2    # str: FileState.*
FS_ROLE_DUMMY = Qt.ItemDataRole.UserRole + 3    # True: 懒加载占位子（撑展开箭头）

ICON_DIR = Path(__file__).resolve().parent.parent / "icons"
ICON_GRAY = "#5f6368"
RAW_DOT = "#e8c44c"                              # 待收录=黄点；ATOMIC 无点（健康态求静默）

_BASE_ICON = {FileState.ATOMIC: "note", FileState.RAW: "note",
              FileState.CANVAS: "canvas", FileState.IMAGE: "image",
              FileState.OTHER: "file", FileState.FOLDER: "topic",
              FileState.FOLDER_EMPTY: "topic"}
_ICON_CACHE: dict = {}


def _render_svg(name: str, color: str, size: int) -> "QPixmap":
    """与 main_window._render_svg 同法（currentColor 替换光栅化）。此处自持一份，
    避免 fs_tree ← main_window 的循环 import；真源仍是 app/icons/*.svg。"""
    path = ICON_DIR / f"{name}.svg"
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    if not path.exists():
        return pix
    text = path.read_text(encoding="utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(text.encode("utf-8")))
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return pix


def badge_icon(state: str, size: int = 16) -> QIcon:
    """base 图标 + 右下角状态点合成（16px 下唯一低成本又可读的徽标方案）。"""
    dot = RAW_DOT if state == FileState.RAW else None
    key = (state, size)
    if key not in _ICON_CACHE:
        pix = _render_svg(_BASE_ICON.get(state, "file"), ICON_GRAY, size)
        if dot:
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.setBrush(QColor(dot))
            painter.drawEllipse(size - 8, size - 8, 7, 7)
            painter.end()
        _ICON_CACHE[key] = QIcon(pix)
    return _ICON_CACHE[key]


class FSFileTree(QTreeView):
    """Vault 懒加载文件树。dropped=外部 urls 落入（主窗口接 _import_paths）。"""

    dropped = Signal(list)                     # list[str] 本地绝对路径

    def __init__(self, root: Path, indexed_fn, parent=None):
        """indexed_fn: () -> frozenset[str]，vault_files 路径快照（缓存归调用方，
        以 store.epoch 失效——这里只消费不关心来源）。"""
        super().__init__(parent)
        self._root = Path(root)
        self._indexed_fn = indexed_fn
        self._model = QStandardItemModel(self)
        self._model.setHorizontalHeaderLabels(["名称"])
        self.setModel(self._model)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setUniformRowHeights(True)
        self.setExpandsOnDoubleClick(False)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.expanded.connect(self._on_expanded)
        self.reload_root()

    # ---------- 枚举与填充 ----------
    def _hidden(self, p: Path) -> bool:
        n = p.name
        return n.startswith(".") or n.startswith("~$") or n.lower() == "desktop.ini"

    def _children(self, rel: str):
        base = self._root if not rel else self._root / rel
        try:
            entries = list(base.iterdir())
        except OSError:
            return []
        entries = [p for p in entries if not self._hidden(p)]
        entries.sort(key=lambda p: (not p.is_dir(), p.name.casefold()))
        return entries

    def _fill(self, parent_item: QStandardItem, rel: str) -> None:
        """填充 rel 的直接子项；目录塞 dummy 子占位撑出展开箭头。"""
        indexed = frozenset(self._indexed_fn())
        for p in self._children(rel):
            child_rel = f"{rel}/{p.name}" if rel else p.name
            is_dir = p.is_dir()
            st = file_state(child_rel, is_dir, indexed,
                            has_children=bool(self._children(child_rel)) if is_dir else True)
            item = QStandardItem(badge_icon(st), p.name)
            item.setEditable(False)
            item.setData(child_rel, FS_ROLE_REL)
            item.setData(st, FS_ROLE_STATE)
            if st == FileState.FOLDER_EMPTY:
                item.setForeground(QBrush(QColor("#9aa0a6")))
            if is_dir:
                ph = QStandardItem("")
                ph.setEditable(False)
                ph.setData(True, FS_ROLE_DUMMY)
                item.appendRow(ph)
            parent_item.appendRow(item)

    def reload_root(self) -> None:
        root = self._model.invisibleRootItem()
        root.removeRows(0, root.rowCount())
        self._fill(root, "")

    def _item_for(self, rel: str):
        if rel == "":
            return self._model.invisibleRootItem()
        it = self._model.invisibleRootItem()
        cur = ""
        for part in rel.split("/"):
            cur = f"{cur}/{part}" if cur else part
            nxt = None
            for i in range(it.rowCount()):
                ch = it.child(i)
                if ch.data(FS_ROLE_REL) == cur:
                    nxt = ch
                    break
            if nxt is None:
                return None
            it = nxt
        return it

    def item_index(self, rel: str):
        it = self._item_for(rel)
        return self._model.indexFromItem(it) if it else None

    # ---------- 懒加载 ----------
    def _on_expanded(self, index) -> None:
        """⚠ 两条实测铁律：① QStandardItemModel 的 index 禁用 internalPointer()
        （内部是编码指针，直接段错误——必须 model.itemFromIndex）；
        ② expanded 回调里同步改模型=视图遍历中失效，填充延迟一轮事件循环。"""
        it = self._model.itemFromIndex(index)
        if it is None or it.rowCount() != 1:
            return
        first = it.child(0)
        if first is None or not first.data(FS_ROLE_DUMMY):
            return
        rel = it.data(FS_ROLE_REL) or ""
        QTimer.singleShot(0, lambda: self._do_populate(rel))

    def _do_populate(self, rel: str) -> None:
        it = self._item_for(rel)
        if it is None:
            return
        first = it.child(0)
        if it.rowCount() == 0 or (first is not None and first.data(FS_ROLE_DUMMY)):
            it.removeRows(0, it.rowCount())
            self._fill(it, rel)

    # ---------- 增量刷新 ----------
    def refresh_subtree(self, rel: str) -> bool:
        """重扫 rel 目录的 children（rel=''=根）。节点不在树上→False，调用方回落父级。
        保留该节点自身与直接子目录的展开态。"""
        if rel == "":
            self.reload_root()
            return True
        it = self._item_for(rel)
        if it is None:
            return False
        was_open = self.isExpanded(self._model.indexFromItem(it))
        open_kids = set()
        for i in range(it.rowCount()):
            ch = it.child(i)
            crel = ch.data(FS_ROLE_REL)
            if crel and self.isExpanded(self._model.indexFromItem(ch)):
                open_kids.add(crel)
        it.removeRows(0, it.rowCount())
        self._fill(it, rel)
        self.setExpanded(self._model.indexFromItem(it), was_open)
        for crel in open_kids:
            ch = self._item_for(crel)
            if ch is not None:
                ch.removeRows(0, ch.rowCount())
                self._fill(ch, crel)
                self.setExpanded(self._model.indexFromItem(ch), True)
        return True

    # ---------- 拖出成卡 ----------
    @staticmethod
    def mime_for(rel, state, root: Path):
        """构造拖出 mime（静态方法可探针断言，绕开 QDrag.exec）。
        放行：已入库/原始 .md 系文本、图片；白板与其它类型、文件夹禁拖。"""
        if not rel or not isinstance(rel, str):
            return None
        if state not in (FileState.ATOMIC, FileState.RAW, FileState.IMAGE):
            return None
        low = rel.lower()
        if not (low.endswith(FILE_EXTS) or low.endswith(IMAGE_EXTS)):
            return None
        md = QMimeData()
        md.setData("application/x-ai-kms-note", rel.encode("utf-8"))
        md.setText(rel)
        md.setUrls([QUrl.fromLocalFile(str(Path(root) / rel))])
        return md

    def startDrag(self, _supported_actions):
        idx = self.currentIndex()
        if not idx.isValid():
            return
        md = self.mime_for(idx.data(FS_ROLE_REL), idx.data(FS_ROLE_STATE),
                           self._root)
        if md is None:
            return
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.DropAction.CopyAction)

    # ---------- 拖入 vault（外部文件落盘后刷新；拷贝动作交主窗口 dropped 处理） ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if paths:
            self.dropped.emit(paths)
            e.acceptProposedAction()

    # ---------- 查询辅助（右键菜单/分发用） ----------
    def rel_of(self, index):
        return index.data(FS_ROLE_REL)

    def state_of(self, index):
        return index.data(FS_ROLE_STATE)

    def selected_rels(self):
        out = []
        for idx in self.selectionModel().selectedRows():
            r = idx.data(FS_ROLE_REL)
            if r:
                out.append(r)
        return out


class VaultDirPicker(QDialog):
    """选 Vault 内目标目录（M-C 移动用）。selected_rel()：''=根，None=取消。"""

    def __init__(self, root: Path, title: str = "移动到…", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(320, 400)
        lay = QVBoxLayout(self)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        root_item = QTreeWidgetItem(["（Vault 根目录）"])
        root_item.setData(0, Qt.ItemDataRole.UserRole, "")
        self._tree.addTopLevelItem(root_item)
        self._add_children(root, "", root_item)
        self._tree.expandAll()
        lay.addWidget(self._tree, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add_children(self, base: Path, rel: str, parent_item) -> None:
        try:
            dirs = sorted((p for p in base.iterdir()
                           if p.is_dir() and not p.name.startswith(".")),
                          key=lambda p: p.name.casefold())
        except OSError:
            return
        for p in dirs:
            child_rel = f"{rel}/{p.name}" if rel else p.name
            item = QTreeWidgetItem([p.name])
            item.setData(0, Qt.ItemDataRole.UserRole, child_rel)
            parent_item.addChild(item)
            self._add_children(p, child_rel, item)

    def selected_rel(self):
        items = self._tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
