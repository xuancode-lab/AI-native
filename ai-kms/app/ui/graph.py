"""知识图谱可视化（自研，QGraphicsView，浅色科技风）。

- 力导向布局（斥力 + 弹簧引力）定位节点，无第三方图库依赖
- 浅色底 + 规则点阵 + 随机星尘 + 视口水印衬托；节点=落地投影 + 球面渐变 + 高光斑 +
  外发光光晕；边=辉光描边，semantic 虚线流动
- 节点颜色按主题色；孤立点红色描边高亮
- 交互：滚轮锚点缩放（限幅）、左键抓空白/中键/空格 掌形平移、单击=选中高亮、
  双击=打开笔记、悬停/选中高亮相邻节点并淡化其余、刷新保留视角
- link 边=实线，semantic 边=流动虚线，双击节点回调 on_select(rel_path)
"""
from __future__ import annotations

import math
import random

try:
    from PySide6.QtCore import Qt, QRectF, QPointF, QTimer
    from PySide6.QtGui import (
        QColor, QPen, QBrush, QFont, QPainter, QPainterPath, QRadialGradient,
        QTransform,
    )
    from PySide6.QtWidgets import (
        QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsPathItem,
    )
    _HAVE_QT = True
except ImportError:  # pragma: no cover
    _HAVE_QT = False

# ---- 画布底色 / 点阵 / 星点 / 水印（浅色）----
BG_COLOR = "#f5f6f8"
BG_GRID_DOT = "#e1e5ea"
BG_GRID_DOT_MAJOR = "#c9d0d8"   # 每 4 格的准星点
BG_GRID_STEP = 32                # 场景坐标网格步长(px)
STAR_TILE = 240.0                # 星点平铺瓦片（场景坐标，确定性随机）
STAR_PER_TILE = 5                # 每瓦片星点数
STAR_MAX_TOTAL = 1200            # 缩小时瓦片自适应放大，星点总数上限
WM_MAIN = "AI-NATIVE KMS"        # 水印主标（视口居中，不随平移缩放）
WM_SUB = "KNOWLEDGE GRAPH"

# ---- 文本 ----
TEXT_LABEL = "#202124"
TEXT_LABEL_DIM = "#9aa0a6"
LABEL_BG = "#ffffff"            # 标签底片（QColor 不解析 rgba() 字符串，必须用 hex）
LABEL_BORDER = "#dde2e8"        # 标签底片描边

# ---- 主题色：topic -> (中心亮色, 边缘暗色)，浅底可读 ----
NEON_TOPIC = {
    "技术": ("#6bb6ff", "#1a66cc"),
    "学习": ("#4ade80", "#15803d"),
    "课题": ("#ffc247", "#b45309"),
    "创作": ("#ff7a8a", "#d1242f"),
    "生活": ("#c79aff", "#7c3aed"),
    "未分类": ("#aab4c0", "#656d76"),
}
ISOLATED_CORE = "#ff8a8a"        # 孤立点球体中心
ISOLATED_EDGE = "#d92d20"        # 孤立点描边
ISOLATED_DEEP = "#e5484d"        # 孤立点球体边缘

# ---- 边 ----
EDGE_LINK_COLOR = "#2563eb"      # link 实线（蓝）
EDGE_SEMANTIC_COLOR = "#0d9488"  # semantic 虚线（青）
EDGE_GLOW_ALPHA = 70             # 底层光晕透明度
EDGE_GLOW_ALPHA_HI = 140         # 高亮态光晕透明度
EDGE_HIGHLIGHT_ALPHA = 210

# ---- 节点半径：rr = BASE + GAIN * sqrt(min(weight, 49)) ----
RADIUS_BASE = 13.0
RADIUS_GAIN = 4.5
GROW_GLOW = 0.55                 # 光晕外扩比例
LABEL_SPACE = 20.0

