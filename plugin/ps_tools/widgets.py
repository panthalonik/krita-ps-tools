# -*- coding: utf-8 -*-
"""Small PS-like widgets for the Properties panel."""
import math

from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QIcon, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import (QColorDialog, QDoubleSpinBox, QHBoxLayout, QLabel,
                             QPushButton, QSizePolicy, QSlider, QSpinBox,
                             QVBoxLayout, QWidget)


# ------------------------------------------------------------------- icons
def adjustment_icon(size, color, dpr=1.0):
    """PS adjustment icon: circle, left half filled."""
    px = int(round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(dpr, dpr)
    s = float(size)
    r = QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64)
    p.setPen(QPen(QColor(color), max(1.0, s * 0.07)))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(r)
    path = QPainterPath()
    path.moveTo(r.center())
    path.arcTo(r, 45, 180)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def make_icon(fn, size, color):
    icon = QIcon()
    for dpr in (1.0, 1.25, 1.5, 2.0):
        icon.addPixmap(fn(size, color, dpr))
    return icon


def clip_icon(size, color, dpr=1.0):
    px = int(round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(dpr, dpr)
    s = float(size)
    pen = QPen(QColor(color), max(1.0, s * 0.08), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(QPointF(s * 0.3, s * 0.15), QPointF(s * 0.3, s * 0.55))
    p.drawLine(QPointF(s * 0.3, s * 0.55), QPointF(s * 0.6, s * 0.55))
    p.drawLine(QPointF(s * 0.6, s * 0.55), QPointF(s * 0.5, s * 0.45))
    p.drawLine(QPointF(s * 0.6, s * 0.55), QPointF(s * 0.5, s * 0.65))
    p.drawRect(QRectF(s * 0.2, s * 0.7, s * 0.6, s * 0.18))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


# --------------------------------------------------------------- slider row
class SliderRow(QWidget):
    """Label (+ optional end labels) / slider / spin box, like PS panels."""
    valueChanged = pyqtSignal(float)

    def __init__(self, label, vmin, vmax, value=0, decimals=0, left=None, right=None,
                 gradient=None, parent=None):
        super(SliderRow, self).__init__(parent)
        self.scale = 10 ** decimals
        self._block = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(1)
        top = QHBoxLayout()
        if left is not None:
            top.addWidget(QLabel(left))
            top.addStretch(1)
            if label:
                lab = QLabel(label)
                top.addWidget(lab)
                top.addStretch(1)
            top.addWidget(QLabel(right or ""))
        else:
            top.addWidget(QLabel(label))
            top.addStretch(1)
        if decimals:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(decimals)
            self.spin.setSingleStep(1.0 / self.scale)
        else:
            self.spin = QSpinBox()
        if decimals:
            self.spin.setRange(vmin, vmax)
        else:
            self.spin.setRange(int(vmin), int(vmax))
        self.spin.setFixedWidth(70)
        self.spin.setKeyboardTracking(False)
        top.addWidget(self.spin)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(round(vmin * self.scale)), int(round(vmax * self.scale)))
        if gradient:
            stops = ", ".join("stop:%s %s" % (i / float(len(gradient) - 1), c)
                              for i, c in enumerate(gradient))
            self.slider.setStyleSheet(
                "QSlider::groove:horizontal { height: 6px; border-radius: 3px;"
                " background: qlineargradient(x1:0, y1:0, x2:1, y2:0, %s); }"
                "QSlider::handle:horizontal { width: 10px; margin: -5px 0;"
                " border-radius: 5px; background: palette(button);"
                " border: 1px solid palette(mid); }" % stops)
        lay.addWidget(self.slider)
        self.setValue(value)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)

    def value(self):
        return self.spin.value()

    def _sv(self, v):
        if isinstance(self.spin, QSpinBox):
            self.spin.setValue(int(round(v)))
        else:
            self.spin.setValue(float(v))

    def setValue(self, v):
        self._block = True
        self._sv(v)
        self.slider.setValue(int(round(v * self.scale)))
        self._block = False

    def setRange(self, vmin, vmax):
        self._block = True
        if isinstance(self.spin, QSpinBox):
            self.spin.setRange(int(vmin), int(vmax))
        else:
            self.spin.setRange(vmin, vmax)
        self.slider.setRange(int(round(vmin * self.scale)), int(round(vmax * self.scale)))
        self._block = False

    def _from_slider(self, iv):
        if self._block:
            return
        self._block = True
        self._sv(iv / float(self.scale))
        self._block = False
        self.valueChanged.emit(self.spin.value())

    def _from_spin(self, v):
        if self._block:
            return
        self._block = True
        self.slider.setValue(int(round(v * self.scale)))
        self._block = False
        self.valueChanged.emit(v)


