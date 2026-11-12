# -*- coding: utf-8 -*-
"""Camera Raw Filter (Krita PS): Photoshop's Filter > Camera Raw Filter...
with the Light, Color, Effects and Detail panels.

The pixels are computed by the C++ filter "pscameraraw"
(krita/plugins/filters/psfilters/ps_cameraraw.h); this module is its user
interface:
- Filter > Camera Raw Filter... (Shift+Ctrl+A) on a layer adds a filter mask
  "Camera Raw Filter" to it - like a Smart Filter, it can be opened and
  changed later (double-click it / the Properties panel);
- the adjustment layer menu has "Camera Raw..." (a filter layer, works on
  everything below);
- the Properties panel shows the same panels for a selected Camera Raw
  layer / mask.
The canvas is the preview: changes are shown live."""
import math

from krita import Krita, Selection
from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QConicalGradient, QFont, QIcon, QImage, QLinearGradient,
                         QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QRadialGradient)
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QFrame, QGridLayout,
                             QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                             QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from . import core

FILTER_ID = "pscameraraw"
MASK_NAME = u"Camera Raw Filter"

COLORS = ["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]
COLOR_LABELS = [u"Reds", u"Oranges", u"Yellows", u"Greens", u"Aquas", u"Blues", u"Purples",
                u"Magentas"]
COLOR_SWATCH = ["#e8322a", "#ef8a1f", "#f2d52a", "#3dbb3a", "#2fc8c8", "#2f5fe0", "#8a3fe0",
                "#e03fc0"]
ZONES = ["shadows", "midtones", "highlights", "global"]
ZONE_LABELS = [u"Shadows", u"Midtones", u"Highlights", u"Global"]
CURVES = ["curve_rgb", "curve_red", "curve_green", "curve_blue"]
IDENTITY = [[0.0, 0.0], [1.0, 1.0]]

# key -> default (everything the C++ filter reads, see ps::cr::settingNames)
DEFAULTS = {
    "exposure": 0.0, "contrast": 0, "highlights": 0, "shadows": 0, "whites": 0, "blacks": 0,
    "pc_highlights": 0, "pc_lights": 0, "pc_darks": 0, "pc_shadows": 0,
    "pc_split1": 25, "pc_split2": 50, "pc_split3": 75,
    "temperature": 0, "tint": 0, "vibrance": 0, "saturation": 0, "bw": 0,
    "cg_blending": 50, "cg_balance": 0,
    "texture": 0, "clarity": 0, "dehaze": 0,
    "vig_style": 0, "vig_amount": 0, "vig_midpoint": 50, "vig_roundness": 0, "vig_feather": 50,
    "vig_highlights": 0,
    "grain_amount": 0, "grain_size": 25, "grain_roughness": 50,
    "sharp_amount": 0, "sharp_radius": 1.0, "sharp_detail": 25, "sharp_masking": 0,
    "nr_lum": 0, "nr_lum_detail": 50, "nr_lum_contrast": 0,
    "nr_color": 0, "nr_color_detail": 50, "nr_color_smooth": 50,
}
for _c in COLORS:
    for _k in ("hue", "sat", "lum"):
        DEFAULTS["mix_%s_%s" % (_k, _c)] = 0
for _z in ZONES:
    DEFAULTS["cg_%s_hue" % _z] = 0
    DEFAULTS["cg_%s_sat" % _z] = 0
    DEFAULTS["cg_%s_lum" % _z] = 0
FLOAT_KEYS = ("exposure", "sharp_radius")


def defaults():
    p = dict(DEFAULTS)
    for c in CURVES:
        p[c] = [list(x) for x in IDENTITY]
    p["wb_mode"] = "as_shot"
    return p


def available():
    try:
        return FILTER_ID in Krita.instance().filters()
    except Exception:
        return False


# ------------------------------------------------------------- params <-> props
def _num(v):
    return ("%.4f" % v).rstrip("0").rstrip(".") if isinstance(v, float) and v != int(v) else str(int(round(v)))


def curve_text(points):
    pts = sorted([float(x), float(y)] for x, y in (points or IDENTITY))
    return ";".join("%s,%s" % (_num(x), _num(y)) for x, y in pts)


def parse_curve(text):
    out = []
    for item in str(text or "").split(";"):
        try:
            x, y = item.split(",")
            out.append([min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y)))])
        except ValueError:
            pass
    return out if len(out) >= 2 else [list(x) for x in IDENTITY]


def build_props(p):
    """Filter configuration properties for params (only what differs from
    the defaults is needed, but everything is written: a reset slider must
    overwrite an old value)."""
    props = {}
    for k, d in DEFAULTS.items():
        v = p.get(k, d)
        try:
            props[k] = float(v)
        except (TypeError, ValueError):
            props[k] = float(d)
    for c in CURVES:
        props[c] = curve_text(p.get(c) or IDENTITY)
    return props


def params_from_props(props):
    p = defaults()
    for k, d in DEFAULTS.items():
        if k in props:
            try:
                v = float(props[k])
                p[k] = v if k in FLOAT_KEYS else int(round(v))
            except (TypeError, ValueError):
                pass
    for c in CURVES:
        if props.get(c):
            p[c] = parse_curve(props[c])
    return p


def params_from_node(node):
    try:
        return params_from_props(node.filter().configuration().properties())
    except Exception:
        return defaults()


def is_camera_raw(node):
    try:
        return node is not None and node.type() in ("filterlayer", "filtermask") and \
            node.filter() is not None and node.filter().name() == FILTER_ID
    except Exception:
        return False


def apply_to_node(node, p):
    f = Krita.instance().filter(FILTER_ID)
    cfg = f.configuration()
    for k, v in build_props(p).items():
        cfg.setProperty(k, v)
    f.setConfiguration(cfg)
    node.setFilter(f)


# ------------------------------------------------------------- analysis
def _sample(doc, node, size=192):
    """(r, g, b) linear float lists of what the filter works on: the layer
    under a mask, or the image below a filter layer."""
    from PyQt5.QtGui import QImage
    img = None
    try:
        if node.type() == "filtermask":
            img = node.parentNode().thumbnail(size, size)
        else:
            with core.below_only(doc, node) as d:
                img = d.thumbnail(size, size)
    except Exception:
        img = None
    if img is None or img.isNull():
        return None
    img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    raw = core.qimage_bytes(img, w, h, 4)
    lut = [(i / 255.0) / 12.92 if i <= 10 else ((i / 255.0 + 0.055) / 1.055) ** 2.4 for i in range(256)]
    r, g, b = [], [], []
    for i in range(0, len(raw), 4):
        if raw[i + 3] < 128:
            continue
        b.append(lut[raw[i]])
        g.append(lut[raw[i + 1]])
        r.append(lut[raw[i + 2]])
    return (r, g, b) if r else None


def wb_from_color(r, g, b):
    """Temperature / tint (Camera Raw's -100..100) that make this linear
    colour neutral (the kernel: R*2^(0.6t), G*2^(-0.36ti), B*2^(-0.6t))."""
    r, g, b = max(r, 1e-4), max(g, 1e-4), max(b, 1e-4)
    t = math.log(b / r, 2) / 1.2
    rb = math.sqrt(r * b)                     # after the temperature step
    ti = math.log(g / rb, 2) / 0.36
    return (int(round(max(-100, min(100, t * 100)))), int(round(max(-100, min(100, ti * 100)))))


