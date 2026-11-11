# -*- coding: utf-8 -*-
"""
Photoshop-like iconic side strip between the canvas and the right dockers.
Buttons open Krita's own Brush Presets popup (F6) and Brush Editor (F5)
right next to the strip, like the collapsed panels in Photoshop.
"""
import time

from krita import Krita
from PyQt5 import sip
from PyQt5.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer
from PyQt5.QtWidgets import (QApplication, QFrame, QHBoxLayout, QMenu,
                             QToolButton, QVBoxLayout, QWidget)

SETTINGS_GROUP = "ps_tools"

PANELS = [
    # key, Krita action, class inside the popup frame, icon, tooltip
    ("presets", "show_brush_presets", "KisPaintOpPresetsChooserPopup",
     "paintop_settings_01", u"Brushes (F6)"),
    ("editor", "show_brush_editor", "KisPaintOpPresetsEditor",
     "paintop_settings_02", u"Brush Settings (F5)"),
]


def _setting(key, default):
    v = Krita.instance().readSetting(SETTINGS_GROUP, key, default)
    return v


def _set_setting(key, value):
    Krita.instance().writeSetting(SETTINGS_GROUP, key, value)


def find_frame(inner_class):
    """Krita's popup frame (KisPopupButtonFrame) that holds the given widget."""
    for w in QApplication.topLevelWidgets():
        try:
            if w.objectName() != "KisPopupButtonFrame":
                continue
            for c in w.children():
                if isinstance(c, QWidget) and c.metaObject().className() == inner_class:
                    return w
        except RuntimeError:
            continue
    return None


class _FrameWatcher(QObject):
    """Remembers when a popup frame was hidden (to make the button a toggle)."""

    def __init__(self, strip, key):
        super(_FrameWatcher, self).__init__()
        self.strip, self.key = strip, key

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Hide:
            self.strip.hidden_at[self.key] = time.time()
            self.strip.sync_buttons()
        elif ev.type() == QEvent.Show:
            self.strip.sync_buttons()
        return False


class _CanvasWatcher(QObject):
    """While a panel is open: a click on the canvas collapses it (like PS)."""

    def __init__(self, strip):
        super(_CanvasWatcher, self).__init__()
        self.strip = strip

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.MouseButtonPress, QEvent.TabletPress):
            QTimer.singleShot(0, self.strip.collapse_all)
        return False


