# -*- coding: utf-8 -*-
"""Photoshop-like Shift + drag with the brush: the stroke is locked to a
horizontal or vertical line (chosen by the direction of the first few
pixels of the movement).

Krita has no such option, so the plugin sits in front of Krita's own input
handling: while a Shift-stroke of the Freehand Brush tool is running, each
pen / mouse move on the canvas is replaced by a copy of the event with one
coordinate fixed to where the stroke started. Pressure, tilt, rotation and
timestamps are kept, so brush dynamics work as usual.

The filter is an application-wide one: Krita re-installs its own filter on
the canvas whenever the canvas gets focus (which happens right at a pen
press), so a filter on the canvas widget itself would end up behind
Krita's and never see the pen moves. Application filters always run first.
It is only installed while the Freehand Brush tool is active.

Krita must still start painting when Shift is held: in Canvas Input
Settings the Tool Invocation action needs "Shift + Left Button -> Activate"
(see the manual)."""
import time
import traceback

from krita import Krita
from PyQt5 import sip
from PyQt5.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PyQt5.QtGui import QMouseEvent, QTabletEvent
from PyQt5.QtWidgets import QApplication, QOpenGLWidget, QToolButton, QWidget

GROUP = "ps_tools"
KEY_ON = "shiftAxisLock"
CANVAS_CLASSES = ("KisOpenGLCanvas2", "KisQPainterCanvas")
TOOLS = ("KritaShape/KisToolBrush",)
THRESHOLD = 4.0          # px of movement before the axis is decided
PEN_GRACE = 0.6          # s after a pen event during which mouse events are Windows' copies

_PRESS = (QEvent.TabletPress, QEvent.MouseButtonPress)
_MOVE = (QEvent.TabletMove, QEvent.MouseMove)
_RELEASE = (QEvent.TabletRelease, QEvent.MouseButtonRelease)
_TABLET = (QEvent.TabletPress, QEvent.TabletMove, QEvent.TabletRelease)
_ALL = frozenset(int(t) for t in _PRESS + _MOVE + _RELEASE)


def enabled():
    return Krita.instance().readSetting(GROUP, KEY_ON, "true") == "true"


def set_enabled(on):
    Krita.instance().writeSetting(GROUP, KEY_ON, "true" if on else "false")


_log_budget = [40]           # diagnostic lines per session


def _log(text):
    if _log_budget[0] <= 0:
        return
    _log_budget[0] -= 1
    try:
        from .extension import log
        log("shift axis lock: " + text)
    except Exception:
        pass


def constrain(origin, pos, axis, threshold=THRESHOLD):
    """-> (new pos, axis). axis: None (undecided), 'x' (horizontal), 'y'."""
    dx, dy = pos.x() - origin.x(), pos.y() - origin.y()
    if axis is None:
        if max(abs(dx), abs(dy)) < threshold:
            return QPointF(origin), None
        axis = "x" if abs(dx) >= abs(dy) else "y"
    if axis == "x":
        return QPointF(pos.x(), origin.y()), axis
    return QPointF(origin.x(), pos.y()), axis


def _is_canvas(obj):
    try:
        return isinstance(obj, QWidget) and obj.metaObject().className() in CANVAS_CLASSES
    except Exception:
        return False


def _tool_active(win):
    """Is one of TOOLS the current tool (its toolbox button is checked)?"""
    if win is None:
        return False
    for tid in TOOLS:
        b = win.findChild(QToolButton, tid)
        if b is not None and b.isChecked():
            return True
    return False


_OTHER_MODS = int(Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)


def shift_only(ev_mods):
    """Shift held (in the event or on the keyboard right now), nothing else."""
    try:
        kb = int(QApplication.queryKeyboardModifiers())
    except Exception:
        kb = 0
    mods = int(ev_mods) | kb
    return bool(mods & int(Qt.ShiftModifier)) and not (mods & _OTHER_MODS)


class _Stroke(object):
    def __init__(self, canvas, tablet, origin):
        self.canvas = canvas
        self.tablet = tablet
        self.origin = origin
        self.axis = None
        self.moves = 0
        self.locked = 0