def auto_white_balance(doc, node):
    s = _sample(doc, node)
    if not s:
        return None
    r, g, b = s
    n = float(len(r))
    return wb_from_color(sum(r) / n, sum(g) / n, sum(b) / n)


def auto_tone(doc, node):
    """Like Camera Raw's Auto (Light): exposure from the median, whites /
    blacks from the ends of the histogram, highlights / shadows to open
    them up a little."""
    s = _sample(doc, node)
    if not s:
        return None
    r, g, b = s
    lum = sorted(0.2126 * x + 0.7152 * y + 0.0722 * z for x, y, z in zip(r, g, b))
    n = len(lum)
    enc = lambda v: 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
    med = enc(lum[n // 2])
    lo = enc(lum[int(n * 0.005)])
    hi = enc(lum[min(n - 1, int(n * 0.995))])
    exposure = max(-2.0, min(2.0, math.log(0.46 / max(0.02, med), 2) * 0.55 if med > 0 else 0))
    p = {
        "exposure": round(exposure, 2),
        "contrast": int(max(-30, min(30, (0.75 - (hi - lo)) * 60))),
        "highlights": int(max(-80, min(0, -(hi - 0.85) * 300))) if hi > 0.85 else 0,
        "shadows": int(max(0, min(60, (0.12 - lo) * 400))) if lo < 0.12 else 0,
        "whites": int(max(-40, min(40, (0.97 - hi) * 120))),
        "blacks": int(max(-40, min(40, (0.03 - lo) * 120))),
    }
    return p


# ================================================================= look
# Camera Raw's dark theme
ACR_STYLE = """
QWidget#acr, QDialog#acr { background: #535353; color: #e8e8e8; }
QWidget#acrPanel, QWidget#acrPanel QWidget { background: #535353; color: #e8e8e8; }
QScrollArea#acrScroll { background: #535353; border: none; }
QLabel { color: #e8e8e8; }
QLabel#acrDim { color: #a8a8a8; }
QLineEdit#acrValue { background: #454545; color: #f0f0f0; border: 1px solid #6c6c6c;
    border-radius: 2px; padding: 0 3px; selection-background-color: #2f6fcf; }
QPushButton { background: #5f5f5f; color: #f0f0f0; border: 1px solid #3c3c3c; border-radius: 3px;
    padding: 2px 8px; }
QPushButton:hover { background: #6a6a6a; }
QPushButton:checked { background: #2f6fcf; border-color: #2f6fcf; }
QPushButton#acrOk { border: 1px solid #e0e0e0; border-radius: 12px; padding: 4px 26px;
    background: transparent; }
QPushButton#acrCancel { border: none; background: transparent; padding: 4px 16px; }
QComboBox { background: #454545; color: #f0f0f0; border: 1px solid #6c6c6c; border-radius: 2px;
    padding: 1px 6px; }
QComboBox QAbstractItemView { background: #454545; color: #f0f0f0; selection-background-color: #2f6fcf; }
QToolButton { background: transparent; border: none; color: #e8e8e8; }
QToolButton:checked { color: #ffffff; }
"""


def _eye_icon(on, size=14):
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    col = QColor(200, 200, 200) if on else QColor(120, 120, 120)
    p.setPen(QPen(col, 1.2))
    path = QPainterPath()
    path.moveTo(1, size / 2.0)
    path.quadTo(size / 2.0, 1, size - 1, size / 2.0)
    path.quadTo(size / 2.0, size - 1, 1, size / 2.0)
    p.drawPath(path)
    p.setBrush(col)
    p.drawEllipse(QPointF(size / 2.0, size / 2.0), 2.2, 2.2)
    if not on:
        p.drawLine(QPointF(2, size - 2), QPointF(size - 2, 2))
    p.end()
    return QIcon(pm)


def _dropper_icon(size=16):
    """White balance tool (an eyedropper)."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor(220, 220, 220), 1.4))
    p.drawLine(QPointF(3, 13), QPointF(10, 6))
    p.setBrush(QColor(220, 220, 220))
    p.drawEllipse(QPointF(11.5, 4.5), 2.6, 2.6)
    p.drawLine(QPointF(8, 4), QPointF(12, 8))
    p.end()
    return QIcon(pm)


# ================================================================= widgets
class Track(QWidget):
    """Camera Raw's slider: a thin line (with a gradient for colour
    sliders) and a small triangle under it."""
    moved = pyqtSignal(float)
    reset = pyqtSignal()

    def __init__(self, vmin, vmax, gradient=None, parent=None):
        super(Track, self).__init__(parent)
        self.vmin, self.vmax = float(vmin), float(vmax)
        self.value = 0.0
        self.gradient = gradient
        self.setFixedHeight(12)
        self.setMinimumWidth(80)
        self.setCursor(Qt.PointingHandCursor)

    def _x(self, v):
        m = 5
        return m + (v - self.vmin) / (self.vmax - self.vmin) * (self.width() - 2 * m)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        m = 5
        rc = QRectF(m, 2, self.width() - 2 * m, 2.5)
        if self.gradient:
            g = QLinearGradient(rc.topLeft(), rc.topRight())
            n = len(self.gradient) - 1
            for i, c in enumerate(self.gradient):
                g.setColorAt(i / float(max(1, n)), QColor(c))
            p.fillRect(rc, QBrush(g))
        else:
            p.fillRect(rc, QColor(205, 205, 205))
        x = self._x(self.value)
        p.setPen(QPen(QColor(40, 40, 40), 1))
        p.setBrush(QColor(230, 230, 230))
        p.drawPolygon(QPolygonF([QPointF(x, 4.5), QPointF(x - 4.5, 11), QPointF(x + 4.5, 11)]))

    def _set_from(self, x):
        m = 5
        t = (x - m) / float(max(1, self.width() - 2 * m))
        self.moved.emit(self.vmin + max(0.0, min(1.0, t)) * (self.vmax - self.vmin))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._set_from(e.pos().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._set_from(e.pos().x())

    def mouseDoubleClickEvent(self, _e):
        self.reset.emit()


class CrSlider(QWidget):
    """Name on the left, the value in a small box on the right, the track
    under them. Double-click (track or name) resets it."""
    valueChanged = pyqtSignal(object)

    def __init__(self, label, vmin, vmax, default=0, decimals=0, gradient=None, parent=None):
        super(CrSlider, self).__init__(parent)
        self.default = default
        self.decimals = decimals
        self.vmin, self.vmax = vmin, vmax
        self._v = default
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setHorizontalSpacing(4)
        lay.setVerticalSpacing(0)
        self.label = QLabel(label)
        f = self.label.font()
        f.setPointSizeF(max(7.5, f.pointSizeF() - 0.5))
        self.label.setFont(f)
        self.edit = QLineEdit()
        self.edit.setObjectName("acrValue")
        self.edit.setFixedWidth(52)
        self.edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.edit.setFont(f)
        self.edit.editingFinished.connect(self._typed)
        self.track = Track(vmin, vmax, gradient)
        self.track.moved.connect(self._moved)
        self.track.reset.connect(self._reset)
        lay.addWidget(self.label, 0, 0)
        lay.addWidget(self.edit, 0, 1, Qt.AlignRight)
        lay.addWidget(self.track, 1, 0, 1, 2)
        self.label.installEventFilter(self)
        self._show()

    # tests / old code use .slider: the track keeps that name too
    @property
    def slider(self):
        return self

    def eventFilter(self, obj, e):
        if e.type() == e.MouseButtonDblClick:
            self._reset()
            return True
        return False

    def _fmt(self, v):
        if self.decimals:
            t = ("%%.%df" % self.decimals) % v
        else:
            t = "%d" % int(round(v))
        return ("+" + t) if v > 0 and self.vmin < 0 else t

    def _show(self):
        self.edit.setText(self._fmt(self._v))
        self.track.value = float(self._v)
        self.track.update()

    def _norm(self, v):
        v = max(self.vmin, min(self.vmax, float(v)))
        return round(v, self.decimals) if self.decimals else int(round(v))

    def value(self):
        return self._v

    def setValue(self, v):
        self._v = self._norm(v)
        self._show()

    def _changed(self, v):
        v = self._norm(v)
        if v != self._v:
            self._v = v
            self._show()
            self.valueChanged.emit(self._v)
        else:
            self._show()

    def _moved(self, v):
        self._changed(v)

    def _typed(self):
        try:
            self._changed(float(self.edit.text().replace(",", ".")))
        except ValueError:
            self._show()

    def _reset(self):
        self._v = self._norm(self.default)
        self._show()
        self.valueChanged.emit(self._v)


class Section(QWidget):
    """A Camera Raw panel: arrow + title, the eye (switches the panel's
    effect off and on), the body."""
    toggledEffect = pyqtSignal()

    def __init__(self, title, open_=False, parent=None):
        super(Section, self).__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 7, 8, 7)
        self.arrow = QToolButton()
        self.arrow.setCheckable(True)
        self.arrow.setChecked(open_)
        self.arrow.setText(u"⌄" if open_ else u"›")
        self.arrow.setFixedWidth(14)
        self.title = QToolButton()
        self.title.setText(title)
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        self.eye = QToolButton()
        self.eye.setCheckable(True)
        self.eye.setChecked(True)
        self.eye.setIcon(_eye_icon(True))
        self.eye.setIconSize(QSize(14, 14))
        self.eye.setToolTip(u"Show / hide the effect of this panel")
        self.eye.toggled.connect(self._eye)
        hl.addWidget(self.arrow)
        hl.addWidget(self.title)
        hl.addStretch(1)
        hl.addWidget(self.eye)
        lay.addWidget(head)
        self.body = QWidget()
        self.box = QVBoxLayout(self.body)
        self.box.setContentsMargins(18, 0, 14, 10)
        self.box.setSpacing(3)
        lay.addWidget(self.body)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #3c3c3c;")
        lay.addWidget(line)
        self.body.setVisible(open_)
        self.arrow.toggled.connect(self._open)
        self.title.clicked.connect(self.arrow.toggle)

    def _open(self, on):
        self.arrow.setText(u"⌄" if on else u"›")
        self.body.setVisible(on)

    def _eye(self, on):
        self.eye.setIcon(_eye_icon(on))
        self.toggledEffect.emit()

    def effect_on(self):
        return self.eye.isChecked()


class ColorWheel(QWidget):
    """Hue / saturation wheel of Color Grading (hue 0..360, sat 0..100)."""
    changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super(ColorWheel, self).__init__(parent)
        self.hue, self.sat = 0.0, 0.0
        self.setMinimumSize(150, 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(170)

    def setValues(self, hue, sat):
        self.hue, self.sat = float(hue), float(sat)
        self.update()

    def _geom(self):
        r = min(self.width(), self.height()) / 2.0 - 6
        return QPointF(self.width() / 2.0, self.height() / 2.0), r

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        c, r = self._geom()
        g = QConicalGradient(c, 0)
        for i in range(13):
            g.setColorAt(i / 12.0, QColor.fromHsvF((i / 12.0) % 1.0, 0.85, 0.95))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, r, r)
        rg = QRadialGradient(c, r)
        rg.setColorAt(0, QColor(120, 120, 120, 255))
        rg.setColorAt(1, QColor(120, 120, 120, 0))
        p.setBrush(QBrush(rg))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor(40, 40, 40), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, r, r)
        a = math.radians(self.hue)
        d = self.sat / 100.0 * r
        pt = QPointF(c.x() + math.cos(a) * d, c.y() - math.sin(a) * d)
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.setBrush(QColor.fromHsvF((self.hue / 360.0) % 1.0, self.sat / 100.0, 1.0))
        p.drawEllipse(pt, 6, 6)

    def _pick(self, pos):
        c, r = self._geom()
        dx, dy = pos.x() - c.x(), c.y() - pos.y()
        self.hue = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
        self.sat = min(100.0, math.hypot(dx, dy) / max(1.0, r) * 100.0)
        self.update()
        self.changed.emit(self.hue, self.sat)

    def mousePressEvent(self, e):
        self._pick(e.pos())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._pick(e.pos())

    def mouseDoubleClickEvent(self, _e):
        self.hue, self.sat = 0.0, 0.0
        self.update()
        self.changed.emit(self.hue, self.sat)


class Histogram(QWidget):
    """RGB histogram (overlapping channels, like Camera Raw)."""

    def __init__(self, parent=None):
        super(Histogram, self).__init__(parent)
        self.counts = None
        self.setFixedHeight(120)

    def set_image(self, img):
        if img is None or img.isNull():
            self.counts = None
            self.update()
            return
        small = img.scaled(256, 256, Qt.KeepAspectRatio, Qt.FastTransformation)
        small = small.convertToFormat(QImage.Format_ARGB32)
        w, h = small.width(), small.height()
        ptr = small.constBits()
        ptr.setsize(small.bytesPerLine() * h)
        raw = bytes(ptr)
        bpl = small.bytesPerLine()
        if bpl != w * 4:
            raw = b"".join(raw[y * bpl:y * bpl + w * 4] for y in range(h))
        out = []
        for off in (2, 1, 0):                      # R G B (BGRA in memory)
            ch = raw[off::4]
            out.append([ch.count(i) for i in range(256)])
        self.counts = out
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))
        if not self.counts:
            return
        p.setRenderHint(QPainter.Antialiasing, True)
        top = max(1, max(max(c[2:254]) for c in self.counts))
        w, h = self.width(), self.height() - 4
        p.setCompositionMode(QPainter.CompositionMode_Plus)
        for col, cnt in zip((QColor(190, 40, 40), QColor(40, 170, 40), QColor(40, 70, 200)), self.counts):
            path = QPainterPath()
            path.moveTo(0, h + 2)
            for i, n in enumerate(cnt):
                path.lineTo(i / 255.0 * w, h + 2 - min(1.0, n / float(top)) * h)
            path.lineTo(w, h + 2)
            path.closeSubpath()
            p.fillPath(path, col)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)


# ================================================================= panel
GRAD_TEMP = ["#2f69d8", "#d0d0d0", "#e3c22f"]
GRAD_TINT = ["#38b838", "#d0d0d0", "#c638c6"]
GRAD_EXPOSURE = ["#202020", "#f0f0f0"]

# which params belong to which panel (the eye of a panel resets them)
SECTION_KEYS = {
    "light": ["exposure", "contrast", "highlights", "shadows", "whites", "blacks"],
    "color": ["temperature", "tint", "vibrance", "saturation"],
    "effects": ["texture", "clarity", "dehaze"] + [k for k in DEFAULTS if k.startswith(("vig_", "grain_"))],
    "curve": [k for k in DEFAULTS if k.startswith("pc_")] + CURVES,
    "mixer": [k for k in DEFAULTS if k.startswith("mix_")],
    "grading": [k for k in DEFAULTS if k.startswith("cg_")],
    "detail": [k for k in DEFAULTS if k.startswith(("sharp_", "nr_"))],
}


class CameraRawPanel(QWidget):
    """Camera Raw's Edit panel: Auto / B&W / Profile and the panels Light,
    Color, Effects, Curve, Color Mixer, Color Grading, Detail.
    changed: the params changed (emitted while dragging)."""
    changed = pyqtSignal()

    def __init__(self, parent=None, compact=False):
        super(CameraRawPanel, self).__init__(parent)
        self.setObjectName("acrPanel")
        self.p = defaults()
        self.sliders = {}
        self.sections = {}
        self._loading = False
        self.on_auto_tone = None          # callbacks set by the dialog / docker
        self.on_auto_wb = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QWidget()
        hl = QGridLayout(head)
        hl.setContentsMargins(14, 8, 10, 8)
        edit = QLabel(u"Edit")
        f = edit.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1)
        edit.setFont(f)
        hl.addWidget(edit, 0, 0)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.b_auto = QPushButton(u"Auto")
        self.b_auto.setToolTip(u"Automatic exposure, contrast, highlights, shadows, whites, blacks")
        self.b_auto.clicked.connect(lambda: self.on_auto_tone and self.on_auto_tone())
        self.b_bw = QPushButton(u"B&&W")
        self.b_bw.setCheckable(True)
        self.b_bw.setToolTip(u"Black & White (Profile: Monochrome)")
        self.b_bw.toggled.connect(self._bw)
        btns.addWidget(self.b_auto)
        btns.addWidget(self.b_bw)
        hl.addLayout(btns, 0, 1)
        hl.addWidget(QLabel(u"Profile"), 1, 0)
        self.profile = QComboBox()
        self.profile.addItem(u"Color")
        self.profile.addItem(u"Monochrome")
        self.profile.currentIndexChanged.connect(lambda i: self.b_bw.setChecked(i == 1))
        hl.addWidget(self.profile, 1, 1)
        hl.setColumnStretch(1, 1)
        lay.addWidget(head)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #3c3c3c;")
        lay.addWidget(line)

        sc = QScrollArea()
        sc.setObjectName("acrScroll")
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        self._light()
        self._color()
        self._effects()
        self._curve()
        self._mixer()
        self._grading()
        self._detail()
        self.body.addStretch(1)
        sc.setWidget(body)
        lay.addWidget(sc, 1)
        self.scroll = sc

    # -- building blocks
    def _section(self, key, title, open_=False):
        sec = Section(title, open_)
        sec.toggledEffect.connect(self._emit)
        self.sections[key] = sec
        self.body.addWidget(sec)
        return sec

    def _slider(self, box, key, label, vmin, vmax, decimals=0, gradient=None):
        s = CrSlider(label, vmin, vmax, DEFAULTS[key], decimals, gradient)
        s.valueChanged.connect(lambda v, k=key: self._set(k, v))
        box.addWidget(s)
        self.sliders[key] = s
        return s

    def _set(self, key, v):
        if self._loading:
            return
        self.p[key] = v
        self.changed.emit()

    def _emit(self):
        if not self._loading:
            self.changed.emit()

    def _bw(self, on):
        was = self._loading
        self._loading = True
        try:
            self.profile.setCurrentIndex(1 if on else 0)
        finally:
            self._loading = was
        self.p["bw"] = 1 if on else 0
        self._mixer_mode()
        self._emit()

    # -- Light
    def _light(self):
        box = self._section("light", u"Light", True).box
        self._slider(box, "exposure", u"Exposure", -5, 5, 2, GRAD_EXPOSURE)
        self._slider(box, "contrast", u"Contrast", -100, 100)
        self._slider(box, "highlights", u"Highlights", -100, 100)
        self._slider(box, "shadows", u"Shadows", -100, 100)
        self._slider(box, "whites", u"Whites", -100, 100)
        self._slider(box, "blacks", u"Blacks", -100, 100)

    # -- Color
    def _color(self):
        box = self._section("color", u"Color").box
        wb = QHBoxLayout()
        wb.addWidget(QLabel(u"White balance"))
        self.wb_mode = QComboBox()
        self.wb_mode.addItem(u"As Shot", "as_shot")
        self.wb_mode.addItem(u"Auto", "auto")
        self.wb_mode.addItem(u"Custom", "custom")
        wb.addWidget(self.wb_mode, 1)
        fg = QToolButton()
        fg.setIcon(_dropper_icon())
        fg.setIconSize(QSize(16, 16))
        fg.setToolTip(u"White balance: the foreground colour (pick a colour that should be neutral "
                      u"grey with Krita's colour picker first)")
        fg.clicked.connect(self._wb_from_foreground)
        wb.addWidget(fg)
        box.addLayout(wb)
        self.wb_mode.activated.connect(self._wb_mode)
        self._slider(box, "temperature", u"Temperature", -100, 100, 0, GRAD_TEMP)
        self._slider(box, "tint", u"Tint", -100, 100, 0, GRAD_TINT)
        self.sliders["temperature"].valueChanged.connect(self._wb_custom)
        self.sliders["tint"].valueChanged.connect(self._wb_custom)
        box.addSpacing(6)
        self._slider(box, "vibrance", u"Vibrance", -100, 100, 0, ["#8a8a8a", "#c8487a"])
        self._slider(box, "saturation", u"Saturation", -100, 100, 0, ["#8a8a8a", "#e03a3a"])

    # -- Effects
    def _effects(self):
        box = self._section("effects", u"Effects").box
        self._slider(box, "texture", u"Texture", -100, 100)
        self._slider(box, "clarity", u"Clarity", -100, 100)
        self._slider(box, "dehaze", u"Dehaze", -100, 100)
        box.addSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel(u"Vignette"))
        self.vig_style = QComboBox()
        for text in (u"Highlight Priority", u"Color Priority", u"Paint Overlay"):
            self.vig_style.addItem(text)
        self.vig_style.currentIndexChanged.connect(lambda i: self._set("vig_style", int(i)))
        row.addWidget(self.vig_style, 1)
        box.addLayout(row)
        self._slider(box, "vig_amount", u"Amount", -100, 100, 0, ["#101010", "#8a8a8a", "#f6f6f6"])
        self._slider(box, "vig_midpoint", u"Midpoint", 0, 100)
        self._slider(box, "vig_roundness", u"Roundness", -100, 100)
        self._slider(box, "vig_feather", u"Feather", 0, 100)
        self._slider(box, "vig_highlights", u"Highlights", 0, 100)
        box.addSpacing(6)
        lab = QLabel(u"Grain")
        box.addWidget(lab)
        self._slider(box, "grain_amount", u"Amount", 0, 100)
        self._slider(box, "grain_size", u"Size", 0, 100)
        self._slider(box, "grain_roughness", u"Roughness", 0, 100)

    # -- Curve
    def _curve(self):
        sec = self._section("curve", u"Curve")
        box = sec.box
        row = QHBoxLayout()
        row.setSpacing(4)
        self.curve_mode = QComboBox()          # the buttons below drive it
        self.curve_mode.setVisible(False)
        for text in (u"Parametric", u"RGB", u"Red", u"Green", u"Blue"):
            self.curve_mode.addItem(text)
        self.curve_buttons = QButtonGroup(self)
        colors = [None, "#e8e8e8", "#e04040", "#40c040", "#4080f0"]
        tips = [u"Parametric curve", u"Point curve: RGB", u"Point curve: Red", u"Point curve: Green",
                u"Point curve: Blue"]
        for i, c in enumerate(colors):
            b = QToolButton()
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setToolTip(tips[i])
            b.setFixedSize(24, 24)
            if c is None:
                b.setText(u"∿")
            else:
                pm = QPixmap(32, 32)
                pm.setDevicePixelRatio(2.0)
                pm.fill(Qt.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.Antialiasing, True)
                p.setPen(QPen(QColor(30, 30, 30), 1))
                p.setBrush(QColor(c))
                p.drawEllipse(QPointF(8, 8), 6, 6)
                p.end()
                b.setIcon(QIcon(pm))
            b.setStyleSheet("QToolButton:checked { background: #6a6a6a; border-radius: 3px; }")
            self.curve_buttons.addButton(b, i)
            row.addWidget(b)
        row.addStretch(1)
        reset = QToolButton()
        reset.setText(u"↺")
        reset.setToolTip(u"Reset this curve")
        reset.clicked.connect(self._reset_curve)
        row.addWidget(reset)
        box.addLayout(row)
        box.addWidget(self.curve_mode)
        self.curve_buttons.buttonClicked.connect(
            lambda b: self.curve_mode.setCurrentIndex(self.curve_buttons.id(b)))
        self.curve_stack = QStackedWidget()
        para = QWidget()
        pv = QVBoxLayout(para)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(3)
        self.para_view = ParametricView(self)
        pv.addWidget(self.para_view)
        self._slider(pv, "pc_highlights", u"Highlights", -100, 100)
        self._slider(pv, "pc_lights", u"Lights", -100, 100)
        self._slider(pv, "pc_darks", u"Darks", -100, 100)
        self._slider(pv, "pc_shadows", u"Shadows", -100, 100)
        self._slider(pv, "pc_split1", u"Shadows / Darks split", 5, 90)
        self._slider(pv, "pc_split2", u"Darks / Lights split", 10, 95)
        self._slider(pv, "pc_split3", u"Lights / Highlights split", 15, 98)
        self.curve_stack.addWidget(para)
        from .widgets import CurveView
        self.curve_view = CurveView()
        self.curve_view.setMinimumSize(220, 220)
        self.curve_view.changed.connect(self._curve_changed)
        pw = QWidget()
        pwl = QVBoxLayout(pw)
        pwl.setContentsMargins(0, 0, 0, 0)
        pwl.addWidget(self.curve_view)
        hint = QLabel(u"Click to add a point, drag it, Delete removes it.")
        hint.setObjectName("acrDim")
        hint.setWordWrap(True)
        pwl.addWidget(hint)
        self.curve_stack.addWidget(pw)
        box.addWidget(self.curve_stack)
        self.curve_mode.currentIndexChanged.connect(self._curve_mode)
        for k in ("pc_highlights", "pc_lights", "pc_darks", "pc_shadows", "pc_split1", "pc_split2",
                  "pc_split3"):
            self.sliders[k].valueChanged.connect(lambda _v: self.para_view.update())

    def _curve_key(self):
        i = self.curve_mode.currentIndex()
        return CURVES[i - 1] if i > 0 else None

    def _curve_mode(self, i):
        self.curve_stack.setCurrentIndex(0 if i == 0 else 1)
        b = self.curve_buttons.button(i)
        if b is not None and not b.isChecked():
            b.setChecked(True)
        key = self._curve_key()
        if key:
            colors = {"curve_rgb": "#ffffff", "curve_red": "#ff4040", "curve_green": "#40d040",
                      "curve_blue": "#4080ff"}
            self.curve_view.color = QColor(colors[key])
            self.curve_view.setPoints(self.p.get(key) or IDENTITY)

    def _curve_changed(self):
        key = self._curve_key()
        if key and not self._loading:
            self.p[key] = [list(pt) for pt in self.curve_view.points]
            self.changed.emit()

    def _reset_curve(self):
        key = self._curve_key()
        if key:
            self.p[key] = [list(x) for x in IDENTITY]
            self.curve_view.setPoints(IDENTITY)
        else:
            for k in ("pc_highlights", "pc_lights", "pc_darks", "pc_shadows", "pc_split1",
                      "pc_split2", "pc_split3"):
                self.p[k] = DEFAULTS[k]
                self.sliders[k].setValue(DEFAULTS[k])
            self.para_view.update()
        self.changed.emit()

    # -- Color Mixer
    def _mixer(self):
        box = self._section("mixer", u"Color Mixer").box
        row = QHBoxLayout()
        self.mixer_label = QLabel(u"Adjust")
        row.addWidget(self.mixer_label)
        self.mix_kind = QComboBox()
        for text in (u"Hue", u"Saturation", u"Luminance"):
            self.mix_kind.addItem(text)
        row.addWidget(self.mix_kind, 1)
        box.addLayout(row)
        self.mix_stack = QStackedWidget()
        for kind in ("hue", "sat", "lum"):
            page = QWidget()
            pv = QVBoxLayout(page)
            pv.setContentsMargins(0, 2, 0, 0)
            pv.setSpacing(3)
            for i, c in enumerate(COLORS):
                if kind == "hue":
                    prev = COLOR_SWATCH[(i - 1) % 8] if i else "#e03f8f"
                    grad = [prev, COLOR_SWATCH[i], COLOR_SWATCH[(i + 1) % 8]]
                elif kind == "sat":
                    grad = ["#8a8a8a", COLOR_SWATCH[i]]
                else:
                    grad = ["#101010", COLOR_SWATCH[i], "#f8f8f8"]
                self._slider(pv, "mix_%s_%s" % (kind, c), COLOR_LABELS[i], -100, 100, 0, grad)
            self.mix_stack.addWidget(page)
        box.addWidget(self.mix_stack)
        self.mix_kind.currentIndexChanged.connect(self.mix_stack.setCurrentIndex)

    def _mixer_mode(self):
        """B&W: the mixer is the B&W Mix (the luminance sliders)."""
        bw = bool(self.p.get("bw"))
        self.sections["mixer"].title.setText(u"B&W Mix" if bw else u"Color Mixer")
        self.mix_kind.setVisible(not bw)
        self.mixer_label.setVisible(not bw)
        if bw:
            self.mix_stack.setCurrentIndex(2)
        else:
            self.mix_stack.setCurrentIndex(self.mix_kind.currentIndex())
        for kind in ("hue", "sat"):
            for c in COLORS:
                self.sliders["mix_%s_%s" % (kind, c)].setEnabled(not bw)
        for k in ("vibrance", "saturation"):
            self.sliders[k].setEnabled(not bw)

    # -- Color Grading
    def _grading(self):
        box = self._section("grading", u"Color Grading").box
        zrow = QHBoxLayout()
        zrow.setSpacing(2)
        zrow.addWidget(QLabel(u"Adjust"))
        self.zone_buttons = QButtonGroup(self)
        for i, z in enumerate(ZONE_LABELS):
            b = QToolButton()
            b.setText(z[0])
            b.setToolTip(z)
            b.setCheckable(True)
            b.setChecked(i == 1)
            b.setFixedSize(26, 22)
            b.setStyleSheet("QToolButton:checked { background: #6a6a6a; border-radius: 3px; }")
            self.zone_buttons.addButton(b, i)
            zrow.addWidget(b)
        zrow.addStretch(1)
        self.zone_name = QLabel(ZONE_LABELS[1])
        zrow.addWidget(self.zone_name)
        box.addLayout(zrow)
        self.wheel = ColorWheel()
        self.wheel.changed.connect(self._wheel)
        box.addWidget(self.wheel)
        self.zone_hue = CrSlider(u"Hue", 0, 359, 0, 0,
                                 ["#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff",
                                  "#ff0000"])
        self.zone_sat = CrSlider(u"Saturation", 0, 100, 0, 0, ["#8a8a8a", "#ff8040"])
        self.zone_lum = CrSlider(u"Luminance", -100, 100, 0, 0, ["#101010", "#f0f0f0"])
        for s in (self.zone_hue, self.zone_sat, self.zone_lum):
            s.valueChanged.connect(self._zone_slider)
            box.addWidget(s)
        box.addSpacing(4)
        self._slider(box, "cg_blending", u"Blending", 0, 100)
        self._slider(box, "cg_balance", u"Balance", -100, 100, 0, ["#202020", "#e0e0e0"])
        self.zone_buttons.buttonClicked.connect(lambda _b: self._zone_load())
        self._zone_load()

    def _zone(self):
        return ZONES[max(0, self.zone_buttons.checkedId())]

    def _zone_load(self):
        z = self._zone()
        self.zone_name.setText(ZONE_LABELS[ZONES.index(z)])
        was = self._loading
        self._loading = True
        try:
            self.wheel.setValues(self.p["cg_%s_hue" % z], self.p["cg_%s_sat" % z])
            self.zone_hue.setValue(self.p["cg_%s_hue" % z])
            self.zone_sat.setValue(self.p["cg_%s_sat" % z])
            self.zone_lum.setValue(self.p["cg_%s_lum" % z])
        finally:
            self._loading = was

    def _wheel(self, hue, sat):
        z = self._zone()
        self.p["cg_%s_hue" % z] = int(round(hue)) % 360
        self.p["cg_%s_sat" % z] = int(round(sat))
        was = self._loading
        self._loading = True
        try:
            self.zone_hue.setValue(self.p["cg_%s_hue" % z])
            self.zone_sat.setValue(self.p["cg_%s_sat" % z])
        finally:
            self._loading = was
        self.changed.emit()

    def _zone_slider(self, _v):
        if self._loading:
            return
        z = self._zone()
        self.p["cg_%s_hue" % z] = self.zone_hue.value()
        self.p["cg_%s_sat" % z] = self.zone_sat.value()
        self.p["cg_%s_lum" % z] = self.zone_lum.value()
        self.wheel.setValues(self.p["cg_%s_hue" % z], self.p["cg_%s_sat" % z])
        self.changed.emit()

    # -- Detail
    def _detail(self):
        box = self._section("detail", u"Detail").box
        self._slider(box, "sharp_amount", u"Sharpening", 0, 150)
        self._slider(box, "sharp_radius", u"Radius", 0.5, 3.0, 1)
        self._slider(box, "sharp_detail", u"Detail", 0, 100)
        self._slider(box, "sharp_masking", u"Masking", 0, 100)
        box.addSpacing(6)
        self._slider(box, "nr_lum", u"Noise Reduction", 0, 100)
        self._slider(box, "nr_lum_detail", u"Detail", 0, 100)
        self._slider(box, "nr_lum_contrast", u"Contrast", 0, 100)
        box.addSpacing(6)
        self._slider(box, "nr_color", u"Color Noise Reduction", 0, 100)
        self._slider(box, "nr_color_detail", u"Detail", 0, 100)
        self._slider(box, "nr_color_smooth", u"Smoothness", 0, 100)

    # -- white balance
    def _wb_custom(self, _v):
        if not self._loading:
            self.p["wb_mode"] = "custom"
            self.wb_mode.setCurrentIndex(self.wb_mode.findData("custom"))

    def _wb_mode(self, _i):
        mode = self.wb_mode.currentData()
        self.p["wb_mode"] = mode
        if mode == "as_shot":
            self._set_values({"temperature": 0, "tint": 0})
        elif mode == "auto" and self.on_auto_wb:
            self.on_auto_wb()

    def _wb_from_foreground(self):
        try:
            view = Krita.instance().activeWindow().activeView()
            c = view.foregroundColor().colorForCanvas(view.canvas())
        except Exception:
            return
        dec = lambda v: v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
        t, ti = wb_from_color(dec(c.redF()), dec(c.greenF()), dec(c.blueF()))
        self._set_values({"temperature": t, "tint": ti})
        self.p["wb_mode"] = "custom"
        self.wb_mode.setCurrentIndex(self.wb_mode.findData("custom"))

    # -- API
    def _set_values(self, values):
        self.p.update(values)
        was = self._loading
        self._loading = True
        try:
            for k, val in values.items():
                if k in self.sliders:
                    self.sliders[k].setValue(val)
        finally:
            self._loading = was
        self.changed.emit()

    def set_params(self, p):
        self.p = defaults()
        self.p.update(p or {})
        self._loading = True
        try:
            for k, s in self.sliders.items():
                s.setValue(self.p.get(k, DEFAULTS[k]))
            self.vig_style.setCurrentIndex(int(self.p.get("vig_style", 0)))
            i = self.wb_mode.findData(self.p.get("wb_mode", "as_shot"))
            self.wb_mode.setCurrentIndex(max(0, i))
            self.b_bw.setChecked(bool(self.p.get("bw")))
            self.profile.setCurrentIndex(1 if self.p.get("bw") else 0)
            self._mixer_mode()
            self._curve_mode(self.curve_mode.currentIndex())
            self._zone_load()
            self.para_view.update()
        finally:
            self._loading = False

    def params(self):
        """Everything set in the panels (also of panels switched off)."""
        import json
        return json.loads(json.dumps(self.p))

    def effective_params(self):
        """What is applied: panels switched off with their eye count as reset."""
        p = self.params()
        for key, sec in self.sections.items():
            if sec.effect_on():
                continue
            for k in SECTION_KEYS[key]:
                if k in CURVES:
                    p[k] = [list(x) for x in IDENTITY]
                else:
                    p[k] = DEFAULTS[k]
            if key == "mixer" and p.get("bw"):
                pass                               # B&W stays, only its mix goes
        return p


class ParametricView(QWidget):
    """The parametric curve (drawn from the sliders) with its regions."""

    def __init__(self, panel):
        super(ParametricView, self).__init__(panel)
        self.panel = panel
        self.setFixedHeight(150)

    def paintEvent(self, _e):
        p = QPainter(self)
        rc = QRectF(4, 4, self.width() - 8, self.height() - 8)
        p.fillRect(rc, QColor(48, 48, 48))
        pp = self.panel.p
        splits = [pp.get("pc_split1", 25) / 100.0, pp.get("pc_split2", 50) / 100.0,
                  pp.get("pc_split3", 75) / 100.0]
        p.setPen(QPen(QColor(80, 80, 80), 1))
        for i in range(1, 4):
            x = rc.left() + rc.width() * i / 4.0
            y = rc.top() + rc.height() * i / 4.0
            p.drawLine(QPointF(x, rc.top()), QPointF(x, rc.bottom()))
            p.drawLine(QPointF(rc.left(), y), QPointF(rc.right(), y))
        p.setPen(QPen(QColor(110, 110, 110), 1, Qt.DashLine))
        for sp in splits:
            x = rc.left() + rc.width() * sp
            p.drawLine(QPointF(x, rc.top()), QPointF(x, rc.bottom()))
        # the same bumps as the C++ tone curve (ps_cameraraw.h ToneLuts)
        s1, s2, s3 = splits
        s1 = min(max(s1, 0.05), 0.9)
        s2 = min(max(s2, s1 + 0.02), 0.95)
        s3 = min(max(s3, s2 + 0.02), 0.98)
        zones = [(0.0, s1 * 0.5, s2), (0.0, (s1 + s2) * 0.5, s3), (s1, (s2 + s3) * 0.5, 1.0),
                 (s2, (s3 + 1.0) * 0.5, 1.0)]
        amounts = [pp.get("pc_shadows", 0) / 100.0, pp.get("pc_darks", 0) / 100.0,
                   pp.get("pc_lights", 0) / 100.0, pp.get("pc_highlights", 0) / 100.0]
        path = QPainterPath()
        for i in range(101):
            x = i / 100.0
            y = x
            for (l, m, r), a in zip(zones, amounts):
                if not a or x <= l or x >= r:
                    continue
                if x < m:
                    t = 0.5 - 0.5 * math.cos(math.pi * (x - l) / max(1e-4, m - l))
                else:
                    t = 0.5 + 0.5 * math.cos(math.pi * (x - m) / max(1e-4, r - m))
                y += a * 0.11 * t
            pt = QPointF(rc.left() + x * rc.width(), rc.bottom() - min(1, max(0, y)) * rc.height())
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(235, 235, 235), 1.5))
        p.drawPath(path)


# ================================================================= session
class Session(object):
    """The node being edited (filter layer or filter mask) and how to undo
    the dialog: remove a new node, or put the old settings back."""

    def __init__(self, doc, node, created):
        self.doc, self.node, self.created = doc, node, created
        self.original = params_from_node(node)
        self.p = dict(self.original)

    def apply(self, p):
        self.p = p
        apply_to_node(self.node, p)

    def cancel(self):
        try:
            if self.created:
                self.node.remove()
            else:
                apply_to_node(self.node, self.original)
        except Exception:
            pass

    def auto_wb(self):
        return auto_white_balance(self.doc, self.node)

    def auto_tone(self):
        return auto_tone(self.doc, self.node)

    def idle(self):
        try:
            if not self.doc.tryBarrierLock():
                return False
            self.doc.unlock()
        except Exception:
            pass
        return True


class PreviewView(QWidget):
    """The image (Krita renders it: the document with the filter, as on the
    canvas). Fit or a zoom factor; drag to pan when zoomed in."""
    zoomChanged = pyqtSignal()

    def __init__(self, session, parent=None):
        super(PreviewView, self).__init__(parent)
        self.session = session
        self.zoom = None                     # None = fit
        self.center = None                   # image point in the middle (zoomed)
        self.img = None
        self.img_rect = QRectF()
        self._drag = None
        self.setMinimumSize(400, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background: #262626;")

    def doc_size(self):
        return self.session.doc.width(), self.session.doc.height()

    def fit_scale(self):
        W, H = self.doc_size()
        return min((self.width() - 16) / float(max(1, W)), (self.height() - 16) / float(max(1, H)))

    def scale(self):
        return self.fit_scale() if self.zoom is None else self.zoom

    def refresh(self):
        """Grabs the current rendering (call when Krita is idle)."""
        doc = self.session.doc
        W, H = self.doc_size()
        s = self.scale()
        try:
            if self.zoom is None or s <= self.fit_scale() + 1e-6:
                w, h = max(1, int(W * s)), max(1, int(H * s))
                self.img = doc.thumbnail(w, h)
                self.img_rect = QRectF((self.width() - w) / 2.0, (self.height() - h) / 2.0, w, h)
            else:
                if self.center is None:
                    self.center = QPointF(W / 2.0, H / 2.0)
                vw, vh = self.width() / s, self.height() / s
                x0 = int(max(0, min(W - vw, self.center.x() - vw / 2.0)))
                y0 = int(max(0, min(H - vh, self.center.y() - vh / 2.0)))
                w, h = int(min(W, vw + 2)), int(min(H, vh + 2))
                img = doc.projection(x0, y0, w, h)
                self.center = QPointF(x0 + vw / 2.0, y0 + vh / 2.0)
                if s != 1.0:
                    img = img.scaled(int(w * s), int(h * s), Qt.IgnoreAspectRatio,
                                     Qt.SmoothTransformation if s < 1 else Qt.FastTransformation)
                self.img = img
                ox = (self.width() - min(self.width(), W * s)) / 2.0
                oy = (self.height() - min(self.height(), H * s)) / 2.0
                self.img_rect = QRectF(ox, oy, img.width(), img.height())
        except Exception:
            self.img = None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(38, 38, 38))
        if self.img is not None and not self.img.isNull():
            p.drawImage(self.img_rect.topLeft(), self.img)

    def set_zoom(self, z):
        self.zoom = z
        self.refresh()
        self.zoomChanged.emit()

    def resizeEvent(self, e):
        super(PreviewView, self).resizeEvent(e)
        QTimer.singleShot(0, self.refresh)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.zoom is not None:
            self._drag = (e.pos(), QPointF(self.center) if self.center else None)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag and self._drag[1] is not None:
            d = e.pos() - self._drag[0]
            s = self.scale()
            self.center = QPointF(self._drag[1].x() - d.x() / s, self._drag[1].y() - d.y() / s)

    def mouseReleaseEvent(self, _e):
        if self._drag:
            self._drag = None
            self.setCursor(Qt.ArrowCursor)
            self.refresh()

    def mouseDoubleClickEvent(self, e):
        # like Camera Raw: double-click toggles Fit / 100% at that point
        if self.zoom is None:
            s = self.fit_scale()
            pt = (QPointF(e.pos()) - self.img_rect.topLeft()) / max(1e-6, s)
            self.center = pt
            self.set_zoom(1.0)
        else:
            self.set_zoom(None)

    def wheelEvent(self, e):
        steps = [None, 0.25, 0.5, 1.0, 2.0, 4.0]
        cur = self.zoom
        i = steps.index(cur) if cur in steps else 0
        i = min(len(steps) - 1, i + 1) if e.angleDelta().y() > 0 else max(0, i - 1)
        if steps[i] is not None and steps[i] <= self.fit_scale():
            i = 0 if e.angleDelta().y() < 0 else i
        self.set_zoom(steps[i])


class CameraRawDialog(QDialog):
    ZOOMS = [(u"Fit", None), (u"25%", 0.25), (u"50%", 0.5), (u"100%", 1.0), (u"200%", 2.0),
             (u"400%", 4.0)]

    def __init__(self, session, parent=None):
        super(CameraRawDialog, self).__init__(parent)
        self.session = session
        self.setObjectName("acr")
        self.setStyleSheet(ACR_STYLE)
        self.setWindowTitle(u"Camera Raw Filter")
        self.setModal(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = QLabel(session.node.name())
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("background: #474747; color: #e8e8e8; padding: 8px;")
        root.addWidget(title)

        mid = QHBoxLayout()
        mid.setSpacing(0)
        self.view = PreviewView(session)
        mid.addWidget(self.view, 1)
        side = QWidget()
        side.setObjectName("acrPanel")
        side.setFixedWidth(330)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self.hist = Histogram()
        hw = QWidget()
        hwl = QVBoxLayout(hw)
        hwl.setContentsMargins(8, 8, 8, 8)
        hwl.addWidget(self.hist)
        sl.addWidget(hw)
        self.panel = CameraRawPanel(side)
        self.panel.set_params(session.p)
        self.panel.on_auto_wb = self._auto_wb
        self.panel.on_auto_tone = self._auto_tone
        sl.addWidget(self.panel, 1)
        mid.addWidget(side)
        root.addLayout(mid, 1)

        bar = QWidget()
        bar.setStyleSheet("background: #474747;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 4, 12, 4)
        self.zoom_box = QComboBox()
        for text, _z in self.ZOOMS:
            self.zoom_box.addItem(text)
        self.zoom_box.activated.connect(lambda i: self.view.set_zoom(self.ZOOMS[i][1]))
        self.view.zoomChanged.connect(self._zoom_shown)
        bl.addWidget(self.zoom_box)
        bl.addStretch(1)
        self.before = QToolButton()
        self.before.setText(u"◧")
        self.before.setCheckable(True)
        self.before.setToolTip(u"Before / after (\\ or P)")
        self.before.toggled.connect(self._before)
        bl.addWidget(self.before)
        reset = QToolButton()
        reset.setText(u"↺")
        reset.setToolTip(u"Reset all settings")
        reset.clicked.connect(self._reset)
        bl.addWidget(reset)
        bl.addSpacing(24)
        ok = QPushButton(u"OK")
        ok.setObjectName("acrOk")
        ok.setDefault(True)
        cancel = QPushButton(u"Cancel")
        cancel.setObjectName("acrCancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        bl.addWidget(ok)
        bl.addWidget(cancel)
        root.addWidget(bar)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self._apply)
        self.panel.changed.connect(self.timer.start)
        self.grab_timer = QTimer(self)
        self.grab_timer.setSingleShot(True)
        self.grab_timer.setInterval(120)
        self.grab_timer.timeout.connect(self._grab)
        self._size()
        QTimer.singleShot(50, self._grab)

    def _size(self):
        try:
            geo = QApplication.primaryScreen().availableGeometry()
            self.resize(int(geo.width() * 0.85), int(geo.height() * 0.85))
        except Exception:
            self.resize(1400, 900)

    def _zoom_shown(self):
        z = self.view.zoom
        for i, (_t, v) in enumerate(self.ZOOMS):
            if v == z:
                self.zoom_box.setCurrentIndex(i)
                return
        self.zoom_box.setCurrentIndex(0)

    def _apply(self):
        try:
            self.session.apply(self.panel.effective_params())
        except Exception as e:
            print("[ps_tools] camera raw: %s" % e)
        self.grab_timer.start()

    def _grab(self):
        """Updates the preview and the histogram once Krita has rendered."""
        if not self.session.idle():
            self.grab_timer.start()
            return
        self.view.refresh()
        try:
            self.hist.set_image(self.session.doc.thumbnail(256, 256))
        except Exception:
            pass

    def _before(self, on):
        try:
            self.session.node.setVisible(not on)
            self.session.doc.refreshProjection()
        except Exception:
            pass
        self.grab_timer.start()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_P, Qt.Key_Backslash) and not (e.modifiers() & Qt.ControlModifier):
            self.before.toggle()
            return
        super(CameraRawDialog, self).keyPressEvent(e)

    def _auto_wb(self):
        r = self.session.auto_wb()
        if r:
            self.panel._set_values({"temperature": r[0], "tint": r[1]})
            self.panel.p["wb_mode"] = "auto"

    def _auto_tone(self):
        r = self.session.auto_tone()
        if r:
            self.panel._set_values(r)

    def _reset(self):
        self.panel.set_params(defaults())
        self.timer.start()

    def _restore_visibility(self):
        if self.before.isChecked():
            self.session.node.setVisible(True)

    def accept(self):
        self.timer.stop()
        self._restore_visibility()
        try:
            self.session.apply(self.panel.effective_params())
        except Exception as e:
            print("[ps_tools] camera raw: %s" % e)
        super(CameraRawDialog, self).accept()

    def reject(self):
        self.timer.stop()
        self._restore_visibility()
        self.session.cancel()
        super(CameraRawDialog, self).reject()


_open = []


def _show(session):
    parent = None
    try:
        parent = Krita.instance().activeWindow().qwindow()
    except Exception:
        pass
    dlg = CameraRawDialog(session, parent)
    _open.append(dlg)
    dlg.finished.connect(lambda _r, d=dlg: _open.remove(d) if d in _open else None)
    dlg.show()
    dlg.raise_()
    return dlg


def _need_filter():
    if available():
        return True
    QMessageBox.information(None, u"Camera Raw Filter",
                            u"The Camera Raw Filter needs Krita PS (this repository's build of "
                            u"Krita) - its pixels are computed by a C++ filter.")
    return False


def open_filter():
    """Filter > Camera Raw Filter... (Shift+Ctrl+A): edits the selected
    Camera Raw layer / mask, or adds a Camera Raw filter mask to the active
    layer (a "smart filter" that can be changed later)."""
    if not _need_filter():
        return None
    doc = Krita.instance().activeDocument()
    if doc is None:
        return None
    node = doc.activeNode()
    if node is None:
        return None
    if is_camera_raw(node):
        return _show(Session(doc, node, False))
    if core.is_mask(node):
        node = node.parentNode()
    for c in node.childNodes():
        if is_camera_raw(c):
            doc.setActiveNode(c)
            return _show(Session(doc, c, False))
    if node.type() not in ("paintlayer", "grouplayer", "filelayer", "vectorlayer", "clonelayer",
                           "filllayer"):
        QMessageBox.information(None, u"Camera Raw Filter",
                                u"Select a layer to add the Camera Raw Filter to.")
        return None
    f = Krita.instance().filter(FILTER_ID)
    f.setConfiguration(f.configuration())
    sel = Selection()
    sel.select(0, 0, doc.width(), doc.height(), 255)
    mask = doc.createFilterMask(MASK_NAME, f, sel)
    node.addChildNode(mask, None)
    apply_to_node(mask, defaults())
    doc.setActiveNode(mask)
    return _show(Session(doc, mask, True))


def open_layer(node, created=False):
    """The dialog for a Camera Raw adjustment layer."""
    if not _need_filter():
        return None
    doc = Krita.instance().activeDocument()
    if doc is None or node is None:
        return None
    return _show(Session(doc, node, created))
