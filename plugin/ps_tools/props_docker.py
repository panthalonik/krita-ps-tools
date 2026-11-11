# -*- coding: utf-8 -*-
"""Photoshop-like Properties panel for adjustment / fill layers."""
import math
import os
import traceback

from krita import DockWidget, Krita
from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox,
                             QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                             QMessageBox, QPushButton, QRadioButton, QScrollArea,
                             QSpinBox, QStackedWidget, QToolButton, QVBoxLayout,
                             QWidget)

from . import adjustments as A
from . import clip, core
from .widgets import (ColorButton, CurveView, HandleBar, HistogramView, SliderRow,
                      adjustment_icon, clip_icon, make_icon)

DOCKER_ID = "ps_tools_properties"


# ================================================================= editors
class Editor(QWidget):
    changed = pyqtSignal()
    needs_histogram = False

    def __init__(self, parent=None):
        super(Editor, self).__init__(parent)
        self._loading = False
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)

    def set_params(self, p):
        self._loading = True
        try:
            self.load(p)
        finally:
            self._loading = False

    def emit(self, *_a):
        if not self._loading:
            self.changed.emit()

    def load(self, p):
        pass

    def params(self):
        return {}

    def set_histogram(self, h):
        pass


class NoteEditor(Editor):
    def __init__(self, text, parent=None):
        super(NoteEditor, self).__init__(parent)
        lab = QLabel(text)
        lab.setWordWrap(True)
        self.lay.addWidget(lab)
        self.lay.addStretch(1)


class LevelsEditor(Editor):
    needs_histogram = True

    def __init__(self, parent=None):
        super(LevelsEditor, self).__init__(parent)
        self.hist = HistogramView()
        self.lay.addWidget(self.hist)
        self.inbar = HandleBar(["#000000", "#808080", "#ffffff"])
        self.lay.addWidget(self.inbar)
        row = QHBoxLayout()
        self.sb_black = QSpinBox(); self.sb_black.setRange(0, 253)
        self.sb_gamma = QDoubleSpinBox(); self.sb_gamma.setRange(0.01, 9.99)
        self.sb_gamma.setDecimals(2); self.sb_gamma.setSingleStep(0.05)
        self.sb_white = QSpinBox(); self.sb_white.setRange(2, 255)
        for w in (self.sb_black, self.sb_gamma, self.sb_white):
            w.setKeyboardTracking(False)
        row.addWidget(self.sb_black); row.addStretch(1)
        row.addWidget(self.sb_gamma); row.addStretch(1)
        row.addWidget(self.sb_white)
        self.lay.addLayout(row)
        self.lay.addWidget(QLabel(u"Output Levels:"))
        self.outbar = HandleBar(["#000000", "#ffffff"])
        self.lay.addWidget(self.outbar)
        row2 = QHBoxLayout()
        self.sb_oblack = QSpinBox(); self.sb_oblack.setRange(0, 255)
        self.sb_owhite = QSpinBox(); self.sb_owhite.setRange(0, 255)
        for w in (self.sb_oblack, self.sb_owhite):
            w.setKeyboardTracking(False)
        row2.addWidget(self.sb_oblack); row2.addStretch(1); row2.addWidget(self.sb_owhite)
        self.lay.addLayout(row2)
        self.lay.addStretch(1)
        self.p = A.defaults("levels")
        self.inbar.handleMoved.connect(self._in_moved)
        self.outbar.handleMoved.connect(self._out_moved)
        for w in (self.sb_black, self.sb_gamma, self.sb_white, self.sb_oblack, self.sb_owhite):
            w.valueChanged.connect(self._spins)

    def _mid(self):
        b, w, g = self.p["in_black"], self.p["in_white"], self.p["gamma"]
        return b + (w - b) * (0.5 ** g)

    def _sync(self):
        self._loading, was = True, self._loading
        self.sb_black.setValue(self.p["in_black"])
        self.sb_white.setValue(self.p["in_white"])
        self.sb_gamma.setValue(self.p["gamma"])
        self.sb_oblack.setValue(self.p["out_black"])
        self.sb_owhite.setValue(self.p["out_white"])
        self.inbar.setValues([self.p["in_black"], self._mid(), self.p["in_white"]])
        self.outbar.setValues([self.p["out_black"], self.p["out_white"]])
        self._loading = was

    def load(self, p):
        self.p = dict(p)
        self._sync()

    def params(self):
        return dict(self.p)

    def set_histogram(self, h):
        self.hist.setCounts(h.get("rgb") if h else None)

    def _in_moved(self, i, v):
        b, w = self.p["in_black"], self.p["in_white"]
        if i == 0:
            self.p["in_black"] = int(min(v, w - 2))
        elif i == 2:
            self.p["in_white"] = int(max(v, b + 2))
        else:
            t = (v - b) / float(max(1, w - b))
            t = min(0.99, max(0.01, t))
            self.p["gamma"] = round(min(9.99, max(0.01, math.log(t) / math.log(0.5))), 2)
        self._sync()
        self.emit()

    def _out_moved(self, i, v):
        self.p["out_black" if i == 0 else "out_white"] = int(v)
        self._sync()
        self.emit()

    def _spins(self, *_a):
        if self._loading:
            return
        self.p.update(in_black=self.sb_black.value(), in_white=self.sb_white.value(),
                      gamma=self.sb_gamma.value(), out_black=self.sb_oblack.value(),
                      out_white=self.sb_owhite.value())
        if self.p["in_white"] <= self.p["in_black"]:
            self.p["in_white"] = self.p["in_black"] + 2
        self._sync()
        self.emit()


