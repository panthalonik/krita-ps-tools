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
from PyQt5.QtCore import QPointF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PyQt5.QtWidgets import (QAbstractSpinBox, QButtonGroup, QCheckBox, QComboBox, QDialog,
                             QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                             QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget)

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
    "temperature": 0, "tint": 0, "vibrance": 0, "saturation": 0,
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


# ================================================================= widgets
class CrSlider(QWidget):
    """Camera Raw slider: name and value above, a gradient slider below.
    Double-click on the slider (or the name) resets it."""
    valueChanged = pyqtSignal(object)

    def __init__(self, label, vmin, vmax, default=0, decimals=0, gradient=None, parent=None):
        super(CrSlider, self).__init__(parent)
        self.default = default
        self.decimals = decimals
        self.scale = 10 ** decimals
        self._block = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(0)
        top = QHBoxLayout()
        self.label = QLabel(label)
        top.addWidget(self.label)
        top.addStretch(1)
        if decimals:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(decimals)
            self.spin.setSingleStep(1.0 / self.scale)
            self.spin.setRange(vmin, vmax)
        else:
            self.spin = QSpinBox()
            self.spin.setRange(int(vmin), int(vmax))
        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignRight)
        self.spin.setFixedWidth(52)
        self.spin.setKeyboardTracking(False)
        self.spin.setFrame(False)
        top.addWidget(self.spin)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(round(vmin * self.scale)), int(round(vmax * self.scale)))
        self.slider.setPageStep(max(1, int((vmax - vmin) * self.scale / 20)))
        stops = gradient or ["#5a5a5a", "#5a5a5a"]
        css = ", ".join("stop:%s %s" % (i / float(len(stops) - 1), c) for i, c in enumerate(stops))
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; border-radius: 2px;"
            " background: qlineargradient(x1:0, y1:0, x2:1, y2:0, %s); }"
            "QSlider::handle:horizontal { width: 9px; height: 9px; margin: -4px 0;"
            " border-radius: 5px; background: #e6e6e6; border: 1px solid #333; }" % css)
        lay.addWidget(self.slider)
        self.slider.installEventFilter(self)
        self.label.installEventFilter(self)
        self.setValue(default)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)

    def eventFilter(self, obj, e):
        if e.type() == e.MouseButtonDblClick:
            self.setValue(self.default)
            self.valueChanged.emit(self.value())
            return True
        return False

    def value(self):
        v = self.spin.value()
        return float(v) if self.decimals else int(v)

    def setValue(self, v):
        self._block = True
        try:
            if self.decimals:
                self.spin.setValue(float(v))
            else:
                self.spin.setValue(int(round(float(v))))
            self.slider.setValue(int(round(float(v) * self.scale)))
        finally:
            self._block = False

    def _from_slider(self, iv):
        if self._block:
            return
        self._block = True
        try:
            v = iv / float(self.scale)
            self.spin.setValue(v if self.decimals else int(round(v)))
        finally:
            self._block = False
        self.valueChanged.emit(self.value())

    def _from_spin(self, v):
        if self._block:
            return
        self._block = True
        try:
            self.slider.setValue(int(round(float(v) * self.scale)))
        finally:
            self._block = False
        self.valueChanged.emit(self.value())


class Section(QWidget):
    """A collapsible section (Camera Raw's panel headers)."""

    def __init__(self, title, open_=True, parent=None):
        super(Section, self).__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 2)
        lay.setSpacing(2)
        self.head = QToolButton()
        self.head.setText(title)
        self.head.setCheckable(True)
        self.head.setChecked(open_)
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.setArrowType(Qt.DownArrow if open_ else Qt.RightArrow)
        self.head.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self.head.toggled.connect(self._toggle)
        lay.addWidget(self.head)
        self.body = QWidget()
        self.box = QVBoxLayout(self.body)
        self.box.setContentsMargins(8, 0, 2, 0)
        self.box.setSpacing(2)
        lay.addWidget(self.body)
        self.body.setVisible(open_)

    def _toggle(self, on):
        self.head.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)


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
            # Qt's conical gradient turns counter-clockwise from 3 o'clock
            g.setColorAt(i / 12.0, QColor.fromHsvF((i / 12.0) % 1.0, 1.0, 1.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(c, r, r)
        rg = QRadialGradient(c, r)
        rg.setColorAt(0, QColor(128, 128, 128, 255))
        rg.setColorAt(1, QColor(128, 128, 128, 0))
        p.setBrush(QBrush(rg))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor(30, 30, 30), 1))
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


