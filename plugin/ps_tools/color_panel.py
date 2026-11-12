# -*- coding: utf-8 -*-
"""Photoshop's Color panel and Color Picker.

The "Color (PS)" docker: the foreground / background squares on the left
(swap and default colours under them), a saturation / brightness field and
a hue strip - like Photoshop's Color panel in its default (HSB) view.
Clicking the active square opens the Photoshop Color Picker: the big field
with a strip that follows the selected radio button (H, S, B, R, G, B, L,
a, b), "new" / "current", the H S B / R G B / L a b / C M Y K / # fields,
Only Web Colors and Add to Swatches (the swatches under the panel).
Both work with Krita's foreground and background colours."""
import json

from krita import DockWidget, Krita, ManagedColor
from PyQt5.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPolygonF)
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QRadioButton, QSizePolicy, QSpinBox,
                             QVBoxLayout, QWidget)

DOCKER_ID = "ps_tools_color"
SETTINGS = ("ps_tools", "psSwatches")


# ------------------------------------------------------------------ colour math
def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def hsv_to_rgb(h, s, v):
    """h 0..360, s / v 0..1 -> r, g, b 0..1"""
    h = (h % 360.0) / 60.0
    i = int(h) % 6
    f = h - int(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]


def rgb_to_hsv(r, g, b, keep_h=0.0, keep_s=0.0):
    """-> h 0..360, s, v 0..1. A grey keeps the given hue (and black the
    saturation), like Photoshop."""
    mx, mn = max(r, g, b), min(r, g, b)
    v = mx
    d = mx - mn
    s = d / mx if mx > 0 else keep_s
    if d <= 1e-9:
        return keep_h, (s if mx > 0 else keep_s), v
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60.0, s, v