class CurvesEditor(Editor):
    needs_histogram = True
    COLORS = {"rgb": QColor(230, 230, 230), "red": QColor(255, 70, 70),
              "green": QColor(70, 220, 70), "blue": QColor(80, 140, 255)}

    def __init__(self, parent=None):
        super(CurvesEditor, self).__init__(parent)
        top = QHBoxLayout()
        top.addWidget(QLabel(u"Channel:"))
        self.combo = QComboBox()
        for key, label in (("rgb", "RGB"), ("red", "Red"), ("green", "Green"), ("blue", "Blue")):
            self.combo.addItem(label, key)
        top.addWidget(self.combo)
        top.addStretch(1)
        self.lay.addLayout(top)
        self.view = CurveView()
        self.lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel(u"Input:"))
        self.sb_in = QSpinBox(); self.sb_in.setRange(0, 255)
        row.addWidget(self.sb_in)
        row.addWidget(QLabel(u"Output:"))
        self.sb_out = QSpinBox(); self.sb_out.setRange(0, 255)
        row.addWidget(self.sb_out)
        row.addStretch(1)
        self.lay.addLayout(row)
        hint = QLabel(u"Click to add a point, drag it out of the graph (or press Delete) "
                      u"to remove it.")
        hint.setWordWrap(True)
        f = hint.font(); f.setPointSizeF(f.pointSizeF() * 0.9); f.setItalic(True)
        hint.setFont(f)
        self.lay.addWidget(hint)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color: #e0a030;")
        self.warn.hide()
        self.lay.addWidget(self.warn)
        self.p = A.defaults("curves")
        self.hist = None
        self.combo.currentIndexChanged.connect(self._channel)
        self.view.changed.connect(self._view_changed)
        self.view.selectionChanged.connect(self._sel)
        self.sb_in.valueChanged.connect(self._spins)
        self.sb_out.valueChanged.connect(self._spins)

    def _key(self):
        return self.combo.currentData()

    def load(self, p):
        self.p = {k: [list(pt) for pt in v] for k, v in p.items()}
        self._channel()

    def params(self):
        return {k: [list(pt) for pt in v] for k, v in self.p.items()}

    def set_histogram(self, h):
        self.hist = h
        self.view.counts = h.get(self._key()) if h else None
        self.view.update()

    def show_unsupported(self, on):
        self.warn.setText(u"This Krita version does not let plugins set curves "
                          u"(needs Krita 5.3+). Use \"Krita editor…\" below.")
        self.warn.setVisible(on)

    def _channel(self, *_a):
        k = self._key()
        self.view.color = self.COLORS[k]
        self.view.setPoints(self.p[k])
        self.view.counts = self.hist.get(k) if self.hist else None
        self._sel()

    def _view_changed(self):
        self.p[self._key()] = [list(pt) for pt in self.view.points]
        self.emit()

    def _sel(self):
        i = self.view.sel
        was = self._loading
        self._loading = True
        en = i is not None
        self.sb_in.setEnabled(en)
        self.sb_out.setEnabled(en)
        if en:
            x, y = self.view.points[i]
            self.sb_in.setValue(int(round(x * 255)))
            self.sb_out.setValue(int(round(y * 255)))
        self._loading = was

    def _spins(self, *_a):
        if self._loading or self.view.sel is None:
            return
        i = self.view.sel
        pts = self.view.points
        x = self.sb_in.value() / 255.0
        if i == 0:
            x = min(x, pts[1][0] - 0.01)
        elif i == len(pts) - 1:
            x = max(x, pts[-2][0] + 0.01)
        else:
            x = min(max(x, pts[i - 1][0] + 0.01), pts[i + 1][0] - 0.01)
        pts[i] = [x, self.sb_out.value() / 255.0]
        self.view.update()
        self._view_changed()


class SlidersEditor(Editor):
    """Generic editor made of SliderRows: spec = [(key, label, min, max, decimals)]."""

    def __init__(self, spec, parent=None, extra_top=None):
        super(SlidersEditor, self).__init__(parent)
        if extra_top is not None:
            self.lay.addWidget(extra_top)
        self.rows = {}
        for item in spec:
            key, label, vmin, vmax, dec = item[:5]
            kw = item[5] if len(item) > 5 else {}
            row = SliderRow(label, vmin, vmax, 0, dec, **kw)
            row.valueChanged.connect(self.emit)
            self.lay.addWidget(row)
            self.rows[key] = row
        self.extra = QVBoxLayout()
        self.lay.addLayout(self.extra)
        self.lay.addStretch(1)
        self.p = {}

    def load(self, p):
        self.p = dict(p)
        for k, row in self.rows.items():
            if k in p:
                row.setValue(p[k])

    def params(self):
        p = dict(self.p)
        for k, row in self.rows.items():
            v = row.value()
            p[k] = v if isinstance(row.spin, QDoubleSpinBox) else int(v)
        return p


class BrightnessContrastEditor(SlidersEditor):
    """PS Brightness/Contrast: the modern curve, or "Use Legacy" (linear)."""

    def __init__(self, parent=None):
        super(BrightnessContrastEditor, self).__init__([
            ("brightness", u"Brightness:", -150, 150, 0, {"gradient": ["#000000", "#ffffff"]}),
            ("contrast", u"Contrast:", -50, 100, 0, {"gradient": ["#808080", "#000000", "#ffffff"]}),
        ], parent)
        self.legacy = QCheckBox(u"Use Legacy")
        self.legacy.toggled.connect(self.emit)
        self.extra.addWidget(self.legacy)

    def load(self, p):
        super(BrightnessContrastEditor, self).load(p)
        self.legacy.setChecked(bool(p.get("legacy", False)))

    def params(self):
        p = super(BrightnessContrastEditor, self).params()
        p["legacy"] = self.legacy.isChecked()
        return p


