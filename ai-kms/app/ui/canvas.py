"""白板画布工作区（JSON Canvas 文档编辑器，QGraphicsView 自研，浅色风）。

- 卡片=圆角矩形（笔记卡带标题条 + Markdown 富文本正文预览；图片卡显缩略图），
  可拖动、多选框选、右下角缩放手柄、右键样式菜单、Ctrl+C/V/D；
  双击文本卡原地编辑（失焦/Esc 提交），双击笔记卡跳笔记工作区（卡内不编正文）。
- 阶段 C：group 容器（几何成员随拖）、画布内 AI 发现关联、外部改动冲突保护、
  图谱→画布批量导入、图片附件复制入 vault。
- 交互骨架照搬 graph.py 已验证常量与套路：滚轮锚点缩放（0.15~5.0）、
  中键/空格掌形平移、场景点阵网格；空白左键 = 框选（与图谱页不同）。
- 编辑操作全部走 QUndoStack 命令：命令只动 core.canvas 模型 + 视图同步，
  数据 diff 对称，redo/undo 可重放；"边→建议"桥在 M4 接入。
- 保存 = SnapshotManager.write（先快照后写盘），版本历史免费复用。
"""
from __future__ import annotations

import copy
import math
import re
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF, QSize, QUrl, QEvent, QObject, QTimer
from PySide6.QtGui import (
    QColor, QPen, QBrush, QFont, QFontMetrics, QPainter, QPainterPath,
    QPainterPathStroker, QKeySequence, QShortcut, QUndoCommand, QUndoStack,
    QTextDocument, QImageReader, QPixmap, QDesktopServices,
)
from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsPathItem,
    QGraphicsProxyWidget,
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLabel,
    QDialog, QDialogButtonBox, QPlainTextEdit, QInputDialog, QMessageBox, QMenu,
    QFileDialog, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy,
)

from core.canvas import (CanvasDocument, CanvasNode, CanvasEdge, _new_id,
                         FILE_EXTS, IMAGE_EXTS, ATTACH_DIR, color_hex,
                         content_fp, extract_subgraph, grid_positions,
                         remap_copy, unique_attach_rel)
from core.storage.vault import Vault, slugify

# ---- 视觉（与图谱页同一浅色家族）----
BG_COLOR = "#f7f8fa"
GRID_STEP = 32.0
GRID_DOT = "#e3e7ec"
GRID_DOT_MAJOR = "#ccd3db"
CARD_BG = "#ffffff"
CARD_FILE_HEAD = "#eef3fb"
CARD_TEXT_BG = "#fffdf6"
CARD_BORDER = "#d6dbe2"
CARD_SELECT = "#1a73e8"
TITLE_INK = "#202124"
BODY_INK = "#5f6368"
MISSING_INK = "#d93025"
HANDLE_INK = "#9aa0a6"
EDGE_NEUTRAL = "#8a94a6"

KMS_NOTE_MIME = "application/x-ai-kms-note"   # NavTree 拖出主通道（值=vault 相对路径）
MISSING_TEXT = "文件不存在"                     # card_label 的哨兵串（paint 靠它标红）
_EXCERPT_LEN = 600                              # 富文本预览喂给 markdown 的截断
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PALETTE_NAMES = (("1", "红"), ("2", "粉"), ("3", "紫"), ("4", "蓝"), ("5", "绿"), ("6", "黄"))
CARD_R = 10.0                     # 圆角半径
HEAD_H = 30.0                     # 笔记卡标题条高
HANDLE_SIZE = 14.0                # 缩放手柄命中边长
ANCHOR_R = 11.0                   # 四边锚点命中半径（场景 px）
ANCHOR_DOT = 4.0                  # 锚点小圆半径
SIDES = ("top", "right", "bottom", "left")
MIN_W, MIN_H = 120.0, 60.0
MIN_ZOOM, MAX_ZOOM = 0.15, 5.0
ZOOM_STEP = 1.15
CLICK_THRESHOLD = 6.0             # 视口 px，位移小于它不算拖动


def _stem(rel: str) -> str:
    return Path(rel).name.rsplit(".", 1)[0] if "." in Path(rel).name else rel


LEGEND_HINT = "拖锚点=连线 · 右键=菜单 · 双击=编辑 · 滚轮=缩放"
LEGEND_HELP = ("拖边锚点=连线（自动进审阅队列） · 空白拖=框选 · 右键=卡片菜单 · "
               "双击=打开笔记/原地编辑 · 空格/中键=平移 · 滚轮=缩放 · Del=删除 · "
               "Ctrl+C/V/D 复制粘贴 · Ctrl+Z/Y 撤销重做 · Ctrl+S 保存")