def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _enc(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


_WHITE = (0.95047, 1.0, 1.08883)


def rgb_to_lab(r, g, b):
    r, g, b = _lin(r), _lin(g), _lin(b)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / _WHITE[0]
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / _WHITE[1]
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / _WHITE[2]
    f = lambda t: t ** (1 / 3.0) if t > 216 / 24389.0 else (24389 / 27.0 * t + 16) / 116.0
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def lab_to_rgb(L, a, b):
    fy = (L + 16) / 116.0
    fx, fz = fy + a / 500.0, fy - b / 200.0
    inv = lambda t: t ** 3 if t ** 3 > 216 / 24389.0 else (116 * t - 16) / (24389 / 27.0)
    x, y, z = inv(fx) * _WHITE[0], inv(fy) * _WHITE[1], inv(fz) * _WHITE[2]
    r = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    g = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    bl = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z
    return clamp(_enc(max(0, r))), clamp(_enc(max(0, g))), clamp(_enc(max(0, bl)))


def rgb_to_cmyk(r, g, b):
    k = 1 - max(r, g, b)
    if k >= 1:
        return 0.0, 0.0, 0.0, 1.0
    return (1 - r - k) / (1 - k), (1 - g - k) / (1 - k), (1 - b - k) / (1 - k), k


def cmyk_to_rgb(c, m, y, k):
    return (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)


def web_safe(v):
    return round(v * 5) / 5.0


class Color(object):
    """A colour kept as HSB (so hue survives greys, like in Photoshop)."""

    def __init__(self, h=0.0, s=0.0, v=0.0):
        self.h, self.s, self.v = h, s, v

    @staticmethod
    def from_rgb(r, g, b, like=None):
        h, s, v = rgb_to_hsv(r, g, b, like.h if like else 0.0, like.s if like else 0.0)
        return Color(h, s, v)

    @staticmethod
    def from_qcolor(c, like=None):
        return Color.from_rgb(c.redF(), c.greenF(), c.blueF(), like)

    def rgb(self):
        return hsv_to_rgb(self.h, self.s, self.v)

    def qcolor(self):
        r, g, b = self.rgb()
        return QColor.fromRgbF(clamp(r), clamp(g), clamp(b))

    def rgb255(self):
        return tuple(int(round(clamp(x) * 255)) for x in self.rgb())

    def hex(self):
        return "%02x%02x%02x" % self.rgb255()

    def copy(self):
        return Color(self.h, self.s, self.v)

    def same(self, other):
        return other is not None and self.rgb255() == other.rgb255()


# --------------------------------------------------------- Krita fg / bg colours
def _view():
    try:
        w = Krita.instance().activeWindow()
        return w.activeView() if w is not None else None
    except Exception:
        return None


def get_color(background=False):
    v = _view()
    if v is None or v.canvas() is None:
        return None
    try:
        mc = v.backgroundColor() if background else v.foregroundColor()
        return mc.colorForCanvas(v.canvas())
    except Exception:
        return None


def set_color(qcolor, background=False):
    v = _view()
    if v is None or v.canvas() is None:
        return False
    try:
        mc = ManagedColor.fromQColor(qcolor, v.canvas())
        if background:
            v.setBackGroundColor(mc)
        else:
            v.setForeGroundColor(mc)
        return True
    except Exception:
        return False


# ================================================================ widgets
def _triangle(p, x, y, right, size=6):
    s = size
    pts = [QPointF(x, y), QPointF(x - s if right else x + s, y - s), QPointF(x - s if right else x + s, y + s)]
    p.drawPolygon(QPolygonF(pts))


class Field(QWidget):
    """The big square. mode: which component the strip controls; the field
    shows the other two (like Photoshop's Color Picker)."""
    picked = pyqtSignal(float, float)          # x, y 0..1 (y: 1 = top)

    def __init__(self, parent=None, size=256):
        super(Field, self).__init__(parent)
        self.mode = "h"
        self.color = Color(0, 0, 0)
        self.setMinimumSize(120, 120)
        if size:
            self.setFixedSize(size, size)
        self._cache_key = None
        self._img = None

    def set_state(self, mode, color):
        self.mode, self.color = mode, color.copy()
        self.update()

    def xy(self):
        """The marker position (0..1) for the colour."""
        c = self.color
        r, g, b = c.rgb()
        m = self.mode
        if m == "h":
            return c.s, c.v
        if m == "s":
            return c.h / 360.0, c.v
        if m == "v":
            return c.h / 360.0, c.s
        if m == "r":
            return b, g
        if m == "g":
            return b, r
        if m == "bl":
            return r, g
        L, A, B = rgb_to_lab(r, g, b)
        if m == "L":
            return (B + 128) / 255.0, (A + 128) / 255.0
        if m == "a":
            return (B + 128) / 255.0, L / 100.0
        return (A + 128) / 255.0, L / 100.0

    def _lab_image(self):
        key = (self.mode, round(self._lab_fixed(), 1))
        if key == self._cache_key and self._img is not None:
            return self._img
        n = 64
        img = QImage(n, n, QImage.Format_RGB32)
        fixed = self._lab_fixed()
        for j in range(n):
            yv = 1 - j / float(n - 1)
            for i in range(n):
                xv = i / float(n - 1)
                if self.mode == "L":
                    rgb = lab_to_rgb(fixed, yv * 255 - 128, xv * 255 - 128)
                elif self.mode == "a":
                    rgb = lab_to_rgb(yv * 100, fixed, xv * 255 - 128)
                else:
                    rgb = lab_to_rgb(yv * 100, xv * 255 - 128, fixed)
                img.setPixel(i, j, QColor.fromRgbF(*rgb).rgb())
        self._cache_key, self._img = key, img
        return img

    def _lab_fixed(self):
        L, A, B = rgb_to_lab(*self.color.rgb())
        return {"L": L, "a": A, "b": B}.get(self.mode, 0)

    def paintEvent(self, _e):
        p = QPainter(self)
        rc = QRectF(0, 0, self.width(), self.height())
        c, m = self.color, self.mode
        if m == "h":
            p.fillRect(rc, QColor.fromHsvF((c.h % 360) / 360.0, 1, 1))
            g = QLinearGradient(rc.topLeft(), rc.topRight())
            g.setColorAt(0, QColor(255, 255, 255, 255))
            g.setColorAt(1, QColor(255, 255, 255, 0))
            p.fillRect(rc, g)
            g = QLinearGradient(rc.topLeft(), rc.bottomLeft())
            g.setColorAt(0, QColor(0, 0, 0, 0))
            g.setColorAt(1, QColor(0, 0, 0, 255))
            p.fillRect(rc, g)
        elif m in ("s", "v"):
            g = QLinearGradient(rc.topLeft(), rc.topRight())
            for i in range(7):
                g.setColorAt(i / 6.0, QColor.fromHsvF((i / 6.0) % 1.0, 1, 1))
            p.fillRect(rc, g)
            if m == "s":
                p.fillRect(rc, QColor(255, 255, 255, int(round((1 - c.s) * 255))))
                g = QLinearGradient(rc.topLeft(), rc.bottomLeft())
                g.setColorAt(0, QColor(0, 0, 0, 0))
                g.setColorAt(1, QColor(0, 0, 0, 255))
                p.fillRect(rc, g)
            else:
                g = QLinearGradient(rc.topLeft(), rc.bottomLeft())
                g.setColorAt(0, QColor(255, 255, 255, 0))
                g.setColorAt(1, QColor(255, 255, 255, 255))
                p.fillRect(rc, g)
                p.fillRect(rc, QColor(0, 0, 0, int(round((1 - c.v) * 255))))
        elif m in ("r", "g", "bl"):
            r, gg, b = c.rgb()
            fixed = {"r": QColor.fromRgbF(clamp(r), 0, 0), "g": QColor.fromRgbF(0, clamp(gg), 0),
                     "bl": QColor.fromRgbF(0, 0, clamp(b))}[m]
            xcol = {"r": QColor(0, 0, 255), "g": QColor(0, 0, 255), "bl": QColor(255, 0, 0)}[m]
            ycol = {"r": QColor(0, 255, 0), "g": QColor(255, 0, 0), "bl": QColor(0, 255, 0)}[m]
            p.fillRect(rc, QColor(0, 0, 0))
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            g = QLinearGradient(rc.topLeft(), rc.topRight())
            g.setColorAt(0, QColor(0, 0, 0))
            g.setColorAt(1, xcol)
            p.fillRect(rc, g)
            g = QLinearGradient(rc.bottomLeft(), rc.topLeft())
            g.setColorAt(0, QColor(0, 0, 0))
            g.setColorAt(1, ycol)
            p.fillRect(rc, g)
            p.fillRect(rc, fixed)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        else:
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(rc, self._lab_image())
        x, y = self.xy()
        pt = QPointF(clamp(x) * (self.width() - 1), (1 - clamp(y)) * (self.height() - 1))
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)
        lum = 0.3 * c.rgb()[0] + 0.59 * c.rgb()[1] + 0.11 * c.rgb()[2]
        p.setPen(QPen(QColor(255, 255, 255) if lum < 0.6 else QColor(0, 0, 0), 1.2))
        p.drawEllipse(pt, 5, 5)

    def _pick(self, pos):
        x = clamp(pos.x() / float(max(1, self.width() - 1)))
        y = clamp(1 - pos.y() / float(max(1, self.height() - 1)))
        self.picked.emit(x, y)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pick(e.pos())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._pick(e.pos())