class HueSatEditor(SlidersEditor):
    def __init__(self, parent=None):
        super(HueSatEditor, self).__init__([
            ("hue", u"Hue:", -180, 180, 0, {"gradient": ["#00ffff", "#0000ff", "#ff00ff", "#ff0000", "#ffff00", "#00ff00", "#00ffff"]}),
            ("saturation", u"Saturation:", -100, 100, 0, {"gradient": ["#808080", "#ff3030"]}),
            ("lightness", u"Lightness:", -100, 100, 0, {"gradient": ["#000000", "#ffffff"]}),
        ], parent)
        self.colorize = QCheckBox(u"Colorize")
        self.extra.addWidget(self.colorize)
        self.colorize.toggled.connect(self._colorize)

    def _colorize(self, on):
        if self._loading:
            return
        if on:
            self.rows["hue"].setRange(0, 360)
            self.rows["saturation"].setRange(0, 100)
            self.rows["hue"].setValue(30)
            self.rows["saturation"].setValue(25)
        else:
            self.rows["hue"].setRange(-180, 180)
            self.rows["saturation"].setRange(-100, 100)
            self.rows["hue"].setValue(0)
            self.rows["saturation"].setValue(0)
        self.emit()

    def load(self, p):
        on = bool(p.get("colorize"))
        self.colorize.setChecked(on)
        self.rows["hue"].setRange(0 if on else -180, 360 if on else 180)
        self.rows["saturation"].setRange(0 if on else -100, 100)
        SlidersEditor.load(self, p)

    def params(self):
        p = SlidersEditor.params(self)
        p["colorize"] = self.colorize.isChecked()
        return p


class ColorBalanceEditor(Editor):
    def __init__(self, parent=None):
        super(ColorBalanceEditor, self).__init__(parent)
        tone = QHBoxLayout()
        tone.addWidget(QLabel(u"Tone:"))
        self.tones = QComboBox()
        for key, label in (("midtones", "Midtones"), ("shadows", "Shadows"),
                           ("highlights", "Highlights")):
            self.tones.addItem(label, key)
        tone.addWidget(self.tones)
        tone.addStretch(1)
        self.lay.addLayout(tone)
        self.rows = [
            SliderRow("", -100, 100, 0, 0, left=u"Cyan", right=u"Red",
                      gradient=["#00ffff", "#ff0000"]),
            SliderRow("", -100, 100, 0, 0, left=u"Magenta", right=u"Green",
                      gradient=["#ff00ff", "#00ff00"]),
            SliderRow("", -100, 100, 0, 0, left=u"Yellow", right=u"Blue",
                      gradient=["#ffff00", "#0000ff"]),
        ]
        for r in self.rows:
            self.lay.addWidget(r)
            r.valueChanged.connect(self._slid)
        self.preserve = QCheckBox(u"Preserve Luminosity")
        self.lay.addWidget(self.preserve)
        self.lay.addStretch(1)
        self.p = A.defaults("color_balance")
        self.tones.currentIndexChanged.connect(self._tone)
        self.preserve.toggled.connect(self._pres)

    def _tone(self, *_a):
        was = self._loading
        self._loading = True
        for r, v in zip(self.rows, self.p[self.tones.currentData()]):
            r.setValue(v)
        self._loading = was

    def load(self, p):
        self.p = {k: (list(v) if isinstance(v, list) else v) for k, v in p.items()}
        self.preserve.setChecked(bool(p.get("preserve", True)))
        self._tone()

    def _slid(self, *_a):
        if self._loading:
            return
        self.p[self.tones.currentData()] = [int(r.value()) for r in self.rows]
        self.emit()

    def _pres(self, on):
        if self._loading:
            return
        self.p["preserve"] = on
        self.emit()

    def params(self):
        return {k: (list(v) if isinstance(v, list) else v) for k, v in self.p.items()}


class BlackWhiteEditor(Editor):
    MODES = [(1, u"Luminosity (BT.709)"), (2, u"Luminosity (BT.601)"), (0, u"Lightness"),
             (3, u"Average"), (4, u"Minimum (darkest channel)"), (5, u"Maximum (brightest channel)")]

    def __init__(self, parent=None):
        super(BlackWhiteEditor, self).__init__(parent)
        self.lay.addWidget(QLabel(u"Conversion:"))
        self.combo = QComboBox()
        for v, label in self.MODES:
            self.combo.addItem(label, v)
        self.lay.addWidget(self.combo)
        note = QLabel(u"Krita has no per-color sliders like PS Black & White. "
                      u"For a tinted look add Photo Filter or Hue/Saturation (Colorize) above.")
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.lay.addStretch(1)
        self.combo.currentIndexChanged.connect(self.emit)

    def load(self, p):
        i = self.combo.findData(int(p.get("mode", 1)))
        self.combo.setCurrentIndex(max(0, i))

    def params(self):
        return {"mode": self.combo.currentData()}