class SideStrip(QFrame):
    def __init__(self, qwin, parent=None):
        super(SideStrip, self).__init__(parent)
        self.qwin = qwin
        self.setObjectName("psToolsSideStrip")
        self.setFixedWidth(34)
        self.setStyleSheet(
            "#psToolsSideStrip { border-left: 1px solid palette(dark);"
            " border-right: 1px solid palette(dark); }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 6, 3, 6)
        lay.setSpacing(4)
        self.buttons = {}
        self.watchers = {}
        self.hidden_at = {}
        k = Krita.instance()
        for key, action, cls, icon, tip in PANELS:
            b = QToolButton(self)
            b.setAutoRaise(True)
            b.setCheckable(True)
            b.setIcon(k.icon(icon))
            b.setIconSize(QSize(22, 22))
            b.setFixedSize(28, 28)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, kk=key: self.toggle(kk))
            lay.addWidget(b, 0, Qt.AlignHCenter)
            self.buttons[key] = b
        lay.addStretch(1)
        self.canvas_watcher = _CanvasWatcher(self)
        self.watched_canvases = []
        self._watching = False
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)

    # ---------------------------------------------------------- settings
    def auto_collapse(self):
        return str(_setting("autoCollapse", "true")).lower() == "true"

    def _menu(self, pos):
        m = QMenu(self)
        a = m.addAction(u"Auto-collapse panels (click on canvas closes them)")
        a.setCheckable(True)
        a.setChecked(self.auto_collapse())
        a.toggled.connect(lambda on: _set_setting("autoCollapse", "true" if on else "false"))
        m.exec_(self.mapToGlobal(pos))

    # ------------------------------------------------------------ panels
    def _panel(self, key):
        return next(p for p in PANELS if p[0] == key)

    def frame(self, key):
        return find_frame(self._panel(key)[2])

    def toggle(self, key):
        f = self.frame(key)
        if f is not None and f.isVisible():
            f.hide()
        elif time.time() - self.hidden_at.get(key, 0) < 0.3:
            pass  # the click that closed the popup - keep it closed
        else:
            # one panel at a time, like PS
            for other in self.buttons:
                if other != key:
                    of = self.frame(other)
                    if of is not None and of.isVisible():
                        of.hide()
            Krita.instance().action(self._panel(key)[1]).trigger()
            QTimer.singleShot(0, lambda: self.place(key))
        QTimer.singleShot(50, self.sync_buttons)

    def place(self, key):
        f = self.frame(key)
        if f is None:
            return
        if key not in self.watchers:
            w = _FrameWatcher(self, key)
            f.installEventFilter(w)
            self.watchers[key] = w
        if not f.isVisible():
            f.show()
        b = self.buttons[key]
        top = b.mapToGlobal(QPoint(0, 0))
        left = self.mapToGlobal(QPoint(0, 0)).x()
        x = left - f.width() - 2
        y = top.y()
        try:
            scr = self.screen().availableGeometry()
            y = max(scr.top(), min(y, scr.bottom() - f.height()))
            x = max(scr.left(), x)
        except Exception:
            pass
        f.move(x, y)
        f.raise_()
        self.sync_buttons()

    def collapse_all(self):
        for key in self.buttons:
            f = self.frame(key)
            if f is not None and f.isVisible():
                f.hide()
        self.sync_buttons()

    def sync_buttons(self):
        any_open = False
        for key, b in self.buttons.items():
            try:
                f = self.frame(key)
                on = f is not None and f.isVisible()
            except RuntimeError:
                on = False
            any_open = any_open or on
            b.setChecked(on)
        want = any_open and self.auto_collapse()
        if want != self._watching:
            self._watching = want
            self._watch_canvas(want)

    # ----------------------------------------- auto-collapse on canvas click
    def _canvases(self):
        out = []
        for w in self.qwin.findChildren(QWidget):
            n = w.metaObject().className()
            if n.startswith("KisOpenGLCanvas") or n == "KisQPainterCanvas":
                out.append(w)
        return out

    def _watch_canvas(self, on):
        for c in self.watched_canvases:
            try:
                if not sip.isdeleted(c):
                    c.removeEventFilter(self.canvas_watcher)
            except RuntimeError:
                pass
        self.watched_canvases = []
        if on:
            for c in self._canvases():
                c.installEventFilter(self.canvas_watcher)
                self.watched_canvases.append(c)

    # -------------------------------------------------------- visibility
    def update_visibility(self):
        """Hidden on the welcome screen and in canvas-only mode (Tab)."""
        k = Krita.instance()
        w = k.activeWindow()
        has_view = bool(w and w.views())
        co = k.action("view_show_canvas_only")
        canvas_only = bool(co is not None and co.isChecked())
        want = has_view and not canvas_only
        if want != self.isVisible():
            self.setVisible(want)
            if not want:
                self.collapse_all()


def install(qwin):
    """Puts the strip between the canvas area and the right-hand dockers by
    wrapping Krita's central widget. Returns the strip (or None)."""
    central = qwin.centralWidget()
    if central is None:
        return None
    if central.objectName() == "psToolsCentralWrap":
        return central.findChild(SideStrip)
    old = qwin.takeCentralWidget()
    wrap = QWidget()
    wrap.setObjectName("psToolsCentralWrap")
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(old, 1)
    strip = SideStrip(qwin, wrap)
    lay.addWidget(strip)
    qwin.setCentralWidget(wrap)
    old.show()
    strip.update_visibility()
    return strip