class Strip(QWidget):
    """The vertical strip of the component chosen by the radio button."""
    picked = pyqtSignal(float)                 # 0..1, 1 = top

    def __init__(self, parent=None, height=256, width=20, arrows=True):
        super(Strip, self).__init__(parent)
        self.mode = "h"
        self.color = Color()
        self.arrows = arrows
        self.M = 7 if arrows else 0
        self.setFixedWidth(width + 2 * self.M)
        if height:
            self.setFixedHeight(height)
        self.setMinimumHeight(60)

    def set_state(self, mode, color):
        self.mode, self.color = mode, color.copy()
        self.update()

    def value(self):
        c, m = self.color, self.mode
        r, g, b = c.rgb()
        if m == "h":
            return c.h / 360.0
        if m == "s":
            return c.s
        if m == "v":
            return c.v
        if m == "r":
            return r
        if m == "g":
            return g
        if m == "bl":
            return b
        L, A, B = rgb_to_lab(r, g, b)
        return {"L": L / 100.0, "a": (A + 128) / 255.0, "b": (B + 128) / 255.0}[m]

    def _color_at(self, t):
        c, m = self.color, self.mode
        r, g, b = c.rgb()
        if m == "h":
            return QColor.fromHsvF(t % 1.0 if t < 1 else 0.0, 1, 1)
        if m == "s":
            return QColor.fromRgbF(*[clamp(x) for x in hsv_to_rgb(c.h, t, c.v)])
        if m == "v":
            return QColor.fromRgbF(*[clamp(x) for x in hsv_to_rgb(c.h, c.s, t)])
        if m == "r":
            return QColor.fromRgbF(t, clamp(g), clamp(b))
        if m == "g":
            return QColor.fromRgbF(clamp(r), t, clamp(b))
        if m == "bl":
            return QColor.fromRgbF(clamp(r), clamp(g), t)
        L, A, B = rgb_to_lab(r, g, b)
        if m == "L":
            return QColor.fromRgbF(*lab_to_rgb(t * 100, A, B))
        if m == "a":
            return QColor.fromRgbF(*lab_to_rgb(L, t * 255 - 128, B))
        return QColor.fromRgbF(*lab_to_rgb(L, A, t * 255 - 128))

    def paintEvent(self, _e):
        p = QPainter(self)
        rc = QRectF(self.M, 0, self.width() - 2 * self.M, self.height())
        g = QLinearGradient(rc.bottomLeft(), rc.topLeft())
        steps = 12 if self.mode in ("h", "L", "a", "b") else 2
        for i in range(steps + 1):
            t = i / float(steps)
            g.setColorAt(t, self._color_at(t))
        p.fillRect(rc, g)
        y = (1 - clamp(self.value())) * (self.height() - 1)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self.arrows:
            p.setPen(QPen(QColor(40, 40, 40), 1))
            p.setBrush(QColor(235, 235, 235))
            _triangle(p, self.M, y, True)
            _triangle(p, self.width() - self.M, y, False)
        else:
            p.setPen(QPen(QColor(0, 0, 0), 1))
            p.setBrush(QColor(255, 255, 255))
            p.drawRect(QRectF(rc.left() - 1, y - 2, rc.width() + 2, 4))

    def _pick(self, pos):
        self.picked.emit(clamp(1 - pos.y() / float(max(1, self.height() - 1))))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._pick(e.pos())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._pick(e.pos())