class PhotoFilterEditor(Editor):
    def __init__(self, parent=None):
        super(PhotoFilterEditor, self).__init__(parent)
        self.r_filter = QRadioButton(u"Filter:")
        self.r_color = QRadioButton(u"Color:")
        grp = QButtonGroup(self)
        grp.addButton(self.r_filter)
        grp.addButton(self.r_color)
        row = QHBoxLayout()
        row.addWidget(self.r_filter)
        self.combo = QComboBox()
        for name, col in A.PHOTO_FILTERS:
            self.combo.addItem(name, col)
        row.addWidget(self.combo, 1)
        self.lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(self.r_color)
        self.btn = ColorButton()
        row2.addWidget(self.btn, 1)
        self.lay.addLayout(row2)
        self.density = SliderRow(u"Density:", 0, 100, 25, 0)
        self.lay.addWidget(self.density)
        self.preserve = QCheckBox(u"Preserve Luminosity")
        self.lay.addWidget(self.preserve)
        self.lay.addStretch(1)
        self.combo.currentIndexChanged.connect(self._preset)
        self.btn.colorChanged.connect(self._color)
        self.density.valueChanged.connect(self.emit)
        self.preserve.toggled.connect(self.emit)
        self.r_filter.toggled.connect(self._mode)

    def _preset(self, *_a):
        if self._loading:
            return
        self.r_filter.setChecked(True)
        self.btn.setColor(self.combo.currentData())
        self.emit()

    def _color(self, _c):
        self.r_color.setChecked(True)
        self.emit()

    def _mode(self, on):
        if self._loading:
            return
        if on:
            self.btn.setColor(self.combo.currentData())
            self.emit()

    def load(self, p):
        col = QColor(p.get("color", "#EC8A00")).name().upper()
        i = next((j for j, (_n, c) in enumerate(A.PHOTO_FILTERS) if c.upper() == col), -1)
        if i >= 0:
            self.combo.setCurrentIndex(i)
            self.r_filter.setChecked(True)
        else:
            self.r_color.setChecked(True)
        self.btn.setColor(col)
        self.density.setValue(p.get("density", 25))
        self.preserve.setChecked(bool(p.get("preserve", True)))

    def params(self):
        return {"color": self.btn.color(), "density": int(self.density.value()),
                "preserve": self.preserve.isChecked()}


class ColorFillEditor(Editor):
    def __init__(self, parent=None):
        super(ColorFillEditor, self).__init__(parent)
        self.lay.addWidget(QLabel(u"Color:"))
        self.btn = ColorButton()
        self.lay.addWidget(self.btn)
        self.lay.addStretch(1)
        self.btn.colorChanged.connect(self.emit)

    def load(self, p):
        self.btn.setColor(p.get("color", "#808080"))

    def params(self):
        return {"color": self.btn.color()}


class ThresholdEditor(SlidersEditor):
    needs_histogram = True

    def __init__(self, parent=None):
        self.hist = HistogramView()
        super(ThresholdEditor, self).__init__(
            [("level", u"Threshold Level:", 1, 255, 0)], parent, extra_top=self.hist)

    def set_histogram(self, h):
        self.hist.setCounts(h.get("rgb") if h else None)


class GenericEditor(Editor):
    """Fallback for any other Krita filter layer: numbers and checkboxes."""

    def __init__(self, parent=None):
        super(GenericEditor, self).__init__(parent)
        self.form = QVBoxLayout()
        self.lay.addLayout(self.form)
        self.lay.addStretch(1)
        self.widgets = {}
        self.props = {}

    def load(self, props):
        while self.form.count():
            w = self.form.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.widgets = {}
        self.props = dict(props)
        for k in sorted(props):
            v = props[k]
            if isinstance(v, bool):
                w = QCheckBox(k)
                w.setChecked(v)
                w.toggled.connect(self.emit)
            elif isinstance(v, int):
                w = SliderRow(k, -100000, 100000, v, 0)
                w.slider.hide()
                w.valueChanged.connect(self.emit)
            elif isinstance(v, float):
                w = SliderRow(k, -100000.0, 100000.0, v, 3)
                w.slider.hide()
                w.valueChanged.connect(self.emit)
            else:
                continue
            self.form.addWidget(w)
            self.widgets[k] = w
        if not self.widgets:
            lab = QLabel(u"This layer has no simple settings. Use \"Krita editor…\".")
            lab.setWordWrap(True)
            self.form.addWidget(lab)

    def params(self):
        out = dict(self.props)
        for k, w in self.widgets.items():
            if isinstance(w, QCheckBox):
                out[k] = w.isChecked()
            else:
                v = w.value()
                out[k] = int(v) if isinstance(self.props[k], int) else float(v)
        return out