class ColorButton(QPushButton):
    colorChanged = pyqtSignal(str)

    def __init__(self, color="#808080", parent=None):
        super(ColorButton, self).__init__(parent)
        self.setMinimumHeight(26)
        self._color = color
        self.clicked.connect(self._pick)
        self._update()

    def color(self):
        return self._color

    def setColor(self, c):
        self._color = QColor(c).name()
        self._update()

    def _update(self):
        self.setStyleSheet("QPushButton { background: %s; border: 1px solid palette(mid);"
                           " border-radius: 3px; }" % self._color)

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, u"Pick color")
        if c.isValid():
            self.setColor(c.name())
            self.colorChanged.emit(self._color)


# ---------------------------------------------------------------- histogram
def _paint_histogram(p, rect, counts, color):
    if not counts:
        return
    m = max(counts[1:255] or [1]) or 1
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    path = QPainterPath()
    path.moveTo(rect.left(), rect.bottom())
    for i, c in enumerate(counts):
        x = rect.left() + rect.width() * i / 255.0
        y = rect.bottom() - rect.height() * min(1.0, c / float(m))
        path.lineTo(x, y)
    path.lineTo(rect.right(), rect.bottom())
    path.closeSubpath()
    p.drawPath(path)


class HistogramView(QWidget):
    def __init__(self, parent=None):
        super(HistogramView, self).__init__(parent)
        self.counts = None
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def setCounts(self, c):
        self.counts = c
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillRect(r, self.palette().base())
        _paint_histogram(p, r, self.counts, self.palette().text().color())
        p.setPen(QPen(self.palette().mid().color()))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r)
        p.end()


# ------------------------------------------------------------ handle bars
class HandleBar(QWidget):
    """Gradient bar with N triangle handles (values 0..255)."""
    handleMoved = pyqtSignal(int, float)
    M = 7

    def __init__(self, colors, parent=None):
        super(HandleBar, self).__init__(parent)
        self.colors = [QColor(c) for c in colors]
        self.values = [0.0] * len(colors)
        self._drag = None
        self.setMinimumHeight(24)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def setValues(self, vals):
        self.values = [float(v) for v in vals]
        self.update()

    def _span(self):
        return max(1.0, self.width() - 2 * self.M - 1)

    def _x(self, v):
        return self.M + v / 255.0 * self._span()

    def _v(self, x):
        return min(255.0, max(0.0, (x - self.M) / self._span() * 255.0))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bar = QRectF(self.M, 1, self._span(), 9)
        g = QLinearGradient(bar.topLeft(), bar.topRight())
        g.setColorAt(0, QColor(0, 0, 0))
        g.setColorAt(1, QColor(255, 255, 255))
        p.fillRect(bar, g)
        y = bar.bottom() + 2
        for v, c in zip(self.values, self.colors):
            x = self._x(v)
            p.setPen(QPen(QColor(120, 120, 120), 1))
            p.setBrush(c)
            p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x + 5.5, y + 9),
                                     QPointF(x - 5.5, y + 9)]))
        p.end()

    def mousePressEvent(self, e):
        x = e.pos().x()
        d = [abs(self._x(v) - x) for v in self.values]
        i = d.index(min(d))
        # prefer the handle on the side we clicked when they overlap
        same = [j for j, dj in enumerate(d) if abs(dj - d[i]) < 0.5]
        if len(same) > 1:
            i = same[0] if x < self._x(self.values[i]) else same[-1]
        self._drag = i

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.handleMoved.emit(self._drag, self._v(e.pos().x()))

    def mouseReleaseEvent(self, _e):
        self._drag = None


# --------------------------------------------------------------- curves
def spline_eval(points, xs):
    """Natural cubic spline through points (like Krita's curves)."""
    pts = sorted(points)
    n = len(pts)
    if n == 2:
        (x0, y0), (x1, y1) = pts
        return [y0 + (y1 - y0) * (x - x0) / max(1e-9, x1 - x0) for x in xs]
    x = [p[0] for p in pts]
    y = [p[1] for p in pts]
    h = [max(1e-9, x[i + 1] - x[i]) for i in range(n - 1)]
    alpha = [0.0] * n
    for i in range(1, n - 1):
        alpha[i] = 3 / h[i] * (y[i + 1] - y[i]) - 3 / h[i - 1] * (y[i] - y[i - 1])
    l, mu, z = [1.0] * n, [0.0] * n, [0.0] * n
    for i in range(1, n - 1):
        l[i] = 2 * (x[i + 1] - x[i - 1]) - h[i - 1] * mu[i - 1]
        mu[i] = h[i] / l[i]
        z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / l[i]
    c, b, d = [0.0] * n, [0.0] * n, [0.0] * n
    for j in range(n - 2, -1, -1):
        c[j] = z[j] - mu[j] * c[j + 1]
        b[j] = (y[j + 1] - y[j]) / h[j] - h[j] * (c[j + 1] + 2 * c[j]) / 3
        d[j] = (c[j + 1] - c[j]) / (3 * h[j])
    out = []
    for xv in xs:
        if xv <= x[0]:
            out.append(y[0])
            continue
        if xv >= x[-1]:
            out.append(y[-1])
            continue
        j = 0
        while j < n - 2 and xv > x[j + 1]:
            j += 1
        dx = xv - x[j]
        out.append(y[j] + b[j] * dx + c[j] * dx * dx + d[j] * dx * dx * dx)
    return [min(1.0, max(0.0, v)) for v in out]