def apply_pick(mode, color, x=None, y=None, z=None):
    """The colour after a click in the field (x, y) or the strip (z)."""
    c = color.copy()
    r, g, b = c.rgb()
    if mode in ("h", "s", "v"):
        if z is not None:
            if mode == "h":
                c.h = 0.0 if z >= 1 else z * 360.0
            elif mode == "s":
                c.s = z
            else:
                c.v = z
        if x is not None:
            if mode == "h":
                c.s, c.v = x, y
            elif mode == "s":
                c.h, c.v = min(359.99, x * 360.0), y
            else:
                c.h, c.s = min(359.99, x * 360.0), y
        return c
    if mode in ("r", "g", "bl"):
        if z is not None:
            if mode == "r":
                r = z
            elif mode == "g":
                g = z
            else:
                b = z
        if x is not None:
            if mode == "r":
                b, g = x, y
            elif mode == "g":
                b, r = x, y
            else:
                r, g = x, y
        return Color.from_rgb(r, g, b, c)
    L, A, B = rgb_to_lab(r, g, b)
    if z is not None:
        if mode == "L":
            L = z * 100
        elif mode == "a":
            A = z * 255 - 128
        else:
            B = z * 255 - 128
    if x is not None:
        if mode == "L":
            B, A = x * 255 - 128, y * 255 - 128
        elif mode == "a":
            B, L = x * 255 - 128, y * 100
        else:
            A, L = x * 255 - 128, y * 100
    return Color.from_rgb(*lab_to_rgb(L, A, B), like=c)