class AxisLock(QObject):
    def __init__(self, parent=None):
        super(AxisLock, self).__init__(parent)
        self.stroke = None
        self.sending = False
        self._errors = 0
        self._last_key = None
        self.canvases = []
        self._last_pen = 0.0

    # -------------------------------------------------------- events
    def eventFilter(self, obj, ev):
        try:
            t = ev.type()
            if t == QEvent.FocusIn and obj in self.canvases:
                # Krita re-installs its canvas filter on focus-in: get in front again
                QTimer.singleShot(0, self.keep_first)
                return False
            if self.sending or int(t) not in _ALL:
                return False
            # the same event can come through the application filter and the
            # canvas filter (both installed): handle it once
            key = (int(t), ev.timestamp(), id(obj), self._raw_pos(ev))
            if key == self._last_key:
                return False
            self._last_key = key
            return self._handle(obj, ev, t)
        except Exception:
            self._errors += 1
            if self._errors <= 5:
                _log(traceback.format_exc())
            self.stroke = None
            return False

    def _handle(self, obj, ev, t):
        tablet = t in _TABLET
        # pen input may also arrive as mouse events Windows made from the pen
        # (source = synthesized by system) - e.g. when Krita gets no real
        # tablet events. They are only used when no pen stroke is running.
        synth = (not tablet) and ev.source() != Qt.MouseEventNotSynthesized
        kind = "pen" if tablet else ("pen-as-mouse" if synth else "mouse")
        now = time.monotonic()
        if tablet:
            self._last_pen = now
        elif (self.stroke is not None and self.stroke.tablet) or now - self._last_pen < PEN_GRACE:
            # Windows sends its own mouse copy of every pen press / move (not
            # always marked as synthesized); Krita drops them, so must we -
            # otherwise the copy would replace the pen stroke
            return False
        if t in _PRESS:
            self.stroke = None
            if ev.button() == Qt.LeftButton and _is_canvas(obj) and enabled():
                shift = shift_only(ev.modifiers())
                tool = _tool_active(obj.window())
                if shift:
                    _log("%s press on %s: tool active=%s, modifiers=%#x, keyboard=%#x"
                         % (kind, obj.metaObject().className(), tool, int(ev.modifiers()),
                            int(QApplication.queryKeyboardModifiers())))
                if shift and tool:
                    self.stroke = _Stroke(obj, tablet, QPointF(self._pos(ev)))
                    self.stroke.kind = kind
            return False
        st = self.stroke
        if st is None or obj is not st.canvas:
            return False
        if tablet != st.tablet:
            st.other = getattr(st, "other", 0) + 1
            return False
        # (no buttons() check for the pen: some Windows Ink drivers report
        # NoButton on pen moves while the pen touches the tablet)
        if not tablet and not synth and t in _MOVE and not (ev.buttons() & Qt.LeftButton):
            self.stroke = None      # missed the release
            return False
        pos = self._pos(ev)
        new, st.axis = constrain(st.origin, pos, st.axis)
        st.moves += 1
        if t in _RELEASE:
            self.stroke = None
            _log("%s stroke: %d events, %d straightened, axis %s, other events %d"
                 % (st.kind, st.moves, st.locked + (new != pos), st.axis,
                    getattr(st, "other", 0)))
        if new == pos:
            return False
        st.locked += 1
        copy = self._copy(ev, t, new, new - pos)
        self.sending = True
        try:
            QApplication.sendEvent(obj, copy)
        finally:
            self.sending = False
        ev.accept()
        return True

    @staticmethod
    def _raw_pos(ev):
        p = ev.posF() if isinstance(ev, QTabletEvent) else ev.localPos()
        return (p.x(), p.y())

    # ------------------------------------------------- canvas filter
    def find_canvases(self):
        found = []
        try:
            wins = [w.qwindow() for w in Krita.instance().windows()]
        except Exception:
            wins = []
        for w in wins:
            if w is None:
                continue
            try:
                cands = w.findChildren(QOpenGLWidget)
                if not any(_is_canvas(c) for c in cands):
                    cands = w.findChildren(QWidget)
                found.extend(c for c in cands if _is_canvas(c))
            except RuntimeError:
                pass
        self.canvases = found

    def keep_first(self):
        """Also filter on the canvas itself, in front of Krita's filter (Qt runs
        the most recently installed one first) - a second line of defence in
        case the application filter does not get the pen events."""
        alive = []
        for c in self.canvases:
            try:
                if sip.isdeleted(c):
                    continue
                c.installEventFilter(self)
                alive.append(c)
            except RuntimeError:
                pass
        self.canvases = alive

    def release_canvases(self):
        for c in self.canvases:
            try:
                if not sip.isdeleted(c):
                    c.removeEventFilter(self)
            except RuntimeError:
                pass

    @staticmethod
    def _pos(ev):
        return ev.posF() if isinstance(ev, QTabletEvent) else ev.localPos()

    @staticmethod
    def _copy(ev, t, pos, delta):
        if isinstance(ev, QTabletEvent):
            c = QTabletEvent(t, pos, ev.globalPosF() + delta, ev.deviceType(),
                             ev.pointerType(), ev.pressure(), ev.xTilt(), ev.yTilt(),
                             ev.tangentialPressure(), ev.rotation(), ev.z(),
                             ev.modifiers(), ev.uniqueId(), ev.button(), ev.buttons())
        else:
            try:
                c = QMouseEvent(t, pos, ev.windowPos() + delta, ev.screenPos() + delta,
                                ev.button(), ev.buttons(), ev.modifiers(), ev.source())
            except TypeError:
                c = QMouseEvent(t, pos, ev.windowPos() + delta, ev.screenPos() + delta,
                                ev.button(), ev.buttons(), ev.modifiers())
        c.setTimestamp(ev.timestamp())
        return c