class _LegendLabel(QLabel):
    """操作提示标签：宽度不够自动省略号截断；帮助态 tooltip 恒为全量操作说明；
    set_note 临时覆盖提示、几秒后自动滚回帮助文案。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hint = LEGEND_HINT
        self._active = LEGEND_HINT
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._swap(self._hint))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(80)
        self.setStyleSheet("color:#5f6368;")
        f = self.font()                # 真 font（非 QSS 字号）：fontMetrics 同步，elide 才准
        f.setPointSize(9)
        self.setFont(f)
        self.setToolTip(LEGEND_HELP)
        self._reflow()

    def set_note(self, text: str, msec: int = 7000) -> None:
        self._swap(text)
        self._timer.start(msec)

    def set_hint(self, text: str) -> None:
        self._hint = text
        self._timer.stop()
        self._swap(text)

    def _swap(self, text: str) -> None:
        self._active = text
        self.setToolTip(LEGEND_HELP if text == self._hint else text)
        self._reflow()

    def resizeEvent(self, event):
        self._reflow()
        super().resizeEvent(event)

    def _reflow(self) -> None:
        QLabel.setText(self, self.fontMetrics().elidedText(
            self._active, Qt.TextElideMode.ElideRight, max(60, self.width() - 2)))


class _EditFocusFilter(QObject):
    """QObject 事件过滤器（QGraphicsItem 不是 QObject，不能自己接 widget 事件）。"""

    def __init__(self, item: "_CardItem"):
        super().__init__()
        self.item = item

    def eventFilter(self, obj, event):
        return self.item.filter_edit_event(obj, event)


class _CardItem(QGraphicsItem):
    """file/text 卡片；node 是模型对象，几何实时态在 _w/_h/_live 上维护。"""

    def __init__(self, node: CanvasNode, title: str, excerpt: str, view: "CanvasView"):
        super().__init__()
        self.node = node
        self.view = view
        self.title = title
        self.excerpt = excerpt
        self._w, self._h = node.width, node.height
        self.setPos(node.x, node.y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self._resizing = False
        self._press_local = QPointF()
        self._press_wh = (0.0, 0.0)
        self._hover = False
        self._ldoc: QTextDocument | None = None    # 富文本排版缓存（C4，懒建）
        self._lkey: tuple = ()
        self._missing = excerpt == MISSING_TEXT
        self._is_image = bool(node.file) and node.file.lower().endswith(IMAGE_EXTS)
        self._proxy = None                     # 原地编辑态（C9）：QGraphicsProxyWidget

    def _edit_target(self) -> tuple[str, str]:
        """原地编辑的字段与现值：text 卡编 text，file 卡编 label（自定摘要）。"""
        if self.node.type == "text":
            return "text", self.node.text or ""
        return "label", self.node.label or ""

    # ---------- 原地编辑（C9：text 卡双击即打字，失焦/Esc 提交） ----------
    def begin_inline_edit(self) -> None:
        if self._proxy is not None:
            return
        ws = self.view.ws
        ws.discard_editor()                    # 同一时刻只允许一个编辑态
        ws._sc_disable(True)                   # 编辑期让位：Ctrl+Z/Y/C/V/D 归编辑器
        e = QPlainTextEdit()
        e.setPlainText(self._edit_target()[1])
        f = QFont()
        f.setPointSize(9)
        e.setFont(f)
        e.setStyleSheet(
            f"QPlainTextEdit {{ background: transparent; border: none; color: {TITLE_INK}; }}")
        margin = 8.0
        e.setFixedSize(max(20, int(self._w - 2 * margin)),
                       max(20, int(self._h - 2 * margin)))
        proxy = QGraphicsProxyWidget(self)
        proxy.setWidget(e)
        proxy.setPos(-self._w / 2 + margin, -self._h / 2 + margin)
        proxy.setZValue(5)
        filt = _EditFocusFilter(self)
        self._proxy = (proxy, e, filt)
        ws._editing = self
        e.installEventFilter(filt)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        e.setFocus()

    def filter_edit_event(self, obj, event) -> bool:
        """失焦/Esc = 提交（C9 焦点态机；QPlainTextEdit 无 focusOut 信号）。"""
        consumed = False
        if self._proxy is not None and obj is self._proxy[1]:
            t = event.type()
            if t == QEvent.Type.FocusOut:
                self._end_inline_edit(True)
            elif t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._end_inline_edit(True)
                consumed = True
        return consumed

    def _end_inline_edit(self, commit: bool) -> None:
        ws = self.view.ws
        if self._proxy is None:
            return
        proxy, e, filt = self._proxy
        self._proxy = None
        ws._editing = None
        ws._sc_disable(False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        new = e.toPlainText()
        proxy.setWidget(None)
        proxy.setParent(None)
        e.deleteLater()
        filt.deleteLater()
        if commit:
            field, old = self._edit_target()
            if field == "text":
                if new != old:
                    ws._push(EditCardTextCmd(ws, self.node.id,
                                             self.node.text or "", new))
            elif new.strip() != old:           # file 卡 → label=自定摘要；清空=回自动预览
                ws.commit_set_props("node", [self.node.id],
                                    {"label": new.strip() or None}, text="编辑摘要")
        self.update()

    # ---------- 几何 ----------
    def w(self) -> float:
        return self._w

    def h(self) -> float:
        return self._h

    def set_geom(self, x: float, y: float, w: float, h: float) -> None:
        self.prepareGeometryChange()
        self._w, self._h = w, h
        self.setPos(x, y)
        self.update()
        self.view.recompute_edges_of(self.node.id)

    def _ensure_layout(self, width: float, src: str) -> QTextDocument:
        """markdown 排版缓存：宽度或内容变了才重建（paint 里 drawContents 保持矢量清晰）。"""
        key = (round(width, 1), src)
        if self._ldoc is None or self._lkey != key:
            d = QTextDocument()
            f = QFont()
            f.setPointSize(9)
            d.setDefaultFont(f)
            d.setDefaultStyleSheet(
                f"body,p,li,span,code,a {{ color: {BODY_INK}; }} "
                f"h1,h2,h3,h4 {{ color: {TITLE_INK}; }}")
            d.setMarkdown(src.strip() or "（双击编辑）")
            d.setTextWidth(width)
            self._ldoc, self._lkey = d, key
        return self._ldoc

    def boundingRect(self) -> QRectF:
        m = 3.0                     # 描边外扩
        return QRectF(-self._w / 2 - m, -self._h / 2 - m,
                      self._w + 2 * m, self._h + 2 * m)

    def anchor_local(self, side: str) -> QPointF:
        w, h = self._w / 2, self._h / 2
        return {"left": QPointF(-w, 0), "right": QPointF(w, 0),
                "top": QPointF(0, -h), "bottom": QPointF(0, h)}[side]

    def shape_anchor(self, side: str | None) -> QPointF:
        """某边的锚点（局部系）；未知 side 返回 None 由调用方按方向自选。"""
        if side not in SIDES:
            return None
        return self.anchor_local(side)

    def anchor_at(self, local: QPointF) -> str | None:
        """命中测试：local 落在哪个边的锚点半径内（无则 None）。"""
        best, bd = None, ANCHOR_R
        for side in SIDES:
            a = self.anchor_local(side)
            d = math.hypot(local.x() - a.x(), local.y() - a.y())
            if d <= bd:
                best, bd = side, d
        return best

    def anchor_scene(self, side: str) -> QPointF:
        return self.mapToScene(self.anchor_local(side))

    # ---------- 绘制 ----------
    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(-self._w / 2, -self._h / 2, self._w, self._h)
        selected = bool(self.isSelected())
        is_file = self.node.type == "file"

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CARD_BG if is_file else CARD_TEXT_BG))
        painter.drawRoundedRect(r, CARD_R, CARD_R)

        if is_file:
            head = QRectF(r.left(), r.top(), r.width(), min(HEAD_H, r.height()))
            painter.setBrush(QColor(CARD_FILE_HEAD))
            path = QPainterPath()
            path.addRoundedRect(head, CARD_R, CARD_R)
            painter.drawPath(path.simplified())

        own = color_hex(self.node.color)   # 自定色优先于选中蓝，选中态另画外圈表达
        pen = QPen(QColor(own or (CARD_SELECT if selected else CARD_BORDER)))
        pen.setWidthF(2.0 if selected else 1.4 if own else 1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(r, CARD_R, CARD_R)
        if selected:
            ring = QPen(QColor(CARD_SELECT))
            ring.setWidthF(1.0)
            ring.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(ring)
            painter.drawRoundedRect(r.adjusted(-4, -4, 4, 4), CARD_R + 3, CARD_R + 3)

        f = QFont()
        f.setPointSize(10)
        pad = 10.0
        if is_file:
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(QColor(MISSING_INK) if not self.node.file else QColor(TITLE_INK))
            fm = QFontMetrics(f)
            painter.drawText(QRectF(r.left() + pad, r.top() + 2, r.width() - 2 * pad, HEAD_H),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(self.title, Qt.TextElideMode.ElideRight,
                                           int(r.width() - 2 * pad)))
            if self._h > HEAD_H + 18:
                box = QRectF(r.left() + pad, r.top() + HEAD_H + 6,
                             r.width() - 2 * pad, r.height() - HEAD_H - 2 * pad)
                painter.save()
                painter.setClipRect(box)
                if self._is_image:
                    pm = self.view.ws.pixmap_for(self.node.file,
                                                 box.width(), box.height())
                    if pm is None:
                        painter.setPen(QColor(MISSING_INK))
                        painter.drawText(box, Qt.AlignmentFlag.AlignLeft
                                         | Qt.AlignmentFlag.AlignTop, "图片找不到")
                    else:
                        painter.drawPixmap(
                            int(box.left() + (box.width() - pm.width()) / 2),
                            int(box.top() + (box.height() - pm.height()) / 2), pm)
                elif self._missing or not self.node.file:
                    f.setBold(False)
                    f.setPointSize(8)
                    painter.setFont(f)
                    painter.setPen(QColor(MISSING_INK))
                    painter.drawText(box, Qt.AlignmentFlag.AlignLeft
                                     | Qt.AlignmentFlag.AlignTop, self.excerpt)
                else:
                    painter.translate(box.topLeft())
                    # label（自定摘要，spec 通用字段）优先，未设置=正文自动预览
                    self._ensure_layout(box.width(),
                                        self.node.label or self.excerpt).drawContents(painter)
                painter.restore()
        else:
            box = r.adjusted(pad, pad, -pad, -pad)
            painter.save()
            painter.setClipRect(box)
            painter.translate(box.topLeft())
            self._ensure_layout(box.width(), self.node.text or "").drawContents(painter)
            painter.restore()

        if self.node.color:
            # 色标：右上角小圆点，色板号/十六进制都收（非法值静默不画）；file/text 卡通用
            hx = color_hex(self.node.color)
            if hx:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(hx))
                painter.drawEllipse(QPointF(r.right() - 12, r.top() + 12), 4, 4)

        if selected or self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(HANDLE_INK))
            hx, hy = r.right() - 3, r.bottom() - 3
            painter.drawEllipse(QPointF(hx, hy), 4, 4)

        if self._hover and not self.view.link_active():
            # 四边锚点：悬停出点，按下即拖线（link_active 时隐藏避免噪声）
            for side in SIDES:
                a = self.anchor_local(side)
                painter.setPen(QPen(QColor(CARD_SELECT), 1.2))
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(a, ANCHOR_DOT, ANCHOR_DOT)

    # ---------- 交互 ----------
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.view.recompute_edges_of(self.node.id)
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        # ⚠ QGraphicsItem 没有 viewport()：光标必须经视图设置（hover 崩溃回归点）
        self.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().hoverLeaveEvent(event)

    def _in_handle(self, local: QPointF) -> bool:
        return (local.x() > self._w / 2 - HANDLE_SIZE and
                local.y() > self._h / 2 - HANDLE_SIZE)

    def hoverMoveEvent(self, event):
        if self.anchor_at(event.pos()) is not None:
            self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
        elif self._in_handle(event.pos()):
            self.view.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.view.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        if self._proxy is not None:            # 原地编辑态：卡不响应锚点/手柄/拖动
            super().mousePressEvent(event)
            return
        # 锚点优先：左键按锚点 = 起线（不选中、不拖动、不缩放）
        if (event.button() == Qt.MouseButton.LeftButton
                and not self.view.space_held()):
            side = self.anchor_at(event.pos())
            if side:
                self.view.start_link(self, side, event.scenePos())
                event.accept()
                return
        if (event.button() == Qt.MouseButton.LeftButton and self._in_handle(event.pos())
                and not self.view.scene().selectedItems().__len__() > 1):
            self._resizing = True
            self._press_local = QPointF(event.pos())
            self._press_wh = (self._w, self._h)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            dw = event.pos().x() - self._press_local.x()
            dh = event.pos().y() - self._press_local.y()
            self.prepareGeometryChange()
            self._w = max(MIN_W, self._press_wh[0] + dw)
            self._h = max(MIN_H, self._press_wh[1] + dh)
            self.update()
            self.view.recompute_edges_of(self.node.id)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = False
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            if (abs(self._w - self._press_wh[0]) > 1 or
                    abs(self._h - self._press_wh[1]) > 1):
                self.view.on_resize_commit(self, self._press_wh)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.view.on_card_dblclick(self)
        event.accept()

    def sync_from_doc(self) -> None:
        """模型属性被命令改过（颜色/尺寸/文本）后把 item 拉回与 node 一致。"""
        self.prepareGeometryChange()
        self._w, self._h = self.node.width, self.node.height
        self._lkey = ()                          # 尺寸/文本都可能变了，排版缓存作废
        self.setPos(self.node.x, self.node.y)
        self.update()
        self.view.recompute_edges_of(self.node.id)


class _GroupItem(QGraphicsItem):
    """组=半透明圆角框（C6）。成员=几何包含（无 children 字段）：
    拖组时 view 把成员并入选中集一起移动；成员拖出框即脱离，无痕。"""

    LABEL_H = 22.0

    def __init__(self, node: CanvasNode, view: "CanvasView"):
        super().__init__()
        self.node = node
        self.view = view
        self._w, self._h = node.width, node.height
        self.setPos(node.x, node.y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(-2)
        self._hover = False
        self._resizing = False
        self._press_local = QPointF()
        self._press_wh = (0.0, 0.0)

    def w(self):
        return self._w

    def h(self):
        return self._h

    def boundingRect(self) -> QRectF:
        m = 3.0
        return QRectF(-self._w / 2 - m, -self._h / 2 - m - self.LABEL_H,
                      self._w + 2 * m, self._h + 2 * m + self.LABEL_H)

    def set_geom(self, x: float, y: float, w: float, h: float) -> None:
        self.prepareGeometryChange()
        self._w, self._h = w, h
        self.setPos(x, y)
        self.update()

    def sync_from_doc(self) -> None:
        self.prepareGeometryChange()
        self._w, self._h = self.node.width, self.node.height
        self.setPos(self.node.x, self.node.y)
        self.update()

    def _in_handle(self, local: QPointF) -> bool:
        return (local.x() > self._w / 2 - HANDLE_SIZE
                and local.y() > self._h / 2 - HANDLE_SIZE)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(-self._w / 2, -self._h / 2, self._w, self._h)
        sel = bool(self.isSelected())
        hx = color_hex(self.node.color)
        fill = QColor(hx) if hx else QColor("#9aa0a6")
        fill.setAlpha(26)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(r, CARD_R, CARD_R)
        border = QPen(QColor(CARD_SELECT if sel else (hx or CARD_BORDER)))
        if not sel:
            border.setStyle(Qt.PenStyle.DashLine)
        border.setWidthF(2.0 if sel else 1.2)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(r, CARD_R, CARD_R)
        f = QFont()
        f.setPointSize(9)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(QColor(TITLE_INK))
        painter.drawText(QRectF(r.left(), r.top() - self.LABEL_H,
                                r.width(), self.LABEL_H),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self.node.label or "组")
        if sel or self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(HANDLE_INK))
            painter.drawEllipse(QPointF(r.right() - 3, r.bottom() - 3), 4, 4)

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.view.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event):
        self.view.viewport().setCursor(
            Qt.CursorShape.SizeFDiagCursor if self._in_handle(event.pos())
            else Qt.CursorShape.PointingHandCursor)
        super().hoverMoveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self._in_handle(event.pos())
                and not self.view.space_held()
                and len([i for i in self.view.scene().selectedItems()
                         if isinstance(i, (_CardItem, _GroupItem))]) <= 1):
            self._resizing = True
            self._press_local = QPointF(event.pos())
            self._press_wh = (self._w, self._h)
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resizing:
            dw = event.pos().x() - self._press_local.x()
            dh = event.pos().y() - self._press_local.y()
            self.prepareGeometryChange()
            self._w = max(MIN_W, self._press_wh[0] + dw)
            self._h = max(MIN_H, self._press_wh[1] + dh)
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing:
            self._resizing = False
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            if (abs(self._w - self._press_wh[0]) > 1
                    or abs(self._h - self._press_wh[1]) > 1):
                self.view.on_resize_commit(self, self._press_wh)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _EdgeItem(QGraphicsPathItem):
    """手绘连线：可选中可删；pen 按建议状态着色；尾端画箭头表方向。"""

    def __init__(self, edge: CanvasEdge, view: "CanvasView"):
        super().__init__()
        self.edge = edge
        self.view = view
        self.status = ""                 # ""=中性；pending/applied/skipped/obsolete
        self._hover = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(-1)
        self.refresh()

    def refresh(self):
        p1, p2 = self.view.edge_endpoints(self.edge)
        path = QPainterPath(p1)
        path.lineTo(p2)
        # 尾侧箭头：方向即"谁被写回双链"的承诺
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        ln = math.hypot(dx, dy)
        if ln > 1e-6:
            ux, uy = dx / ln, dy / ln
            bx, by = p2.x() - ux * 9, p2.y() - uy * 9
            path.moveTo(p2)
            path.lineTo(bx - uy * 5, by + ux * 5)
            path.lineTo(bx + uy * 5, by - ux * 5)
            path.closeSubpath()
        self.setPath(path)
        self._apply_pen()

    def boundingRect(self):
        return super().boundingRect().adjusted(-8, -8, 8, 8)

    def shape(self):
        """命中区加宽到 12px，细线也好点。"""
        stroker = QPainterPathStroker()
        stroker.setWidth(12.0)
        return stroker.createStroke(self.path())

    def set_status(self, status: str) -> None:
        self.status = status
        self._apply_pen()

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.edge.label:
            f = QFont()
            f.setPointSize(7)
            painter.setFont(f)
            painter.setPen(QColor(BODY_INK))
            mid = self.path().boundingRect().center()
            painter.drawText(QRectF(mid.x() - 70, mid.y() - 9, 140, 14),
                             Qt.AlignmentFlag.AlignCenter, self.edge.label)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def _apply_pen(self):
        base, style = QColor(EDGE_NEUTRAL), Qt.PenStyle.SolidLine
        if self.status == "pending":
            base, style = QColor("#f59e0b"), Qt.PenStyle.DashLine
        elif self.status == "applied":
            base = QColor("#2563eb")
        elif self.status == "skipped":
            base, style = QColor("#9aa0a6"), Qt.PenStyle.DashLine
        elif self.status == "obsolete":
            base, style = QColor("#c0c6cf"), Qt.PenStyle.DotLine
        elif color_hex(self.edge.color):
            base = QColor(color_hex(self.edge.color))   # 无状态色时尊重用户线色
        if self.isSelected():
            base = QColor(CARD_SELECT)
        pen = QPen(base)
        pen.setStyle(style)
        pen.setWidthF(2.6 if (self._hover or self.isSelected()) else 1.8)
        self.setPen(pen)


class CanvasView(QGraphicsView):
    """画布视图：网格背景 + 缩放/平移（照搬图谱页三件套）+ 框选 + 卡片增删拖缩。"""

    def __init__(self, workspace: "CanvasWorkspace", parent=None):
        super().__init__(parent)
        self.ws = workspace
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(self.renderHints() |
                            QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QBrush(QColor(BG_COLOR)))
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

        self._cards: dict[str, _CardItem] = {}
        self._groups: dict[str, _GroupItem] = {}
        self._edges: dict[str, _EdgeItem] = {}

        self._panning = False
        self._pan_start = None
        self._space_held = False
        self._drag_start: dict[str, tuple] = {}
        self._press_pos = None
        self._hint = True                # 无文档时画引导语
        self._link_src: _CardItem | None = None
        self._link_side: str | None = None
        self._link_pt: QPointF | None = None
        self._link_target: _CardItem | None = None

    def space_held(self) -> bool:
        return self._space_held

    def link_active(self) -> bool:
        return self._link_src is not None

    # ---------- 连线手势 ----------
    def start_link(self, card: _CardItem, side: str, scene_pt: QPointF) -> None:
        self._link_src, self._link_side = card, side
        self._link_pt = QPointF(scene_pt)
        self._link_target = None
        self.viewport().update()

    def _finish_link(self, release_scene_pt: QPointF) -> None:
        src, side = self._link_src, self._link_side
        self._link_src = self._link_side = self._link_pt = self._link_target = None
        self.viewport().update()
        it = self.itemAt(self.mapFromScene(release_scene_pt))
        if not isinstance(it, _CardItem) or it is src:
            return
        local = it.mapFromScene(release_scene_pt)
        to_side = it.anchor_at(local)
        self.ws.commit_link(src.node.id, it.node.id, side, to_side)

    def draw_link_rubber(self, painter: QPainter) -> None:
        if self._link_src is None or self._link_pt is None:
            return
        p1 = self._link_src.anchor_scene(self._link_side)
        pen = QPen(QColor(CARD_SELECT))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(1.6)
        painter.setPen(pen)
        painter.drawLine(p1, self._link_pt)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CARD_SELECT))
        painter.drawEllipse(self._link_pt, 3, 3)
        if self._link_target is not None:
            hl = QPen(QColor(CARD_SELECT))
            hl.setWidthF(2.2)
            painter.setPen(hl)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self._link_target.boundingRect(), CARD_R, CARD_R)

    # ---------- 文档装载 ----------
    def set_hint(self, on: bool) -> None:
        self._hint = on
        self.viewport().update()

    def build(self, doc: CanvasDocument | None, preserve_view: bool = False) -> None:
        """整场景重建。preserve_view=True 用于外部改动静默重载：保住缩放和视野中心，
        否则 _fit_content 会把用户视野按在地上摩擦。"""
        self.ws.discard_editor()             # 重建场景前先提交并销毁编辑态
        old_zoom, old_center = self.transform().m11(), None
        if preserve_view:
            old_center = self.mapToScene(self.viewport().rect().center())
        self._scene.clear()
        self._cards, self._groups, self._edges = {}, {}, {}
        if doc is None:
            self.set_hint(True)
            return
        self.set_hint(False)
        for n in doc.nodes:
            if n.type == "group":
                self.add_group_item(n)
            else:
                self.add_card_item(n)
        for e in doc.edges:
            self.add_edge_item(e)
        if preserve_view and old_center is not None:
            self.setSceneRect(self._scene.itemsBoundingRect().adjusted(-400, -400, 400, 400))
            self.resetTransform()
            self.scale(old_zoom, old_zoom)
            self.centerOn(old_center)
        else:
            self._fit_content()

    # ---------- 统一 item 查找 / 属性刷新（M-C0 基建，C6 起兼查组） ----------
    def item_of(self, node_id: str):
        return self._cards.get(node_id) or self._groups.get(node_id)

    def refresh_props(self, kind: str, ids: list[str]) -> None:
        if kind == "node":
            for nid in ids:
                it = self.item_of(nid)
                if it is not None:
                    it.sync_from_doc()
        else:
            for eid in ids:
                it = self._edges.get(eid)
                if it is not None:
                    it.refresh()

    def select_all_cards(self) -> None:
        self._scene.clearSelection()
        for c in self._cards.values():
            c.setSelected(True)
        for g in self._groups.values():
            g.setSelected(True)

    def add_card_item(self, node: CanvasNode) -> _CardItem | None:
        if node.type not in ("file", "text"):
            return None                  # link 等其它未知类型：模型里透传，不渲染
        title, excerpt = self.ws.card_label(node)
        it = _CardItem(node, title, excerpt, self)
        self._scene.addItem(it)
        self._cards[node.id] = it
        return it

    def add_group_item(self, node: CanvasNode) -> _GroupItem | None:
        if node.type != "group":
            return None
        it = _GroupItem(node, self)
        self._scene.addItem(it)
        self._groups[node.id] = it
        return it

    def add_edge_item(self, edge: CanvasEdge) -> _EdgeItem | None:
        if edge.from_node not in self._cards or edge.to_node not in self._cards:
            return None                  # 端点是未渲染的透传节点：不出线
        it = _EdgeItem(edge, self)
        self._scene.addItem(it)
        self._edges[edge.id] = it
        return it

    def remove_card_item(self, node_id: str) -> None:
        """删卡/删组统一入口（DeleteCardsCmd/GroupCmd 都调它）。"""
        it = self._cards.pop(node_id, None) or self._groups.pop(node_id, None)
        if it is not None:
            self._scene.removeItem(it)

    def remove_edge_item(self, edge_id: str) -> None:
        it = self._edges.pop(edge_id, None)
        if it is not None:
            self._scene.removeItem(it)

    def update_card_label(self, node: CanvasNode) -> None:
        it = self._cards.get(node.id)
        if it is not None:
            it.title, it.excerpt = self.ws.card_label(node)
            it._missing = it.excerpt == MISSING_TEXT
            it._lkey = ()
            it.update()

    # ---------- 边几何 ----------
    def recompute_edges_of(self, node_id: str) -> None:
        for e in self._edges.values():
            if node_id in (e.edge.from_node, e.edge.to_node):
                e.refresh()

    def _card(self, nid: str) -> _CardItem | None:
        return self._cards.get(nid)

    def edge_endpoints(self, edge: CanvasEdge) -> tuple[QPointF, QPointF]:
        c1, c2 = self._card(edge.from_node), self._card(edge.to_node)
        p1 = self._border_point(c1, c2, edge.from_side)
        p2 = self._border_point(c2, c1, edge.to_side)
        return p1, p2

    def _border_point(self, src: _CardItem, dst: _CardItem,
                      side: str | None) -> QPointF:
        if side:
            local = src.shape_anchor(side)
            return src.mapToScene(local) if local is not None else QPointF(src.pos())
        c1, c2 = src.pos(), dst.pos()
        dx, dy = c2.x() - c1.x(), c2.y() - c1.y()
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return QPointF(c1.x(), c1.y() - src.h() / 2)
        tx = (src.w() / 2) / abs(dx) if abs(dx) > 1e-6 else float("inf")
        ty = (src.h() / 2) / abs(dy) if abs(dy) > 1e-6 else float("inf")
        t = min(tx, ty)
        return QPointF(c1.x() + dx * t, c1.y() + dy * t)

    # ---------- 回调（来自 item） ----------
    def on_resize_commit(self, item: _CardItem, old_wh: tuple) -> None:
        self.ws.commit_resize(item.node.id, old_wh, (item._w, item._h))

    def on_card_dblclick(self, item: _CardItem) -> None:
        self.ws.on_card_dblclick(item)

    # ---------- 背景：点阵网格（照搬 graph._draw_grid） ----------
    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QColor(BG_COLOR))
        step = GRID_STEP
        while step < 4096:
            if (int(rect.width() / step) + 2) * (int(rect.height() / step) + 2) <= 12000:
                break
            step *= 2
        x0 = math.floor(rect.left() / step) * step
        y0 = math.floor(rect.top() / step) * step
        painter.setPen(Qt.PenStyle.NoPen)
        y, iy = y0, 0
        while y <= rect.bottom():
            x, ix = x0, 0
            while x <= rect.right():
                major = (ix % 4 == 0) and (iy % 4 == 0)
                painter.setBrush(QColor(GRID_DOT_MAJOR if major else GRID_DOT))
                r = 1.4 if major else 1.0
                painter.drawEllipse(QPointF(x, y), r, r)
                x += step
                ix += 1
            y += step
            iy += 1

    def drawForeground(self, painter: QPainter, rect: QRectF):
        super().drawForeground(painter, rect)
        self.draw_link_rubber(painter)
        if not self._hint:
            return
        painter.save()
        painter.resetTransform()
        vp = self.viewport().rect()
        col = QColor(BODY_INK)
        col.setAlpha(120)
        painter.setPen(col)
        f = QFont()
        f.setPointSize(12)
        painter.setFont(f)
        painter.drawText(vp, Qt.AlignmentFlag.AlignCenter,
                         "新建或打开一个 .canvas 白板\n\n从文件树把笔记拖进来生成卡片")
        painter.restore()

    # ---------- 键盘 / 右键 ----------
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.ws.delete_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    # 右键统一分派：与文件树同款 CustomContextMenu 机制（DefaultContextMenu 的
    # scene→item 转发链在真实环境里不弹出，这里 view 收事件后按 itemAt 自己派发）
    def _on_context_menu(self, pos) -> None:
        """pos = customContextMenuRequested 传来的 viewport QPoint。"""
        if self.ws.doc is None:
            return
        it = self.itemAt(pos)
        gp = self.viewport().mapToGlobal(pos)
        if isinstance(it, _CardItem):
            menu = self.ws.build_card_menu(it)
        elif isinstance(it, _EdgeItem):
            menu = self.ws.build_edge_menu(it)
        elif isinstance(it, _GroupItem):
            menu = self.ws.build_group_menu(it)
        else:
            menu = self.ws.build_blank_menu()
        if menu is not None:
            self.ws._show_menu(menu, gp)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._space_held = False
        super().focusOutEvent(event)

    # ---------- 鼠标：平移 / 组拖动记账 ----------
    def mousePressEvent(self, event):
        btn = event.button()
        it = self.itemAt(event.position().toPoint()) if btn == Qt.MouseButton.LeftButton \
            else None
        grab = btn == Qt.MouseButton.MiddleButton or (
            btn == Qt.MouseButton.LeftButton and self._space_held)
        if grab:
            self._panning = True
            self._pan_start = QPointF(event.position())
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        self._press_pos = QPointF(event.position())
        self._drag_start = {}
        if btn == Qt.MouseButton.LeftButton and isinstance(it, (_CardItem, _GroupItem)):
            if isinstance(it, _GroupItem) and not it.isSelected():
                # 拖组 = 组 + 几何成员一起选中一起动（成员移出框则自然脱队）
                self._scene.clearSelection()
                it.setSelected(True)
                doc = self.ws.doc
                if doc is not None:
                    for mid in doc.group_members(it.node.id):
                        mi = self.item_of(mid)
                        if mi is not None:
                            mi.setSelected(True)
            sel = [i for i in self._scene.selectedItems()
                   if isinstance(i, (_CardItem, _GroupItem))]
            group = sel if (it.isSelected() and len(sel) > 1) else [it]
            self._drag_start = {c.node.id: (c.pos().x(), c.pos().y()) for c in group}
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._link_src is not None:
            self._link_pt = self.mapToScene(event.position().toPoint())
            it = self.itemAt(event.position().toPoint())
            self._link_target = it if isinstance(it, _CardItem) and it is not self._link_src else None
            self.viewport().update()
            event.accept()
            return
        if self._panning and self._pan_start is not None:
            d = QPointF(event.position()) - self._pan_start
            self._pan_start = QPointF(event.position())
            sb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
            sb.setValue(int(sb.value() - d.x()))
            vb.setValue(int(vb.value() - d.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._link_src is not None and event.button() == Qt.MouseButton.LeftButton:
            self._finish_link(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if self._panning and event.button() in (Qt.MouseButton.MiddleButton,
                                                Qt.MouseButton.LeftButton):
            self._panning = False
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        start, self._drag_start = self._drag_start, {}
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton or not start:
            return
        moves = {}
        for nid, (x0, y0) in start.items():
            c = self.item_of(nid)
            if c is None:
                continue
            if abs(c.pos().x() - x0) > 0.5 or abs(c.pos().y() - y0) > 0.5:
                moves[nid] = (x0, y0, c.pos().x(), c.pos().y())
        if moves:
            self.ws.commit_move(moves)

    # ---------- 缩放 ----------
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP
        self._scale_at(factor, QPointF(event.position()))

    def _scale_at(self, factor: float, view_pos: QPointF) -> bool:
        cur = self.transform().m11()
        target = cur * factor
        if target < MIN_ZOOM or target > MAX_ZOOM:
            clamped = MIN_ZOOM if target < MIN_ZOOM else MAX_ZOOM
            factor = clamped / cur
            if abs(factor - 1.0) < 1e-6:
                return False
        before = self.mapToScene(int(view_pos.x()), int(view_pos.y()))
        self.scale(factor, factor)
        after = self.mapFromScene(before)
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(int(h.value() + after.x() - view_pos.x()))
        v.setValue(int(v.value() + after.y() - view_pos.y()))
        return True

    def zoom_in(self):
        c = self.viewport().rect().center()
        self._scale_at(ZOOM_STEP, QPointF(c))

    def zoom_out(self):
        c = self.viewport().rect().center()
        self._scale_at(1.0 / ZOOM_STEP, QPointF(c))

    def _fit_content(self):
        items_rect = self._scene.itemsBoundingRect()
        if items_rect.isEmpty():
            return
        self.setSceneRect(items_rect.adjusted(-400, -400, 400, 400))
        self.fitInView(items_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _fit_scene_rect_soft(self):
        """只扩场景矩形包住内容，不动缩放（拖出视野时滚动条跟得上）。"""
        items_rect = self._scene.itemsBoundingRect().adjusted(-200, -200, 200, 200)
        cur = self.sceneRect()
        if not cur.isNull() and cur.contains(items_rect):
            return
        self.setSceneRect(items_rect if cur.isNull() else cur.united(items_rect))

    # ---------- 拖入建卡：树拖出（自定义 mime）+ explorer（urls）双通道 ----------
    def _mime_ok(self, event) -> bool:
        md = event.mimeData()
        return md.hasFormat(KMS_NOTE_MIME) or md.hasUrls()

    def dragEnterEvent(self, event):
        if self._mime_ok(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._mime_ok(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        pos = self.mapToScene(event.position().toPoint())
        md = event.mimeData()
        if md.hasFormat(KMS_NOTE_MIME):
            rel = bytes(md.data(KMS_NOTE_MIME)).decode("utf-8", "replace")
            self.ws.add_note_card(rel, pos)
        else:
            self.ws.drop_note_urls(md.urls(), pos)
        event.acceptProposedAction()


# ================= 撤销命令（只持数据 diff） =================
class AddCardCmd(QUndoCommand):
    def __init__(self, ws: "CanvasWorkspace", node: CanvasNode, added: list):
        super().__init__("添加卡片")
        self.ws, self.node, self._added = ws, node, added   # added: 边级联（新建时为空）

    def redo(self):
        if self.ws.doc.node(self.node.id) is None:
            self.ws.doc.restore_node(self.node)
            self.ws.view.add_card_item(self.node)
            for e in self._added:
                if self.ws.doc.edge(e.id) is None:
                    self.ws.doc.restore_edge(e)
                    self.ws.view.add_edge_item(e)
                    self.ws.bridge_add(e)

    def undo(self):
        gone = self.ws.doc.remove_node(self.node.id)
        for e in gone:
            self.ws.view.remove_edge_item(e.id)
            self.ws.bridge_remove(e.id)
        self.ws.view.remove_card_item(self.node.id)


class DeleteCardsCmd(QUndoCommand):
    def __init__(self, ws: "CanvasWorkspace", node_ids: list[str]):
        super().__init__("删除卡片")
        self.ws, self.ids = ws, list(node_ids)
        self._nodes: list[CanvasNode] = []
        self._edges: list[CanvasEdge] = []
        self._edge_src: dict[str, dict] = {}    # 级联边的 source/kws 快照（undo 回带）

    def redo(self):
        for nid in self.ids:
            n = self.ws.doc.node(nid)
            if n is None:
                continue
            gone = self.ws.doc.remove_node(nid)
            for e in gone:
                st = self.ws._edge_state.get(e.id)
                if st:
                    self._edge_src[e.id] = {"source": st.get("source", "canvas"),
                                            "kws": list(st.get("kws") or [])}
                self.ws.view.remove_edge_item(e.id)
                self.ws.bridge_remove(e.id)
                self._edges.append(e)
            self.ws.view.remove_card_item(nid)
            self._nodes.append(n)

    def undo(self):
        for n in self._nodes:
            self.ws.doc.restore_node(n)
            self.ws.view.add_card_item(n)
        for e in self._edges:
            if self.ws.doc.edge(e.id) is None:
                self.ws.doc.restore_edge(e)
                self.ws.view.add_edge_item(e)
                src = self._edge_src.get(e.id)
                if src:
                    st = self.ws._edge_state.setdefault(e.id, {})
                    st.setdefault("source", src["source"])
                    st.setdefault("kws", list(src["kws"]))
                self.ws.bridge_add(e)


class MoveCardsCmd(QUndoCommand):
    def __init__(self, ws: "CanvasWorkspace", moves: dict[str, tuple]):
        super().__init__("移动卡片")
        self.ws, self.moves = ws, moves

    def _apply(self, sel):
        for nid, (x0, y0, x1, y1) in self.moves.items():
            n = self.ws.doc.node(nid)
            if n is None:
                continue
            self.ws.doc.set_geometry(nid, x=sel(x0, x1), y=sel(y0, y1))
            c = self.ws.view.item_of(nid)
            if c is not None:
                c.set_geom(n.x, n.y, n.width, n.height)
        self.ws.view._fit_scene_rect_soft()

    def redo(self):
        self._apply(lambda a, b: b)

    def undo(self):
        self._apply(lambda a, b: a)


class ResizeCardCmd(QUndoCommand):
    def __init__(self, ws: "CanvasWorkspace", nid: str, old_wh, new_wh):
        super().__init__("调整卡片大小")
        self.ws, self.nid = ws, nid
        self.old_wh, self.new_wh = old_wh, new_wh

    def _apply(self, wh):
        n = self.ws.doc.node(self.nid)
        if n is None:
            return
        self.ws.doc.set_geometry(self.nid, width=wh[0], height=wh[1])
        c = self.ws.view.item_of(self.nid)
        if c is not None:
            c.set_geom(n.x, n.y, n.width, n.height)

    def redo(self):
        self._apply(self.new_wh)

    def undo(self):
        self._apply(self.old_wh)


class EditCardTextCmd(QUndoCommand):
    def __init__(self, ws: "CanvasWorkspace", nid: str, old: str, new: str):
        super().__init__("编辑文本卡片")
        self.ws, self.nid, self.old, self.new = ws, nid, old, new

    def _apply(self, text):
        n = self.ws.doc.node(self.nid)
        if n is None:
            return
        self.ws.doc.set_text(self.nid, text)
        c = self.ws.view._cards.get(self.nid)
        if c is not None:
            c.update()

    def redo(self):
        self._apply(self.new)

    def undo(self):
        self._apply(self.old)


class AddEdgeCmd(QUndoCommand):
    """建边+落建议；source/kws 由命令持有（不是 ws 状态）——undo 会 pop
    _edge_state 条目，redo 重放若只靠 setdefault 会把 AI 标记退化回手绘。"""

    def __init__(self, ws: "CanvasWorkspace", edge: CanvasEdge,
                 source: str = "canvas", kws: list[str] | None = None):
        super().__init__("添加连线")
        self.ws, self.edge = ws, edge
        self.source, self.kws = source, list(kws or [])

    def redo(self):
        doc = self.ws.doc
        if doc.edge(self.edge.id) is not None:
            return
        if doc.node(self.edge.from_node) is None or doc.node(self.edge.to_node) is None:
            return
        doc.restore_edge(self.edge)
        self.ws.view.add_edge_item(self.edge)
        st = self.ws._edge_state.setdefault(self.edge.id, {})
        st.setdefault("source", self.source)
        st.setdefault("kws", list(self.kws))
        self.ws.bridge_add(self.edge)

    def undo(self):
        if self.ws.doc.remove_edge(self.edge.id) is not None:
            self.ws.view.remove_edge_item(self.edge.id)
        self.ws.bridge_remove(self.edge.id)


class RemoveEdgeCmd(QUndoCommand):
    def __init__(self, ws: "CanvasWorkspace", edge: CanvasEdge):
        super().__init__("删除连线")
        self.ws, self.edge = ws, edge
        st = ws._edge_state.get(edge.id) or {}
        self.source = st.get("source", "canvas")          # undo 重放时恢复标记
        self.kws = list(st.get("kws") or [])

    def redo(self):
        if self.ws.doc.remove_edge(self.edge.id) is not None:
            self.ws.view.remove_edge_item(self.edge.id)
        self.ws.bridge_remove(self.edge.id)

    def undo(self):
        if self.ws.doc.edge(self.edge.id) is not None:
            return
        try:
            self.ws.doc.restore_edge(self.edge)
        except ValueError:
            return                    # 端点已被"删卡"命令持有：由那条命令的 undo 连带还原
        self.ws.view.add_edge_item(self.edge)
        st = self.ws._edge_state.setdefault(self.edge.id, {})
        st.setdefault("source", self.source)
        st.setdefault("kws", list(self.kws))
        self.ws.bridge_add(self.edge)


class SetPropCmd(QUndoCommand):
    """样式属性通吃命令：kind=node|edge，首跑记旧值快照，undo 按旧值逐条回放。"""

    def __init__(self, ws: "CanvasWorkspace", kind: str, ids: list[str],
                 new_props: dict, text: str = "修改样式"):
        super().__init__(text)
        self.ws, self.kind = ws, kind
        self.ids, self.new = list(ids), dict(new_props)
        self.old: dict[str, dict] | None = None

    def _set(self, ids, props):
        setter = (self.ws.doc.set_node_props if self.kind == "node"
                  else self.ws.doc.set_edge_props)
        return setter(ids, props)

    def redo(self):
        if self.ws.doc is None:
            return
        if self.old is None:
            self.old = self._set(self.ids, self.new)
        else:
            self._set(self.ids, self.new)
        self.ws.view.refresh_props(self.kind, self.ids)

    def undo(self):
        if self.ws.doc is None or self.old is None:
            return
        for nid, snap in self.old.items():
            self._set([nid], snap)
        self.ws.view.refresh_props(self.kind, list(self.old))


class GroupCmd(QUndoCommand):
    """成组：首跑按当前成员包围盒建组；undo 只删框（成员永不受影响）。"""

    def __init__(self, ws: "CanvasWorkspace", ids: list[str], label: str = ""):
        super().__init__("成组")
        self.ws, self.ids, self.label = ws, list(ids), label
        self.node: CanvasNode | None = None

    def redo(self):
        if self.node is None:
            self.node = self.ws.doc.create_group(self.ids, label=self.label)
            self.ws.view.add_group_item(self.node)
        elif self.ws.doc.node(self.node.id) is None:
            self.ws.doc.restore_node(self.node)
            self.ws.view.add_group_item(self.node)

    def undo(self):
        if self.node is not None and self.ws.doc.node(self.node.id) is not None:
            self.ws.doc.remove_node(self.node.id)
            self.ws.view.remove_card_item(self.node.id)


# ================= 工作区容器 =================
class CanvasWorkspace(QWidget):
    def __init__(self, store, vault: Vault, on_open=None, curator=None,
                 on_docs_changed=None, parent=None):
        super().__init__(parent)
        self.store = store
        self.vault = vault
        self._on_open = on_open
        self.curator = curator
        # 新 .canvas 落盘后回调（主窗口接 _refresh_tree，否则树看不到白板分区）
        self.on_docs_changed = on_docs_changed
        self._clipboard: tuple[list[CanvasNode], list[CanvasEdge]] | None = None
        self._editing: "_CardItem | None" = None    # 原地编辑中的卡（C9，全局至多一个）
        self._scs: list = []                          # ws 快捷键，编辑态临时让位
        self._edge_state: dict[str, dict] = {}  # edge_id → {sid,status,source,kws}
        self._label_cache: dict[str, tuple] = {}  # rel → (mtime, (title, body))
        self._pix_cache: dict[str, tuple] = {}    # 图片 rel → (mtime, QPixmap|None)
        from core.aipilot.snapshot import SnapshotManager
        self.snapshots = SnapshotManager(store, vault)
        self.doc: CanvasDocument | None = None
        self._undo = QUndoStack(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # 本页按钮条紧凑化（局部 QSS，笔记页同款手法）
        self.setStyleSheet(
            "QPushButton{font-size:12px; padding:2px 10px;}"
            "QComboBox{font-size:12px; padding:1px 4px;}")
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._new_btn = QPushButton("＋ 新建")
        self._new_btn.setToolTip("新建白板（.canvas 存入 Vault）")
        self._new_btn.clicked.connect(self.new_document)
        self._open_combo = QComboBox()
        self._open_combo.setMinimumWidth(140)
        self._open_combo.setMaximumWidth(220)
        self._open_combo.setToolTip("打开 Vault 内的 .canvas 白板")
        self._open_combo.activated.connect(self._combo_activated)
        self._save_btn = QPushButton("保存")
        self._save_btn.setToolTip("保存白板（Ctrl+S，自动留版本快照）")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self.save_current)
        self._close_btn = QPushButton("关闭")
        self._close_btn.clicked.connect(self.close_document)
        self._title = QLabel("白板")
        self._title.setStyleSheet("color:#202124; font-weight:600; font-size:13px;")
        self._zoom_out = QPushButton("－")
        self._zoom_out.setFixedSize(26, 24)
        self._zoom_out.setToolTip("缩小")
        self._zoom_out.clicked.connect(lambda: self.view.zoom_out())
        self._zoom_in = QPushButton("＋")
        self._zoom_in.setFixedSize(26, 24)
        self._zoom_in.setToolTip("放大")
        self._zoom_in.clicked.connect(lambda: self.view.zoom_in())
        self._disc_btn = QPushButton("⚡ 发现关联")
        self._disc_btn.setToolTip("对画布上的笔记卡跑关键词/标题关联，\n"
                                  "候选连线画成琥珀虚线并进审阅队列（Ctrl+Z 撤整批）")
        self._disc_btn.clicked.connect(self.do_discover)
        self._imp_btn = QPushButton("⇤ 导入图谱")
        self._imp_btn.setToolTip("从图谱多选笔记，批量铺卡（既有双链自动连边）")
        self._imp_btn.clicked.connect(self.import_from_graph_dialog)
        self._legend = _LegendLabel()
        # 布局：文件操作靠左 · 标题 · 弹性空隙 · 功能+缩放靠右 · 图例吃剩余宽度（溢出省略号）
        for x in (self._new_btn, self._open_combo, self._save_btn,
                  self._close_btn, self._title):
            bar.addWidget(x)
        bar.addStretch(1)
        for x in (self._disc_btn, self._imp_btn, self._zoom_out, self._zoom_in):
            bar.addWidget(x)
        bar.addWidget(self._legend, 2)        # 图例吃 2 份剩余宽度（与 stretch 1 份配比）
        lay.addLayout(bar)

        self.view = CanvasView(self)
        lay.addWidget(self.view, 1)

        for seq, slot in ((QKeySequence.Undo, self._undo.undo),
                          (QKeySequence.Redo, self._undo.redo),
                          (QKeySequence.Copy, self.copy_selection),
                          (QKeySequence.Paste, self.paste),
                          # PySide6 无 StandardKey.Duplicate，字面键按平台自动映射 ⌘/Ctrl
                          (QKeySequence("Ctrl+D"), self.duplicate_selection)):
            sc = QShortcut(seq, self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)
            self._scs.append(sc)

        self.refresh_doc_list()

    # ---------- 文档列表 ----------
    def refresh_doc_list(self) -> None:
        cur = self._current_rel()
        self._open_combo.blockSignals(True)
        self._open_combo.clear()
        self._open_combo.addItem("打开白板…", None)
        for rel in self.vault.list_canvas_files():
            self._open_combo.addItem(rel, rel)
        if cur:
            idx = self._open_combo.findData(cur)
            if idx >= 0:
                self._open_combo.setCurrentIndex(idx)
        self._open_combo.blockSignals(False)

    def _current_rel(self) -> str | None:
        return self.doc.rel_path if self.doc else None

    def _combo_activated(self, _idx: int) -> None:
        rel = self._open_combo.currentData()
        self._open_combo.setCurrentIndex(0)    # 复位到提示项，选中即动作
        if rel:
            self.open_document(rel)

    # ---------- 文档生命周期 ----------
    def open_document(self, rel: str) -> bool:
        if not self.confirm_discard():
            return False
        doc, report = CanvasDocument.open(self.vault, rel)
        self.doc = doc
        self._undo.clear()
        self.view.build(doc)
        self._load_edge_status()
        skipped = len(report["skipped_nodes"]) + len(report["skipped_edges"])
        if skipped:
            self._legend.set_note(f"已忽略 {skipped} 条损坏条目（Obsidian 修改格式或手工编辑所致，原样保留在磁盘副本）")
        self.refresh_doc_list()
        self._update_title()
        self.view.setFocus()
        return True

    # ---------- 手绘/AI 边：建桥 / 视觉态（_edge_state 单表：sid/status/source/kws） ----------
    def _load_edge_status(self) -> None:
        """重开画布：edge_id → 状态全量还原（不信任内存旧映射，AI 标记同还原）。"""
        self._edge_state = {}
        if self.doc is None or self.curator is None or not self.doc.rel_path:
            return
        for eid, info in self.curator.canvas_edge_map(self.doc.rel_path).items():
            self._edge_state[eid] = {"sid": info["sid"], "status": info["status"],
                                     "source": info.get("source", "canvas"),
                                     "kws": info.get("kws") or []}
            it = self.view._edges.get(eid)
            if it is not None:
                it.set_status(info["status"])

    def refresh_edge_status(self) -> None:
        """审阅面板任何变动后调用：按行现态刷新已映射的边。"""
        for eid, st in list(self._edge_state.items()):
            sid = st.get("sid")
            if sid is None:
                continue
            row = self.store.get_suggestion(sid)
            it = self.view._edges.get(eid)
            if row is None:
                self._edge_state.pop(eid, None)
                if it is not None:
                    it.set_status("")
            else:
                st["status"] = row["status"]
                if it is not None:
                    it.set_status(row["status"])

    def commit_link(self, from_id: str, to_id: str,
                    from_side: str | None, to_side: str | None) -> None:
        """用户在两卡之间拖出边：模型建边 + 落 pending 建议，同入撤销栈。"""
        if self.doc is None or from_id == to_id:
            return
        e = CanvasEdge(id=_new_id(), from_node=from_id, to_node=to_id,
                       from_side=from_side, to_side=to_side, to_end="arrow")
        self._push(AddEdgeCmd(self, e))

    def bridge_add(self, edge: CanvasEdge) -> None:
        """边 → pending 建议。source/kws 从 _edge_state 读（discover 批量预置
        canvas_ai；手绘走默认 canvas），undo/redo 重放不丢 AI 标记。"""
        if self.curator is None or self.doc is None or not self.doc.rel_path:
            return
        a, b = self.doc.node(edge.from_node), self.doc.node(edge.to_node)
        if not (a and b and a.type == "file" and b.type == "file" and a.file and b.file):
            return                          # 文本卡之间不产建议（无正文可写回）
        st = self._edge_state.setdefault(edge.id, {"source": "canvas", "kws": []})
        res = self.curator.canvas_edge_add(self.doc.rel_path, edge.id, a.file, b.file,
                                           source=st.get("source", "canvas"),
                                           shared_keywords=st.get("kws") or None)
        if res and "sid" in res:
            st["sid"], st["status"] = res["sid"], res["status"]
        else:
            # already_linked / 重画已处理过的边：按 edge_id 反查历史行绑定状态
            info = self.curator.canvas_edge_map(self.doc.rel_path).get(edge.id)
            if info:
                st["sid"], st["status"] = info["sid"], info["status"]
        it = self.view._edges.get(edge.id)
        if it is not None:
            it.set_status(st.get("status", ""))

    def bridge_remove(self, edge_id: str) -> None:
        st = self._edge_state.pop(edge_id, None)
        sid = (st or {}).get("sid")
        if sid is not None and self.curator is not None:
            self.curator.canvas_edge_remove(sid)

    # ---------- 画布内"发现关联"（C5） ----------
    def discover_links(self) -> int:
        """对画布上的笔记卡跑候选关联 → 批量画成琥珀虚线边（AI 建议，进审阅队列）。"""
        if self.doc is None or self.curator is None or not self.doc.rel_path:
            return 0
        by_rel: dict[str, list[str]] = {}
        for n in self.doc.nodes:
            if n.type == "file" and n.file:
                by_rel.setdefault(n.file, []).append(n.id)
        existing = set()
        for e in self.doc.edges:
            f, t = self.doc.node(e.from_node), self.doc.node(e.to_node)
            if f and t and f.file and t.file:
                existing.add(frozenset((f.file, t.file)))
        sug = self.curator.canvas_suggest([(r, ids) for r, ids in by_rel.items()],
                                          existing)
        if not sug:
            return 0
        self._undo.beginMacro(f"接受 {len(sug)} 条 AI 建议")
        for s in sug:
            edge = CanvasEdge(id=_new_id(), from_node=s["from_node"],
                              to_node=s["to_node"], to_end="arrow")
            self._push(AddEdgeCmd(self, edge, source="canvas_ai", kws=s["kws"]))
        self._undo.endMacro()
        return len(sug)

    def do_discover(self) -> None:
        n = self.discover_links()
        self._legend.set_note(
            f"⚡ 已把 {n} 条 AI 建议画到画布——去审阅队列逐条定夺（Ctrl+Z 撤整批）"
            if n else "未发现可关联的卡片：需 ≥2 张已入库笔记卡，且有共享关键词/标题关联")

    def new_document(self) -> None:
        self.new_document_interactive()

    def new_document_interactive(self) -> str | None:
        """起名并立刻落盘空 .canvas（树/下拉立即可见，编辑前文档是干净态）。"""
        if not self.confirm_discard():
            return None
        rel = self._ask_new_rel()
        if not rel:
            return None
        doc = CanvasDocument(rel_path=rel)
        try:
            self.snapshots.write(rel, doc.dumps(), "新建白板", require_confirm=False)
        except Exception as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return None
        doc.finalize_save(doc.dumps())        # 盘上基线=刚写入的空文档
        self.doc = doc
        self._edge_state = {}
        self._undo.clear()
        self.view.build(doc)
        self.refresh_doc_list()
        if self.on_docs_changed:
            self.on_docs_changed()      # 让主窗口重建文件树（白板分区）
        self._update_title()
        self.view.setFocus()
        return rel

    def close_document(self) -> None:
        if not self.confirm_discard():
            return
        self.doc = None
        self._edge_state = {}
        self._label_cache.clear()         # 上限=当前文档的卡数，关文档即收
        self._pix_cache.clear()
        self._undo.clear()
        self.view.build(None)
        self._update_title()

    def _ask_new_rel(self) -> str | None:
        name, ok = QInputDialog.getText(self, "新建白板", "白板名称：")
        if not ok or not name.strip():
            return None
        rel = slugify(name.strip())
        if not rel.lower().endswith(".canvas"):
            rel += ".canvas"
        if (self.vault.vault_path / rel).exists():
            QMessageBox.warning(self, "已存在", f"Vault 里已有 {rel}，请换一个名字。")
            return None
        return rel

    def save_current(self) -> bool:
        if self.doc is None:
            return False
        self.discard_editor()                 # 原地编辑未失焦：先提交再序列化
        if not self.doc.rel_path:
            rel = self._ask_new_rel()
            if not rel:
                return False
            self.doc.rel_path = rel
        text = self.doc.dumps()
        # C1：写盘前先对表磁盘——外部（Obsidian/另一窗口）改过就必须表态，绝不静默覆盖
        if self.doc.stale_on_disk(content_fp(self.vault.read_note(self.doc.rel_path))) \
                and not self._resolve_conflict():
            return False
        is_new = not (self.vault.vault_path / self.doc.rel_path).exists()
        try:
            self.snapshots.write(self.doc.rel_path, text, "白板保存",
                                 require_confirm=False)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return False
        self.doc.finalize_save(text)
        self._update_title()
        self.refresh_doc_list()
        if is_new and self.on_docs_changed:
            self.on_docs_changed()      # 首次落盘（文件从无到有）→ 刷新树
        return True

    # ---------- 外部改动保护（C1） ----------
    def sync_disk_quiet(self) -> None:
        """切回白板页且本地干净：盘被外部改过 → 静默重载（保视口）。"""
        if self.doc is None or self.doc.dirty or not self.doc.rel_path:
            return
        if self.doc.stale_on_disk(content_fp(self.vault.read_note(self.doc.rel_path))):
            self.reload_from_disk()

    def reload_from_disk(self) -> None:
        doc, _ = CanvasDocument.open(self.vault, self.doc.rel_path)
        self.doc = doc
        self._edge_state = {}
        self._undo.clear()
        self.view.build(doc, preserve_view=True)
        self._load_edge_status()
        self._update_title()

    def _resolve_conflict(self) -> bool:
        """True=覆盖（继续保存）；False=中止本次保存（已重载或取消）。"""
        box = QMessageBox(QMessageBox.Icon.Warning, "磁盘冲突",
                          "该白板被外部程序（Obsidian / 另一窗口）修改过。\n如何处理：",
                          QMessageBox.StandardButton.Save
                          | QMessageBox.StandardButton.Discard
                          | QMessageBox.StandardButton.Cancel, self)
        box.button(QMessageBox.StandardButton.Save).setText("以本地版本覆盖")
        box.button(QMessageBox.StandardButton.Discard).setText("重载磁盘版本")
        box.button(QMessageBox.StandardButton.Cancel).setText("取消")
        ch = box.exec()
        if ch == QMessageBox.StandardButton.Save:
            return True
        if ch == QMessageBox.StandardButton.Discard:
            if self.doc.dirty:
                q = QMessageBox.question(
                    self, "放弃本地改动？",
                    "本地白板有未保存改动，重载磁盘版本将丢弃这些改动。",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if q != QMessageBox.StandardButton.Yes:
                    return False
            self.reload_from_disk()
        return False

    def confirm_discard(self) -> bool:
        """未保存改动 → 保存/丢弃/取消；返回 False 表示取消当前动作。"""
        if self.doc is None or not self.doc.dirty:
            return True
        box = QMessageBox(QMessageBox.Icon.Warning, "未保存的白板",
                          f"{self.doc.rel_path or '未命名白板'} 有未保存改动。",
                          QMessageBox.StandardButton.Save |
                          QMessageBox.StandardButton.Discard |
                          QMessageBox.StandardButton.Cancel, self)
        box.button(QMessageBox.StandardButton.Save).setText("保存")
        box.button(QMessageBox.StandardButton.Discard).setText("丢弃")
        box.button(QMessageBox.StandardButton.Cancel).setText("取消")
        ch = box.exec()
        if ch == QMessageBox.StandardButton.Save:
            return self.save_current()
        return ch == QMessageBox.StandardButton.Discard

    def _update_title(self) -> None:
        if self.doc is None:
            self._title.setText("白板")
            self._save_btn.setEnabled(False)
            return
        dot = " ●" if self.doc.dirty else ""
        self._title.setText(f"{self.doc.rel_path or '未命名'}{dot}")
        self._save_btn.setEnabled(self.doc.dirty)

    # ---------- 卡片标签（标题+摘要） ----------
    def refresh_card_labels(self) -> None:
        """正文可能在笔记工作区被改过：切回白板页时刷新全部笔记卡摘要。"""
        if self.doc is None:
            return
        for n in self.doc.nodes:
            if n.type == "file":
                self.view.update_card_label(n)

    def card_label(self, node: CanvasNode) -> tuple[str, str]:
        """笔记卡标题+正文预览（markdown 原样保留换行）。mtime 缓存：
        refresh_card_labels 每切页全量调用，只有真改过的笔记才会重读。"""
        if node.type == "text":
            return ("文本", "")
        rel = node.file or ""
        f = self.store.get_file(rel)
        title = (f["title"] if f else _stem(rel)) or "（空）"
        if rel.lower().endswith(IMAGE_EXTS):
            return (title, "")             # 图片卡不读正文（read_note 遇二进制必炸）
        try:
            mtime = (self.vault.vault_path / rel).stat().st_mtime
        except OSError:
            return (title, MISSING_TEXT)
        hit = self._label_cache.get(rel)
        if hit and hit[0] == mtime:
            return hit[1]
        content = self.vault.read_note(rel)
        if not content:
            out = (title, MISSING_TEXT)
        else:
            body = "\n".join(l for l in content.splitlines()
                             if not l.startswith("# ")).strip()
            out = (title, body[:_EXCERPT_LEN])
        self._label_cache[rel] = (mtime, out)
        return out

    def pixmap_for(self, rel: str, w: float, h: float):
        """缩略图缓存：mtime 变了才重读；解码失败缓存 None（破图占位由 paint 画）。"""
        from PySide6.QtGui import QPixmap
        path = self.vault.vault_path / rel
        try:
            m = path.stat().st_mtime
        except OSError:
            return None
        hit = self._pix_cache.get(rel)
        if hit is not None and hit[0] == m:
            return hit[1]
        img = QImageReader(str(path)).read()
        pm = None if img.isNull() else QPixmap.fromImage(img).scaled(
            max(1, int(w)), max(1, int(h)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        self._pix_cache[rel] = (m, pm)
        return pm

    # ---------- 图片入板（C7） ----------
    def import_external_images(self, files: list) -> list[str]:
        """外部图片复制进 vault 附件/ 目录（shutil 二进制拷贝，不走 write_note）。
        返回新 rel 列表；重名自动 -1/-2。"""
        existing = set()
        adir = self.vault.vault_path / ATTACH_DIR
        if adir.exists():
            existing = {f"{ATTACH_DIR}/{p.name}" for p in adir.iterdir() if p.is_file()}
        adir.mkdir(parents=True, exist_ok=True)
        out = []
        for src in files:
            src = Path(src)
            if not src.is_file() or not src.suffix.lower() in IMAGE_EXTS:
                continue
            rel = unique_attach_rel(src.name, existing)
            shutil.copyfile(src, self.vault.vault_path / rel)
            existing.add(rel)
            out.append(rel)
        return out

    def add_externals_with_ask(self, files: list, pos: QPointF) -> int:
        """批量只问一次：允许则复制入附件/再成卡。返回成卡数。"""
        if not files:
            return 0
        q = QMessageBox.question(
            self, "白板附件",
            f"{len(files)} 张图在 Vault 外，需先复制进『{ATTACH_DIR}/』目录才能成为画布卡片。允许吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if q != QMessageBox.StandardButton.Yes:
            return 0
        n = 0
        for i, rel in enumerate(self.import_external_images(files)):
            off = QPointF(pos.x() + i * 24, pos.y() + i * 24)
            if self.add_card_by_rel(rel, off):
                n += 1
        return n

    # ---------- 来自视图的提交入口（全部经撤销栈） ----------
    def _push(self, cmd) -> None:
        self._undo.push(cmd)
        self._update_title()

    def commit_move(self, moves: dict[str, tuple]) -> None:
        self._push(MoveCardsCmd(self, moves))

    def commit_resize(self, nid: str, old_wh, new_wh) -> None:
        self._push(ResizeCardCmd(self, nid, old_wh, new_wh))

    def delete_selection(self) -> None:
        if self.doc is None:
            return
        sel = self.view._scene.selectedItems()
        cards = [c.node.id for c in sel if isinstance(c, (_CardItem, _GroupItem))]
        edges = [c.edge for c in sel if isinstance(c, _EdgeItem)]
        if not cards and not edges:
            return
        self._undo.beginMacro("删除")
        if cards:
            self._push(DeleteCardsCmd(self, cards))
        for e in edges:
            self._push(RemoveEdgeCmd(self, e))
        self._undo.endMacro()

    def on_card_dblclick(self, item: _CardItem) -> None:
        if item.node.type == "file" and item.node.file:
            if item.node.file.lower().endswith(IMAGE_EXTS):
                QDesktopServices.openUrl(QUrl.fromLocalFile(
                    str(self.vault.vault_path / item.node.file)))   # 交系统预览
            elif self._on_open:
                self._on_open(item.node.file)
        elif item.node.type == "text":
            self.edit_text_card(item)

    def edit_text_card(self, item: _CardItem) -> None:
        """C9：双击/菜单进入卡内原地编辑（弹窗 _TextDialog 退役为无焦点场景兜底不用）。"""
        item.begin_inline_edit()

    # ---------- 原地编辑全局支撑（C9） ----------
    def _sc_disable(self, off: bool) -> None:
        for sc in self._scs:
            sc.setEnabled(not off)

    def discard_editor(self) -> None:
        if self._editing is not None:
            self._editing._end_inline_edit(True)

    # ---------- 右键菜单：build_* 构建（可测） + _show_menu 弹出（真实环境一行） ----------
    def _show_menu(self, menu, global_pos) -> None:
        menu.exec(global_pos)

    def build_card_menu(self, item: _CardItem) -> QMenu | None:
        if self.doc is None:
            return None
        if not item.isSelected():
            self.view._scene.clearSelection()
            item.setSelected(True)
        ids = [c.node.id for c in self.view._scene.selectedItems()
               if isinstance(c, _CardItem)]
        if not ids:
            return None
        menu = QMenu(self.view)
        node = item.node
        is_img = bool(node.file) and node.file.lower().endswith(IMAGE_EXTS)
        if len(ids) == 1 and node.type == "file" and node.file:
            if is_img:
                menu.addAction("打开图片", lambda: self._act_open_file(node))
            else:
                menu.addAction("打开笔记", lambda: self._act_open_note(node))
                menu.addAction("✎ 编辑摘要", lambda: item.begin_inline_edit())
                if node.label:
                    menu.addAction("恢复自动摘要", lambda: self.commit_set_props(
                        "node", [node.id], {"label": None}, text="恢复自动摘要"))
        if len(ids) == 1 and node.type == "text":
            menu.addAction("编辑文本", lambda: self.edit_text_card(item))
        colors = menu.addMenu("颜色")
        for key, name in PALETTE_NAMES:
            colors.addAction(name, lambda k=key: self._act_set_color(ids, k))
        colors.addAction("自定义 Hex…", lambda: self._act_custom_color(ids, "node"))
        colors.addAction("清除颜色", lambda: self._act_set_color(ids, None))
        if len(ids) >= 2:
            menu.addAction("▣ 成组…", lambda: self._act_group(ids))
        menu.addAction("复制", self.copy_selection)
        menu.addAction("删除", self.delete_selection)
        return menu

    def build_group_menu(self, item: _GroupItem) -> QMenu | None:
        if self.doc is None:
            return None
        if not item.isSelected():
            self.view._scene.clearSelection()
            item.setSelected(True)
        gid = item.node.id
        menu = QMenu(self.view)
        menu.addAction("✎ 组标签…",
                       lambda: self._act_group_label(gid, item.node.label or ""))
        colors = menu.addMenu("颜色")
        for key, name in PALETTE_NAMES:
            colors.addAction(name, lambda k=key: self.commit_set_props(
                "node", [gid], {"color": k}, text="设置组颜色"))
        colors.addAction("自定义 Hex…", lambda: self._act_custom_color([gid], "node"))
        colors.addAction("清除颜色", lambda: self.commit_set_props(
            "node", [gid], {"color": None}, text="清除组颜色"))
        menu.addAction("删除组（不删成员）", self.delete_selection)
        return menu

    def _act_group(self, ids: list[str]) -> None:
        self._push(GroupCmd(self, ids))

    def _act_group_label(self, gid: str, cur: str) -> None:
        text, ok = QInputDialog.getText(self, "组标签", "组名（留空显示\"组\"）：", text=cur)
        if not ok:
            return
        self.commit_set_props("node", [gid], {"label": text.strip() or None},
                              text="重命名组")

    def build_edge_menu(self, edge_item: _EdgeItem) -> QMenu | None:
        if self.doc is None:
            return None
        if not edge_item.isSelected():
            self.view._scene.clearSelection()
            edge_item.setSelected(True)
        eid = edge_item.edge.id
        menu = QMenu(self.view)
        menu.addAction("编辑标签…",
                       lambda: self._act_edge_label(eid, edge_item.edge.label or ""))
        colors = menu.addMenu("颜色")
        for key, name in PALETTE_NAMES:
            colors.addAction(name, lambda k=key:
                             self.commit_set_props("edge", [eid], {"color": k},
                                                   text="设置连线颜色"))
        colors.addAction("自定义 Hex…", lambda: self._act_custom_color([eid], "edge"))
        colors.addAction("清除颜色", lambda: self.commit_set_props(
            "edge", [eid], {"color": None}, text="清除连线颜色"))
        menu.addAction("切换箭头方向", lambda: self._act_toggle_arrow(edge_item.edge))
        menu.addAction("删除", self.delete_selection)
        return menu

    def build_blank_menu(self) -> QMenu:
        menu = QMenu(self.view)
        if self._clipboard is not None:
            menu.addAction("粘贴", self.paste)
        menu.addAction("全选", self.view.select_all_cards)
        menu.addAction("🖼 插入图片…", self.insert_image)
        menu.addAction("⚡ 对画布卡片发现关联", self.do_discover)
        return menu

    def _act_open_note(self, node: CanvasNode) -> None:
        if self._on_open and node.file:
            self._on_open(node.file)

    def _act_open_file(self, node: CanvasNode) -> None:
        if node.file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(
                str(self.vault.vault_path / node.file)))

    def _act_set_color(self, ids: list[str], v: str | None) -> None:
        self.commit_set_props("node", ids, {"color": v},
                              text="设置颜色" if v else "清除颜色")

    def _act_custom_color(self, ids: list[str], kind: str) -> None:
        text, ok = QInputDialog.getText(self, "自定义颜色", "十六进制（#RRGGBB）：")
        if not ok:
            return
        text = text.strip()
        if not _HEX_RE.match(text):
            QMessageBox.warning(self, "颜色无效", "需要 #RRGGBB 形式，如 #1a73e8。")
            return
        self.commit_set_props(kind, ids, {"color": text}, text="自定义颜色")

    def _act_edge_label(self, eid: str, cur: str) -> None:
        text, ok = QInputDialog.getText(self, "连线标签", "标签（留空清除）：", text=cur)
        if not ok:
            return
        self.commit_set_props("edge", [eid], {"label": text.strip() or None},
                              text="编辑连线标签")

    def _act_toggle_arrow(self, edge: CanvasEdge) -> None:
        """双端箭头态互换：无→尾箭头，尾→头，头→双，双→无。"""
        self.commit_set_props("edge", [edge.id],
                              {"from_end": edge.to_end, "to_end": edge.from_end},
                              text="切换箭头方向")

    def commit_set_props(self, kind: str, ids: list[str], props: dict,
                         text: str = "修改样式") -> None:
        if self.doc is None or not ids:
            return
        self._push(SetPropCmd(self, kind, ids, props, text))

    # ---------- 复制粘贴（C3：会话内剪贴板，deepcopy 隔离） ----------
    def copy_selection(self) -> None:
        if self.doc is None:
            return
        ids = [c.node.id for c in self.view._scene.selectedItems()
               if isinstance(c, _CardItem)]
        if ids:
            self._clipboard = extract_subgraph(self.doc, ids)

    def paste(self) -> None:
        if self.doc is None or self._clipboard is None:
            return
        nodes, edges = copy.deepcopy(self._clipboard)
        remap_copy(nodes, edges)
        self._undo.beginMacro("粘贴")
        for n in nodes:
            self._push(AddCardCmd(self, n, []))
        for e in edges:
            self._push(AddEdgeCmd(self, e))
        self._undo.endMacro()
        self.view._scene.clearSelection()
        for n in nodes:
            it = self.view.item_of(n.id)
            if it is not None:
                it.setSelected(True)
        self.view._fit_scene_rect_soft()

    def duplicate_selection(self) -> None:
        self.copy_selection()
        self.paste()

    # ---------- 图谱 → 画布批量导入（C8） ----------
    def import_from_graph_dialog(self) -> None:
        dlg = _ImportDialog(self.store.all_files, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            n = self.import_from_graph(dlg.selected_rels())
            if n:
                self._legend.set_note(f"⇤ 已从图谱导入 {n} 张卡片（既有双链自动画为实线边）")

    def import_from_graph(self, rels: list[str]) -> int:
        """网格排布成卡；选中笔记间的既有 links 连成边（走桥时 already_linked
        → 中性实线零队列污染：已成双链的关联无需再审）。"""
        if self.doc is None or not rels:
            return 0
        have = {n.file for n in self.doc.nodes if n.type == "file" and n.file}
        todo = [r for r in dict.fromkeys(rels) if r not in have]
        if not todo:
            return 0
        center = self.view.mapToScene(self.view.viewport().rect().center())
        cols = max(1, math.ceil(math.sqrt(len(todo))))
        rows = max(1, math.ceil(len(todo) / cols))
        start = (center.x() - (cols - 1) * 280.0 / 2,
                 center.y() - (rows - 1) * 160.0 / 2)
        pts = grid_positions(len(todo), start)
        self._undo.beginMacro("从图谱导入")
        made = []
        for r, (x, y) in zip(todo, pts):
            if self.add_card_by_rel(r, QPointF(x, y)):
                made.append(r)
        if made:
            card_of: dict[str, str] = {}
            for n in self.doc.nodes:
                if n.type == "file" and n.file and n.file not in card_of:
                    card_of[n.file] = n.id
            stem_of = {_stem(r): r for r in card_of}
            for row in self.store.conn.execute(
                    "SELECT from_path, to_target FROM links"):
                to_rel = stem_of.get(row["to_target"])
                if (row["from_path"] in card_of and to_rel in card_of
                        and row["from_path"] != to_rel):
                    e = CanvasEdge(id=_new_id(), from_node=card_of[row["from_path"]],
                                   to_node=card_of[to_rel], to_end="arrow")
                    self._push(AddEdgeCmd(self, e))
        self._undo.endMacro()
        self.view._fit_scene_rect_soft()
        return len(made)

    def drop_note_urls(self, urls, scene_pos: QPointF) -> None:
        inside, outside_imgs = [], []
        for url in urls:
            p = Path(url.toLocalFile())
            if not p.exists():
                continue
            try:
                inside.append(self.vault.rel_path_for(p))
            except ValueError:
                if p.suffix.lower() in IMAGE_EXTS:      # vault 外图片走附件通道
                    outside_imgs.append(p)
        for rel in inside:
            self.add_card_by_rel(rel, scene_pos)
        if outside_imgs:
            self.add_externals_with_ask(outside_imgs, scene_pos)

    def add_note_card(self, rel: str, scene_pos: QPointF) -> bool:
        return self.add_card_by_rel(rel, scene_pos)

    def add_card_by_rel(self, rel: str, scene_pos: QPointF) -> bool:
        """建卡统一路由（树拖出 / explorer 拖入 / 右键插入 / 批量导入共用）。
        笔记须已入库或在 vault；图片须在 vault 附件位。"""
        if self.doc is None:
            return False
        rel = str(rel).replace("\\", "/")
        low = rel.lower()
        if low.endswith(FILE_EXTS):
            ok = (self.store.get_file(rel) is not None
                  or (self.vault.vault_path / rel).exists())
        elif low.endswith(IMAGE_EXTS):
            ok = (self.vault.vault_path / rel).exists()
        else:
            return False
        if not ok:
            return False
        n = self.doc.add_file_card(rel, scene_pos.x(), scene_pos.y())
        self._push(AddCardCmd(self, n, []))
        self.view.add_card_item(n)     # redo 幂等：首跑不重建
        self.view._fit_scene_rect_soft()
        return True

    def insert_image(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "插入图片", "", "图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.svg)")
        if not files:
            return
        pos = self.view.mapToScene(self.view.viewport().rect().center())
        inside, outside = [], []
        for f in files:
            p = Path(f)
            try:
                inside.append(self.vault.rel_path_for(p))
            except ValueError:
                outside.append(p)
        for rel in inside:
            self.add_card_by_rel(rel, pos)
        self.add_externals_with_ask(outside, pos)


class _ImportDialog(QDialog):
    """图谱→画布批量导入选择器（C8）：搜索过滤 + 多选。
    数据源注入 list_fn（返回 [{path,title,topic}]），客户端子串过滤——
    不上 KnowledgeSearch：这里只要名字清单，且空查询要全列。"""

    def __init__(self, list_fn, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从图谱导入笔记到白板")
        self.resize(420, 460)
        lay = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText("过滤标题/路径/主题…")
        self._search.textChanged.connect(self._filter)
        lay.addWidget(self._search)
        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for f in list_fn() or []:
            d = dict(f)                        # store.all_files 是 sqlite3.Row
            item = QListWidgetItem(f"[{d.get('topic') or '未分类'}] {d.get('title', '')}")
            item.setData(Qt.ItemDataRole.UserRole, d.get("path"))
            self._list.addItem(item)
        lay.addWidget(self._list, 1)
        row = QHBoxLayout()
        b_all = QPushButton("全选可见")
        b_all.clicked.connect(self._select_visible)
        self._count = QLabel("已选 0 项（建议单次 ≤100）")
        self._list.itemSelectionChanged.connect(self._upd_count)
        row.addWidget(b_all)
        row.addWidget(self._count, 1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _filter(self, q: str) -> None:
        q = q.strip().lower()
        for i in range(self._list.count()):
            it = self._list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def _select_visible(self) -> None:
        for i in range(self._list.count()):
            it = self._list.item(i)
            if not it.isHidden():
                it.setSelected(True)

    def _upd_count(self) -> None:
        self._count.setText(f"已选 {len(self.selected_rels())} 项（建议单次 ≤100）")

    def selected_rels(self) -> list[str]:
        return [it.data(Qt.ItemDataRole.UserRole)
                for it in self._list.selectedItems()
                if it.data(Qt.ItemDataRole.UserRole)]


class _TextDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑文本卡片")
        self.resize(420, 260)
        lay = QVBoxLayout(self)
        self._edit = QPlainTextEdit()
        self._edit.setPlainText(text)
        lay.addWidget(self._edit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def text(self) -> str:
        return self._edit.toPlainText()