class ColorLookupEditor(Editor):
    """PS Color Lookup: pick a 3D LUT (.cube / .3dl / .look)."""

    def __init__(self, parent=None):
        super(ColorLookupEditor, self).__init__(parent)
        from . import baked, pixelops
        self.baked = baked
        self.lay.addWidget(QLabel(u"3DLUT File:"))
        self.combo = QComboBox()
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(12)
        self.lay.addWidget(self.combo)
        row = QHBoxLayout()
        self.b_load = QPushButton(u"Load 3D LUT…")
        self.b_dirs = QPushButton(u"LUT folders…")
        row.addWidget(self.b_load)
        row.addWidget(self.b_dirs)
        self.lay.addLayout(row)
        self.path = QLabel()
        self.path.setWordWrap(True)
        f = self.path.font()
        f.setPointSizeF(max(6.0, f.pointSizeF() * 0.85))
        self.path.setFont(f)
        self.path.setEnabled(False)
        self.lay.addWidget(self.path)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #e06c6c;")
        self.lay.addWidget(self.status)
        if pixelops.backend() != "native":
            note = QLabel(u"Note: the fast native module could not be loaded "
                          u"(%s mode) – large images will be slow." % pixelops.backend())
            note.setWordWrap(True)
            self.lay.addWidget(note)
        self.lay.addStretch(1)
        self.cur = {"lut": "", "name": ""}
        self.combo.currentIndexChanged.connect(self._picked)
        self.b_load.clicked.connect(self._load_file)
        self.b_dirs.clicked.connect(self._folders)

    def _fill(self):
        self._loading = True
        try:
            self.combo.clear()
            self.combo.addItem(u"None", "")
            luts = self.baked.available_luts()
            for name, path in luts:
                self.combo.addItem(name, path)
            path, name = self.cur.get("lut") or "", self.cur.get("name") or ""
            idx = 0
            if path or name:
                idx = self.combo.findData(path) if path else -1
                if idx < 0 and name:
                    idx = self.combo.findText(name)
                if idx < 0:
                    self.combo.addItem(u"%s (not in the LUT folders)" % (name or path), path)
                    idx = self.combo.count() - 1
            self.combo.setCurrentIndex(idx)
            if not luts:
                self.status.setText(u"No LUT folder yet: use \"LUT folders…\" to add the folder "
                                    u"with your .cube / .3dl / .look files (Photoshop's "
                                    u"Presets\\3DLUTs folder is found automatically).")
            else:
                self.status.setText(u"")
            self._show_path()
        finally:
            self._loading = False

    def _show_path(self):
        p = self.cur.get("lut") or ""
        self.path.setText(p)
        self.path.setToolTip(p)

    def load(self, p):
        cur = {"lut": p.get("lut", ""), "name": p.get("name", "")}
        if self.combo.view().isVisible():
            return                      # never rebuild the list while it is open
        if cur == self.cur and self.combo.count() > 0:
            return
        self.cur = cur
        self._fill()

    def params(self):
        return dict(self.cur)

    def show_error(self, text):
        self.status.setText(text or u"")

    def _picked(self, i):
        if self._loading or i < 0:
            return
        path = self.combo.itemData(i) or ""
        name = self.combo.itemText(i) if path else ""
        if name.endswith(u" (not in the LUT folders)"):
            name = name[:-len(u" (not in the LUT folders)")]
        self.cur = {"lut": path, "name": name}
        self.status.setText(u"")
        self._show_path()
        self.emit()

    def _load_file(self):
        from PyQt5.QtWidgets import QFileDialog
        start = os.path.dirname(self.cur.get("lut") or "") or \
            (self.baked.lut_dirs() or [os.path.expanduser("~")])[0]
        path, _f = QFileDialog.getOpenFileName(
            self, u"Load 3D LUT", start,
            u"3D LUT (*.cube *.CUBE *.3dl *.3DL *.look *.LOOK);;All files (*)")
        if not path:
            return
        path = os.path.normpath(path)
        self.baked.set_user_lut_dirs(self.baked.user_lut_dirs() + [os.path.dirname(path)])
        self.cur = {"lut": path, "name": os.path.splitext(os.path.basename(path))[0]}
        self._fill()
        self.emit()

    def _folders(self):
        from PyQt5.QtWidgets import QFileDialog, QMenu
        m = QMenu(self)
        a_add = m.addAction(u"Add LUT folder…")
        dirs = self.baked.user_lut_dirs()
        rem = {}
        if dirs:
            m.addSeparator()
            for d in dirs:
                rem[m.addAction(u"Remove: %s" % d)] = d
        auto = self.baked.photoshop_lut_dirs()
        if auto:
            m.addSeparator()
            for d in auto:
                a = m.addAction(u"Photoshop: %s" % d)
                a.setEnabled(False)
        act = m.exec_(self.b_dirs.mapToGlobal(self.b_dirs.rect().bottomLeft()))
        if act is None:
            return
        if act is a_add:
            d = QFileDialog.getExistingDirectory(self, u"Folder with 3D LUTs",
                                                 dirs[-1] if dirs else os.path.expanduser("~"))
            if d:
                self.baked.set_user_lut_dirs(dirs + [os.path.normpath(d)])
        elif act in rem:
            self.baked.set_user_lut_dirs([d for d in dirs if d != rem[act]])
        self._fill()


SELECTIVE_SWATCH = {"reds": "#ff0000", "yellows": "#ffff00", "greens": "#00ff00",
                    "cyans": "#00ffff", "blues": "#0000ff", "magentas": "#ff00ff",
                    "whites": "#ffffff", "neutrals": "#808080", "blacks": "#000000"}