# ============================================================ Color Picker
class ColorPickerDialog(QDialog):
    """Photoshop's Color Picker (Foreground / Background Color)."""

    def __init__(self, color, title=u"Color Picker (Foreground Color)", parent=None, on_swatch=None):
        super(ColorPickerDialog, self).__init__(parent)
        self.setWindowTitle(title)
        self.current = color.copy()
        self.color = color.copy()
        self.mode = "h"
        self.on_swatch = on_swatch
        self._busy = False

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        row = QHBoxLayout()
        self.field = Field(self, 256)
        self.strip = Strip(self, 256, 20)
        row.addWidget(self.field)
        row.addWidget(self.strip)
        left.addLayout(row)
        self.web = QCheckBox(u"Only Web Colors")
        self.web.toggled.connect(lambda _on: self._set(self.color))
        left.addWidget(self.web)
        left.addStretch(1)
        root.addLayout(left)

        mid = QVBoxLayout()
        mid.addWidget(QLabel(u"new"), 0, Qt.AlignHCenter)
        self.new_swatch = QLabel()
        self.new_swatch.setFixedSize(64, 38)
        self.cur_swatch = QPushButton()
        self.cur_swatch.setFixedSize(64, 38)
        self.cur_swatch.setFlat(True)
        self.cur_swatch.setToolTip(u"Click: back to the current colour")
        self.cur_swatch.clicked.connect(lambda: self._set(self.current))
        mid.addWidget(self.new_swatch, 0, Qt.AlignHCenter)
        mid.addWidget(self.cur_swatch, 0, Qt.AlignHCenter)
        mid.addWidget(QLabel(u"current"), 0, Qt.AlignHCenter)
        mid.addSpacing(12)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        self.radios = QButtonGroup(self)
        self.spins = {}

        def add(row, col, key, label, lo, hi, unit, radio=True):
            if radio:
                rb = QRadioButton(label + ":")
                rb.setChecked(key == "h")
                self.radios.addButton(rb)
                rb.toggled.connect(lambda on, k=key: on and self._mode(k))
                grid.addWidget(rb, row, col)
            else:
                grid.addWidget(QLabel(label + ":"), row, col, Qt.AlignRight)
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setKeyboardTracking(False)
            sp.setButtonSymbols(QSpinBox.NoButtons)
            sp.setFixedWidth(46)
            sp.valueChanged.connect(lambda _v, k=key: self._typed(k))
            grid.addWidget(sp, row, col + 1)
            grid.addWidget(QLabel(unit), row, col + 2)
            self.spins[key] = sp

        add(0, 0, "h", u"H", 0, 360, u"°")
        add(1, 0, "s", u"S", 0, 100, u"%")
        add(2, 0, "v", u"B", 0, 100, u"%")
        add(4, 0, "r", u"R", 0, 255, u"")
        add(5, 0, "g", u"G", 0, 255, u"")
        add(6, 0, "bl", u"B", 0, 255, u"")
        add(0, 3, "L", u"L", 0, 100, u"")
        add(1, 3, "a", u"a", -128, 127, u"")
        add(2, 3, "b", u"b", -128, 127, u"")
        add(4, 3, "c", u"C", 0, 100, u"%", radio=False)
        add(5, 3, "m", u"M", 0, 100, u"%", radio=False)
        add(6, 3, "y", u"Y", 0, 100, u"%", radio=False)
        add(7, 3, "k", u"K", 0, 100, u"%", radio=False)
        grid.addWidget(QLabel(u"#"), 8, 0, Qt.AlignRight)
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setFixedWidth(80)
        self.hex.editingFinished.connect(self._hex)
        grid.addWidget(self.hex, 8, 1, 1, 3)
        mid.addLayout(grid)
        mid.addStretch(1)
        root.addLayout(mid)

        right = QVBoxLayout()
        ok = QPushButton(u"OK")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        cancel = QPushButton(u"Cancel")
        cancel.clicked.connect(self.reject)
        add_sw = QPushButton(u"Add to Swatches")
        add_sw.clicked.connect(self._add_swatch)
        add_sw.setEnabled(on_swatch is not None)
        for b in (ok, cancel, add_sw):
            b.setMinimumWidth(150)
            right.addWidget(b)
        right.addStretch(1)
        root.addLayout(right)

        self.field.picked.connect(lambda x, y: self._set(apply_pick(self.mode, self.color, x=x, y=y)))
        self.strip.picked.connect(lambda z: self._set(apply_pick(self.mode, self.color, z=z)))
        self._set(self.color)

    def _mode(self, key):
        self.mode = key
        self._refresh()

    def _set(self, color):
        if self.web.isChecked():
            r, g, b = (web_safe(x) for x in color.rgb())
            color = Color.from_rgb(r, g, b, color)
        self.color = color
        self._refresh()

    def _refresh(self):
        self._busy = True
        try:
            c = self.color
            r, g, b = c.rgb()
            L, A, B = rgb_to_lab(r, g, b)
            cc, mm, yy, kk = rgb_to_cmyk(r, g, b)
            vals = {"h": int(round(c.h)) % 360, "s": int(round(c.s * 100)), "v": int(round(c.v * 100)),
                    "r": int(round(r * 255)), "g": int(round(g * 255)), "bl": int(round(b * 255)),
                    "L": int(round(L)), "a": int(round(A)), "b": int(round(B)),
                    "c": int(round(cc * 100)), "m": int(round(mm * 100)), "y": int(round(yy * 100)),
                    "k": int(round(kk * 100))}
            for k, v in vals.items():
                self.spins[k].setValue(v)
            self.hex.setText(c.hex())
            self.field.set_state(self.mode, c)
            self.strip.set_state(self.mode, c)
            self.new_swatch.setStyleSheet("background: #%s; border: 1px solid #222;" % c.hex())
            self.cur_swatch.setStyleSheet("background: #%s; border: 1px solid #222;" % self.current.hex())
        finally:
            self._busy = False

    def _typed(self, key):
        if self._busy:
            return
        v = {k: sp.value() for k, sp in self.spins.items()}
        c = self.color
        if key in ("h", "s", "v"):
            c = Color(v["h"] % 360, v["s"] / 100.0, v["v"] / 100.0)
        elif key in ("r", "g", "bl"):
            c = Color.from_rgb(v["r"] / 255.0, v["g"] / 255.0, v["bl"] / 255.0, c)
        elif key in ("L", "a", "b"):
            c = Color.from_rgb(*lab_to_rgb(v["L"], v["a"], v["b"]), like=c)
        else:
            c = Color.from_rgb(*cmyk_to_rgb(v["c"] / 100.0, v["m"] / 100.0, v["y"] / 100.0,
                                            v["k"] / 100.0), like=c)
        self._set(c)

    def _hex(self):
        t = self.hex.text().strip().lstrip("#")
        if len(t) == 3:
            t = "".join(ch * 2 for ch in t)
        try:
            r, g, b = int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
        except (ValueError, IndexError):
            self._refresh()
            return
        self._set(Color.from_rgb(r / 255.0, g / 255.0, b / 255.0, self.color))

    def _add_swatch(self):
        if self.on_swatch:
            self.on_swatch(self.color.copy())