class Controller(object):
    """Installs the application-wide filter while the brush tool is active
    (and the option is on), removes it otherwise - no cost for other tools."""

    def __init__(self):
        self.filter = None
        self.installed = False
        self._button = None
        self._win = None
        self._ticks = 0

    def _brush_active(self):
        w = Krita.instance().activeWindow()
        qwin = w.qwindow() if w is not None else None
        if qwin is None:
            return False
        if self._win is not qwin or self._button is None or sip.isdeleted(self._button):
            self._win = qwin
            self._button = qwin.findChild(QToolButton, TOOLS[0])
        return self._button is not None and self._button.isChecked()

    def tick(self):
        """From the extension's poll (~300 ms)."""
        from . import core
        if core.engine_level() >= 4:
            # Krita PS does the axis lock in C++ (ps_axis_lock.cpp): just tell
            # it whether the option is on, no Python event filter at all
            on = enabled()
            app = QApplication.instance()
            if app.property("ps_tools_axislock") != on:
                app.setProperty("ps_tools_axislock", on)
            if self.installed and self.filter is not None and self.filter.stroke is None:
                app.removeEventFilter(self.filter)
                self.filter.release_canvases()
                self.installed = False
            return
        want = enabled() and self._brush_active()
        if self.filter is None:
            self.filter = AxisLock()
        self._ticks += 1
        if want and not self.installed:
            QApplication.instance().installEventFilter(self.filter)
            self.installed = True
            self.filter.find_canvases()
            _log("on (brush active); canvases: %s" % ", ".join(
                c.metaObject().className() for c in self.filter.canvases) or "none")
        elif not want and self.installed and self.filter.stroke is None:
            QApplication.instance().removeEventFilter(self.filter)
            self.filter.release_canvases()
            self.installed = False
        if self.installed and self.filter.stroke is None:
            if self._ticks % 7 == 0:
                self.filter.find_canvases()
            self.filter.keep_first()

    def shutdown(self):
        if self.installed and self.filter is not None:
            QApplication.instance().removeEventFilter(self.filter)
            self.filter.release_canvases()
            self.installed = False


LOCK = Controller()