class SelectiveColorEditor(Editor):
    def __init__(self, parent=None):
        super(SelectiveColorEditor, self).__init__(parent)
        from PyQt5.QtGui import QIcon, QPixmap
        row = QHBoxLayout()
        row.addWidget(QLabel(u"Colors:"))
        self.combo = QComboBox()
        for r in A.SELECTIVE_RANGES:
            pm = QPixmap(14, 14)
            pm.fill(QColor(SELECTIVE_SWATCH[r]))
            self.combo.addItem(QIcon(pm), r.capitalize(), r)
        row.addWidget(self.combo, 1)
        self.lay.addLayout(row)
        self.rows = []
        for label, grad in ((u"Cyan:", ["#ff4040", "#ffffff", "#00c8ff"]),
                            (u"Magenta:", ["#30d030", "#ffffff", "#ff30ff"]),
                            (u"Yellow:", ["#3050ff", "#ffffff", "#ffe000"]),
                            (u"Black:", ["#ffffff", "#000000"])):
            r = SliderRow(label, -100, 100, 0, 0, gradient=grad)
            r.valueChanged.connect(self._slid)
            self.lay.addWidget(r)
            self.rows.append(r)
        mrow = QHBoxLayout()
        self.r_rel = QRadioButton(u"Relative")
        self.r_abs = QRadioButton(u"Absolute")
        grp = QButtonGroup(self)
        grp.addButton(self.r_rel)
        grp.addButton(self.r_abs)
        mrow.addWidget(self.r_rel)
        mrow.addWidget(self.r_abs)
        mrow.addStretch(1)
        self.lay.addLayout(mrow)
        self.lay.addStretch(1)
        self.p = A.defaults("selective_color")
        self.combo.currentIndexChanged.connect(self._range)
        self.r_rel.toggled.connect(self._method)

    def _cur(self):
        return self.combo.currentData() or "reds"

    def _range(self, *_a):
        was = self._loading
        self._loading = True
        try:
            vals = self.p.get(self._cur()) or [0, 0, 0, 0]
            for r, v in zip(self.rows, vals):
                r.setValue(v)
        finally:
            self._loading = was

    def _slid(self, *_a):
        if self._loading:
            return
        self.p[self._cur()] = [int(r.value()) for r in self.rows]
        self.emit()

    def _method(self, *_a):
        if self._loading:
            return
        self.p["method"] = "relative" if self.r_rel.isChecked() else "absolute"
        self.emit()

    def load(self, p):
        self.p = A.defaults("selective_color")
        self.p.update(p or {})
        self.r_rel.setChecked(self.p.get("method", "relative") == "relative")
        self.r_abs.setChecked(self.p.get("method", "relative") != "relative")
        self._range()

    def params(self):
        return json_copy(self.p)


def json_copy(d):
    import json
    return json.loads(json.dumps(d))


def make_editor(kind):
    if kind == "levels":
        return LevelsEditor()
    if kind == "curves":
        return CurvesEditor()
    if kind == "brightness_contrast":
        return BrightnessContrastEditor()
    if kind == "exposure":
        return SlidersEditor([("exposure", u"Exposure:", -20, 20, 2,
                               {"gradient": ["#000000", "#ffffff"]}),
                              ("offset", u"Offset:", -0.5, 0.5, 4,
                               {"gradient": ["#000000", "#ffffff"]}),
                              ("gamma", u"Gamma Correction:", 0.01, 9.99, 2,
                               {"gradient": ["#ffffff", "#000000"]})])
    if kind == "hue_saturation":
        return HueSatEditor()
    if kind == "color_balance":
        return ColorBalanceEditor()
    if kind == "black_white":
        return BlackWhiteEditor()
    if kind == "photo_filter":
        return PhotoFilterEditor()
    if kind == "posterize":
        return SlidersEditor([("levels", u"Levels:", 2, 255, 0)])
    if kind == "threshold":
        return ThresholdEditor()
    if kind == "grain":
        return SlidersEditor([("amount", u"Amount:", 0, 100, 0),
                              ("intensity", u"Intensity:", 0, 100, 0)])
    if kind == "clarity":
        return SlidersEditor([("amount", u"Clarity:", 0, 100, 0),
                              ("radius", u"Radius (0 = auto):", 0, 200, 0)])
    if kind == "solid_color":
        return ColorFillEditor()
    if kind == "color_lookup":
        return ColorLookupEditor()
    if kind == "selective_color":
        return SelectiveColorEditor()
    if kind == "invert":
        return NoteEditor(u"Invert has no settings.")
    if kind == "gradient_map":
        return NoteEditor(u"Pick the gradient with \"Krita editor…\" below "
                          u"(Krita's gradient map editor).")
    if kind in ("gradient", "pattern", "generic_fill"):
        return NoteEditor(u"Edit this fill with \"Krita editor…\" below.")
    return GenericEditor()


