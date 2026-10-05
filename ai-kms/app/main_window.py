"""AI-native KMS 桌面端（IDE 风格）。

参考 Android Studio 的多面板布局：
  - 顶部：工具栏（图标按钮）
  - 左侧：项目导航树（按主题/文件夹组织笔记）
  - 中间：工作区（笔记预览 / 知识图谱 / AI 管家，可切换）
  - 右侧：属性栏（笔记元信息 / 原子 / 相关笔记）
  - 底部：输出区（日志 / 版本历史）

依赖：PySide6。未装则 CLI 仍可用。
"""
from __future__ import annotations

import re
from pathlib import Path

from config import settings
from core.classify.rules import TOPIC_RULES
from core.graph.engine import GraphEngine
from core.graph.search import KnowledgeSearch
from core.ingest.pipeline import IngestPipeline, collect_importable
from core.ingest.watcher import DropWatcher
from core.storage.sqlite_db import SQLiteStore
from core.storage.vault import Vault
from core.aipilot.manager import AIPilot
from core.aipilot.snapshot import ConfirmationNeeded
from core.curator import Curator

try:
    from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize, QEvent, QPoint, QRect
    from PySide6.QtGui import QAction, QKeySequence, QIcon, QFont, QStandardItemModel, QStandardItem, QCursor
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import (
        QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
        QListWidget, QListWidgetItem, QTextBrowser, QTextEdit, QPlainTextEdit,
        QPushButton, QLabel,
        QLineEdit, QTabWidget, QApplication, QToolButton, QComboBox,
        QMessageBox, QGroupBox, QGridLayout, QFormLayout, QTreeView,
        QToolBar, QStackedWidget, QSizePolicy, QHeaderView, QAbstractItemView,
        QTableWidget, QTableWidgetItem, QDoubleSpinBox, QMenu, QAbstractButton,
    )
    _HAVE_QT = True
except ImportError:  # pragma: no cover
    _HAVE_QT = False


# ---------- IDE 风格 QSS ----------
IDE_STYLE = """
QMainWindow {
    background-color: #fafafa;
}
QToolBar {
    background-color: #ffffff;
    border-bottom: 1px solid #e6e6e6;
    spacing: 8px;
    padding: 4px;
}
QToolBar QToolButton {
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 5px 9px;
    color: #5f6368;
    font-size: 13px;
}
QToolBar QToolButton:hover {
    background-color: rgba(95, 99, 104, 0.10);
    color: #202124;
}
QToolBar QToolButton:checked {
    background-color: rgba(26, 115, 232, 0.12);
    color: #1a73e8;
}
QSplitter::handle {
    background-color: #e6e6e6;
    width: 1px;
    height: 1px;
}
QSplitter::handle:hover {
    background-color: #b8d0f0;
}
QTreeView {
    background-color: #ffffff;
    border: 1px solid #ececec;
    font-size: 13px;
    padding: 4px;
}
QTreeView::item {
    padding: 4px;
}
QTreeView::item:selected {
    background-color: #d0e0f0;
    color: #222;
}
QTreeView::item:hover {
    background-color: #e8f0f8;
}
QTabWidget::pane {
    border: 1px solid #ececec;
    background-color: #ffffff;
}
QTabBar::tab {
    background-color: #f0f0f0;
    border: 1px solid #ececec;
    padding: 8px 16px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    border-bottom: 2px solid #4e79a7;
}
QTextBrowser, QTextEdit, QPlainTextEdit {
    background-color: #ffffff;
    border: 1px solid #ececec;
    font-family: Consolas, 'Courier New', monospace;
    font-size: 13px;
    padding: 3px 6px;
}
QLineEdit, QComboBox {
    background-color: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 13px;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #4e79a7;
}
/* 下拉指示：抹掉原生黑色三角（由 FlatCombo 自绘细线 chevron 替代） */
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: none;
    width: 0;
    height: 0;
}
QPushButton {
    background-color: #e8e8e8;
    border: 1px solid #dcdcdc;
    border-radius: 4px;
    padding: 6px 12px;
    font-size: 13px;
}
QPushButton:hover {
    background-color: #d8e0e8;
}
QPushButton:pressed {
    background-color: #c8d0d8;
}
/* Activity Bar 按钮（常驻活动栏，扁平丝滑，类似 Android Studio 新 UI） */
QToolButton#activityBtn {
    background-color: transparent;
    border: none;
    border-radius: 7px;
    padding: 4px;
    margin: 1px;
}
QToolButton#activityBtn:hover {
    background-color: rgba(95, 99, 104, 0.12);
}
QToolButton#activityBtn:checked {
    background-color: rgba(26, 115, 232, 0.12);
}
QGroupBox {
    border: 1px solid #ececec;
    border-radius: 4px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: bold;
}
QGroupBox:title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    background-color: #fafafa;
}

/* ===== 自绘标题栏（白底 + 可见窗口按钮）===== */
QWidget#titleBar {
    background-color: #ffffff;
    border-bottom: 1px solid #e6e6e6;
}
QLabel#titleBarIcon {
    color: #1a73e8;
    font-size: 15px;
    font-weight: bold;
    background: transparent;
}
QLabel#titleBarLabel {
    color: #202124;
    font-size: 13px;
    font-weight: 600;
    background: transparent;
}
QPushButton#minBtn, QPushButton#maxBtn, QPushButton#closeBtn {
    background-color: transparent;
    border: none;
    border-radius: 4px;
    color: #5f6368;
    font-size: 15px;
    padding: 0;
    margin: 0 2px;
}
QPushButton#minBtn:hover, QPushButton#maxBtn:hover {
    background-color: #ececec;
    color: #202124;
}
QPushButton#closeBtn:hover {
    background-color: #e81123;
    color: #ffffff;
}
QPushButton#closeBtn:pressed {
    background-color: #c50f1f;
    color: #ffffff;
}

/* ===== 极细滚动条 ===== */
QScrollBar:vertical {
    background: transparent;
    width: 6px;
    margin: 0;
    border: none;
}
QScrollBar::handle:vertical {
    background: #dcdcdc;
    min-height: 30px;
    border-radius: 3px;
    border: none;
}
QScrollBar::handle:vertical:hover { background: #bdbdbd; }
QScrollBar::handle:horizontal:hover { background: #bdbdbd; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0; width: 0; background: transparent; border: none;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent; border: none;
}
QScrollBar:horizontal {
    background: transparent;
    height: 6px;
    margin: 0;
    border: none;
}
QScrollBar::handle:horizontal {
    background: #dcdcdc;
    min-width: 30px;
    border-radius: 3px;
    border: none;
}
QScrollBar:corner { background: transparent; border: none; }
"""


