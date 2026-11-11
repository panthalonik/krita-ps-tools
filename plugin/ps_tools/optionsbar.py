# -*- coding: utf-8 -*-
"""Photoshop-like  Opacity: [100%]v  Flow: [100%]v  Smoothing: [0%]v  fields
in the tool options bar, in place of Krita's first brush slider (Opacity).

  * type a value, or click v for a slider, or drag on the label (scrubby
    slider, like Photoshop);
  * Opacity / Flow are the painting opacity / flow of the current brush;
  * Smoothing drives the Freehand Brush tool's "Brush Smoothing" (Tool
    Options): 0% = None, more = Weighted (or Stabilizer, see the v menu)
    with a larger distance."""
import math
import traceback

from krita import Krita
from PyQt5 import sip
from PyQt5.QtCore import Q_ARG, QMetaObject, QPoint, QSize, Qt, QTimer
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (QAbstractSpinBox, QAction, QActionGroup, QApplication,
                             QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                             QMenu, QSlider, QSpinBox, QToolBar, QToolButton,
                             QVBoxLayout, QWidget)

from .jumpslider import JumpSlider

GROUP = "ps_tools"
SMOOTH_NONE, SMOOTH_BASIC, SMOOTH_WEIGHTED, SMOOTH_STABILIZER = 0, 1, 2, 3
# Krita 5.2: None/Basic/Weighted/Stabilizer + one Distance slider
# Krita 5.3+: ... + Pixel, and two sliders (Distance at Max / Min Speed)
DIST_MIN, DIST_MAX_PS = 3.0, 300.0      # 100% smoothing = distance 300


def _get(key, default):
    try:
        return Krita.instance().readSetting(GROUP, key, default)
    except Exception:
        return default


def _set(key, value):
    try:
        Krita.instance().writeSetting(GROUP, key, value)
    except Exception:
        pass


def _log(text):
    try:
        from .extension import log
        log("options bar: " + text)
    except Exception:
        pass


def active_view():
    w = Krita.instance().activeWindow()
    return w.activeView() if w is not None else None


# ------------------------------------------------------------ smoothing
def percent_to_distance(p):
    p = min(100.0, max(0.0, p)) / 100.0
    return DIST_MIN + (p ** 2) * (DIST_MAX_PS - DIST_MIN)


def distance_to_percent(d):
    t = (max(DIST_MIN, min(DIST_MAX_PS, d)) - DIST_MIN) / (DIST_MAX_PS - DIST_MIN)
    return int(round(math.sqrt(t) * 100))


SMOOTH_ITEMS = ("none", "basic", "weighted", "stabilizer")
_diag = {"logged": False}


def _is_smoothing_combo(c):
    if c.count() not in (4, 5):
        return False
    texts = tuple(c.itemText(i).strip().lower() for i in range(4))
    if texts == SMOOTH_ITEMS:
        return True
    # translated UI: rely on the container Krita names "<tool id>option widget"
    p = c.parentWidget()
    return p is not None and p.objectName().endswith("KisToolBrushoption widget")


def _distance_spins(combo):
    """The Distance / Sample count slider(s) next to the combo: one in Krita
    5.2, two in 5.3+ (at max speed, at min speed)."""
    p = combo.parentWidget()
    while p is not None:
        found = [s for s in p.findChildren(QDoubleSpinBox)
                 if abs(s.minimum() - DIST_MIN) < 1e-6 and s.maximum() >= 999]
        if found:
            return found
        if p.objectName().endswith("option widget"):
            break
        p = p.parentWidget()
    return []


def smoothing_controls():
    """[(type combo, distance spin box)] of the Freehand Brush tool options
    (one per open canvas; they exist once the brush tool has been used)."""
    out = []
    for c in QApplication.allWidgets():
        try:
            if not isinstance(c, QComboBox) or not _is_smoothing_combo(c):
                continue
            spins = _distance_spins(c)
            if spins:
                out.append((c, spins))
        except RuntimeError:
            pass
    if not out and not _diag["logged"]:
        _diag["logged"] = True
        try:
            names = sorted(set(w.objectName() for w in QApplication.allWidgets()
                               if w.objectName().endswith("option widget")))
            combos = [[c.itemText(i) for i in range(c.count())]
                      for c in QApplication.allWidgets()
                      if isinstance(c, QComboBox) and c.count() in (4, 5)][:8]
            _log("smoothing controls not found; option widgets: %s; 4/5-item combos: %s"
                 % (names, combos))
        except Exception:
            pass
    elif out and not _diag.get("found"):
        _diag["found"] = True
        _log("smoothing controls found: %d (distance sliders per tool: %d)"
             % (len(out), len(out[0][1])))
    return out