# ================================================================== docker
class PropertiesDocker(DockWidget):
    def __init__(self):
        super(PropertiesDocker, self).__init__()
        self.setWindowTitle(u"Properties")
        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(6, 4, 6, 4)

        head = QHBoxLayout()
        self.icon = QLabel()
        self.title = QLabel(u"Properties")
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        head.addWidget(self.icon)
        head.addWidget(self.title, 1)
        v.addLayout(head)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        v.addWidget(line)

        self.stack = QStackedWidget()
        self.empty = QLabel(u"Select an adjustment layer\n(or add one with the ◐ button "
                            u"at the bottom of the Layers docker).")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.stack.addWidget(self.empty)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.stack)
        v.addWidget(scroll, 1)

        bar = QHBoxLayout()
        self.b_clip = self._tool(u"Clip to layer below (Ctrl+Alt+G)")
        self.b_blend = QPushButton(u"Blend If…")
        self.b_native = QPushButton(u"Krita editor…")
        self.b_native.setToolTip(u"Open Krita's own dialog for this layer")
        self.b_reset = self._tool(u"Reset to defaults")
        self.b_reset.setText(u"↺")
        self.b_eye = self._tool(u"Toggle layer visibility")
        self.b_eye.setText(u"👁")
        self.b_del = self._tool(u"Delete this layer")
        self.b_del.setText(u"🗑")
        for w in (self.b_clip, self.b_blend, self.b_native):
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (self.b_reset, self.b_eye, self.b_del):
            bar.addWidget(w)
        self.bar_widgets = [self.b_clip, self.b_blend, self.b_native, self.b_reset,
                            self.b_eye, self.b_del]
        v.addLayout(bar)
        self.setWidget(root)

        self.editors = {}
        self.node = None
        self.node_key = None
        self.kind = None
        self.doc = None
        self.doc_key = None
        self.snapshot = None
        self.pending = None
        self._pending_target = None
        self._applying = False
        self.hist_cache = {}

        self.poll = QTimer(self)
        self.poll.setInterval(300)
        self.poll.timeout.connect(self._poll)
        self.apply_timer = QTimer(self)
        self.apply_timer.setSingleShot(True)
        self.apply_timer.setInterval(30)
        self.apply_timer.timeout.connect(self._apply_pending)
        self.blend_timer = QTimer(self)
        self.blend_timer.setSingleShot(True)
        self.blend_timer.setInterval(700)
        self.blend_timer.timeout.connect(self._update_blendif)

        self.b_clip.clicked.connect(self._clip)
        self.b_blend.clicked.connect(self._blendif)
        self.b_native.clicked.connect(self._native)
        self.b_reset.clicked.connect(self._reset)
        self.b_eye.clicked.connect(self._eye)
        self.b_del.clicked.connect(self._delete)
        self._update_icons()
        self._set_enabled(False)
        self.poll.start()

    # ------------------------------------------------------------ helpers
    def _tool(self, tip):
        b = QToolButton()
        b.setAutoRaise(True)
        b.setToolTip(tip)
        b.setIconSize(QSize(18, 18))
        return b

    def _update_icons(self):
        col = self.palette().windowText().color()
        self.b_clip.setIcon(make_icon(clip_icon, 18, col))
        self.icon.setPixmap(adjustment_icon(18, col, self.devicePixelRatioF()))

    def _set_enabled(self, on):
        for w in self.bar_widgets:
            w.setEnabled(on)

    def canvasChanged(self, canvas):
        self.node_key = None

    def _snap(self, node):
        try:
            if node.type() == "filterlayer" and node.filter().name() == "pscolorlookup":
                # the "lut" property is a (megabytes long) table: compare its id
                cfg = node.filter().configuration()
                return "lut|%s|%s|%s|%s|%s" % (cfg.property("lut_id"), cfg.property("name"),
                                               cfg.property("linear"), node.blendingMode(),
                                               node.opacity())
            props = (node.filter().configuration().properties() if node.type() == "filterlayer"
                     else node.filterConfig().properties())
            return repr(sorted((k, repr(v)) for k, v in props.items())) + \
                "|%s|%s" % (node.blendingMode(), node.opacity())
        except Exception:
            return None

    # --------------------------------------------------------------- poll
    def _poll(self):
        if not self.isVisible() or self._applying or getattr(self, "_loading_node", False):
            return
        try:
            doc = Krita.instance().activeDocument()
            node = doc.activeNode() if doc else None
            key = core.node_key(node) if node is not None else None
            dkey = core.node_key(doc.rootNode()) if doc is not None else None
            if key != self.node_key or dkey != self.doc_key:
                self._load(doc, node)
            elif self.node is not None and self.pending is None \
                    and not self.apply_timer.isActive():
                s = self._snap(self.node)
                if s is not None and s != self.snapshot:   # edited elsewhere
                    self._load(doc, node, keep_hist=True)
        except Exception:
            traceback.print_exc()

    def _load(self, doc, node, keep_hist=False):
        self._loading_node = True
        try:
            self._load_now(doc, node, keep_hist)
        finally:
            self._loading_node = False

    def _load_now(self, doc, node, keep_hist=False):
        # Finish the old target before rebinding controls to another layer.
        self.apply_timer.stop()
        if self.pending is not None:
            self._apply_pending()
        if self.blend_timer.isActive():
            self.blend_timer.stop()
            self._update_blendif()
        from . import baked
        baked.forget()
        self.doc = doc
        self.doc_key = core.node_key(doc.rootNode()) if doc is not None else None
        self.node = None
        self.node_key = core.node_key(node) if node is not None else None
        kind = A.kind_of(doc, node) if doc is not None else None
        self.kind = kind
        if kind is None:
            self.title.setText(u"Properties")
            self.stack.setCurrentWidget(self.empty)
            self._set_enabled(False)
            return
        if kind not in self.editors:
            ed = make_editor(kind)
            ed.changed.connect(self._edited)
            self.editors[kind] = ed
            self.stack.addWidget(ed)
        ed = self.editors[kind]
        title = A.KINDS[kind][0] if kind in A.KINDS else node.name()
        self.title.setText(title)
        if kind in A.KINDS:
            params = A.params_from_node(doc, node, kind)
            if kind in ("brightness_contrast", "exposure") and not keep_hist:
                # layers made by older versions of the plugin used Levels
                try:
                    if node.filter().name() == "levels":
                        A.apply(doc, node, kind, params)
                except Exception:
                    pass
        else:
            try:
                params = (node.filter().configuration().properties()
                          if node.type() == "filterlayer" else {})
            except Exception:
                params = {}
        ed.set_params(params)
        self.stack.setCurrentWidget(ed)
        self.node = node
        self.snapshot = self._snap(node)
        self._set_enabled(True)
        self.b_native.setEnabled(kind not in A.BAKED_KINDS)
        self.b_clip.setCheckable(True)
        self.b_clip.setChecked(clip.is_clipped(node))
        if kind == "curves":
            ed.show_unsupported(False)
        if ed.needs_histogram:
            h = self.hist_cache.get(self.node_key)
            if h is None and not keep_hist:
                QTimer.singleShot(0, self._compute_histogram)
            ed.set_histogram(h)

    def _compute_histogram(self):
        node, doc = self.node, self.doc
        if node is None or doc is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            h = A.input_histogram(doc, node)
            self.hist_cache[core.node_key(node)] = h
            ed = self.editors.get(self.kind)
            if ed is not None:
                ed.set_histogram(h)
        except Exception:
            traceback.print_exc()
        finally:
            QApplication.restoreOverrideCursor()
        self.snapshot = self._snap(node)

    # -------------------------------------------------------------- apply
    def _edited(self):
        ed = self.sender()
        if ed is not self.editors.get(self.kind):
            return
        self.pending = ed.params()
        self._pending_target = (self.node, self.doc, self.kind)
        # Throttle instead of debounce: continuous dragging must not postpone
        # preview forever. Only the most recent settings are kept.
        if not self._applying and not self.apply_timer.isActive():
            self.apply_timer.start(self._apply_delay())

    def _apply_delay(self):
        # a computed (baked) layer re-renders the whole image in Python;
        # everything else - Krita PS Color Lookup / Selective Color included -
        # is a live filter layer
        return 120 if A.is_baked_node(self.node, self.kind) else 30

    def _apply_pending(self):
        if self._applying:
            return
        self.apply_timer.stop()
        self._applying = True
        core._preview_busy = getattr(core, "_preview_busy", 0) + 1
        try:
            self._apply_pending_now()
        finally:
            core._preview_busy -= 1
            self._applying = False
            if self.pending is not None and not self.apply_timer.isActive():
                self.apply_timer.start(self._apply_delay())

    def _apply_pending_now(self):
        node, doc, kind = self._pending_target or (self.node, self.doc, self.kind)
        params = self.pending
        self.pending = None
        self._pending_target = None
        if node is None or doc is None or params is None:
            return
        # A timer may outlive a deleted layer or closed document.
        try:
            if not any(core.node_key(d.rootNode()) == core.node_key(doc.rootNode())
                       for d in Krita.instance().documents()):
                return
            if node.parentNode() is None:
                return
        except RuntimeError:  # deleted C++ wrapper
            return
        from . import baked
        baked_node = A.is_baked_node(node, kind)
        if baked_node:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if kind in A.KINDS:
                ok = A.apply(doc, node, kind, params)
                if kind == "curves":
                    self.editors[kind].show_unsupported(not ok)
                if kind == "color_lookup":
                    self.editors[kind].show_error(u"")
            else:
                self._apply_generic(doc, node, params)
        except baked.BakeError as e:
            ed = self.editors.get(kind)
            if hasattr(ed, "show_error"):
                ed.show_error(str(e))
            else:
                QMessageBox.warning(self, A.KINDS[kind][0], str(e))
        except Exception:
            traceback.print_exc()
        finally:
            if baked_node:
                QApplication.restoreOverrideCursor()
                from . import autorefresh
                autorefresh.rebaseline(doc)
        self.snapshot = self._snap(node)
        if baked_node or core.native_blend_if():
            return            # render() / the engine already updated Blend If
        mask = core.find_blendif_mask(node)
        if mask is not None:
            s = core.BlendIfSettings.from_name(mask.name())
            if s is not None and s.this != core.NEUTRAL:
                self.blend_timer.start()

    def _apply_generic(self, doc, node, params):
        if node.type() != "filterlayer":
            return
        f = Krita.instance().filter(node.filter().name())
        cfg = f.configuration()
        for k, v in params.items():
            cfg.setProperty(k, v)
        f.setConfiguration(cfg)
        node.setFilter(f)
        doc.refreshProjection()

    def _update_blendif(self):
        node, doc = self.node, self.doc
        if node is None:
            return
        mask = core.find_blendif_mask(node)
        s = core.BlendIfSettings.from_name(mask.name()) if mask is not None else None
        if s is None:
            return
        try:
            core.apply_settings(doc, node, s)
        except Exception:
            traceback.print_exc()
        self.snapshot = self._snap(node)

    # ------------------------------------------------------------ buttons
    def _clip(self):
        if self.node is None:
            return
        try:
            clip.toggle(self.doc, self.node)
        except core.BlendIfError as e:
            QMessageBox.information(self, u"Clipping", str(e))
        self.node_key = None

    def _blendif(self):
        if self.node is None:
            return
        self.doc.setActiveNode(self.node)
        from .extension import open_blend_if
        open_blend_if()

    def _native(self):
        if self.node is None:
            return
        self.doc.setActiveNode(self.node)
        Krita.instance().action("layer_properties").trigger()
        self.node_key = None

    def _reset(self):
        if self.node is None or self.kind not in A.KINDS:
            return
        p = A.defaults(self.kind)
        if self.kind == "gradient_map":
            p = {}
        self.editors[self.kind].set_params(p)
        self.pending = p
        self._pending_target = (self.node, self.doc, self.kind)
        self._apply_pending()

    def _eye(self):
        if self.node is not None:
            self.node.setVisible(not self.node.visible())
            self.doc.refreshProjection()

    def _delete(self):
        if self.node is not None:
            n = self.node
            self.node = None
            n.remove()
            self.doc.refreshProjection()
            self.node_key = None