class CurveView(QWidget):
    changed = pyqtSignal()
    selectionChanged = pyqtSignal()
    R = 5

    def __init__(self, parent=None):
        super(CurveView, self).__init__(parent)
        self.points = [[0.0, 0.0], [1.0, 1.0]]
        self.color = QColor(255, 255, 255)
        self.counts = None
        self.sel = None
        self._drag = False
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.ClickFocus)

    def heightForWidth(self, w):
        return w

    def setPoints(self, pts):
        self.points = sorted([list(p) for p in pts])
        self.sel = None
        self.update()

    def _area(self):
        s = min(self.width(), self.height()) - 2 * self.R
        return QRectF(self.R, self.R, s, s)

    def _to_px(self, pt):
        a = self._area()
        return QPointF(a.left() + pt[0] * a.width(), a.bottom() - pt[1] * a.height())

    def _from_px(self, pos):
        a = self._area()
        return [min(1.0, max(0.0, (pos.x() - a.left()) / a.width())),
                min(1.0, max(0.0, (a.bottom() - pos.y()) / a.height()))]

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        a = self._area()
        p.fillRect(a, self.palette().base())
        hc = QColor(self.palette().text().color())
        hc.setAlpha(60)
        _paint_histogram(p, a, self.counts, hc)
        grid = QColor(self.palette().mid().color())
        p.setPen(QPen(grid, 1))
        for i in range(1, 4):
            x = a.left() + a.width() * i / 4.0
            y = a.top() + a.height() * i / 4.0
            p.drawLine(QPointF(x, a.top()), QPointF(x, a.bottom()))
            p.drawLine(QPointF(a.left(), y), QPointF(a.right(), y))
        p.drawLine(a.bottomLeft(), a.topRight())
        p.drawRect(a)
        xs = [i / 128.0 for i in range(129)]
        ys = spline_eval(self.points, xs)
        path = QPainterPath()
        for i, (x, y) in enumerate(zip(xs, ys)):
            q = self._to_px([x, y])
            if i == 0:
                path.moveTo(q)
            else:
                path.lineTo(q)
        p.setPen(QPen(self.color, 1.6))
        p.drawPath(path)
        for i, pt in enumerate(self.points):
            q = self._to_px(pt)
            p.setPen(QPen(self.color, 1.2))
            p.setBrush(self.color if i == self.sel else self.palette().base())
            p.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))
        p.end()

    def _hit(self, pos):
        for i, pt in enumerate(self.points):
            q = self._to_px(pt)
            if abs(q.x() - pos.x()) <= 6 and abs(q.y() - pos.y()) <= 6:
                return i
        return None

    def mousePressEvent(self, e):
        i = self._hit(e.pos())
        if i is None:
            v = self._from_px(e.pos())
            if 0.0 < v[0] < 1.0:
                # add a point on the curve at this x (like PS)
                v[1] = spline_eval(self.points, [v[0]])[0]
                self.points.append(v)
                self.points.sort()
                i = self.points.index(v)
                self.changed.emit()
        self.sel = i
        self._drag = i is not None
        self.selectionChanged.emit()
        self.update()

    def mouseMoveEvent(self, e):
        if not self._drag or self.sel is None:
            return
        i = self.sel
        a = self._area()
        inner = 0 < i < len(self.points) - 1
        if inner and (e.pos().y() < a.top() - 25 or e.pos().y() > a.bottom() + 25
                      or e.pos().x() < a.left() - 25 or e.pos().x() > a.right() + 25):
            del self.points[i]           # dragged out -> remove (like PS)
            self.sel = None
            self._drag = False
            self.selectionChanged.emit()
            self.changed.emit()
            self.update()
            return
        v = self._from_px(e.pos())
        if i == 0:
            v[0] = min(v[0], self.points[1][0] - 0.01)
        elif i == len(self.points) - 1:
            v[0] = max(v[0], self.points[-2][0] + 0.01)
        else:
            v[0] = min(max(v[0], self.points[i - 1][0] + 0.01), self.points[i + 1][0] - 0.01)
        self.points[i] = v
        self.changed.emit()
        self.selectionChanged.emit()
        self.update()

    def mouseReleaseEvent(self, _e):
        self._drag = False

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.sel is not None \
                and 0 < self.sel < len(self.points) - 1:
            del self.points[self.sel]
            self.sel = None
            self.changed.emit()
            self.selectionChanged.emit()
            self.update()
        else:
            super(CurveView, self).keyPressEvent(e)