# ---- 交互 ----
CLICK_THRESHOLD = 6.0            # 视口像素，press→release 位移 < 此值才算单击
MIN_ZOOM, MAX_ZOOM = 0.15, 5.0   # transform().m11() 限制
ZOOM_STEP = 1.15
ANIM_INTERVAL = 40               # ms，semantic dash 动画帧间隔
ANIM_DASH_STEP = 1.6             # 每帧 dashOffset 增量
ANIM_DASH_PERIOD = 11.0          # dash pattern [6,5] 的周期
DIM_OPACITY = 0.18               # 非邻居淡化
GLOW_MAX_NODES = 400             # 超过则关闭节点光晕
ANIM_MAX_EDGES = 500             # 超过则动画降频

SEED = 42


def radius_for(weight: int) -> float:
    """weight → 节点半径（sqrt 拉开差异，防止过大）。"""
    w = min(max(int(weight), 0), 49)
    return RADIUS_BASE + RADIUS_GAIN * math.sqrt(w)


def _neon_for(topic: str):
    """topic → (中心亮色 QColor, 球体边缘深色 QColor)，未知主题回落 未分类。"""
    core, edge = NEON_TOPIC.get(topic, NEON_TOPIC["未分类"])
    return QColor(core), QColor(edge)


class _NodeItem(QGraphicsItem):
    def __init__(self, rel_path: str, title: str, topic: str, isolated: bool,
                 usage: int, glow_enabled: bool = True):
        super().__init__()
        self.rel_path = rel_path
        self.title = title
        self.topic = topic
        self.isolated = isolated
        self.usage = usage
        self.r = radius_for(usage)
        self.glow_enabled = glow_enabled
        self.hovered = False
        self.selected = False
        self.dimmed = False
        self.on_move = None          # 移动回调：拖动时实时更新相连边
        self.on_hover = None         # 悬停回调：fn(node, entered)
        self.on_open = None          # 双击回调：fn(rel_path)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{title}\n主题:{topic} · 使用:{usage}\n双击打开\n{rel_path}")

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            if self.on_move:
                self.on_move(self)
        return super().itemChange(change, value)

    def boundingRect(self) -> QRectF:
        # 必须计入光晕外扩 + 标签，否则发光/文字被裁剪
        g = self.r * GROW_GLOW * 1.35 + 8
        half_w = max(self.r + g, 84.0)   # 9pt 粗体 12 字的标签可能比球宽
        return QRectF(-half_w, -self.r - g,
                      2 * half_w, 2 * (self.r + g) + LABEL_SPACE + 6)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rr = self.r
        active = self.hovered or self.selected

        if self.isolated:
            core = QColor(ISOLATED_CORE)
            edge = QColor(ISOLATED_DEEP)
            stroke = QColor(ISOLATED_EDGE)
        else:
            core, edge = _neon_for(self.topic)
            stroke = QColor(edge)   # 深色描边贴合浅色底

        # ① 落地投影：球下方的软阴影，产生浮起的立体感
        sh_c = QPointF(0, rr * 0.72)
        sh_rx = rr * 1.05
        sh_ry = rr * 0.34
        sh = QRectF(sh_c.x() - sh_rx, sh_c.y() - sh_ry, 2 * sh_rx, 2 * sh_ry)
        shg = QRadialGradient(sh.center(), sh_rx)
        shc = QColor(20, 30, 50)
        shc.setAlpha(50 if not active else 70)
        shg.setColorAt(0.0, shc)
        shg.setColorAt(1.0, QColor(20, 30, 50, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(shg))
        painter.drawEllipse(sh)

        # ② 外发光光晕
        if self.glow_enabled:
            glow_r = rr * (1 + GROW_GLOW) * (1.25 if active else 1.0)
            grad = QRadialGradient(QPointF(0, 0), glow_r)
            c = QColor(core)
            c.setAlpha(110 if active else 55)
            e = QColor(core)
            e.setAlpha(0)
            grad.setColorAt(min(rr / glow_r, 0.99), c)
            grad.setColorAt(1.0, e)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawEllipse(QPointF(0, 0), glow_r, glow_r)

        # ③ 球体四段径向渐变：顶亮 → 主色 → 深边 → 底部更暗（球面感）
        bg = QRadialGradient(QPointF(-rr * 0.32, -rr * 0.38), rr * 1.55)
        top = QColor(core).lighter(118)
        bg.setColorAt(0.0, top)
        bg.setColorAt(0.42, QColor(core))
        bg.setColorAt(0.78, QColor(edge))
        bg.setColorAt(1.0, QColor(edge).darker(140))
        painter.setBrush(QBrush(bg))

        # ④ 主题色描边（选中/悬停时加粗）
        painter.setPen(QPen(stroke, 3.0 if active else 2.0))
        painter.drawEllipse(QRectF(-rr, -rr, 2 * rr, 2 * rr))

        # ⑤ 左上高光斑：白色径向渐变小亮点，模拟光源反射
        hl_c = QPointF(-rr * 0.34, -rr * 0.40)
        hl_r = rr * 0.42
        hl = QRadialGradient(hl_c, hl_r)
        hl.setColorAt(0.0, QColor(255, 255, 255, 190))
        hl.setColorAt(0.55, QColor(255, 255, 255, 60))
        hl.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(hl))
        painter.drawEllipse(hl_c, hl_r * 0.95, hl_r * 0.72)

        # ⑥ 标签：浅色粗体文字 + 不透明圆角底片
        max_chars = 12 if rr >= 30 else 9
        t = self.title
        if len(t) > max_chars:
            t = t[:max_chars - 1] + "…"
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(t)
        lh = fm.height()
        label_rect = QRectF(-tw / 2 - 6, rr + 2, tw + 12, lh + 4)
        painter.setPen(QPen(QColor(LABEL_BORDER), 1))
        painter.setBrush(QColor(LABEL_BG))
        painter.drawRoundedRect(label_rect, 4, 4)
        painter.setPen(QColor(TEXT_LABEL_DIM if self.dimmed else TEXT_LABEL))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, t)

    # ---------- 事件 ----------
    def hoverEnterEvent(self, event):
        if self.on_hover:
            self.on_hover(self, True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        if self.on_hover:
            self.on_hover(self, False)
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self.on_open:
            self.on_open(self.rel_path)
        event.accept()


class _EdgeItem(QGraphicsPathItem):
    """发光边：底层宽光晕 + 核心线；semantic 带流动虚线；highlighted 提亮。"""

    def __init__(self, src: _NodeItem, dst: _NodeItem, semantic: bool):
        super().__init__()
        self.src = src
        self.dst = dst
        self.semantic = semantic
        self.highlighted = False
        self.dash_offset = 0.0
        self.setZValue(0)
        # 边不接收鼠标，点击穿透到画布
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self) -> QRectF:
        # 宽光晕 Pen 需要外扩，防止裁剪
        return super().boundingRect().adjusted(-5, -5, 5, 5)

    def set_dash_offset(self, v: float) -> None:
        self.dash_offset = v
        self.update()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base = QColor(EDGE_SEMANTIC_COLOR) if self.semantic else QColor(EDGE_LINK_COLOR)

        # 底层光晕
        glow = QColor(base)
        glow.setAlpha(EDGE_GLOW_ALPHA_HI if self.highlighted else EDGE_GLOW_ALPHA)
        painter.setPen(QPen(glow, 5.0 if self.highlighted else 3.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.path())

        # 核心线
        core = QColor(base)
        core.setAlpha(EDGE_HIGHLIGHT_ALPHA if self.highlighted else 200)
        cw = (1.4 if self.semantic else 1.6) + (0.8 if self.highlighted else 0.0)
        if self.semantic:
            pen = QPen(core, cw, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap)
            pen.setDashPattern([6.0, 5.0])
            pen.setDashOffset(self.dash_offset)
        else:
            pen = QPen(core, cw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(self.path())


class GraphView(QGraphicsView):
    def __init__(self, on_select=None, parent=None):
        super().__init__(parent)
        self._on_select = on_select
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(self.renderHints() |
                            QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.TextAntialiasing)
        self.setBackgroundBrush(QBrush(BG_COLOR))
        # 不用橡皮筋选择；平移走 空格/中键
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 默认小手：空白处=张手（可抓），按住=握拳；节点自带指针光标
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)

        self._nodes_by_path = {}
        self._edges = []                  # [_EdgeItem]
        self._edges_by_node = {}          # node -> [_EdgeItem]
        self._adj = {}                    # node -> set(node)，含 semantic+link
        self._layout_iterations = 220

        # 视角
        self._first_load = True
        self._pending_fit = False

        # 点击 / 拖动 / 平移状态
        self._press_pos = None            # QPointF | None（视口坐标）
        self._press_item = None           # _NodeItem | None
        self._press_node_pos = None       # 按下时节点 pos
        self._panning = False
        self._pan_start = None
        self._space_held = False

        # 高亮状态
        self._hover_node = None
        self._selected_node = None

        # semantic 流动动画（默认关，由 main_window 切到图谱页时开启）
        self._anim_on = False
        self._dash = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(ANIM_INTERVAL)
        self._anim_timer.timeout.connect(self._tick_dash)

    # ---------- 数据 ----------
    def set_data(self, graph: dict, *, refit: bool | None = None):
        """graph: GraphEngine.build() 输出 {nodes, edges, isolated}。

        refit=None  → 仅首次 fitInView，之后保留视角
        refit=True  → 重建后适应视图（用户主动「重新布局」）
        refit=False → 尽量恢复刷新前的视角（后台自动刷新）
        """
        # 捕获视角必须在 clear 之前
        restore = None
        if not self._first_load and refit is False:
            restore = (QTransform(self.transform()),
                       self.mapToScene(self.viewport().rect().center()))

        self._scene.clear()
        self._nodes_by_path = {}
        self._edges = []
        self._edges_by_node = {}
        self._adj = {}
        self._hover_node = None
        self._selected_node = None

        isolated_set = set(graph["isolated"])
        glow_ok = len(graph["nodes"]) <= GLOW_MAX_NODES
        items = []
        for n in graph["nodes"]:
            item = _NodeItem(n["id"], n["title"], n["topic"],
                             n["id"] in isolated_set, n["weight"],
                             glow_enabled=glow_ok)
            item.setPos(QPointF(0, 0))
            self._scene.addItem(item)
            self._nodes_by_path[n["id"]] = item
            self._adj.setdefault(item, set())   # 孤立点也占位
            items.append(item)

        edges_by_kind = {"link": [], "semantic": []}
        for e in graph["edges"]:
            src = self._nodes_by_path.get(graph["nodes"][e["source"]]["id"])
            dst = self._nodes_by_path.get(graph["nodes"][e["target"]]["id"])
            if src and dst:
                edges_by_kind[e["kind"]].append((src, dst))

        if len(items) <= 1:
            for it in items:
                it.setPos(QPointF(0, 0))
        else:
            self._force_layout(items, edges_by_kind["link"] + edges_by_kind["semantic"])

        # 边画在节点下方
        for src, dst in edges_by_kind["semantic"]:
            self._add_edge(src, dst, semantic=True)
        for src, dst in edges_by_kind["link"]:
            self._add_edge(src, dst, semantic=False)

        # 节点回调
        for it in items:
            it.setZValue(1)
            it.on_move = self._on_node_move
            it.on_hover = self._on_node_hover
            it.on_open = self._on_open_node
        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-60, -60, 60, 60))

        # 视角策略
        if self._first_load or refit is True:
            if self.isVisible():
                self.fitInView(self._scene.sceneRect(),
                               Qt.AspectRatioMode.KeepAspectRatio)
                self._first_load = False
            else:
                self._pending_fit = True       # 首帧未显示，show 时再 fit
        elif restore is not None:
            self.setTransform(restore[0])
            self.centerOn(restore[1])
        # 其余情况（refit=None 且非首次）：transform 未被动过，天然保留

        self._update_anim_timer()

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_fit:
            self._pending_fit = False
            self._first_load = False
            if not self._scene.sceneRect().isEmpty():
                self.fitInView(self._scene.sceneRect(),
                               Qt.AspectRatioMode.KeepAspectRatio)

    def _add_edge(self, src, dst, semantic: bool):
        item = _EdgeItem(src, dst, semantic)
        item.setPath(self._edge_path(src, dst))
        self._scene.addItem(item)
        self._edges.append(item)
        self._edges_by_node.setdefault(src, []).append(item)
        self._edges_by_node.setdefault(dst, []).append(item)
        self._adj.setdefault(src, set()).add(dst)
        self._adj.setdefault(dst, set()).add(src)

    @staticmethod
    def _edge_path(src, dst) -> QPainterPath:
        path = QPainterPath(src.pos())
        path.lineTo(dst.pos())
        return path

    def _on_node_move(self, node):
        """节点拖动回调：只重算该节点相连的边。"""
        for edge_item in self._edges_by_node.get(node, []):
            edge_item.setPath(self._edge_path(edge_item.src, edge_item.dst))
        # 场景随节点外扩，避免拖出可视区
        self._scene.setSceneRect(self._scene.itemsBoundingRect().adjusted(-60, -60, 60, 60))

    # ---------- 高亮状态机 ----------
    def _on_node_hover(self, node, entered: bool):
        self._hover_node = node if entered else None
        self._apply_highlight()

    def _set_selected_node(self, node):
        self._selected_node = node
        self._apply_highlight()

    def _on_open_node(self, rel_path: str):
        if self._on_select:
            self._on_select(rel_path)

    def _apply_highlight(self):
        """高亮源 = hover 优先，否则 selected；源 ∪ 邻居保持，其余淡化。"""
        src = self._hover_node or self._selected_node
        if src is None or src not in self._adj:
            focus = None
        else:
            focus = {src} | self._adj.get(src, set())

        for n in self._nodes_by_path.values():
            n.hovered = (n is self._hover_node)
            n.selected = (n is self._selected_node)
            if focus is None:
                n.dimmed = False
                n.setOpacity(1.0)
            else:
                n.dimmed = n not in focus
                n.setOpacity(1.0 if n in focus else DIM_OPACITY)
            n.update()

        for e in self._edges:
            if focus is None:
                e.highlighted = False
                e.setOpacity(1.0)
            else:
                e.highlighted = e.src in focus and e.dst in focus
                e.setOpacity(1.0 if e.highlighted else DIM_OPACITY + 0.1)
            e.update()

    # ---------- 背景 ----------
    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QColor(BG_COLOR))
        self._draw_watermark(painter)
        self._draw_stars(painter, rect)
        self._draw_grid(painter, rect)

    def _draw_watermark(self, painter: QPainter):
        """视口居中水印：设备坐标绘制，不随平移/缩放移动。"""
        painter.save()
        painter.resetTransform()
        vp = self.viewport().rect()
        main = QColor(TEXT_LABEL)
        main.setAlpha(9)                     # 极淡，仅作衬托
        painter.setPen(main)
        f = QFont()
        f.setPointSize(46)
        f.setBold(True)
        painter.setFont(f)
        r1 = QRectF(vp)
        r1.setHeight(vp.height() // 2 + 20)
        painter.drawText(r1, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         WM_MAIN)
        sub = QColor(TEXT_LABEL)
        sub.setAlpha(14)
        painter.setPen(sub)
        f2 = QFont()
        f2.setPointSize(11)
        f2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 6.0)
        painter.setFont(f2)
        r2 = QRectF(vp)
        r2.moveTop(vp.height() // 2 + 30)
        r2.setHeight(24)
        painter.drawText(r2, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         WM_SUB)
        painter.restore()

    def _draw_stars(self, painter: QPainter, rect: QRectF):
        """随机星尘：瓦片 + 种子哈希，场景坐标确定性分布，平移缩放不闪变。"""
        tile = STAR_TILE
        # 缩小时自适应放大瓦片，控制星点总数
        while (rect.width() / tile + 2) * (rect.height() / tile + 2) * STAR_PER_TILE \
                > STAR_MAX_TOTAL:
            tile *= 2
        x0 = math.floor(rect.left() / tile) * tile
        y0 = math.floor(rect.top() / tile) * tile
        painter.setPen(Qt.PenStyle.NoPen)
        y = y0
        while y <= rect.bottom():
            x = x0
            while x <= rect.right():
                rng = random.Random(f"{SEED}:{int(x / tile)}:{int(y / tile)}")
                for _ in range(STAR_PER_TILE):
                    sx = x + rng.random() * tile
                    sy = y + rng.random() * tile
                    if not rect.contains(QPointF(sx, sy)):
                        continue
                    if rng.random() < 0.12:
                        # 少数亮星：更大更深
                        rr = rng.uniform(1.8, 2.4)
                        col = QColor("#8b97a8")
                        col.setAlpha(rng.randint(70, 110))
                    else:
                        rr = rng.uniform(0.5, 1.5)
                        col = QColor("#a9b4c2")
                        col.setAlpha(rng.randint(35, 90))
                    painter.setBrush(col)
                    painter.drawEllipse(QPointF(sx, sy), rr, rr)
                x += tile
            y += tile

    def _draw_grid(self, painter: QPainter, rect: QRectF):
        """规则点阵网格：场景坐标对齐，平移时随内容移动。"""
        step = float(BG_GRID_STEP)
        # 自适应步长：缩小视图时避免点阵爆炸
        while step < 4096:
            cols = int(rect.width() / step) + 2
            rows = int(rect.height() / step) + 2
            if cols * rows <= 12000:
                break
            step *= 2
        x0 = math.floor(rect.left() / step) * step
        y0 = math.floor(rect.top() / step) * step
        painter.setPen(Qt.PenStyle.NoPen)
        y = y0
        iy = 0
        while y <= rect.bottom():
            x = x0
            ix = 0
            while x <= rect.right():
                major = (ix % 4 == 0) and (iy % 4 == 0)
                painter.setBrush(QColor(BG_GRID_DOT_MAJOR if major else BG_GRID_DOT))
                r = 1.4 if major else 1.0
                painter.drawEllipse(QPointF(x, y), r, r)
                x += step
                ix += 1
            y += step
            iy += 1

    # ---------- 力导向布局 ----------
    def _force_layout(self, items, edges, k=None, iterations=None):
        iterations = iterations or self._layout_iterations
        W, H = 520, 420
        random.seed(SEED)
        pos = {}
        for it in items:
            # 初始位置：按 arc 均匀铺开
            i = len(pos)
            ang = (2 * math.pi * (i + 0.5)) / max(1, len(items))
            pos[it] = QPointF(W / 2 + math.cos(ang) * (W * 0.32),
                              H / 2 + math.sin(ang) * (H * 0.32))
        kk = k or math.sqrt((W * H) / max(1, len(items)))
        edge_pairs = list(edges)
        for _ in range(iterations):
            displacement = {it: QPointF(0, 0) for it in items}
            # 斥力
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    dx = pos[a].x() - pos[b].x()
                    dy = pos[a].y() - pos[b].y()
                    d2 = dx * dx + dy * dy + 1e-6
                    if d2 < 1e-4:
                        dx, dy = random.random() - .5, random.random() - .5
                        d2 = 1.0
                    force = kk * kk / d2
                    fx = (dx / math.sqrt(d2)) * force
                    fy = (dy / math.sqrt(d2)) * force
                    displacement[a] += QPointF(fx, fy)
                    displacement[b] -= QPointF(fx, fy)
            # 引力（弹簧）：目标距离随两端半径缩放
            for a, b in edge_pairs:
                dx = pos[a].x() - pos[b].x()
                dy = pos[a].y() - pos[b].y()
                dist = math.hypot(dx, dy) + 1e-6
                target = 3.0 * (a.r + b.r)
                force = (dist - target) * 0.05
                fx = (dx / dist) * force
                fy = (dy / dist) * force
                displacement[a] -= QPointF(fx, fy)
                displacement[b] += QPointF(fx, fy)
            # 应用 + 边界约束（按各自半径，防大节点贴边裁切）
            for it in items:
                d = displacement[it]
                p = pos[it] + d
                margin = it.r + 14
                p.setX(max(margin, min(W - margin, p.x())))
                p.setY(max(margin, min(H - margin, p.y())))
                pos[it] = p
            # 简单冷却
            kk *= (iterations) / (iterations + 1)
        for it in items:
            it.setPos(pos[it])

    # ---------- 动画 ----------
    def _tick_dash(self):
        self._dash = (self._dash + ANIM_DASH_STEP) % ANIM_DASH_PERIOD
        for e in self._edges:
            if e.semantic:
                e.set_dash_offset(self._dash)

    def set_animation_enabled(self, on: bool):
        self._anim_on = bool(on)
        self._update_anim_timer()

    def _update_anim_timer(self):
        sem = sum(1 for e in self._edges if e.semantic)
        if self._anim_on and sem:
            self._anim_timer.setInterval(
                ANIM_INTERVAL if sem <= ANIM_MAX_EDGES else ANIM_INTERVAL * 2)
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        else:
            self._anim_timer.stop()

    # ---------- 交互：平移 ----------
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            if not self._panning:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if not self._panning:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        # 焦点切走时复位空格态，避免 keyRelease 收不到导致平移卡住
        self._space_held = False
        if not self._panning:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        btn = event.button()
        it = None
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            it = self.itemAt(event.position().toPoint())
        # 抓取画布：中键 / 空格+左键 / 左键按住空白（含边上）→ 掌形平移；
        # 左键按在节点上 = 拖节点（不进入平移）
        grab = btn == Qt.MouseButton.MiddleButton or (
            btn == Qt.MouseButton.LeftButton and
            (self._space_held or not isinstance(it, _NodeItem)))
        if grab:
            if btn == Qt.MouseButton.LeftButton and not self._space_held:
                # 按在空白处：清除选中高亮
                self._scene.clearSelection()
                self._set_selected_node(None)
            self._panning = True
            self._pan_start = QPointF(event.position())
            self._press_pos = None
            self._press_item = None
            self._press_node_pos = None
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        self._press_pos = QPointF(event.position())
        self._press_item = it if isinstance(it, _NodeItem) else None
        self._press_node_pos = None
        if btn == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            if self._press_item is not None:
                self._press_node_pos = QPointF(self._press_item.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
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
        if self._panning and event.button() in (Qt.MouseButton.MiddleButton,
                                                Qt.MouseButton.LeftButton):
            self._panning = False
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return

        press_pos, press_item = self._press_pos, self._press_item
        press_node_pos = self._press_node_pos
        super().mouseReleaseEvent(event)
        self._press_pos = None
        self._press_item = None
        self._press_node_pos = None

        if event.button() != Qt.MouseButton.LeftButton or press_pos is None:
            return
        if press_item is None:
            return
        # 拖动判定：鼠标位移超阈值，或节点被移动过 → 不算点击
        delta = QPointF(event.position()) - press_pos
        if math.hypot(delta.x(), delta.y()) >= CLICK_THRESHOLD:
            return
        if press_node_pos is not None and press_item.pos() != press_node_pos:
            return
        it = self.itemAt(event.position().toPoint())
        if not isinstance(it, _NodeItem) or it is not press_item:
            return
        # 单击 = 仅选中高亮邻居；打开笔记走双击（on_open），防误触跳走
        self._set_selected_node(it)

    # ---------- 交互：缩放 ----------
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP
        self._scale_at(factor, QPointF(event.position()))

    def _scale_at(self, factor: float, view_pos: QPointF) -> bool:
        """以视口坐标 view_pos 为锚缩放，夹紧 [MIN_ZOOM, MAX_ZOOM]。"""
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
        dx = after.x() - view_pos.x()
        dy = after.y() - view_pos.y()
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(int(h.value() + dx))
        v.setValue(int(v.value() + dy))
        return True

    def zoom_in(self):
        c = self.viewport().rect().center()
        self._scale_at(ZOOM_STEP, QPointF(c))

    def zoom_out(self):
        c = self.viewport().rect().center()
        self._scale_at(1.0 / ZOOM_STEP, QPointF(c))

    def reset_view(self):
        if not self._scene.sceneRect().isEmpty():
            self.fitInView(self._scene.sceneRect(),
                           Qt.AspectRatioMode.KeepAspectRatio)
        self._first_load = False
        self._pending_fit = False