# ================================================================ docker
class Swatches(QWidget):
    """The swatches added with "Add to Swatches" (saved in Krita's settings).
    Click: foreground colour, Ctrl+click: background, Alt+click: remove."""
    chosen = pyqtSignal(object, bool)
    S = 16

    def __init__(self, parent=None):
        super(Swatches, self).__init__(parent)
        self.colors = self._load()
        self.setMinimumHeight(self.S + 4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _load(self):
        try:
            data = json.loads(Krita.instance().readSetting(SETTINGS[0], SETTINGS[1], "[]") or "[]")
            return [str(x) for x in data][:200]
        except Exception:
            return []

    def _save(self):
        try:
            Krita.instance().writeSetting(SETTINGS[0], SETTINGS[1], json.dumps(self.colors))
        except Exception:
            pass

    def add(self, color):
        h = color.hex()
        if h in self.colors:
            self.colors.remove(h)
        self.colors.insert(0, h)
        del self.colors[200:]
        self._save()
        self.updateGeometry()
        self.update()

    def _per_row(self):
        return max(1, (self.width() - 2) // (self.S + 2))

    def sizeHint(self):
        rows = max(1, (len(self.colors) + self._per_row() - 1) // self._per_row())
        return QSize(200, rows * (self.S + 2) + 2)

    def heightForWidth(self, w):
        per = max(1, (w - 2) // (self.S + 2))
        return max(1, (len(self.colors) + per - 1) // per) * (self.S + 2) + 2

    def hasHeightForWidth(self):
        return True

    def paintEvent(self, _e):
        p = QPainter(self)
        per = self._per_row()
        if not self.colors:
            p.setPen(self.palette().color(self.palette().Mid))
            p.drawText(self.rect(), Qt.AlignCenter, u"Swatches: Color Picker > Add to Swatches")
            return
        for i, h in enumerate(self.colors):
            x, y = 1 + (i % per) * (self.S + 2), 1 + (i // per) * (self.S + 2)
            p.fillRect(QRect(x, y, self.S, self.S), QColor("#" + h))
            p.setPen(QColor(30, 30, 30))
            p.drawRect(QRect(x, y, self.S - 1, self.S - 1))

    def mousePressEvent(self, e):
        per = self._per_row()
        i = (e.pos().y() - 1) // (self.S + 2) * per + (e.pos().x() - 1) // (self.S + 2)
        if 0 <= i < len(self.colors) and (e.pos().x() - 1) // (self.S + 2) < per:
            if e.modifiers() & Qt.AltModifier:
                del self.colors[i]
                self._save()
                self.update()
                return
            c = QColor("#" + self.colors[i])
            self.chosen.emit(Color.from_qcolor(c), bool(e.modifiers() & Qt.ControlModifier))


class FgBg(QWidget):
    """The foreground / background squares (and swap / default under them)."""
    clicked = pyqtSignal(bool)        # background?
    swap = pyqtSignal()
    defaults = pyqtSignal()

    def __init__(self, parent=None):
        super(FgBg, self).__init__(parent)
        self.fg, self.bg = QColor(0, 0, 0), QColor(255, 255, 255)
        self.active_bg = False
        self.setFixedSize(46, 58)
        self.setToolTip(u"Click the active square: Color Picker. Click the other one: edit it "
                        u"here. X under them: swap, the small squares: black / white.")

    def _rects(self):
        return QRect(1, 1, 26, 26), QRect(17, 17, 26, 26)

    def paintEvent(self, _e):
        p = QPainter(self)
        fg, bg = self._rects()
        for rc, col, active in ((bg, self.bg, self.active_bg), (fg, self.fg, not self.active_bg)):
            p.fillRect(rc, col)
            p.setPen(QPen(QColor(255, 255, 255) if active else QColor(20, 20, 20), 1))
            p.drawRect(rc.adjusted(0, 0, -1, -1))
            if active:
                p.setPen(QPen(QColor(20, 20, 20), 1))
                p.drawRect(rc.adjusted(1, 1, -2, -2))
        # default colours (small) and swap (arrow) under the squares
        p.fillRect(QRect(1, 45, 7, 7), QColor(0, 0, 0))
        p.fillRect(QRect(5, 49, 7, 7), QColor(255, 255, 255))
        p.setPen(QColor(20, 20, 20))
        p.drawRect(QRect(5, 49, 6, 6))
        p.setPen(QPen(self.palette().color(self.palette().WindowText), 1.2))
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.moveTo(30, 47)
        path.quadTo(42, 47, 42, 56)
        p.drawPath(path)
        p.drawLine(QPointF(30, 47), QPointF(33, 44))
        p.drawLine(QPointF(30, 47), QPointF(33, 50))

    def mousePressEvent(self, e):
        fg, bg = self._rects()
        pos = e.pos()
        if pos.y() >= 44:
            if pos.x() < 16:
                self.defaults.emit()
            else:
                self.swap.emit()
            return
        if fg.contains(pos):
            self.clicked.emit(False)
        elif bg.contains(pos):
            self.clicked.emit(True)


class ColorDocker(DockWidget):
    def __init__(self):
        super(ColorDocker, self).__init__()
        self.setWindowTitle(u"Color (PS)")
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.fgbg = FgBg()
        row.addWidget(self.fgbg, 0, Qt.AlignTop)
        self.field = Field(None, 0)
        self.field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        row.addWidget(self.field, 1)
        self.strip = Strip(None, 0, 14, arrows=False)
        self.strip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        row.addWidget(self.strip)
        v.addLayout(row, 1)
        self.swatches = Swatches()
        v.addWidget(self.swatches)
        self.setWidget(w)

        self.fg = Color(0, 0, 0)
        self.bg = Color(0, 0, 1)
        self.editing_bg = False
        self._last = (None, None)

        self.fgbg.clicked.connect(self._square)
        self.fgbg.swap.connect(self._swap)
        self.fgbg.defaults.connect(self._defaults)
        self.field.picked.connect(lambda x, y: self._pick(apply_pick("h", self._cur(), x=x, y=y)))
        self.strip.picked.connect(lambda z: self._pick(apply_pick("h", self._cur(), z=z)))
        self.swatches.chosen.connect(self._swatch)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._sync)
        self.timer.start()
        self._show()

    def canvasChanged(self, canvas):
        self._sync()

    def _cur(self):
        return self.bg if self.editing_bg else self.fg

    def _show(self):
        self.fgbg.fg, self.fgbg.bg = self.fg.qcolor(), self.bg.qcolor()
        self.fgbg.active_bg = self.editing_bg
        self.fgbg.update()
        self.field.set_state("h", self._cur())
        self.strip.set_state("h", self._cur())

    def _sync(self):
        """Krita's colours changed elsewhere (colour picker tool, other dockers)."""
        if not self.isVisible():
            return
        fg, bg = get_color(False), get_color(True)
        changed = False
        if fg is not None and fg.name() != self._last[0]:
            if not self.fg.same(Color.from_qcolor(fg)):
                self.fg = Color.from_qcolor(fg, self.fg)
            changed = True
        if bg is not None and bg.name() != self._last[1]:
            if not self.bg.same(Color.from_qcolor(bg)):
                self.bg = Color.from_qcolor(bg, self.bg)
            changed = True
        self._last = (fg.name() if fg is not None else None, bg.name() if bg is not None else None)
        if changed:
            self._show()

    def _push(self, background):
        c = self.bg if background else self.fg
        if set_color(c.qcolor(), background):
            q = get_color(background)
            if q is not None:
                self._last = (self._last[0], q.name()) if background else (q.name(), self._last[1])

    def _pick(self, color):
        if self.editing_bg:
            self.bg = color
        else:
            self.fg = color
        self._push(self.editing_bg)
        self._show()

    def _square(self, background):
        if background != self.editing_bg:
            self.editing_bg = background
            self._show()
            return
        title = u"Color Picker (Background Color)" if background else u"Color Picker (Foreground Color)"
        dlg = ColorPickerDialog(self._cur(), title, self, on_swatch=self.swatches.add)
        if dlg.exec_() == QDialog.Accepted:
            self._pick(dlg.color)

    def _swap(self):
        self.fg, self.bg = self.bg, self.fg
        self._push(False)
        self._push(True)
        self._show()

    def _defaults(self):
        self.fg, self.bg = Color(0, 0, 0), Color(0, 0, 1)
        self._push(False)
        self._push(True)
        self._show()

    def _swatch(self, color, background):
        if background:
            self.bg = color
        else:
            self.fg = color
        self._push(background)
        self._show()