# ================================================================= panel
GRAD_TEMP = ["#3a6fd8", "#d8d8d8", "#e2c13a"]
GRAD_TINT = ["#3fbf3f", "#d8d8d8", "#c83fc8"]
GRAD_GREY = ["#1a1a1a", "#f0f0f0"]
GRAD_SAT = ["#808080", "#e04040"]


class CameraRawPanel(QWidget):
    """The four tabs. changed: the params changed (emitted while dragging)."""
    changed = pyqtSignal()

    def __init__(self, parent=None, compact=False):
        super(CameraRawPanel, self).__init__(parent)
        self.p = defaults()
        self.sliders = {}
        self._loading = False
        self.on_auto_tone = None          # callbacks set by the dialog / docker
        self.on_auto_wb = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        lay.addWidget(self.tabs)
        self.tabs.addTab(self._scroll(self._light()), u"Light")
        self.tabs.addTab(self._scroll(self._color()), u"Color")
        self.tabs.addTab(self._scroll(self._effects()), u"Effects")
        self.tabs.addTab(self._scroll(self._detail()), u"Detail")

    # -- building blocks
    def _scroll(self, w):
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(w)
        return sc

    def _page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(2)
        return w, v

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

    # -- Light
    def _light(self):
        w, v = self._page()
        top = QHBoxLayout()
        top.addStretch(1)
        b = QPushButton(u"Auto")
        b.setToolTip(u"Automatic exposure, contrast, highlights, shadows, whites and blacks")
        b.clicked.connect(lambda: self.on_auto_tone and self.on_auto_tone())
        top.addWidget(b)
        v.addLayout(top)
        self._slider(v, "exposure", u"Exposure", -5, 5, 2, GRAD_GREY)
        self._slider(v, "contrast", u"Contrast", -100, 100, 0, ["#606060", "#d0d0d0"])
        self._slider(v, "highlights", u"Highlights", -100, 100, 0, ["#606060", "#f0f0f0"])
        self._slider(v, "shadows", u"Shadows", -100, 100, 0, ["#101010", "#909090"])
        self._slider(v, "whites", u"Whites", -100, 100, 0, ["#909090", "#ffffff"])
        self._slider(v, "blacks", u"Blacks", -100, 100, 0, ["#000000", "#707070"])
        sec = Section(u"Curve", True)
        mode = QHBoxLayout()
        self.curve_mode = QComboBox()
        for text in (u"Parametric", u"Point: RGB", u"Point: Red", u"Point: Green", u"Point: Blue"):
            self.curve_mode.addItem(text)
        mode.addWidget(self.curve_mode, 1)
        reset = QPushButton(u"Reset")
        reset.clicked.connect(self._reset_curve)
        mode.addWidget(reset)
        sec.box.addLayout(mode)
        self.curve_stack = QStackedWidget()
        para = QWidget()
        pv = QVBoxLayout(para)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(2)
        self._slider(pv, "pc_highlights", u"Highlights", -100, 100)
        self._slider(pv, "pc_lights", u"Lights", -100, 100)
        self._slider(pv, "pc_darks", u"Darks", -100, 100)
        self._slider(pv, "pc_shadows", u"Shadows", -100, 100)
        lab = QLabel(u"Region split points")
        lab.setStyleSheet("color: palette(mid);")
        pv.addWidget(lab)
        self._slider(pv, "pc_split1", u"Shadows / Darks", 5, 90)
        self._slider(pv, "pc_split2", u"Darks / Lights", 10, 95)
        self._slider(pv, "pc_split3", u"Lights / Highlights", 15, 98)
        self.curve_stack.addWidget(para)
        from .widgets import CurveView
        self.curve_view = CurveView()
        self.curve_view.setMinimumSize(220, 220)
        self.curve_view.changed.connect(self._curve_changed)
        self.curve_stack.addWidget(self.curve_view)
        sec.box.addWidget(self.curve_stack)
        hint = QLabel(u"Point curve: click to add a point, drag it, Delete removes it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        sec.box.addWidget(hint)
        self.curve_mode.currentIndexChanged.connect(self._curve_mode)
        v.addWidget(sec)
        v.addStretch(1)
        return w

    def _curve_key(self):
        i = self.curve_mode.currentIndex()
        return CURVES[i - 1] if i > 0 else None

    def _curve_mode(self, i):
        self.curve_stack.setCurrentIndex(0 if i == 0 else 1)
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
        self.changed.emit()

    # -- Color
    def _color(self):
        w, v = self._page()
        wb = QHBoxLayout()
        wb.addWidget(QLabel(u"White balance"))
        self.wb_mode = QComboBox()
        self.wb_mode.addItem(u"As Shot", "as_shot")
        self.wb_mode.addItem(u"Auto", "auto")
        self.wb_mode.addItem(u"Custom", "custom")
        wb.addWidget(self.wb_mode, 1)
        v.addLayout(wb)
        self.wb_mode.activated.connect(self._wb_mode)
        self._slider(v, "temperature", u"Temperature", -100, 100, 0, GRAD_TEMP)
        self._slider(v, "tint", u"Tint", -100, 100, 0, GRAD_TINT)
        self.sliders["temperature"].valueChanged.connect(self._wb_custom)
        self.sliders["tint"].valueChanged.connect(self._wb_custom)
        fg = QPushButton(u"Neutral = foreground colour")
        fg.setToolTip(u"White balance picker: pick a colour that should be neutral grey with the "
                      u"colour picker (P) first, then click here")
        fg.clicked.connect(self._wb_from_foreground)
        v.addWidget(fg)
        self._slider(v, "vibrance", u"Vibrance", -100, 100, 0, ["#909090", "#d04080"])
        self._slider(v, "saturation", u"Saturation", -100, 100, 0, ["#909090", "#ff3030"])

        mixer = Section(u"Color Mixer", True)
        self.mix_tabs = QTabWidget()
        self.mix_tabs.setDocumentMode(True)
        for kind, title in (("hue", u"Hue"), ("sat", u"Saturation"), ("lum", u"Luminance")):
            page = QWidget()
            pv = QVBoxLayout(page)
            pv.setContentsMargins(0, 2, 0, 0)
            pv.setSpacing(1)
            for i, c in enumerate(COLORS):
                if kind == "hue":
                    prev = COLOR_SWATCH[(i - 1) % 8] if i else "#e03f8f"
                    nxt = COLOR_SWATCH[(i + 1) % 8]
                    grad = [prev, COLOR_SWATCH[i], nxt]
                elif kind == "sat":
                    grad = ["#808080", COLOR_SWATCH[i]]
                else:
                    grad = ["#101010", COLOR_SWATCH[i], "#f8f8f8"]
                self._slider(pv, "mix_%s_%s" % (kind, c), COLOR_LABELS[i], -100, 100, 0, grad)
            pv.addStretch(1)
            self.mix_tabs.addTab(page, title)
        mixer.box.addWidget(self.mix_tabs)
        v.addWidget(mixer)

        grading = Section(u"Color Grading", True)
        zrow = QGridLayout()
        zrow.setSpacing(2)
        self.zone_buttons = QButtonGroup(self)
        for i, z in enumerate(ZONE_LABELS):
            b = QPushButton(z)
            b.setCheckable(True)
            b.setChecked(i == 1)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.zone_buttons.addButton(b, i)
            zrow.addWidget(b, i // 2, i % 2)
        grading.box.addLayout(zrow)
        self.wheel = ColorWheel()
        self.wheel.changed.connect(self._wheel)
        grading.box.addWidget(self.wheel)
        self.zone_hue = CrSlider(u"Hue", 0, 359, 0, 0,
                                 ["#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff",
                                  "#ff0000"])
        self.zone_sat = CrSlider(u"Saturation", 0, 100, 0, 0, ["#808080", "#ff8040"])
        self.zone_lum = CrSlider(u"Luminance", -100, 100, 0, 0, ["#101010", "#f0f0f0"])
        for s in (self.zone_hue, self.zone_sat, self.zone_lum):
            s.valueChanged.connect(self._zone_slider)
            grading.box.addWidget(s)
        self._slider(grading.box, "cg_blending", u"Blending", 0, 100)
        self._slider(grading.box, "cg_balance", u"Balance", -100, 100, 0, ["#202020", "#e0e0e0"])
        self.zone_buttons.buttonClicked.connect(lambda _b: self._zone_load())
        v.addWidget(grading)
        v.addStretch(1)
        self._zone_load()
        return w

    def _zone(self):
        return ZONES[max(0, self.zone_buttons.checkedId())]

    def _zone_load(self):
        z = self._zone()
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

    def _wb_custom(self, _v):
        if not self._loading:
            self.p["wb_mode"] = "custom"
            i = self.wb_mode.findData("custom")
            self.wb_mode.setCurrentIndex(i)

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

    # -- Effects
    def _effects(self):
        w, v = self._page()
        self._slider(v, "texture", u"Texture", -100, 100, 0, ["#707070", "#c0c0c0"])
        self._slider(v, "clarity", u"Clarity", -100, 100, 0, ["#707070", "#c0c0c0"])
        self._slider(v, "dehaze", u"Dehaze", -100, 100, 0, ["#d0d0d0", "#505060"])
        vig = Section(u"Vignette", True)
        row = QHBoxLayout()
        row.addWidget(QLabel(u"Style"))
        self.vig_style = QComboBox()
        for text in (u"Highlight Priority", u"Color Priority", u"Paint Overlay"):
            self.vig_style.addItem(text)
        self.vig_style.currentIndexChanged.connect(lambda i: self._set("vig_style", int(i)))
        row.addWidget(self.vig_style, 1)
        vig.box.addLayout(row)
        self._slider(vig.box, "vig_amount", u"Amount", -100, 100, 0, ["#101010", "#909090", "#f8f8f8"])
        self._slider(vig.box, "vig_midpoint", u"Midpoint", 0, 100)
        self._slider(vig.box, "vig_roundness", u"Roundness", -100, 100)
        self._slider(vig.box, "vig_feather", u"Feather", 0, 100)
        self._slider(vig.box, "vig_highlights", u"Highlights", 0, 100)
        v.addWidget(vig)
        grain = Section(u"Grain", True)
        self._slider(grain.box, "grain_amount", u"Amount", 0, 100)
        self._slider(grain.box, "grain_size", u"Size", 0, 100)
        self._slider(grain.box, "grain_roughness", u"Roughness", 0, 100)
        v.addWidget(grain)
        v.addStretch(1)
        return w

    # -- Detail
    def _detail(self):
        w, v = self._page()
        sh = Section(u"Sharpening", True)
        self._slider(sh.box, "sharp_amount", u"Sharpening", 0, 150)
        self._slider(sh.box, "sharp_radius", u"Radius", 0.5, 3.0, 1)
        self._slider(sh.box, "sharp_detail", u"Detail", 0, 100)
        self._slider(sh.box, "sharp_masking", u"Masking", 0, 100)
        v.addWidget(sh)
        nr = Section(u"Noise Reduction", True)
        self._slider(nr.box, "nr_lum", u"Noise Reduction", 0, 100)
        self._slider(nr.box, "nr_lum_detail", u"Detail", 0, 100)
        self._slider(nr.box, "nr_lum_contrast", u"Contrast", 0, 100)
        self._slider(nr.box, "nr_color", u"Color Noise Reduction", 0, 100)
        self._slider(nr.box, "nr_color_detail", u"Detail", 0, 100)
        self._slider(nr.box, "nr_color_smooth", u"Smoothness", 0, 100)
        v.addWidget(nr)
        hint = QLabel(u"Tip: judge sharpening and noise reduction at 100% zoom.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid);")
        v.addWidget(hint)
        v.addStretch(1)
        return w

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
            self._curve_mode(self.curve_mode.currentIndex())
            self._zone_load()
        finally:
            self._loading = False

    def params(self):
        import json
        return json.loads(json.dumps(self.p))


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


class CameraRawDialog(QDialog):
    def __init__(self, session, parent=None):
        super(CameraRawDialog, self).__init__(parent)
        self.session = session
        self.setWindowTitle(u"Camera Raw Filter - %s" % session.node.name())
        self.setModal(False)
        lay = QVBoxLayout(self)
        self.panel = CameraRawPanel(self)
        self.panel.set_params(session.p)
        self.panel.on_auto_wb = self._auto_wb
        self.panel.on_auto_tone = self._auto_tone
        lay.addWidget(self.panel, 1)
        row = QHBoxLayout()
        self.preview = QCheckBox(u"Preview")
        self.preview.setChecked(True)
        self.preview.setToolTip(u"Before / after (P)")
        self.preview.toggled.connect(self._preview)
        row.addWidget(self.preview)
        reset = QPushButton(u"Reset All")
        reset.clicked.connect(self._reset)
        row.addWidget(reset)
        row.addStretch(1)
        cancel = QPushButton(u"Cancel")
        ok = QPushButton(u"OK")
        ok.setDefault(True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self._apply)
        self.panel.changed.connect(self.timer.start)
        self.resize(400, 760)

    def _apply(self):
        try:
            self.session.apply(self.panel.params())
        except Exception as e:
            print("[ps_tools] camera raw: %s" % e)

    def _preview(self, on):
        try:
            self.session.node.setVisible(on)
            self.session.doc.refreshProjection()
        except Exception:
            pass

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_P and not e.modifiers():
            self.preview.toggle()
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

    def accept(self):
        self.timer.stop()
        self._apply()
        if not self.preview.isChecked():
            self.session.node.setVisible(True)
        super(CameraRawDialog, self).accept()

    def reject(self):
        self.timer.stop()
        if not self.preview.isChecked():
            self.session.node.setVisible(True)
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