def _set_spin(spin, value):
    try:
        if QMetaObject.invokeMethod(spin, "setValue", Q_ARG(float, value)):
            return
    except Exception:
        pass
    spin.setValue(value)


def read_smoothing(controls):
    if not controls:
        return None
    combo, spins = next(((c, s) for c, s in controls if c.isVisible()), controls[0])
    t = combo.currentIndex()
    if t in (SMOOTH_WEIGHTED, SMOOTH_STABILIZER):
        return max(1, distance_to_percent(max(s.value() for s in spins)))
    return 0


def write_smoothing(controls, percent, mode):
    d = percent_to_distance(percent)
    for combo, spins in controls:
        try:
            if percent <= 0:
                if combo.currentIndex() != SMOOTH_NONE:
                    combo.setCurrentIndex(SMOOTH_NONE)
                continue
            if combo.currentIndex() != mode:
                combo.setCurrentIndex(mode)
            if len(spins) == 1:
                _set_spin(spins[0], d)
                continue
            # 5.3+: two sliders, maybe chained by the aspect lock. Set the
            # larger ("at min speed") one; if the lock did not move the other
            # one along, set it as well.
            big = max(spins, key=lambda s: s.value())
            others = [s for s in spins if s is not big]
            before = [s.value() for s in others]
            _set_spin(big, d)
            for s, b in zip(others, before):
                if abs(s.value() - b) < 1e-6:
                    _set_spin(s, d)
        except RuntimeError:
            pass