ICON_DIR = Path(__file__).parent / "icons"
ICON_GRAY = "#5f6368"
ICON_BLUE = "#1a73e8"
TOPIC_ROLE = Qt.ItemDataRole.UserRole + 1   # 文件夹项存分类名（与笔记 path 角色区分）
# 预览渲染：[[目标#锚|别名]] → 可点链接；代码围栏/行内代码内的不替换
_WIKI_FULL_RE = re.compile(r"\[\[([^\[\]|#]+?)(?:#[^\[\]|]*)?(?:\|([^\[\]]*))?\]\]")
_MD_PROTECT_RE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]+`)", re.S)
IMPORT_BATCH = 10                           # 分批导入：每个 tick 处理的文件数
CURATOR_BATCH = 20                          # 策展扫描：每个 tick 处理的笔记数
APPLY_BATCH = 1                             # 批量采纳：每个 tick 应用的建议数（含写盘）


def _render_svg(name: str, color: str, size: int) -> "QPixmap":
    """把 app/icons/<name>.svg 用指定颜色渲染成 QPixmap（替换 currentColor）。"""
    from PySide6.QtGui import QPixmap, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QByteArray
    path = ICON_DIR / f"{name}.svg"
    if not path.exists():
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        return pix
    text = path.read_text(encoding="utf-8").replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(text.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return pix


_ICON_CACHE: dict = {}


def _svg_icon(name: str, color: str = ICON_GRAY, size: int = 20) -> QIcon:
    """记忆化：图标在 _refresh_tree 热点路径逐节点复用，避免每次读盘+rasterize。"""
    key = (name, color, size)
    ic = _ICON_CACHE.get(key)
    if ic is None:
        ic = QIcon(_render_svg(name, color, size))
        _ICON_CACHE[key] = ic
    return ic


class ActivityButton(QToolButton):
    """活动栏按钮：checkable，图标随状态灰↔蓝平滑切换，无硬边框。"""

    def __init__(self, icon_name: str, tooltip: str, panel: str):
        super().__init__()
        self.panel = panel
        self._icon_name = icon_name
        self.setObjectName("activityBtn")
        self.setToolTip(tooltip)
        self.setCheckable(True)
        self.setChecked(True)
        self.setFixedSize(30, 30)
        self.setIconSize(QSize(20, 20))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda _: self._refresh())
        self._refresh()

    def _refresh(self):
        color = ICON_BLUE if self.isChecked() else ICON_GRAY
        self.setIcon(QIcon(_render_svg(self._icon_name, color, 20)))


class NavTree(QTreeView):
    """左侧导航树：接受从资源管理器拖入的文件/文件夹 URL，仅 DropOnly（禁内部重排）。"""

    dropped = Signal(list)          # list[str] 本地路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(False)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        if paths:
            # 不调 super()：禁掉 QAbstractItemView 内建 model-drop，杜绝 item 移动
            self.dropped.emit(paths)   # 枚举/过滤/入库交回 MainWindow 统一处理
            e.acceptProposedAction()
        else:
            e.ignore()


class FlatCombo(QComboBox):
    """极简折叠式下拉：原生黑色三角由 QSS 抹除，此处自绘细线 chevron，
    收起朝下 ⌄、展开朝上 ⌃，与扁平界面观感一致。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pop_open = False

    def showPopup(self):
        super().showPopup()
        self._pop_open = True
        self.update()

    def hidePopup(self):
        super().hidePopup()
        self._pop_open = False
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainter, QPen, QColor
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(ICON_GRAY))
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        cx = self.width() - 11.5
        cy = self.height() / 2.0
        dx, dy = 3.5, 2.2
        if self._pop_open:                       # 展开：朝上 ⌃
            p.drawLine(QPointF(cx - dx, cy + dy), QPointF(cx, cy - dy))
            p.drawLine(QPointF(cx, cy - dy), QPointF(cx + dx, cy + dy))
        else:                                    # 收起：朝下 ⌄
            p.drawLine(QPointF(cx - dx, cy - dy), QPointF(cx, cy + dy))
            p.drawLine(QPointF(cx, cy + dy), QPointF(cx + dx, cy - dy))
        p.end()


class _AIInput(QTextEdit):
    """聊天式输入框：Enter 发送，Shift+Enter 换行；输入 @ 请求挂载笔记。"""
    submit = Signal()
    at_requested = Signal(int)        # 参数：'@' 的光标位置（用于选中后清理）

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
        else:
            super().keyPressEvent(event)
            pos = self.textCursor().position()
            if pos > 0:
                before = self.toPlainText()[:pos]
                if before.endswith("@") and (len(before) == 1
                                             or before[-2] in " \n\t"):
                    self.at_requested.emit(pos)


class _MountPopup(QWidget):
    """搜索挂载浮层：「搜索挂载」按钮与输入框 @ 共用。选中发 chosen(path)。"""
    chosen = Signal(str)

    def __init__(self, search_fn, parent=None):
        super().__init__(parent)
        self.search_fn = search_fn
        self.setWindowFlags(Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("输入几个字检索笔记，↑↓ 选择，Enter 挂载")
        self.list = QListWidget()
        self.list.setStyleSheet("""
            QListWidget { background: #ffffff; border: 1px solid #dadce0;
                outline: 0; font-size: 13px; }
            QListWidget::item { padding: 7px 12px; color: #202124; }
            QListWidget::item:hover { background: #f1f3f4; }
            QListWidget::item:selected { background: #e8f0fe; color: #174ea6; }
        """)
        lay.addWidget(self.edit)
        lay.addWidget(self.list)
        self.setFixedWidth(360)
        self.setFixedHeight(90)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._do_search)
        self.edit.textChanged.connect(lambda _: self._timer.start())
        self.edit.returnPressed.connect(self._pick_current)
        self.list.itemClicked.connect(self._pick)
        self.edit.installEventFilter(self)

    def open_at(self, anchor_widget):
        self.edit.clear()
        self._do_search()
        self.move(anchor_widget.mapToGlobal(QPoint(0, -self.height() - 6)))
        self.show()
        self.edit.setFocus()

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.list.currentRow() + (1 if ev.key() == Qt.Key.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(row, self.list.count() - 1)))
                return True
            if ev.key() == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, ev)

    def _do_search(self):
        q = self.edit.text().strip()
        self.list.clear()
        rows = self.search_fn(q, 8) if q else []
        for r in rows:
            it = QListWidgetItem(f"{r['title']}   · {r['topic']}")
            it.setToolTip(r["path"])
            it.setData(Qt.ItemDataRole.UserRole, r["path"])
            self.list.addItem(it)
        self.list.setFixedHeight(min(240, 30 + max(len(rows), 1) * 32))
        if rows:
            self.list.setCurrentRow(0)
        self.adjustSize()

    def _pick(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.chosen.emit(path)
            self.close()

    def _pick_current(self):
        item = self.list.currentItem()
        if item is None and self.list.count():
            item = self.list.item(0)
        if item is not None:
            self._pick(item)


class _TitleBar(QWidget):
    """无边框窗口的自绘标题栏：白底 + 可见的最小化/最大化/关闭按钮 + 拖动移动。"""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self.setObjectName("titleBar")
        self.setFixedHeight(34)
        self._drag_pos: QPoint | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(4)

        icon = QLabel("◈")
        icon.setObjectName("titleBarIcon")
        title = QLabel("AI-native KMS")
        title.setObjectName("titleBarLabel")
        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addStretch(1)

        for text, slot, name in [
            ("—", self._minimize, "minBtn"),
            ("□", self._maximize, "maxBtn"),
            ("✕", self._close, "closeBtn"),
        ]:
            btn = QPushButton(text)
            btn.setObjectName(name)
            btn.setFixedSize(40, 34)
            btn.clicked.connect(slot)
            lay.addWidget(btn)

    # ---- 按钮动作 ----
    def _minimize(self):
        self._window.showMinimized()

    def _maximize(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def _close(self):
        self._window.close()

    def set_maximized(self, maximized: bool):
        """切换最大化按钮图标（□ ↔ ❐）。"""
        btn = self.findChild(QPushButton, "maxBtn")
        if btn:
            btn.setText("❐" if maximized else "□")

    # ---- 拖动移动 / 双击最大化 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (event.globalPosition().toPoint()
                              - self._window.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):
        if (self._drag_pos is not None
                and event.buttons() & Qt.MouseButton.LeftButton
                and not self._window.isMaximized()):
            self._window.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        self._maximize()


class _LogSink(QObject):
    appended = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI-native KMS")
        self.resize(1280, 800)

        self.store = SQLiteStore()
        self.vault = Vault()
        self.pipe = IngestPipeline(self.store, self.vault)
        self.graph_engine = GraphEngine(self.store, self.vault)
        self.search = KnowledgeSearch(self.store, self.vault)
        self.pilot = AIPilot(self.store, self.vault, on_progress=self._append_log)
        self.curator = Curator(self.store, self.vault, self.pipe)
        self.sink = _LogSink()
        self.sink.appended.connect(self._append_log)

        # 批量导入会话（主线程分批，见 _import_tick）
        self._import_queue = []
        self._import_seen = set()
        self._import_stats = None
        self._import_active = False
        self._import_cancel = False
        self._import_total = 0
        self._import_stop_btn = None

        # 挂载笔记（AI 管家上下文锚点，上限 3）
        self._mounted = []
        self._at_pos = -1              # '@' 触发时光标位置（挂载成功后清理）

        # 策展扫描 / 批量采纳会话
        self._cur_scan = None          # {kind, queue, scanned, added, total, cancel}
        self._apply_queue = []         # 批量采纳待办 id 列表
        self._reread_queue = []        # 重读校准待办 path 列表
        self._busy_stop = None         # 状态栏常驻"取消批处理"按钮

        self._build_ui()

        # 无边框窗口：边缘拖拽缩放状态
        self._resize_edges = None      # (left, right, top, bottom) bool 元组
        self._resize_start_geo = None
        self._resize_start_pos = None
        self._edge_margin = 6          # 边缘感应区（px）
        self._override_active = False  # 是否叠加了全局缩放覆盖光标

        # 边缘缩放：装全局事件过滤器 + 鼠标追踪 + 最小尺寸
        self.setMinimumSize(640, 480)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.setMouseTracking(True)
        for child in self.findChildren(QWidget):
            child.setMouseTracking(True)

        self._log_cursor = 0
        self._boot_msg("就绪。左侧选笔记预览，或丢文件进 data/dropbox 自动收录。")

    # ---------- UI ----------
    def _build_ui(self):
        # 顶部工具栏（独立 widget，稍后由容器排序：标题栏→工具栏→内容）
        self._toolbar = self._build_toolbar()

        # 折叠状态（默认全部收起，点活动栏图标展开）
        self._panel_state = {"left": False, "right": False, "bottom": False}
        self._panel_sizes = {"left": 260, "right": 300, "bottom": 180}
        self._activity_buttons = {}

        # ========== 新布局：Activity Bar + 面板 ==========
        # 参考 Android Studio / VSCode：图标放在常驻活动栏，面板可折叠但图标始终可见

        # 左侧 Activity Bar（常驻）：上=文件树切换，下=日志/底栏切换（Android Studio 惯例）
        self._left_activity = self._make_activity_bar(
            top=[("project", "项目导航（文件树）", "left")],
            bottom=[("logs", "日志/版本历史（底栏）", "bottom")],
            side="left")

        # 左侧：项目导航树（可折叠，支持拖拽入库）
        self.nav_tree = NavTree()
        self.nav_tree.setIconSize(QSize(16, 16))
        self.nav_tree.dropped.connect(self._import_paths)
        self.nav_tree.setHeaderHidden(True)
        self.nav_tree.setRootIsDecorated(True)
        self.nav_tree.setAlternatingRowColors(False)
        self.nav_tree.setMinimumWidth(220)
        self.nav_tree.setMaximumWidth(320)
        self.nav_tree.doubleClicked.connect(self._on_tree_double_clicked)
        # 右键菜单：新建 / 重命名 / 删除
        self.nav_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.nav_tree.customContextMenuRequested.connect(self._tree_context_menu)
        self.nav_tree.setVisible(False)      # 默认折叠

        # Ctrl+S 保存快捷键（笔记区）
        from PySide6.QtGui import QShortcut
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save_note)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self._new_note)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self._close_note)

        # 中间：工作区（堆叠）
        self.workspace = QStackedWidget()
        self.workspace.addWidget(self._build_notes_workspace())
        self.workspace.addWidget(self._build_graph_workspace())
        self.workspace.addWidget(self._build_ai_workspace())

        # 右侧：属性栏（可折叠）
        self.props_panel = self._build_props_panel()
        self.props_panel.setMinimumWidth(240)
        self.props_panel.setMaximumWidth(360)
        self.props_panel.setVisible(False)    # 默认折叠

        # 右侧 Activity Bar（常驻）
        self._right_activity = self._make_activity_bar(
            top=[("properties", "属性面板", "right")],
            bottom=[], side="right")

        # 横向：左活动栏 | 左面板 | 工作区 | 右面板 | 右活动栏
        self._h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter = self._h_splitter
        h_splitter.addWidget(self._left_activity)
        h_splitter.addWidget(self.nav_tree)
        h_splitter.addWidget(self.workspace)
        h_splitter.addWidget(self.props_panel)
        h_splitter.addWidget(self._right_activity)
        h_splitter.setStretchFactor(0, 0)  # left activity
        h_splitter.setStretchFactor(1, 0)  # left panel
        h_splitter.setStretchFactor(2, 1)  # workspace
        h_splitter.setStretchFactor(3, 0)  # right panel
        h_splitter.setStretchFactor(4, 0)  # right activity
        h_splitter.setSizes([28, 0, 988, 0, 28])   # 左右面板默认收起

        # 底部输出区（可折叠，切换图标在左侧活动栏的下半区）
        self.output_tabs = QTabWidget()
        self.output_tabs.addTab(self._build_log_panel(), "日志")
        self.output_tabs.addTab(self._build_snapshots_panel(), "版本历史")
        self.output_tabs.addTab(self._build_review_panel(), "审阅队列")
        self._review_tab_idx = 2
        self.output_tabs.setMaximumHeight(220)
        self.output_tabs.setVisible(False)   # 默认折叠

        bottom_wrapper = self.output_tabs

        self._main_split = QSplitter(Qt.Orientation.Vertical)
        main_split = self._main_split
        main_split.addWidget(h_splitter)
        main_split.addWidget(bottom_wrapper)
        main_split.setStretchFactor(0, 1)
        main_split.setStretchFactor(1, 0)
        main_split.setSizes([800, 0])        # 底栏默认收起

        # 无边框窗口：标题栏(自绘白底) + 内容 垂直包裹
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        container = QWidget()
        container_lay = QVBoxLayout(container)
        container_lay.setContentsMargins(0, 0, 0, 0)
        container_lay.setSpacing(0)
        self._title_bar = _TitleBar(self)
        container_lay.addWidget(self._title_bar)
        container_lay.addWidget(self._toolbar)
        container_lay.addWidget(main_split, 1)
        self.setCentralWidget(container)

        self.statusBar().showMessage("watcher: 正在监听 data/dropbox")

        # 监听器
        self.watcher = DropWatcher(Path(settings.PROJECT_ROOT / "data" / "dropbox"),
                                   self.store, self.pipe)
        n = self.watcher.ingest_existing()
        if n:
            self._boot_msg(f"启动时收录 {n} 篇历史素材。")
        self.watcher.start()

        self._refresh_tree()

        self._timer = QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.start(1200)

        # FTS 对账：行数不等即分批补缺/清幽灵（不阻塞启动，期间检索走 legacy）
        if self.store.fts_ok:
            n_fts, n_vault = self.store.fts_health()
            if n_fts != n_vault:
                self._append_log(f"🔍 搜索索引与笔记数不一致（{n_fts}/{n_vault}），后台对账中…")
                QTimer.singleShot(0, self._fts_rebuild_tick)

    def _fts_rebuild_tick(self):
        r = self.pipe.fts_rebuild_chunk(limit=20)   # 实测 ~90ms/拍，UI 无感
        if not r.get("ok"):
            return
        if r["remaining"]:
            self.statusBar().showMessage(
                f"搜索索引对账中：本批 {r['indexed']} 篇 · 剩 {r['remaining']}")
            QTimer.singleShot(0, self._fts_rebuild_tick)
        else:
            self.statusBar().showMessage("搜索索引对账完成", 3000)
            self._append_log("✅ 搜索索引对账完成。")

    # ---------- Activity Bar ----------
    def _make_activity_bar(self, top: list[tuple[str, str, str]],
                           bottom: list[tuple[str, str, str]] | None = None,
                           side: str = "left") -> QWidget:
        """创建常驻活动栏，支持"上下分组"（Android Studio 式层次）。

        top / bottom: [(SVG 图标名, tooltip, 面板名), ...]
        side: "left" | "right"（竖排） | "bottom"（横排，此时 top/bottom 视作 左/右 组）
        """
        bar = QWidget()
        horiz = side == "bottom"
        layout = QHBoxLayout(bar) if horiz else QVBoxLayout(bar)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)

        def _add_group(items):
            for icon_name, tip, panel_name in items:
                btn = ActivityButton(icon_name, tip, panel_name)
                btn.clicked.connect(lambda _, n=panel_name: self._toggle_panel(n))
                btn.setChecked(self._panel_state.get(panel_name, False))  # 图标态=默认折叠态
                self._activity_buttons[panel_name] = btn
                layout.addWidget(btn)
                if horiz:
                    pass
                else:
                    layout.addSpacing(2)

        _add_group(top)
        layout.addStretch(1)          # 把 bottom 组推到另一端 → 上下区分
        if bottom:
            _add_group(bottom)

        if horiz:
            bar.setFixedHeight(34)
            layout.setContentsMargins(2, 0, 2, 0)
        else:
            bar.setFixedWidth(34)
        return bar

    # ---------- 面板折叠 ----------
    def _toggle_panel(self, name: str):
        """折叠/展开左/右/下面板。Activity Bar 图标始终可见、随状态变色。"""
        main_split = self._main_split
        h_splitter = self._h_splitter
        self._panel_state[name] = not self._panel_state[name]
        expanded = self._panel_state[name]

        btn = self._activity_buttons.get(name)
        if btn and btn.isChecked() != expanded:
            btn.setChecked(expanded)

        if name == "left":
            self.nav_tree.setVisible(expanded)
            sizes = list(h_splitter.sizes())
            sizes[1] = self._panel_sizes["left"] if expanded else 0
            h_splitter.setSizes(sizes)
        elif name == "right":
            self.props_panel.setVisible(expanded)
            sizes = list(h_splitter.sizes())
            sizes[3] = self._panel_sizes["right"] if expanded else 0
            h_splitter.setSizes(sizes)
        elif name == "bottom":
            self.output_tabs.setVisible(expanded)
            sizes = list(main_split.sizes())
            sizes[1] = self._panel_sizes["bottom"] if expanded else 0
            main_split.setSizes(sizes)

    def _build_toolbar(self):
        """创建独立工具栏 widget（不放进 QMainWindow 区域，改由容器排序：标题栏→工具栏→内容）。"""
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))

        def _btn(text, tip, slot, icon=None):
            a = QAction(text, self)
            if icon:
                a.setIcon(_svg_icon(icon, ICON_GRAY, 18))
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        self._btn_add = _btn(" 收录", "Quick Add 录入素材", self._quick_add_dialog, "add")
        self._btn_import = _btn(" 导入文件夹", "批量导入目录中的 .md/.txt/.rst（也可直接拖进左侧树）",
                                self._import_folder_dialog, "import")
        tb.addSeparator()
        self._btn_notes = _btn(" 笔记", "笔记工作区", lambda: self.workspace.setCurrentIndex(0), "notes")
        self._btn_graph = _btn(" 图谱", "知识图谱", lambda: self.workspace.setCurrentIndex(1), "graph")
        self._btn_ai = _btn(" AI", "AI 管家", lambda: self.workspace.setCurrentIndex(2), "ai")
        tb.addSeparator()
        self._btn_watch = _btn(" 监听", "暂停/恢复监听", self._toggle_watch, "watch")
        self._btn_watch.setCheckable(True)
        self._btn_watch.setChecked(True)
        tb.addSeparator()
        _btn(" 审阅", "打开审阅队列", self.open_review, "review")
        _btn(" 设置", "AI Provider / 密钥 / 索引维护", self._settings_dialog, "settings")
        self._btn_export = _btn(" 导出", "导出结构层 JSONL / 全库打包 ZIP（数据不锁定，资产可搬走）",
                                self._export_menu)
        tb.addSeparator()
        # 搜索图标紧贴搜索框（此前隔了审阅/设置/导出三个按钮）
        _btn(" 搜索", "聚焦搜索框并搜索", self._focus_search, "search")
        # 搜索：防抖定时器 + 框下结果浮层。
        # 防抖 450ms：中文输入法组字停顿不会误触发；回车 = 立即检索。
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(450)
        self._search_timer.timeout.connect(self._do_search)
        self._search_bar = QLineEdit()
        self._search_bar.setPlaceholderText("搜索笔记… Enter 打开")
        self._search_bar.setClearButtonEnabled(True)
        self._search_bar.textChanged.connect(lambda _: self._search_timer.start())
        self._search_bar.returnPressed.connect(self._on_search_enter)
        self._search_bar.setMaximumWidth(240)
        tb.addWidget(self._search_bar)

        self._search_popup = QListWidget()
        self._search_popup.setObjectName("searchPopup")
        # ⚠ 不用 Qt.Popup：Popup 会抓取鼠标+键盘（IME 组字被打断、搜索框点不动）。
        # ToolTip 型窗口不吃键盘、不抓鼠标：打字/点框都不受影响；
        # 点外部/Esc/移动窗口的收起逻辑在 eventFilter 里手动做。
        self._search_popup.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus)
        # 弹出时不抢焦点：继续打字仍进搜索框（增量搜索）
        self._search_popup.setAttribute(
            Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._search_popup.setFixedWidth(360)
        self._search_popup.setMaximumHeight(360)
        self._search_popup.setStyleSheet("""
            QListWidget#searchPopup { background: #ffffff; border: 1px solid #dadce0;
                outline: 0; font-size: 13px; }
            QListWidget#searchPopup::item { padding: 7px 12px; color: #202124; }
            QListWidget#searchPopup::item:hover { background: #f1f3f4; }
            QListWidget#searchPopup::item:selected { background: #e8f0fe; color: #174ea6; }
        """)
        self._search_popup.itemClicked.connect(self._open_search_item)
        self._search_popup.itemActivated.connect(self._open_search_item)
        return tb

    # ---------- 工作区 ----------
    def _build_notes_workspace(self) -> QWidget:
        w = QWidget()
        w.setObjectName("notesWs")
        # 本页三个操作按钮做紧凑尺寸（只作用于笔记工作区）
        w.setStyleSheet("""
            #notesWs QPushButton { padding: 2px 10px; font-size: 12px; }
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # 顶栏：新建 / 编辑·预览切换 / 保存 / 关闭 / 路径提示
        bar = QHBoxLayout()
        self._note_new_btn = QPushButton("＋ 新建笔记")
        self._note_new_btn.clicked.connect(self._new_note)
        self._note_edit_btn = QPushButton("编辑")
        self._note_edit_btn.setCheckable(True)
        self._note_edit_btn.toggled.connect(self._toggle_edit_mode)
        self._note_save_btn = QPushButton("保存 (Ctrl+S)")
        self._note_save_btn.clicked.connect(self._save_note)
        self._note_save_btn.setEnabled(False)
        self._note_close_btn = QPushButton("✕ 关闭")
        self._note_close_btn.setToolTip("关闭当前笔记 (Ctrl+W)，未保存改动会提示")
        self._note_close_btn.clicked.connect(self._close_note)
        self._note_close_btn.setEnabled(False)
        self._note_path_lbl = QLabel("（未打开笔记）")
        self._note_path_lbl.setStyleSheet("color:#5f6368;")
        bar.addWidget(self._note_new_btn)
        bar.addWidget(self._note_edit_btn)
        bar.addWidget(self._note_save_btn)
        bar.addWidget(self._note_close_btn)
        bar.addStretch(1)
        bar.addWidget(self._note_path_lbl)
        lay.addLayout(bar)

        # 主体：预览 / 编辑器 堆叠切换
        self._note_stack = QStackedWidget()
        self.notes_preview = QTextBrowser()
        # 必须 False：openExternalLinks 会把自定义 kms-note: 协议也丢给系统
        # ShellExecute → Windows 弹"打开方式/微软商店"。链接分流在 _on_preview_anchor 手动做。
        self.notes_preview.setOpenExternalLinks(False)
        self.notes_preview.anchorClicked.connect(self._on_preview_anchor)
        self.notes_editor = QPlainTextEdit()
        self.notes_editor.setPlaceholderText("在此编辑 Markdown…  Ctrl+S 保存")
        self.notes_editor.setMinimumHeight(400)
        self._note_stack.addWidget(self.notes_preview)   # index 0 = 预览
        self._note_stack.addWidget(self.notes_editor)    # index 1 = 编辑
        self._note_stack.setMinimumHeight(400)
        lay.addWidget(self._note_stack, 1)
        return w

    # ---------- 笔记编辑 ----------
    def _current_note(self) -> str | None:
        return getattr(self, "_cur_note", None)

    # ---------- 预览渲染：[[双链]] → 可点击链接 ----------
    def _resolve_wiki_title(self, target: str) -> str | None:
        """双链目标 → 笔记 path。文件名 stem 优先于 H1 标题（与 graph 的
        title_index 插 entry 顺序一致），同名歧义时取稳定顺序首篇。"""
        title_hit = None
        for f in self.store.all_files():
            if Path(f["path"]).stem == target:
                return f["path"]
            if title_hit is None and f["title"] == target:
                title_hit = f["path"]
        return title_hit

    def _preview_md(self, content: str) -> str:
        """把 [[目标|别名]] 转为内部协议链接；未解析目标标红（Obsidian 式）。
        代码围栏与行内代码保持原样；编辑器侧永远显示原始 Markdown。"""
        from urllib.parse import quote
        parts = re.split(_MD_PROTECT_RE, content or "")
        for i in range(0, len(parts), 2):        # 奇数段是被保护的代码块
            def repl(m):
                target = m.group(1).strip()
                alias = (m.group(2) or target).strip() or target
                resolved = self._resolve_wiki_title(target) is not None
                color = "#1a73e8" if resolved else "#c5221f"
                deco = "none" if resolved else "line-through"
                return (f'<a href="kms-note:{quote(target)}" '
                        f'style="color:{color};text-decoration:{deco};">'
                        f'{alias}</a>')
            parts[i] = _WIKI_FULL_RE.sub(repl, parts[i])
            # Obsidian 式换行：源码单换行 → 行尾补两空格变硬换行。
            # CommonMark 默认把单换行并入同一段——我们的笔记段间无空行，
            # 不处理的话预览会把整段正文并成一行，"看起来完全没渲染"。
            # 空行仍是段落分隔（行内只有空格=空行），列表/标题行为不变。
            parts[i] = re.sub(r"[ \t]*$", "  ", parts[i], flags=re.M)
        return "".join(parts)

    def _on_preview_anchor(self, url):
        """预览区链接分流：kms-note: 站内跳转；http/https/mailto 才交系统浏览器。"""
        s = url.toString()
        if not s.startswith("kms-note:"):
            if s.startswith(("http://", "https://", "mailto:")):
                from PySide6.QtCore import QUrl as _QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(_QUrl(s))
            return
        from urllib.parse import unquote
        target = unquote(s[len("kms-note:"):])
        path = self._resolve_wiki_title(target)
        if path:
            self._open_note(path)
        else:
            self.statusBar().showMessage(
                f"《{target}》还不是笔记——用「新建笔记」创建同名笔记即可接上这条双链", 5000)

    def _toggle_edit_mode(self, editing: bool):
        if not self._current_note():
            if editing:
                self._note_edit_btn.setChecked(False)
                self.statusBar().showMessage("请先打开一篇笔记", 2500)
            return
        if editing:
            # 进入编辑：把原文（Markdown 源码）灌进编辑器
            self.notes_editor.setPlainText(self.vault.read_note(self._current_note()))
            self._note_stack.setCurrentIndex(1)
            self._note_save_btn.setEnabled(True)
            self.notes_editor.setFocus()
        else:
            self._note_stack.setCurrentIndex(0)
            self._note_save_btn.setEnabled(False)

    def _close_note(self):
        """关闭当前笔记：未保存改动先提示；清预览/编辑/属性栏/版本历史。"""
        rel = self._current_note()
        if not rel:
            self.statusBar().showMessage("当前没有打开的笔记", 2000)
            return
        # 编辑态且有未保存改动 → 提示保存/放弃
        if self._note_edit_btn.isChecked():
            if self.notes_editor.toPlainText() != self.vault.read_note(rel):
                answer = QMessageBox.question(
                    self, "关闭笔记",
                    f"《{rel}》有未保存的修改，先保存吗？",
                    QMessageBox.StandardButton.Save
                    | QMessageBox.StandardButton.Discard
                    | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Save)
                if answer == QMessageBox.StandardButton.Cancel:
                    return
                if answer == QMessageBox.StandardButton.Save:
                    self._save_note()
                    if self._current_note() == rel and \
                            self.notes_editor.toPlainText() != self.vault.read_note(rel):
                        return  # 保存失败（弹窗已提示），不关闭
        self._clear_note_views()
        self.statusBar().showMessage(f"已关闭：{rel}", 3000)
        self._append_log(f"✕ 关闭：{rel}")

    def _clear_note_views(self):
        """关闭/删除后的统一清理：退编辑态 + 清预览/属性栏/版本历史。"""
        if self._note_edit_btn.isChecked():
            self._note_edit_btn.setChecked(False)  # 先退编辑态（stack 正常回预览）
        self._cur_note = None
        self.notes_preview.setPlainText("")
        self.notes_editor.setPlainText("")
        self._note_save_btn.setEnabled(False)
        self._note_close_btn.setEnabled(False)
        self._note_path_lbl.setText("（未打开笔记）")
        # 右栏属性 + 底部版本历史一并清空
        self._props_title.setText("（未选择笔记）")
        self._props_info.clear()
        self._props_atoms.clear()
        self._props_related.clear()
        self._snap_list.clear()

    def _save_note(self):
        rel = self._current_note()
        if not rel:
            return
        content = self.notes_editor.toPlainText()
        original = self.vault.read_note(rel)
        if content == original:
            self.statusBar().showMessage("内容未变化，未保存", 2500)
            return
        # 走快照+确认机制（人工编辑默认直接写，AI 才需确认）
        try:
            self.pilot.snapshots.write(rel, content, "人工编辑", require_confirm=False)
        except Exception as e:
            self.statusBar().showMessage(f"保存失败：{e}", 4000)
            return
        # 更新索引（标题/指纹/原子/双链/FTS 全部重建，下沉至 pipeline）
        self.pipe.reindex_note(rel, content)
        self.notes_preview.setMarkdown(self._preview_md(content))
        self._note_path_lbl.setText(f"💾 已保存 · {rel}")
        self.statusBar().showMessage(f"已保存 {rel}", 3000)
        self._append_log(f"💾 保存：{rel}")
        self._refresh_tree()
        self._refresh_ai_notes()

    def _new_note(self):
        """新建笔记：输入标题 → 创建空笔记 → 打开并进入编辑。"""
        from PySide6.QtWidgets import QInputDialog
        title, ok = QInputDialog.getText(self, "新建笔记", "标题：")
        if not ok or not title.strip():
            return
        title = title.strip()
        # 选择主题：分类层全集（内置 ∪ 自定义 ∪ 已有笔记主题）
        topics = sorted({f["topic"] for f in self.store.all_files()}
                        | set(TOPIC_RULES)
                        | {c["name"] for c in self.store.all_categories()}
                        | {"未分类"})
        topic, ok2 = QInputDialog.getItem(self, "新建笔记", "主题：", topics, 0, True)
        if not ok2:
            topic = "未分类"
        content = f"# {title}\n\n"
        res = self.pipe.ingest_raw(content, force_topic=topic)
        if not res.get("ok"):
            self.statusBar().showMessage(f"创建失败：{res.get('reason')}", 4000)
            return
        self._append_log(f"＋ 新建：{res['rel_path']}")
        self._refresh_tree()
        self._refresh_ai_notes()
        self._open_note(res["rel_path"])
        # 直接进入编辑
        if not self._note_edit_btn.isChecked():
            self._note_edit_btn.setChecked(True)
        self.notes_editor.moveCursor(self.notes_editor.textCursor().MoveOperation.End)

    def _build_graph_workspace(self) -> QWidget:
        from app.ui.graph import GraphView
        w = QWidget()
        # 工具栏跟随全局亮色 IDE_STYLE，不设局部主题
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        bar = QHBoxLayout()
        refresh = QPushButton("重新布局")
        refresh.clicked.connect(lambda: self._render_graph(refit=True))
        zoom_in = QPushButton("＋")
        zoom_in.setFixedWidth(36)
        zoom_in.clicked.connect(lambda: self._graph_view.zoom_in())
        zoom_out = QPushButton("－")
        zoom_out.setFixedWidth(36)
        zoom_out.clicked.connect(lambda: self._graph_view.zoom_out())
        legend = QLabel("  ━=双链  ┄=语义  红=孤立  颜色=主题   ·   滚轮缩放 · 拖空白抓手平移 · 双击打开")
        legend.setStyleSheet("color: #5f6368;")  # 显式设色，与全局亮色主题一致
        bar.addWidget(refresh); bar.addWidget(zoom_in); bar.addWidget(zoom_out)
        bar.addStretch(1); bar.addWidget(legend)
        lay.addLayout(bar)
        self._graph_view = GraphView(on_select=self._open_note)
        lay.addWidget(self._graph_view)
        # 仅图谱页（index 1）运行动画；进入时给焦点，保证空格平移可用
        self.workspace.currentChanged.connect(self._on_workspace_changed)
        return w

    def _on_workspace_changed(self, idx: int):
        if hasattr(self, "_graph_view"):
            self._graph_view.set_animation_enabled(idx == 1)
            if idx == 1:
                self._graph_view.setFocus()

    def _build_ai_workspace(self) -> QWidget:
        """聊天式布局：顶部配置行 → 中部输出占满 → 底部输入+发送。"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # 顶部：仅配置（模式 + 笔记）
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        self._ai_mode = FlatCombo()
        self._ai_mode.addItems(["问答", "润色笔记", "矛盾检测", "摘要",
                                "生成双链", "分类校准"])
        self._ai_mode.currentIndexChanged.connect(self._ai_mode_changed)
        self._ai_notes = FlatCombo()
        self._refresh_ai_notes()
        ctrl.addWidget(QLabel("模式："))
        ctrl.addWidget(self._ai_mode, 0)
        ctrl.addWidget(QLabel("最近："))
        ctrl.addWidget(self._ai_notes, 1)
        ctrl.addStretch(1)
        lay.addLayout(ctrl)

        # 挂载条：chips（📌 标题 ×）＋ 搜索挂载按钮；@ 也能触发
        self._mount_bar = QWidget()
        mb = QHBoxLayout(self._mount_bar)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(4)
        mb.addWidget(QLabel("挂载："))
        self._mount_chips_w = QWidget()
        self._mc_lay = QHBoxLayout(self._mount_chips_w)
        self._mc_lay.setContentsMargins(0, 0, 0, 0)
        self._mc_lay.setSpacing(4)
        self._mc_lay.addStretch(1)
        mb.addWidget(self._mount_chips_w, 1)
        btn_mount = QPushButton("＋ 搜索挂载")
        btn_mount.setToolTip("检索笔记挂载为 AI 上下文（也可在输入框打 @）")
        btn_mount.clicked.connect(self._show_mount_popup)
        mb.addWidget(btn_mount)
        self._mount_popup = _MountPopup(
            lambda q, n: self.search.search(q, top_n=n), parent=self)
        self._mount_popup.chosen.connect(self._on_mount_chosen)
        lay.addWidget(self._mount_bar)

        # 策展工具条（生成双链/分类校准 模式显示）
        self._curator_bar = QWidget()
        cb = QHBoxLayout(self._curator_bar)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.setSpacing(6)
        self._cur_scope = FlatCombo()
        self._cur_scope.addItems(["当前笔记", "指定分类", "全库"])
        self._cur_scope.currentIndexChanged.connect(
            lambda i: self._cur_topic.setEnabled(self._cur_scope.currentText() == "指定分类"))
        self._cur_topic = FlatCombo()
        self._cur_topic.setEnabled(False)
        self._btn_cur_dry = QPushButton("预览(dry-run)")
        self._btn_cur_dry.clicked.connect(self._curator_dry_run)
        self._btn_cur_scan = QPushButton("开始扫描")
        self._btn_cur_scan.clicked.connect(self._curator_start_scan)
        self._btn_cur_stop = QPushButton("停止")
        self._btn_cur_stop.setEnabled(False)
        self._btn_cur_stop.clicked.connect(lambda: self._cur_scan and
                                           self._cur_scan.update(cancel=True))
        self._cur_status = QLabel("")
        self._cur_status.setStyleSheet("color:#5f6368;")
        for x in (QLabel("范围："), self._cur_scope, self._cur_topic,
                  self._btn_cur_dry, self._btn_cur_scan, self._btn_cur_stop,
                  self._cur_status):
            cb.addWidget(x)
        cb.addStretch(1)
        self._curator_bar.setVisible(False)
        lay.addWidget(self._curator_bar)

        # 中部：输出区（占满剩余空间）
        self._ai_output = QTextBrowser()
        self._ai_output.setOpenExternalLinks(True)
        self._ai_output.setMinimumHeight(200)
        lay.addWidget(self._ai_output, 1)

        # 操作行：应用润色（生成结果后的动作）
        acts = QHBoxLayout()
        self._ai_apply = QPushButton("应用润色到笔记")
        self._ai_apply.setEnabled(False)
        self._ai_apply.clicked.connect(self._ai_apply_polish)
        acts.addWidget(self._ai_apply)
        acts.addStretch(1)
        lay.addLayout(acts)

        # 底部：输入框 + 发送（聊天式，Enter 发送 / Shift+Enter 换行）
        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self._ai_input = _AIInput()
        self._ai_input.setPlaceholderText(
            "输入问题…  Enter 发送，Shift+Enter 换行")
        self._ai_input.setFixedHeight(72)
        self._ai_input.submit.connect(self._run_ai)
        self._ai_input.at_requested.connect(self._on_at_requested)
        send = QPushButton("发送")
        send.setFixedWidth(72)
        send.clicked.connect(self._run_ai)
        input_row.addWidget(self._ai_input, 1)
        input_row.addWidget(send, 0)
        lay.addLayout(input_row)

        self._ai_last_result = None
        return w

    def _build_props_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        self._props_title = QLabel("（未选择笔记）")
        self._props_title.setStyleSheet("font-size: 14px; font-weight: bold; padding: 2px 4px;")
        lay.addWidget(self._props_title)
        self._props_info = QTextBrowser()
        self._props_info.setMaximumHeight(160)
        lay.addWidget(self._props_info)
        self._props_atoms = QTextBrowser()
        self._props_atoms.setPlaceholderText("原子片段")
        self._props_atoms.setMaximumHeight(180)
        lay.addWidget(self._props_atoms)
        # 相关笔记：可点击列表，点条目直接跳转
        self._props_related = QListWidget()
        self._props_related.setToolTip("点击相关笔记可直接打开")
        self._props_related.itemDoubleClicked.connect(self._open_related)
        lay.addWidget(self._props_related)
        lay.addStretch(1)
        return w

    def _open_related(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._open_note(path)

    def _build_log_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addStretch(1)
        clear = QPushButton("清空")
        clear.setFixedSize(48, 20)
        clear.setStyleSheet("font-size: 11px; padding: 0;")
        clear.clicked.connect(lambda: self.log.clear())
        bar.addWidget(clear)
        lay.addLayout(bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)
        return w

    def _build_snapshots_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._snap_list = QListWidget()
        lay.addWidget(self._snap_list)
        acts = QHBoxLayout()
        rb = QPushButton("回滚选中")
        rb.clicked.connect(self._rollback_selected)
        acts.addWidget(rb)
        acts.addStretch(1)
        lay.addLayout(acts)
        return w

    # ---------- 行为 ----------
    def _refresh_tree(self):
        """重建左侧导航树：按分类层 taxonomy 分组（内置+自定义分类+已有笔记主题）。"""
        model = QStandardItemModel()
        model.setHorizontalHeaderLabels(["名称"])
        root = model.invisibleRootItem()
        notes = self.store.all_files()
        by_topic = {}
        for f in notes:
            t = f["topic"] or "未分类"
            by_topic.setdefault(t, []).append(f)
        # 文件夹 = 分类层全集：内置规则主题 ∪ 自定义分类 ∪ 笔记里已有的主题
        taxonomy = set(TOPIC_RULES) | {c["name"] for c in self.store.all_categories()} \
            | set(by_topic.keys())
        for topic in sorted(taxonomy):
            files = by_topic.get(topic, [])
            topic_item = QStandardItem(f"{topic} ({len(files)})")
            topic_item.setEditable(False)
            topic_item.setIcon(_svg_icon("topic", ICON_BLUE, 16))
            topic_item.setData(topic, TOPIC_ROLE)
            for f in files:
                note_item = QStandardItem(f["title"])
                note_item.setEditable(False)
                note_item.setIcon(_svg_icon("note", ICON_GRAY, 16))
                note_item.setData(f["path"], Qt.ItemDataRole.UserRole)
                topic_item.appendRow(note_item)
            root.appendRow(topic_item)
        self.nav_tree.setModel(model)
        self.nav_tree.expandAll()

    def _on_tree_double_clicked(self, idx):
        model = self.nav_tree.model()
        path = model.data(idx, Qt.ItemDataRole.UserRole)
        if path:
            self._open_note(path)

    def _tree_context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        idx = self.nav_tree.indexAt(pos)
        model = self.nav_tree.model()
        is_folder = idx.isValid() and not idx.parent().isValid()
        path = None
        topic = None
        if idx.isValid():
            if is_folder:
                topic = model.data(idx, TOPIC_ROLE)
            else:
                path = model.data(idx, Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        act_new = menu.addAction("＋ 新建笔记")
        act_new.triggered.connect(self._new_note)
        act_cat = menu.addAction("📁 新建分类…")
        act_cat.triggered.connect(self._new_category)
        if path:
            menu.addSeparator()
            act_send = menu.addAction("📌 发送到 AI 管家")
            act_send.triggered.connect(lambda: self._send_to_pilot(path))
            act_ren = menu.addAction("✎ 重命名")
            act_ren.triggered.connect(lambda: self._rename_note(path))
            act_del = menu.addAction("🗑 删除")
            act_del.triggered.connect(lambda: self._delete_note(path))
        # 空的自定义分类才可删（内置分类受保护）
        if topic and model.rowCount(idx) == 0 and topic not in TOPIC_RULES:
            menu.addSeparator()
            act_delcat = menu.addAction(f"🗑 删除分类「{topic}」")
            act_delcat.triggered.connect(lambda: self._delete_category(topic))
        menu.exec(self.nav_tree.viewport().mapToGlobal(pos))

    def _new_category(self):
        """新建分类（分类层扩展）：名称 + 可选触发关键词，落 categories 表。"""
        import re
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("新建分类")
        form = QFormLayout(dlg)
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("如：旅行")
        kws_edit = QLineEdit()
        kws_edit.setPlaceholderText("可选：逗号分隔，命中即自动归入该分类")
        form.addRow("分类名称：", name_edit)
        form.addRow("触发关键词：", kws_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # 名称清洗：去首尾、非法路径字符替换、限长
        name = re.sub(r'[\\/:*?"<>|]', "-", name_edit.text().strip())[:30]
        if not name or name == "未分类":
            self.statusBar().showMessage("分类名无效", 2500)
            return
        existing_topics = {f["topic"] for f in self.store.all_files()} \
            | set(TOPIC_RULES) | {c["name"] for c in self.store.all_categories()}
        if name in existing_topics:
            self.statusBar().showMessage(f"分类已存在：{name}", 2500)
            return
        kws = [k.strip() for k in re.split(r"[,，、;；]", kws_edit.text()) if k.strip()]
        self.store.add_category(name, kws)
        self.pipe.router.refresh_provider()   # 分类器热加载新分类
        self._append_log(f"📁 新建分类：{name}"
                         + (f"（关键词：{'、'.join(kws)}）" if kws else ""))
        self.statusBar().showMessage(f"已新建分类：{name}", 2500)
        self._refresh_tree()

    def _delete_category(self, topic: str):
        """删除空的自定义分类（有笔记的分类不会出现删除入口）。"""
        from PySide6.QtWidgets import QMessageBox
        has_files = any(f["topic"] == topic for f in self.store.all_files())
        if has_files or topic in TOPIC_RULES:
            self.statusBar().showMessage("该分类受保护或非空，无法删除", 2500)
            return
        answer = QMessageBox.question(
            self, "删除分类", f"确定删除分类「{topic}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.store.drop_category(topic)
        self.pipe.router.refresh_provider()
        self._append_log(f"🗑 删除分类：{topic}")
        self._refresh_tree()

    def _rename_note(self, rel: str):
        from PySide6.QtWidgets import QInputDialog
        old_title = (self.store.get_file(rel) or {"title": ""})["title"]
        new_title, ok = QInputDialog.getText(self, "重命名笔记", "新标题：",
                                             text=old_title)
        if not ok or not new_title.strip() or new_title.strip() == old_title:
            return
        new_title = new_title.strip()
        # 更新正文首行 H1 + 文件名
        content = self.vault.read_note(rel)
        import re as _re
        if _re.search(r"^#\s+.+$", content, _re.M):
            content = _re.sub(r"^#\s+.+$", f"# {new_title}", content, count=1,
                              flags=_re.M)
        else:
            content = f"# {new_title}\n\n{content}"
        new_name = _re.sub(r"[\\/:*?\"<>|]", "-", new_title)[:60]
        new_rel = f"{rel.rsplit('/', 1)[0]}/{new_name}.md" if "/" in rel else f"{new_name}.md"
        # 目标重名则加序号
        n = 2
        while (self.vault.vault_path / new_rel).exists() and new_rel != rel:
            new_rel = f"{rel.rsplit('/',1)[0]}/{new_name}-{n}.md" if "/" in rel else f"{new_name}-{n}.md"
            n += 1
        (self.vault.vault_path / rel).rename(self.vault.vault_path / new_rel)
        self.store.rename_path(rel, new_rel)
        self.vault.write_note(new_rel, content)
        # 修复：改名换 H1 后必须重建原子/双链/FTS（此前只改了 meta，索引是旧的）
        self.pipe.reindex_note(new_rel, content)
        if self._current_note() == rel:
            self._cur_note = new_rel
            self._note_path_lbl.setText(new_rel)
            self.notes_preview.setMarkdown(self._preview_md(content))
        self._append_log(f"✎ 重命名：{rel} → {new_rel}")
        self._refresh_tree()
        self._refresh_ai_notes()

    def _delete_note(self, rel: str):
        answer = QMessageBox.question(
            self, "删除笔记", f"确定删除 {rel} 吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        full = self.vault.vault_path / rel
        if full.exists():
            full.unlink()
        self.store.delete_file(rel)
        if self._current_note() == rel:
            self._clear_note_views()   # 直接清理视图（跳过未保存提示：文件已删）
        self._append_log(f"🗑 删除：{rel}")
        self._refresh_tree()
        self._refresh_ai_notes()
        self._render_graph(refit=True)

    def _open_note(self, rel_path: str):
        """打开笔记：中间预览 + 右侧属性 + 底部版本历史 + 记忆权重+1。"""
        # 若正在编辑别的笔记且已改动，先提示（简单防丢：切走即丢弃编辑态）
        self.store.touch_file(rel_path)
        self._cur_note = rel_path
        content = self.vault.read_note(rel_path)
        self.notes_preview.setMarkdown(self._preview_md(content))
        # 切换笔记时退出编辑态，加载新内容
        if self._note_edit_btn.isChecked():
            self._note_edit_btn.setChecked(False)
        self._note_stack.setCurrentIndex(0)
        self._note_save_btn.setEnabled(False)
        self._note_close_btn.setEnabled(True)
        self._note_path_lbl.setText(rel_path)
        self.workspace.setCurrentIndex(0)

        f = self.store.get_file(rel_path)
        self._props_title.setText(f"📄 {f['title']}")
        atoms = self.store.atoms_for(rel_path)
        atoms_text = "\n".join(f"[{a['kind']}] {a['value']}" for a in atoms[:15])
        self._props_atoms.setPlainText(atoms_text)
        related = self.search.related(rel_path, 5)
        self._props_related.clear()
        if related:
            for r in related:
                self._props_related.addItem(f"• {r['title']}")
                self._props_related.item(self._props_related.count() - 1).setData(
                    Qt.ItemDataRole.UserRole, r["id"])
        else:
            self._props_related.addItem("（无）")
        meta = f"路径：{rel_path}\n主题：{f['topic']}\n类型：{f['type']}\n" \
               f"优先级：{f['priority']}\n使用次数：{f['usage_count']}\n" \
               f"原子数：{len(atoms)}"
        self._props_info.setPlainText(meta)
        self._load_history(rel_path)
        self.statusBar().showMessage(f"已打开：{rel_path}", 3000)

    def _render_graph(self, refit: bool = False):
        # refit=True：用户主动重排/删除后适应视图；False：后台刷新保留视角
        if hasattr(self, "_graph_view"):
            self._graph_view.set_data(self.graph_engine.build(), refit=refit)

    def _refresh_ai_notes(self):
        """下拉降级为"最近使用 20 篇"快捷入口；第一项是占位（不参与取径）。"""
        cur = self._ai_notes.currentData()
        self._ai_notes.blockSignals(True)
        self._ai_notes.clear()
        self._ai_notes.addItem("（最近笔记 ▾ 或 @ 挂载）", None)
        for f in self.store.top_used(20):
            self._ai_notes.addItem(f"[{f['topic']}] {f['title']}", f["path"])
        i = self._ai_notes.findData(cur) if cur else -1
        self._ai_notes.setCurrentIndex(i if i >= 0 else 0)
        self._ai_notes.blockSignals(False)

    def _poll(self):
        rows = self.store.conn.execute(
            "SELECT id, module, message FROM logs WHERE id>? ORDER BY id",
            (self._log_cursor,)).fetchall()
        change = False
        for r in rows:
            self._log_cursor = max(self._log_cursor, int(r["id"]))
            if r["module"] == "watcher":
                self._append_log(f"〔监听〕{r['message']}")
                change = True
        if change:
            self._refresh_tree()
            self._refresh_ai_notes()
            self._render_graph()

    def _boot_msg(self, msg):
        self._append_log(msg)

    def _append_log(self, msg):
        self.log.appendPlainText(msg)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    # ---------- 工具栏动作 ----------
    def _quick_add_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getMultiLineText(
            self, "Quick Add", "录入一条素材：")
        if ok and text.strip():
            res = self.pipe.ingest_raw(text.strip())
            if res["ok"]:
                self._append_log(f"✓ 已收录：{res['rel_path']}（{res['topic']}）")
                self._refresh_tree()
                self._refresh_ai_notes()
            else:
                self._append_log(f" 未收录：{res.get('reason')}")

    # ---------- 结构导出（工具栏按钮，与 CLI export 共用 core/export.py） ----------
    def _export_menu(self):
        menu = QMenu(self)
        menu.addAction("导出结构层（JSONL + 图谱）",
                       lambda: self._export_pick("structure"))
        menu.addAction("全库打包（ZIP，含全部笔记原文）",
                       lambda: self._export_pick("full"))
        menu.exec(QCursor.pos())

    def _export_pick(self, mode):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not d:
            return
        self._run_export(Path(d), mode)

    def _run_export(self, out, mode):
        """同步执行即可：结构导出万篇秒级，全库 zip 几千篇 2~5s，可接受。"""
        from core.export import export_full, export_structure
        try:
            if mode == "full":
                zp = export_full(self.store, self.vault, out)
                msg = f"导出完成 · 全库打包 {zp.name}（{zp.stat().st_size // 1024} KB）"
            else:
                m = export_structure(self.store, self.vault, out)
                c = m["counts"]
                msg = f"导出完成 · {c['files']} 篇 · {c['atoms']} 原子 → {out / 'structure'}"
            self.statusBar().showMessage(msg, 6000)
            self._append_log(f"📦 {msg}")
        except Exception as e:
            self.statusBar().showMessage(f"导出失败：{e}", 6000)

    # ---------- 批量导入（工具栏按钮 / 树拖拽 共用） ----------
    def _import_folder_dialog(self):
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "选择要导入的文件夹")
        if not d:
            return
        self._import_paths([d])

    def _import_paths(self, paths):
        """枚举→入队→启动/延长分批导入。paths 为本地路径（文件或目录，可混排）。"""
        files, skipped = collect_importable(paths)
        new = [f for f in files if str(f.resolve()) not in self._import_seen]
        for f in new:
            self._import_seen.add(str(f.resolve()))
        for s in skipped:
            self._append_log(f"⏭ 跳过（不支持/不存在）：{s}")
        if not new:
            self.statusBar().showMessage("没有可导入的新文件", 3000)
            return
        self._import_queue.extend(new)
        self._import_total += len(new)
        if self._import_active:              # 防重入：只延长队列，不起第二个循环
            self.statusBar().showMessage(
                f"已加入导入队列，待处理 {len(self._import_queue)} 个")
            return
        self._import_active = True
        self._import_cancel = False
        self._import_stats = {"ingested": 0, "duplicate": 0, "empty": 0,
                              "errors": 0, "details": []}
        self._show_import_stop(True)
        self._append_log(f"▶ 开始导入 {self._import_total} 个文件…")
        QTimer.singleShot(0, self._import_tick)

    def _import_tick(self):
        """每 tick 处理 IMPORT_BATCH 个文件后交还事件循环，UI 不冻结。"""
        if self._import_cancel or not self._import_queue:
            self._finish_import()
            return
        batch = self._import_queue[:IMPORT_BATCH]
        del self._import_queue[:IMPORT_BATCH]
        st = self._import_stats
        for p in batch:
            try:
                res = self.pipe.ingest_file(p)
                if res.get("ok"):
                    st["ingested"] += 1
                    st["details"].append(res["rel_path"])
                elif res.get("reason") == "duplicate":
                    st["duplicate"] += 1
                else:
                    st["empty"] += 1
            except Exception as e:
                st["errors"] += 1
                st["details"].append(f"ERR {p.name}: {e}")
                self.store.log("error", "import", f"{p.name}: {e}")
        done = self._import_total - len(self._import_queue)
        self.statusBar().showMessage(
            f"导入中 {done}/{self._import_total} · 收录{st['ingested']} "
            f"重复{st['duplicate']} 空{st['empty']} 失败{st['errors']}")
        QTimer.singleShot(0, self._import_tick)

    def _finish_import(self):
        self._import_active = False
        self._show_import_stop(False)
        st = self._import_stats or {}
        if self._import_cancel:
            prefix = "导入已停止 · "
            self._append_log(f"⏹ 导入已停止（剩余 {len(self._import_queue)} 个未处理）")
        else:
            prefix = "导入完成 · "
        self._append_log(
            f"{prefix}收录 {st.get('ingested', 0)} · 重复 {st.get('duplicate', 0)} · "
            f"空 {st.get('empty', 0)} · 失败 {st.get('errors', 0)}")
        self.statusBar().showMessage(
            f"{prefix}收录{st.get('ingested', 0)} 重复{st.get('duplicate', 0)} "
            f"空{st.get('empty', 0)} 失败{st.get('errors', 0)}", 6000)
        # 会话复位
        self._import_queue = []
        self._import_seen = set()
        self._import_total = 0
        self._import_stats = None
        self._import_cancel = False
        # 收尾一次性刷新（批量期间不逐 tick 重建大模型）
        self._refresh_tree()
        self._refresh_ai_notes()
        self._render_graph(refit=True)

    def _show_import_stop(self, on):
        if on:
            if self._import_stop_btn is None:
                self._import_stop_btn = QToolButton()
                self._import_stop_btn.setText("⏹ 停止导入")
                self._import_stop_btn.clicked.connect(self._cancel_import)
                self.statusBar().addPermanentWidget(self._import_stop_btn)
            self._import_stop_btn.show()
        elif self._import_stop_btn is not None:
            self._import_stop_btn.hide()

    def _cancel_import(self):
        self._import_cancel = True
        self.statusBar().showMessage("正在停止导入…")

    def _toggle_watch(self):
        self.statusBar().showMessage("监听已暂停" if self._btn_watch.isChecked() else "监听已恢复")

    # ---------- 设置（AI Provider / 索引维护） ----------
    def _settings_dialog(self):
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        prov = settings.GENERATIVE["providers"]
        dlg = QDialog(self)
        dlg.setWindowTitle("设置")
        form = QFormLayout(dlg)
        mode_cb = FlatCombo()
        for k, label in [("auto", "自动（Claude → OpenAI兼容 → Ollama → Mock）"),
                         ("claude", "Claude API"),
                         ("openai", "OpenAI 兼容 API（DeepSeek/Kimi/本地网关）"),
                         ("ollama", "本地 Ollama"),
                         ("mock", "离线 Mock")]:
            mode_cb.addItem(label, k)
        mode_cb.setCurrentIndex(max(mode_cb.findData(
            settings.GENERATIVE.get("mode", "auto")), 0))
        key_ed = QLineEdit(prov["claude"].get("api_key", ""))
        key_ed.setEchoMode(QLineEdit.EchoMode.Password)
        key_ed.setPlaceholderText("留空则保持当前密钥不变")
        model_ed = QLineEdit(prov["claude"].get("model", ""))
        oai = prov.setdefault("openai", {})
        akey_ed = QLineEdit(oai.get("api_key", ""))
        akey_ed.setEchoMode(QLineEdit.EchoMode.Password)
        akey_ed.setPlaceholderText("本地网关可留空；留空则保持不变")
        abase_ed = QLineEdit(oai.get("base_url", ""))
        abase_ed.setPlaceholderText("留空=官方；如 https://api.deepseek.com/v1")
        amodel_ed = QLineEdit(oai.get("model", ""))
        amodel_ed.setPlaceholderText("如 deepseek-chat / gpt-4o-mini（填了才启用）")
        ourl_ed = QLineEdit(prov["ollama"].get("base_url", ""))
        omod_ed = QLineEdit(prov["ollama"].get("model", ""))
        omod_ed.setPlaceholderText("如 qwen2.5:14b（留空=不启用 Ollama）")
        form.addRow("生成后端：", mode_cb)
        form.addRow("Claude API Key：", key_ed)
        form.addRow("Claude 模型：", model_ed)
        form.addRow("OpenAI兼容 Key：", akey_ed)
        form.addRow("OpenAI兼容 地址：", abase_ed)
        form.addRow("OpenAI兼容 模型：", amodel_ed)
        form.addRow("Ollama 地址：", ourl_ed)
        form.addRow("Ollama 模型：", omod_ed)
        hint = QLabel("⚠ 密钥以明文存于 ai-kms/.env；判断层规则分类器不需要任何密钥。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#9a6b00;")
        form.addRow(hint)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                | QDialogButtonBox.StandardButton.Cancel)
        b_rebuild = QPushButton("重建搜索索引")
        b_rebuild.setToolTip("清空 notes_fts 后分批重灌（jieba 升级词表漂移后的自救出口）")
        b_rebuild.clicked.connect(self._rebuild_fts_now)
        btns.addButton(b_rebuild, QDialogButtonBox.ButtonRole.ActionRole)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        mode = mode_cb.currentData()
        updates = {
            "KMS_PROVIDER_MODE": mode,
            "CLAUDE_MODEL": model_ed.text().strip() or "claude-3-5-sonnet-20240620",
            "OLLAMA_BASE_URL": ourl_ed.text().strip() or "http://localhost:11434",
            "OLLAMA_MODEL": omod_ed.text().strip(),
            "OPENAI_BASE_URL": abase_ed.text().strip(),
            "OPENAI_MODEL": amodel_ed.text().strip(),
        }
        k = key_ed.text().strip()
        if k or not prov["claude"].get("api_key"):
            updates["ANTHROPIC_API_KEY"] = k
        ak = akey_ed.text().strip()
        if ak or not oai.get("api_key"):
            updates["OPENAI_API_KEY"] = ak
        try:
            settings.update_env_values(updates)
        except OSError as e:
            self._append_log(f"⚠ .env 写入失败：{e}")
            return
        # 运行时即刻生效：get_provider 每次现读 settings
        settings.GENERATIVE["mode"] = mode
        prov["claude"]["model"] = updates["CLAUDE_MODEL"]
        prov["ollama"]["base_url"] = updates["OLLAMA_BASE_URL"]
        prov["ollama"]["model"] = updates["OLLAMA_MODEL"]
        oai["base_url"] = updates["OPENAI_BASE_URL"]
        oai["model"] = updates["OPENAI_MODEL"]
        if k:
            prov["claude"]["api_key"] = k
        if ak:
            oai["api_key"] = ak
        from core.generative.provider import get_provider
        p = get_provider()
        self._append_log(f"⚙ 设置已保存（写入 .env）· 当前生效生成后端：{p.name}")
        self.statusBar().showMessage(f"生成后端：{p.name}", 4000)

    def _rebuild_fts_now(self):
        if not self.store.fts_ok:
            self.statusBar().showMessage("本机 SQLite 不支持 FTS5，无需重建", 4000)
            return
        self.store.conn.execute("DELETE FROM notes_fts")
        self.store.conn.commit()
        self._append_log("🔨 搜索索引全量重建启动…")
        QTimer.singleShot(0, self._fts_rebuild_tick)

    def _focus_search(self):
        self._search_bar.setFocus()
        self._search_bar.selectAll()
        if self._search_bar.text().strip():
            self._do_search()

    def _do_search(self):
        """执行检索并刷新框下浮层；空查询收起浮层。"""
        q = self._search_bar.text().strip()
        self._search_popup.clear()
        if not q:
            self._search_popup.hide()
            return
        res = self.search.search(q)
        if not res:
            it = QListWidgetItem("（无匹配结果）")
            it.setFlags(Qt.ItemFlag.NoItemFlags)   # 不可点
            self._search_popup.addItem(it)
        else:
            for r in res:
                it = QListWidgetItem(f"{r['title']}   · {r['topic']}")
                it.setToolTip(f"{r['path']}\n相关度 {r['score']} · 回链 "
                              f"{len(r['backlinks'])} 条")
                it.setData(Qt.ItemDataRole.UserRole, r["path"])
                self._search_popup.addItem(it)
        # 高度贴合条数
        rows = self._search_popup.count()
        self._search_popup.setFixedHeight(min(10 + rows * 33, 360))
        self._show_search_popup()
        self._append_log(f"搜索 '{q}' → {len(res)} 条结果")

    def _show_search_popup(self):
        bar = self._search_bar
        self._search_popup.move(
            bar.mapToGlobal(QPoint(0, bar.height() + 6)))
        self._search_popup.show()
        self._search_popup.raise_()

    def _open_search_item(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return                      # “无结果”占位项
        self._search_popup.hide()
        self._open_note(path)

    def _on_search_enter(self):
        """Enter：立即检索；已有结果则打开当前行（默认第一条）。"""
        self._search_timer.stop()
        self._do_search()
        pop = self._search_popup
        if not pop.isVisible():
            return
        row = max(pop.currentRow(), 0)
        item = pop.item(row)
        if item is not None and item.data(Qt.ItemDataRole.UserRole):
            self._open_search_item(item)

    # ---------- AI ----------
    def _ai_set_md(self, md: str):
        """QTextBrowser 无 appendMarkdown（PySide6 绑定缺失）→ 自持缓冲。"""
        self._ai_md_buf = md or ""
        self._ai_output.setMarkdown(self._ai_md_buf)

    def _ai_append_md(self, md: str):
        self._ai_md_buf = getattr(self, "_ai_md_buf", "") + "\n\n" + md
        self._ai_output.setMarkdown(self._ai_md_buf)

    def _ai_mode_changed(self, idx):
        mode = self._ai_mode.currentText()
        curator_mode = mode in ("生成双链", "分类校准")
        self._curator_bar.setVisible(curator_mode)
        if curator_mode:
            # 刷新分类选项（与 _new_note 同款全集）
            topics = sorted({f["topic"] for f in self.store.all_files()}
                            | set(TOPIC_RULES)
                            | {c["name"] for c in self.store.all_categories()}
                            | {"未分类"})
            cur = self._cur_topic.currentText()
            self._cur_topic.blockSignals(True)
            self._cur_topic.clear()
            self._cur_topic.addItems(topics)
            if cur in topics:
                self._cur_topic.setCurrentText(cur)
            self._cur_topic.setEnabled(self._cur_scope.currentText() == "指定分类")
            self._cur_scope.blockSignals(False)
            hint = ("扫描笔记正文，生成 [[双链]] 建议进审阅队列"
                    if mode == "生成双链" else
                    "只查分类缓存重算归属；证据不足项需显式『重读校准』")
            self._ai_set_md(f"### {mode}\n{hint}\n\n- **当前笔记**：仅选中/挂载的一篇\n- **指定分类**：该分类下全部\n- **全库**：所有已登记笔记\n\n先「预览(dry-run)」看工作量，再「开始扫描」。建议不会直接改笔记——全部进**审阅队列**逐条确认。")
            return
        if mode == "问答":
            self._ai_input.setPlaceholderText("输入问题（会在知识库中 RAG 检索后作答）；@ 可挂载笔记")
        elif mode == "润色笔记":
            self._ai_input.setPlaceholderText("附加润色要求（可选）")
        elif mode == "矛盾检测":
            self._ai_input.setPlaceholderText("检测选定笔记与相关笔记是否有矛盾")
        elif mode == "摘要":
            self._ai_input.setPlaceholderText("生成选定笔记的 3 句摘要")

    def _current_rel_path(self) -> str | None:
        """取径序：下拉显式选择 → 挂载首篇 → 正在预览的笔记。"""
        idx = self._ai_notes.currentIndex()
        if idx >= 0:
            p = self._ai_notes.itemData(idx)
            if p:
                return p
        if self._mounted:
            return self._mounted[0]
        return getattr(self, "_cur_note", None)

    # ---------- 挂载体系 ----------
    def _show_mount_popup(self):
        self._at_pos = -1
        self._mount_popup.open_at(self._mount_bar)

    def _on_at_requested(self, pos):
        self._at_pos = pos
        self._mount_popup.open_at(self._ai_input)

    def _on_mount_chosen(self, path):
        self._mount_add(path)
        if self._at_pos > 0:               # 清掉输入框里触发用的 @…残串
            cur = self._ai_input.textCursor()
            start = self._at_pos - 1       # '@' 字符位置
            end = min(cur.position(), len(self._ai_input.toPlainText()))
            if end > start:
                cur.setPosition(start)
                cur.setPosition(end, Qt.MoveMode.KeepAnchor)
                cur.removeSelectedText()
            self._at_pos = -1

    def _send_to_pilot(self, path):
        """树右键入口：挂载该笔记并切到 AI 管家工作区。"""
        self._mount_add(path)
        self.workspace.setCurrentIndex(2)
        self.statusBar().showMessage("已挂载到 AI 管家（chips 点 × 可取消）", 3000)

    def _mount_add(self, path):
        f = self.store.get_file(path)
        if not f or path in self._mounted:
            return
        self._mounted.insert(0, path)
        self._mounted = self._mounted[:3]
        self._refresh_mounts()

    def _mount_remove(self, path):
        if path in self._mounted:
            self._mounted.remove(path)
            self._refresh_mounts()

    def _refresh_mounts(self):
        while self._mc_lay.count() > 1:    # 保留尾部 stretch
            item = self._mc_lay.takeAt(0)
            wd = item.widget() if item else None
            if wd is not None:
                wd.setParent(None)         # 先取引用再摘除：setParent 会顺带销毁 item
                wd.deleteLater()
        for i, p in enumerate(self._mounted):
            f = self.store.get_file(p)
            title = f["title"] if f else p
            b = QToolButton()
            b.setText(f"📌 {title} ×")
            b.setAutoRaise(True)
            b.setToolTip(f"{p}\n点击取消挂载")
            b.setStyleSheet("""
                QToolButton { background: #eef3fb; border: 1px solid #d3e0f3;
                    border-radius: 9px; padding: 2px 8px; font-size: 12px; color: #174ea6; }
                QToolButton:hover { background: #dfeafb; }
            """)
            b.clicked.connect(lambda _, x=p: self._mount_remove(x))
            self._mc_lay.insertWidget(i, b)

    def _run_ai(self):
        rel = self._current_rel_path()
        mode = self._ai_mode.currentText()
        self._ai_set_md("")
        self._ai_last_result = None
        self._ai_apply.setEnabled(False)
        try:
            if mode == "问答":
                q = self._ai_input.toPlainText().strip()
                if not q:
                    self._ai_set_md("**请输入问题**")
                    return
                out = self.pilot.ask(q, extra_paths=self._mounted)
                self._ai_set_md(out)
            elif mode == "润色笔记":
                if not rel:
                    self._ai_set_md("**请先选择笔记**")
                    return
                preview = self.pilot.polish_preview(rel)
                self._ai_last_result = preview
                self._ai_set_md(
                    f"### 原文（{len(preview['original'])} 字符）\n{preview['original']}\n"
                    f"### 润色稿（{len(preview['polished'])} 字符）\n{preview['polished']}\n"
                )
                self._ai_apply.setEnabled(True)
            elif mode == "矛盾检测":
                if not rel:
                    self._ai_set_md("**请先选择笔记**")
                    return
                issues = self.pilot.detect_contradicts(rel)
                if not issues:
                    self._ai_set_md("**未检测到矛盾**（Mock 模式恒返回空）。")
                    return
                body = "\n".join(
                    f"- **[{i.get('severity','?').upper()}]** {i.get('issue','')}  "
                    f"(vs {i.get('against','')})\n  → {i.get('suggestion','')}"
                    for i in issues
                )
                self._ai_set_md(f"### 发现 {len(issues)} 处矛盾\n{body}")
            elif mode == "摘要":
                if not rel:
                    self._ai_set_md("**请先选择笔记**")
                    return
                s = self.pilot.summarize_note(rel)
                self._ai_set_md(f"### 摘要\n{s}")
        except ConfirmationNeeded:
            self._ai_set_md("⚠ 已触发人工确认开关。")
        except Exception as e:
            self._ai_set_md(f"❌ 失败：{e}")

    def _ai_apply_polish(self):
        if not self._ai_last_result or not self._current_rel_path():
            return
        rel = self._current_rel_path()
        try:
            res = self.pilot.apply_change(
                rel, self._ai_last_result["polished"], "AI 润色",
                require_confirm=True,
            )
            self._ai_append_md("\n✅ 已直接写回。")
        except ConfirmationNeeded as e:
            answer = QMessageBox.question(
                self, "确认 AI 写回",
                f"即将把 AI 润色稿写入 {e.rel_path}，是否确认？\n"
                f"预览：{e.preview[:200]}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                res = self.pilot.confirm_change(e.rel_path, e.new_content, e.reason)
                self._ai_append_md(f"\n✅ 已确认写回（快照 #{res.get('snapshot_id')}）。")
            else:
                self._ai_append_md("\n⏭ 已取消写回。")
        self._refresh_tree()

    # ---------- 策展扫描（生成双链 / 分类校准） ----------
    CUR_MODE_KIND = {"生成双链": "wikilink", "分类校准": "topic"}

    def _curator_scope(self) -> dict:
        s = self._cur_scope.currentText()
        if s == "指定分类":
            return {"type": "topic", "topic": self._cur_topic.currentText()}
        if s == "全库":
            return {"type": "all"}
        rel = self._current_rel_path() or getattr(self, "_cur_note", None)
        return {"type": "note", "paths": [rel] if rel else []}

    def _curator_dry_run(self):
        kind = self.CUR_MODE_KIND[self._ai_mode.currentText()]
        scope = self._curator_scope()
        if kind == "wikilink":
            r = self.curator.wikilinks_dry_run(scope)
            self._ai_append_md(
                f"\n### Dry-run 预览\n将扫描 **{r['will_scan']}** 篇，逐篇生成建议进审阅队列；"
                "全程可停止，建议不会自动写入任何笔记。")
        else:
            r = self.curator.recalibrate_dry_run(scope)
            self._ai_append_md(
                f"\n### Dry-run 预览\n将扫描 **{r['will_scan']}** 篇（纯查缓存，不读笔记文件）\n"
                f"- 缓存证据充分：**{r['cache_ok']}** 篇\n"
                f"- 证据不足（打平/缺记录）：**{r['needs_reread']}** 篇 → 只标记，"
                "不自动重读；可在审阅队列点『重读校准』显式处理（本地规则，零模型成本）。")

    def _curator_start_scan(self):
        if self._cur_scan:                        # 防重入
            self.statusBar().showMessage("策展扫描进行中…", 2500)
            return
        kind = self.CUR_MODE_KIND[self._ai_mode.currentText()]
        paths = self.curator.resolve_scope(self._curator_scope())
        if not paths:
            self.statusBar().showMessage("所选范围内没有笔记", 3000)
            return
        self._cur_scan = {"kind": kind, "queue": paths, "total": len(paths),
                          "scanned": 0, "added": 0, "cancel": False}
        self._btn_cur_scan.setEnabled(False)
        self._btn_cur_dry.setEnabled(False)
        self._btn_cur_stop.setEnabled(True)
        self._append_log(f"▶ 策展扫描（{kind}）：{len(paths)} 篇")
        QTimer.singleShot(0, self._curator_tick)

    def _curator_tick(self):
        s = self._cur_scan
        if s is None:
            return
        if s["cancel"] or not s["queue"]:
            self._curator_finish()
            return
        fn = (self.curator.wikilinks_scan if s["kind"] == "wikilink"
              else self.curator.recalibrate_scan)
        r = fn(s["queue"], CURATOR_BATCH)
        del s["queue"][:r["scanned"]]
        s["scanned"] += r["scanned"]
        s["added"] += r["added"]
        self._cur_status.setText(f"{s['scanned']}/{s['total']} · 新增建议 {s['added']}")
        QTimer.singleShot(0, self._curator_tick)

    def _curator_finish(self):
        s = self._cur_scan
        self._cur_scan = None
        self._btn_cur_scan.setEnabled(True)
        self._btn_cur_dry.setEnabled(True)
        self._btn_cur_stop.setEnabled(False)
        self._cur_status.setText("")
        if s:
            head = "扫描完成" if not s["cancel"] else f"扫描已停止（剩 {len(s['queue'])} 未处理）"
            msg = f"{head} · 扫描 {s['scanned']} 篇 · 待审阅建议共 {self.store.count_pending()} 条"
            self._append_log(f"✅ {msg}")
            self.statusBar().showMessage(msg, 6000)
        self.open_review()

    def open_review(self):
        if not self._panel_state.get("bottom"):
            self._toggle_panel("bottom")
        self.output_tabs.setCurrentIndex(self._review_tab_idx)
        self._reload_review()

    # ---------- 审阅队列面板 ----------
    _REV_STATUS_CN = {"pending": "待处理", "applied": "已采纳",
                      "skipped": "已跳过", "obsolete": "已失效"}
    _REV_KIND_CN = {"wikilink": "双链", "topic": "改分类"}

    def _build_review_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._rev_kind = FlatCombo()
        self._rev_kind.addItem("全部", None)
        self._rev_kind.addItem("双链", "wikilink")
        self._rev_kind.addItem("改分类", "topic")
        self._rev_status = FlatCombo()
        for key, cn in self._REV_STATUS_CN.items():   # dict 是 英文key→中文label
            self._rev_status.addItem(cn, key)
        self._rev_kind.currentIndexChanged.connect(lambda _: self._reload_review())
        self._rev_status.currentIndexChanged.connect(lambda _: self._reload_review())
        self._rev_thresh = QDoubleSpinBox()
        self._rev_thresh.setRange(0.0, 1.0)
        self._rev_thresh.setSingleStep(0.05)
        self._rev_thresh.setValue(0.80)
        self._rev_thresh.setToolTip("批量采纳的置信度阈值")
        b_batch = QPushButton("≥阈值全部采纳")
        b_batch.clicked.connect(self._batch_approve)
        b_reread = QPushButton("重读校准")
        b_reread.setToolTip("对『证据不足』的分类校准建议显式重读原文重判（本地规则）")
        b_reread.clicked.connect(self._reread_clicked)
        b_skip = QPushButton("跳过当前筛选")
        b_skip.clicked.connect(self._skip_filtered)
        b_purge = QPushButton("清理已处理")
        b_purge.clicked.connect(self._purge_review)
        self._rev_count = QLabel("")
        self._rev_count.setStyleSheet("color:#5f6368;")
        for x in (QLabel("类型"), self._rev_kind, QLabel("状态"), self._rev_status,
                  self._rev_thresh, b_batch, b_reread, b_skip, b_purge):
            bar.addWidget(x)
        bar.addWidget(self._rev_count, 1)
        lay.addLayout(bar)

        self._rev_table = QTableWidget(0, 6)
        self._rev_table.setHorizontalHeaderLabels(
            ["状态", "类型", "笔记", "建议", "置信度", "操作"])
        self._rev_table.verticalHeader().setVisible(False)
        self._rev_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._rev_table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._rev_table.setColumnWidth(0, 64)
        self._rev_table.setColumnWidth(1, 64)
        self._rev_table.setColumnWidth(4, 60)
        self._rev_table.setColumnWidth(5, 158)
        self._rev_table.itemDoubleClicked.connect(self._rev_item_dbl)
        lay.addWidget(self._rev_table, 1)
        return w

    def _reload_review(self):
        kind = self._rev_kind.currentData()
        status = self._rev_status.currentData()
        if status == "pending":
            rows = self.store.pending_suggestions(kind=kind, limit=500)
        else:
            rows = self.store.list_suggestions(status, kind=kind, limit=500)
        t = self._rev_table
        t.setRowCount(len(rows))
        for i, s in enumerate(rows):
            pl = s.get("payload") or {}
            t.setItem(i, 0, QTableWidgetItem(self._REV_STATUS_CN.get(s["status"], s["status"])))
            t.setItem(i, 1, QTableWidgetItem(self._REV_KIND_CN.get(s["kind"], s["kind"])))
            note_item = QTableWidgetItem(s["file_path"])
            note_item.setData(Qt.ItemDataRole.UserRole, s["file_path"])
            t.setItem(i, 2, note_item)
            if s["kind"] == "topic":
                sug = f"{pl.get('now_topic', '?')} → {s['target']}"
                if pl.get("needs_reread"):
                    sug += "（待重读）"
            else:
                sug = f"[[{s['target']}]]"
                if pl.get("insert_at") is None:
                    sug += "（追加相关段）"
            t.setItem(i, 3, QTableWidgetItem(sug))
            t.setItem(i, 4, QTableWidgetItem(f"{s['confidence']:.2f}"))
            if s["status"] == "pending":
                cell = QWidget()
                cl = QHBoxLayout(cell)
                cl.setContentsMargins(2, 0, 2, 0)
                cl.setSpacing(3)
                b_ok = QPushButton("采纳")
                b_ok.clicked.connect(lambda _, x=s["id"]: self._review_apply_one(x))
                b_ed = QPushButton("编辑")
                b_ed.setToolTip("改目标后采纳")
                b_ed.clicked.connect(lambda _, x=s["id"]: self._review_edit_one(x))
                b_no = QPushButton("跳过")
                b_no.clicked.connect(lambda _, x=s["id"]: self._review_skip_one(x))
                for b in (b_ok, b_ed, b_no):
                    b.setStyleSheet("font-size:11px; padding:1px 6px;")
                    cl.addWidget(b)
                t.setCellWidget(i, 5, cell)
        self._rev_count.setText(
            f"待处理 {self.store.count_pending()} · 本页 {len(rows)}")
        self.output_tabs.setTabText(
            self._review_tab_idx, f"审阅队列({self.store.count_pending()})")

    def _rev_item_dbl(self, item):
        if item.column() == 2:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and self.store.file_exists(path):
                self._open_note(path)

    def _review_apply_one(self, sid, override=None):
        res = self.curator.apply_suggestion(sid, override)
        if res.get("ok"):
            self._append_log(f"✓ 采纳建议 #{sid}：{res}")
        else:
            self._append_log(f"⚠ 建议 #{sid} 未采纳：{res.get('reason')}")
        self._refresh_tree()
        self._refresh_ai_notes()
        self._reload_review()

    def _review_edit_one(self, sid):
        from PySide6.QtWidgets import QInputDialog
        s = self.store.get_suggestion(sid)
        if not s:
            return
        tip = "新的分类名：" if s["kind"] == "topic" else "链接目标（笔记文件名）："
        text, ok = QInputDialog.getText(self, "编辑后采纳", tip, text=s["target"])
        if ok and text.strip():
            self._review_apply_one(sid, text.strip())

    def _review_skip_one(self, sid):
        self.store.resolve_suggestion(sid, "skipped")
        self._reload_review()

    def _busy(self) -> bool:
        return bool(self._apply_queue or self._reread_queue)

    def _set_busy_stop(self, text: str, on: bool):
        if on:
            if self._busy_stop is None:
                self._busy_stop = QToolButton()
                self._busy_stop.clicked.connect(self._cancel_busy)
                self.statusBar().addPermanentWidget(self._busy_stop)
            self._busy_stop.setText(text)
            self._busy_stop.show()
        elif self._busy_stop is not None:
            self._busy_stop.hide()

    def _cancel_busy(self):
        n1, n2 = len(self._apply_queue), len(self._reread_queue)
        self._apply_queue = []
        self._reread_queue = []
        self._append_log(f"⏹ 批处理已取消（待采纳 {n1} · 待重读 {n2}）")

    def _batch_approve(self):
        if self._busy():
            self.statusBar().showMessage("已有批处理在运行", 2500)
            return
        th = self._rev_thresh.value()
        rows = self.store.pending_suggestions(min_conf=th, limit=2000)
        if not rows:
            self.statusBar().showMessage(f"没有置信度 ≥ {th:.2f} 的待处理建议", 3000)
            return
        self._apply_queue = [r["id"] for r in rows]
        self._set_busy_stop("⏹ 取消批量采纳", True)
        self._append_log(f"▶ 批量采纳 ≥{th:.2f}：{len(rows)} 条（逐条快照，可中断）")
        QTimer.singleShot(0, self._apply_batch_tick)

    def _apply_batch_tick(self):
        if not self._apply_queue:
            self._set_busy_stop("", False)
            self._append_log("✅ 批量采纳结束")
            self._refresh_tree()
            self._reload_review()
            return
        sid = self._apply_queue.pop(0)
        res = self.curator.apply_suggestion(sid)
        if not res.get("ok"):
            self._append_log(f"⚠ #{sid} 未应用（{res.get('reason')}），留待人工")
        self.statusBar().showMessage(f"批量采纳中 · 剩 {len(self._apply_queue)}")
        QTimer.singleShot(0, self._apply_batch_tick)

    def _reread_clicked(self):
        if self._busy():
            self.statusBar().showMessage("已有批处理在运行", 2500)
            return
        rows = [s for s in self.store.pending_suggestions(kind="topic", limit=5000)
                if s["payload"].get("needs_reread")]
        rels = sorted({s["file_path"] for s in rows})
        rels = [r for r in rels if self.store.file_exists(r)]
        if not rels:
            self.statusBar().showMessage("没有待重读的校准建议", 3000)
            return
        answer = QMessageBox.question(
            self, "重读校准",
            f"将重读 {len(rels)} 篇缓存证据不足的笔记并重跑分类器（本地规则，零模型成本），"
            f"结果覆盖对应建议。继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._reread_queue = rels
        self._set_busy_stop("⏹ 取消重读", True)
        QTimer.singleShot(0, self._reread_tick)

    def _reread_tick(self):
        if not self._reread_queue:
            self._set_busy_stop("", False)
            self._append_log("✅ 重读校准完成")
            self._refresh_tree()
            self._reload_review()
            return
        batch = self._reread_queue[:10]
        del self._reread_queue[:10]
        out = self.curator.reread_recalibrate(batch)
        self.statusBar().showMessage(
            f"重读校准中 · 剩 {len(self._reread_queue)} · 改判 {out['changed']}")
        QTimer.singleShot(0, self._reread_tick)

    def _skip_filtered(self):
        kind = self._rev_kind.currentData()
        rows = self.store.pending_suggestions(kind=kind, limit=2000)
        for r in rows:
            self.store.resolve_suggestion(r["id"], "skipped")
        self._append_log(f"⏭ 跳过当前筛选 {len(rows)} 条")
        self._reload_review()

    def _purge_review(self):
        n = self.store.purge_resolved()
        self._append_log(f"🧹 清理已处理建议 {n} 条")
        self._reload_review()

    def _load_history(self, rel: str):
        """加载某笔记的版本快照到底部"版本历史"列表。"""
        self._snap_list.clear()
        for h in self.pilot.history(rel):
            self._snap_list.addItem(
                f"#{h['id']}  {h['created_at']}  {h['reason']}  ({h['size']}B)")
            self._snap_list.item(self._snap_list.count() - 1).setData(
                Qt.ItemDataRole.UserRole, h["id"])

    def _rollback_selected(self):
        row = self._snap_list.currentRow()
        if row < 0:
            self.statusBar().showMessage("请先在版本历史中选中一条", 3000)
            return
        snap_id = self._snap_list.item(row).data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self, "确认回滚",
            f"回滚到快照 #{snap_id}？当前内容会先被快照保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        reason = self.pilot.rollback(snap_id)
        rel = self._current_note()
        if rel:
            # 回滚后刷新预览/编辑/历史，并重建索引（修复：回滚内容后索引曾腐烂）
            content = self.vault.read_note(rel)
            self.pipe.reindex_note(rel, content)
            self.notes_preview.setMarkdown(self._preview_md(content))
            if self._note_edit_btn.isChecked():
                self.notes_editor.setPlainText(content)
            self._load_history(rel)
            self._refresh_tree()
            self._refresh_ai_notes()
            self._append_log(f"⏪ 回滚：{rel} → 快照 #{snap_id} ({reason})")
            self.statusBar().showMessage(f"已回滚到 #{snap_id}", 3000)

    # ---------- 无边框窗口：边缘拖拽缩放 ----------
    def _edge_at(self, pos) -> tuple:
        """判断窗口内某点（本地图像坐标）落在哪些边缘感应区。

        返回 (left, right, top, bottom) 布尔元组；全 False 表示不在边缘。
        """
        r = self.rect()
        m = self._edge_margin
        return (
            pos.x() <= m,
            pos.x() >= r.width() - m,
            pos.y() <= m,
            pos.y() >= r.height() - m,
        )

    @staticmethod
    def _cursor_for_edges(edges) -> Qt.CursorShape:
        l, r, t, b = edges
        if (l and t) or (r and b):
            return Qt.CursorShape.SizeFDiagCursor
        if (r and t) or (l and b):
            return Qt.CursorShape.SizeBDiagCursor
        if l or r:
            return Qt.CursorShape.SizeHorCursor
        if t or b:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _apply_resize(self, global_pos):
        """根据拖拽位移实时调整窗口 geometry。"""
        geo = self._resize_start_geo
        if geo is None:
            return
        dx = global_pos.x() - self._resize_start_pos.x()
        dy = global_pos.y() - self._resize_start_pos.y()
        l, r, t, b = self._resize_edges

        x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
        min_w, min_h = self.minimumWidth(), self.minimumHeight()

        if l:
            nw = w - dx
            if nw >= min_w:
                x = geo.x() + dx
                w = nw
            else:
                x = geo.x() + (w - min_w)
                w = min_w
        if r:
            w = max(min_w, w + dx)
        if t:
            nh = h - dy
            if nh >= min_h:
                y = geo.y() + dy
                h = nh
            else:
                y = geo.y() + (h - min_h)
                h = min_h
        if b:
            h = max(min_h, h + dy)

        self.setGeometry(x, y, w, h)

    def eventFilter(self, obj, event):
        """无边框窗口：边缘缩放 + 光标反馈。

        光标用 **QApplication 全局覆盖光标（override cursor）**：
        只在"鼠标真正靠近窗口边缘"时叠加覆盖，离开边缘即撤销。
        **全程不修改任何子控件的光标** —— 因此不可能出现子控件光标
        （分割条双箭头 / 文本 IBeam / 标签箭头）被改坏或卡死的问题。
        """
        et = event.type()

        # ---- 搜索浮层收起（ToolTip 窗口不自动关，手动管理）----
        # 放在 in_window 守卫之前：↓ 后焦点在浮层上，Esc/点击都要能收到。
        # try 守卫：关窗清理期 C++ 对象可能先亡（libshiboken RuntimeError），跳过即可。
        pop = getattr(self, "_search_popup", None)
        try:
            if pop is not None and pop.isVisible():
                if et == QEvent.Type.MouseButtonPress:
                    gp = event.globalPosition().toPoint()
                    if (not pop.geometry().contains(gp)
                            and not self._search_bar.rect().contains(
                                self._search_bar.mapFromGlobal(gp))):
                        pop.hide()      # 点外部 → 收起，事件不吞（点哪算哪）
                elif et == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                    pop.hide()
                    return True         # Esc 只关浮层
                elif et in (QEvent.Type.Move, QEvent.Type.Resize) and obj is self:
                    pop.hide()          # 主窗口动了，浮层位置作废
        except RuntimeError:
            pop = None                  # 浮层已析构（清理阶段），后续不再触碰

        in_window = (obj is self) or (
            isinstance(obj, QWidget) and obj.window() is self)
        if not in_window:
            return super().eventFilter(obj, event)

        app = QApplication.instance()

        # 搜索框 ↓：焦点移入结果浮层， ↑↓/Enter 选择打开
        if (et == QEvent.Type.KeyPress
                and obj is getattr(self, "_search_bar", None)
                and event.key() == Qt.Key.Key_Down):
            pop = getattr(self, "_search_popup", None)
            if pop is not None and pop.isVisible() and pop.count():
                pop.setCurrentRow(max(pop.currentRow(), 0))
                pop.setFocus()
                return True
            return False

        if et == QEvent.Type.MouseMove:
            gp = event.globalPosition().toPoint()
            # 正在拖拽缩放 → 只更新尺寸
            if self._resize_edges is not None and self._resize_start_pos is not None:
                self._apply_resize(gp)
                return True
            # 未拖拽：仅靠近窗口边缘时叠加"缩放覆盖光标"，否则撤销。
            lp = self.mapFromGlobal(gp)
            edges = self._edge_at(lp)
            near_edge = any(edges) and not self.isMaximized()
            if near_edge:
                shape = self._cursor_for_edges(edges)
                if self._override_active:
                    app.changeOverrideCursor(QCursor(shape))
                else:
                    app.setOverrideCursor(QCursor(shape))
                    self._override_active = True
            elif self._override_active:
                app.restoreOverrideCursor()
                self._override_active = False

        elif et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            lp = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._edge_at(lp)
            if any(edges) and not self.isMaximized():
                self._resize_edges = edges
                self._resize_start_geo = self.geometry()
                self._resize_start_pos = event.globalPosition().toPoint()
                return True  # 吃掉事件，不让子控件处理

        elif et == QEvent.Type.MouseButtonRelease:
            if self._resize_edges is not None:
                self._resize_edges = None
                self._resize_start_geo = None
                self._resize_start_pos = None
                return True

        elif et == QEvent.Type.Leave:
            # 鼠标离开窗口 → 确保撤销覆盖光标，不留残影
            if self._override_active:
                app.restoreOverrideCursor()
                self._override_active = False

        return super().eventFilter(obj, event)

    def changeEvent(self, event):
        """窗口状态变化（最大化/还原）→ 同步标题栏按钮图标。"""
        if event.type() == QEvent.Type.WindowStateChange:
            if hasattr(self, "_title_bar"):
                self._title_bar.set_maximized(self.isMaximized())
        super().changeEvent(event)

    def closeEvent(self, event):
        try:
            self.watcher.stop()
            self.store.close()
        except Exception:
            pass
        super().closeEvent(event)


def run() -> int:
    if not _HAVE_QT:  # pragma: no cover
        print("PySide6 未安装。GUI 需要它，本批核心请用 CLI：python main.py watch / search")
        return 1
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(IDE_STYLE)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())