# --------------------------------------------------------------- field
class ScrubLabel(QLabel):
    """Label you can drag left/right to change the value (Photoshop)."""

    def __init__(self, text, field):
        super(ScrubLabel, self).__init__(text)
        self.field = field
        self.setCursor(Qt.SizeHorCursor)
        self._x = None
        self._v = 0

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.field.isEnabled():
            self._x, self._v = e.globalX(), self.field.value()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._x is not None:
            self.field.set_value(self._v + (e.globalX() - self._x) // 2, emit=True)

    def mouseReleaseEvent(self, e):
        if self._x is not None:
            self._x = None
            self.field.commit()


class SliderPopup(QFrame):
    def __init__(self, field):
        super(SliderPopup, self).__init__(None, Qt.Popup)
        self.field = field
        self.setFrameShape(QFrame.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        self.slider = JumpSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setFixedWidth(170)
        lay.addWidget(self.slider)
        self.slider.valueChanged.connect(lambda v: field.set_value(v, emit=True, from_slider=True))
        self.slider.sliderReleased.connect(field.commit)

    def hideEvent(self, e):
        self.field.commit()
        super(SliderPopup, self).hideEvent(e)


class PSField(QWidget):
    """Label: [ 100%] v  - compact field like in Photoshop's options bar."""

    def __init__(self, label, tip, parent=None, gear=False):
        super(PSField, self).__init__(parent)
        self.on_change = None       # (value, final) -> None
        self.extra_menu = None      # callable(menu) adding entries to the v menu
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 2, 0)
        lay.setSpacing(3)
        self.label = ScrubLabel(label, self)
        self.label.setToolTip(tip + u"\nDrag the label left/right to change it")
        self.spin = QSpinBox()
        self.spin.setRange(0, 100)
        self.spin.setSuffix(u"%")
        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.spin.setKeyboardTracking(False)
        self.spin.setFixedWidth(self.spin.fontMetrics().horizontalAdvance(u"100%") + 18)
        self.spin.setToolTip(tip)
        self.arrow = QToolButton()
        self.arrow.setArrowType(Qt.DownArrow)
        self.arrow.setAutoRaise(True)
        self.arrow.setFixedSize(QSize(14, 18))
        self.arrow.setToolTip(tip)
        lay.addWidget(self.label)
        lay.addWidget(self.spin)
        lay.addWidget(self.arrow)
        self.gear = None
        if gear:
            self.gear = QToolButton()
            self.gear.setText(u"⚙")
            self.gear.setAutoRaise(True)
            self.gear.setToolTip(u"Smoothing options")
            self.gear.clicked.connect(self._gear_menu)
            lay.addWidget(self.gear)
        self.popup = SliderPopup(self)
        self._busy = False
        self._dirty = False
        self.editing_until = 0.0
        self.spin.valueChanged.connect(self._typed)
        self.spin.editingFinished.connect(self.spin.clearFocus)
        self.arrow.clicked.connect(self._show_popup)

    def value(self):
        return self.spin.value()

    def set_value(self, v, emit=False, from_slider=False):
        v = int(max(0, min(100, v)))
        self._busy = True
        try:
            self.spin.setValue(v)
            if not from_slider:
                self.popup.slider.setValue(v)
        finally:
            self._busy = False
        if emit:
            self._dirty = True
            if self.on_change:
                self.on_change(v, False)

    def commit(self):
        if self._dirty:
            self._dirty = False
            if self.on_change:
                self.on_change(self.spin.value(), True)

    def _typed(self, v):
        if self._busy:
            return
        self.popup.slider.blockSignals(True)
        self.popup.slider.setValue(v)
        self.popup.slider.blockSignals(False)
        if self.on_change:
            self.on_change(v, True)

    def _show_popup(self):
        self.popup.slider.blockSignals(True)
        self.popup.slider.setValue(self.spin.value())
        self.popup.slider.blockSignals(False)
        self.popup.adjustSize()
        self.popup.move(self.spin.mapToGlobal(QPoint(0, self.height())))
        self.popup.show()

    def busy(self):
        return (self.spin.hasFocus() or self.popup.isVisible()
                or self.label._x is not None)

    def _gear_menu(self):
        m = QMenu(self)
        if self.extra_menu is not None:
            self.extra_menu(m)
        m.exec_(self.gear.mapToGlobal(QPoint(0, self.gear.height())))

    def contextMenuEvent(self, e):
        m = QMenu(self)
        if self.extra_menu is not None:
            self.extra_menu(m)
            m.addSeparator()
        BAR.add_common_menu(m)
        m.exec_(e.globalPos())


# ---------------------------------------------------------- the strip
class OptionsStrip(QWidget):
    def __init__(self, parent=None):
        super(OptionsStrip, self).__init__(parent)
        self.setObjectName("psToolsOptionsStrip")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(6)
        self.opacity = PSField(u"Opacity:", u"Painting opacity")
        self.flow = PSField(u"Flow:", u"Painting flow")
        self.smooth = PSField(u"Smoothing:", u"Brush smoothing of the Freehand Brush tool "
                                             u"(Tool Options -> Brush Smoothing)", gear=True)
        for f in (self.opacity, self.flow, self.smooth):
            lay.addWidget(f)
        self.opacity.on_change = self._opacity
        self.flow.on_change = self._flow
        self.smooth.on_change = self._smooth
        self.smooth.extra_menu = self._smooth_menu
        self._controls = []
        self._controls_age = 0

    # -- writes
    def _opacity(self, v, final):
        view = active_view()
        if view is not None:
            view.setPaintingOpacity(v / 100.0)

    def _flow(self, v, final):
        view = active_view()
        if view is not None:
            view.setPaintingFlow(v / 100.0)

    def _smooth(self, v, final):
        if not final:
            return                   # the brush tool re-reads options: apply on release
        self._controls = smoothing_controls()
        if not self._controls:
            return
        write_smoothing(self._controls, v, self.smooth_mode())

    @staticmethod
    def smooth_mode():
        return SMOOTH_STABILIZER if _get("smoothMode", "weighted") == "stabilizer" else SMOOTH_WEIGHTED

    def _smooth_menu(self, m):
        g = QActionGroup(m)
        for key, label in (("weighted", u"Smoothing mode: Weighted"),
                           ("stabilizer", u"Smoothing mode: Stabilizer")):
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(_get("smoothMode", "weighted") == key)
            a.triggered.connect(lambda _c=False, k=key: self._set_mode(k))
            g.addAction(a)
        m.addSeparator()
        a = m.addAction(u"More smoothing options (Tool Options)…")
        a.triggered.connect(self._tool_options)

    @staticmethod
    def _tool_options():
        k = Krita.instance()
        w = k.activeWindow()
        qwin = w.qwindow() if w is not None else None
        if qwin is not None:
            from PyQt5.QtWidgets import QDockWidget
            d = qwin.findChild(QDockWidget, "sharedtooldocker")
            if d is not None:
                d.show()
                d.raise_()
                return
        a = k.action("show_tool_options")
        if a is not None:
            a.trigger()

    def _set_mode(self, key):
        _set("smoothMode", key)
        if self.smooth.value() > 0:
            self._smooth(self.smooth.value(), True)

    # -- reads
    def sync(self):
        view = active_view()
        for f in (self.opacity, self.flow, self.smooth):
            f.setEnabled(view is not None)
        if view is None:
            return
        try:
            if not self.opacity.busy():
                self.opacity.set_value(round(view.paintingOpacity() * 100))
            if not self.flow.busy():
                self.flow.set_value(round(view.paintingFlow() * 100))
        except Exception:
            pass
        # smoothing_controls() walks every widget of Krita: only look again
        # when a found control was deleted, or now and then while none exist
        # (the brush tool's options appear once the tool has been used)
        self._controls_age += 1
        dead = any(sip.isdeleted(c) for c, _spins in self._controls) if self._controls else False
        if dead or self._controls_age % 60 == 1 or (not self._controls and self._controls_age % 10 == 1):
            self._controls = smoothing_controls()
        if not self.smooth.busy():
            p = read_smoothing(self._controls)
            self.smooth.setEnabled(p is not None)
            self.smooth.spin.setToolTip(
                u"Brush smoothing of the Freehand Brush tool" if p is not None else
                u"Select the Freehand Brush tool once to enable this")
            if p is not None:
                self.smooth.set_value(p)


# ------------------------------------------------------- installation
def _find_slider_action(qwin):
    """(toolbar, Krita's "brushslider1" action) - the Opacity slider."""
    act = None
    try:
        act = Krita.instance().action("brushslider1")
    except Exception:
        pass
    for tb in qwin.findChildren(QToolBar):
        acts = tb.actions()
        if act is not None and act in acts:
            return tb, act
    # fallback: a slider in the bar whose prefix/label says Opacity
    for tb in qwin.findChildren(QToolBar):
        for a in tb.actions():
            w = tb.widgetForAction(a)
            if w is None:
                continue
            for sb in w.findChildren(QDoubleSpinBox):
                if sb.window() == tb.window() and "opacity" in sb.prefix().lower():
                    return tb, a
    return None, None


class _Slot(object):
    def __init__(self, qwin):
        self.qwin = qwin
        self.strip = None
        self.native = None


class OptionsBar(object):
    def __init__(self):
        self.slots = []
        self._ticks = 0

    def watch(self, qwin):
        if qwin is None or any(s.qwin is qwin for s in self.slots):
            return
        self.slots.append(_Slot(qwin))

    @staticmethod
    def _alive(o):
        try:
            return o is not None and not sip.isdeleted(o)
        except Exception:
            return False

    def _install(self, slot):
        tb, act = _find_slider_action(slot.qwin)
        if tb is None:
            return False
        acts = tb.actions()
        idx = acts.index(act)
        before = acts[idx + 1] if idx + 1 < len(acts) else None
        strip = OptionsStrip()
        if before is not None:
            tb.insertWidget(before, strip)
        else:
            tb.addWidget(strip)
        slot.strip, slot.native = strip, act
        self.apply_native_visibility()
        strip.sync()
        _log("installed in toolbar '%s'" % tb.objectName())
        return True

    def apply_native_visibility(self):
        hide = _get("optsHideKritaSlider", "true") == "true"
        for s in self.slots:
            if self._alive(s.native) and s.native.isVisible() == hide:
                s.native.setVisible(not hide)

    def add_common_menu(self, m):
        a = m.addAction(u"Show Krita's Opacity slider too")
        a.setCheckable(True)
        a.setChecked(_get("optsHideKritaSlider", "true") != "true")
        a.toggled.connect(lambda on: (_set("optsHideKritaSlider", "false" if on else "true"),
                                      self.apply_native_visibility()))

    def tick(self):
        self._ticks += 1
        if self._ticks % 4 == 0:
            for s in self.slots:
                if self._alive(s.qwin) and (not self._alive(s.strip) or s.strip.parentWidget() is None):
                    try:
                        self._install(s)
                    except Exception:
                        _log("install error: " + traceback.format_exc())
            self.slots = [s for s in self.slots if self._alive(s.qwin)]
            self.apply_native_visibility()
        for s in self.slots:
            if self._alive(s.strip) and s.strip.isVisible():
                try:
                    s.strip.sync()
                except RuntimeError:
                    pass


BAR = OptionsBar()
