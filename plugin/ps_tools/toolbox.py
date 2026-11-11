# -*- coding: utf-8 -*-
"""Compact, Photoshop-like toolbox docker ("Tools (Photoshop-style)").

One (or two) column(s) of tool groups. A group shows its last used tool;
click = use it, press and hold or right-click = fly-out with all tools of
the group (with their shortcuts). Icons, names and the active tool come from
Krita's own Toolbox, so it follows Krita's theme and tool shortcuts; tools
the plugin does not know (e.g. from other plugins) land in an "Other" group.
Krita's Toolbox can be hidden from this docker's right-click menu."""
import json
import re

from krita import DockWidget, Krita
from PyQt5 import sip
from PyQt5.QtCore import QPoint, QPointF, QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPolygonF
from PyQt5.QtWidgets import (QAction, QActionGroup, QApplication, QDockWidget, QFrame,
                             QGridLayout, QLabel, QMainWindow, QMenu, QScrollArea,
                             QToolButton, QVBoxLayout, QWidget)

DOCKER_ID = "ps_tools_toolbox"
GROUP = "ps_tools"

# (section, group name, tool ids - first one is the default)
GROUPS = [
    (0, u"Move / Transform", ["KritaTransform/KisToolMove", "KisToolTransform"]),
    (1, u"Marquee", ["KisToolSelectRectangular", "KisToolSelectElliptical"]),
    (1, u"Lasso", ["KisToolSelectOutline", "KisToolSelectPolygonal",
                   "KisToolSelectMagnetic", "KisToolSelectPath"]),
    (1, u"Magic Wand", ["KisToolSelectContiguous", "KisToolSelectSimilar"]),
    (2, u"Crop", ["KisToolCrop"]),
    (2, u"Assistants / References", ["KisAssistantTool", "KisToolPerspectiveGrid",
                                     "ToolReferenceImages"]),
    (2, u"Eyedropper / Measure", ["KritaSelected/KisToolColorSampler", "KritaShape/KisToolMeasure"]),
    (3, u"Healing", ["KritaShape/KisToolSmartPatch"]),
    (3, u"Brush", ["KritaShape/KisToolBrush", "KritaShape/KisToolDyna",
                   "KritaShape/KisToolMultiBrush"]),
    (3, u"Gradient / Fill", ["KritaFill/KisToolGradient", "KritaFill/KisToolFill",
                             "KisToolEncloseAndFill", "KritaShape/KisToolLazyBrush"]),
    (4, u"Pen", ["KisToolPath", "KisToolPencil", "KisToolPolyline", "KarbonCalligraphyTool"]),
    (4, u"Text", ["SvgTextTool"]),
    (4, u"Shape Selection", ["InteractionTool", "PathTool"]),
    (4, u"Shapes", ["KritaShape/KisToolRectangle", "KritaShape/KisToolEllipse",
                    "KisToolPolygon", "KritaShape/KisToolLine"]),
    (5, u"Hand", ["PanTool"]),
    (5, u"Zoom", ["ZoomTool"]),
]
OTHER_SECTION = 6
SIZES = {"small": (26, 18), "medium": (32, 22), "large": (40, 28)}
HOLD_MS = 300


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


_TIP_RE = re.compile(r"^(.*?)\s*\(([^()]{1,24})\)\s*$")


def split_tooltip(tip):
    """'Freehand Brush Tool (B)' -> ('Freehand Brush Tool', 'B')."""
    tip = re.sub(r"<[^>]+>", "", tip or "").strip()
    m = _TIP_RE.match(tip)
    if m:
        return m.group(1), m.group(2)
    return tip, u""


def build_groups(available):
    """available: tool ids in Krita's toolbox order ->
    [(section, name, [ids present])] incl. an 'Other' group for the rest."""
    out, used = [], set()
    for section, name, ids in GROUPS:
        present = [t for t in ids if t in available]
        used.update(present)
        if present:
            out.append((section, name, present))
    rest = [t for t in available if t not in used]
    if rest:
        out.append((OTHER_SECTION, u"Other", rest))
    return out


def main_window(widget):
    w = widget
    while w is not None and not isinstance(w, QMainWindow):
        w = w.parentWidget()
    if w is None:
        kw = Krita.instance().activeWindow()
        w = kw.qwindow() if kw is not None else None
    return w


def native_buttons(qwin):
    """{tool id: Krita's toolbox button} in Krita's order."""
    if qwin is None:
        return {}
    dock = qwin.findChild(QDockWidget, "ToolBox")
    scope = dock if dock is not None else qwin
    out = {}
    for b in scope.findChildren(QToolButton):
        name = b.objectName()
        if b.isCheckable() and name and (dock is not None or "Tool" in name):
            out[name] = b
    return out


# ------------------------------------------------------------ button
class GroupButton(QToolButton):
    def __init__(self, box, name, ids):
        super(GroupButton, self).__init__(box)
        self.box, self.name, self.ids = box, name, ids
        self.current = box.saved_current(name, ids)
        self.setAutoRaise(True)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.hold = QTimer(self)
        self.hold.setSingleShot(True)
        self.hold.setInterval(HOLD_MS)
        self.hold.timeout.connect(self.show_menu)
        self._menu_shown = False

    # -- state
    def sync(self, natives, active):
        if active in self.ids and active != self.current:
            self.current = active
            self.box.save_current(self.name, active)
        nb = natives.get(self.current)
        if nb is None:
            return
        self.setIcon(nb.icon())
        tname, key = self.box.tool_text(self.current, nb)
        tip = tname + (u" (%s)" % key if key else u"")
        if len(self.ids) > 1:
            tip += u"\nHold or right-click: more %s tools" % self.name
        if tip != self.toolTip():
            self.setToolTip(tip)
        on = active in self.ids
        if self.isChecked() != on:
            self.setChecked(on)
        self.setEnabled(nb.isEnabled())

    # -- mouse
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.show_menu()
            return
        if e.button() == Qt.LeftButton:
            self._menu_shown = False
            self.setDown(True)
            if len(self.ids) > 1:
                self.hold.start()
            e.accept()
            return
        super(GroupButton, self).mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.hold.stop()
            self.setDown(False)
            if not self._menu_shown and self.rect().contains(e.pos()):
                self.box.activate(self.current)
            e.accept()
            return
        super(GroupButton, self).mouseReleaseEvent(e)

    def show_menu(self):
        self._menu_shown = True
        self.setDown(False)
        natives = self.box.natives
        m = QMenu(self)
        grp = QActionGroup(m)
        for tid in self.ids:
            nb = natives.get(tid)
            if nb is None:
                continue
            tname, key = self.box.tool_text(tid, nb)
            a = QAction(nb.icon(), tname + (u"\t" + key if key else u""), m)
            a.setCheckable(True)
            a.setChecked(tid == self.current)
            a.setEnabled(nb.isEnabled())
            a.setData(tid)
            grp.addAction(a)
            m.addAction(a)
        chosen = m.exec_(self.mapToGlobal(QPoint(self.width() + 1, 0)))
        if chosen is not None:
            self.current = chosen.data()
            self.box.save_current(self.name, self.current)
            self.box.activate(self.current)

    def paintEvent(self, e):
        super(GroupButton, self).paintEvent(e)
        if len(self.ids) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(self.palette().color(self.foregroundRole()))
        c.setAlpha(170)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        w, h = self.width(), self.height()
        s = max(3.0, w / 9.0)
        p.drawPolygon(QPolygonF([QPointF(w - 2, h - 2 - s), QPointF(w - 2, h - 2),
                                 QPointF(w - 2 - s, h - 2)]))
        p.end()


# ------------------------------------------------------------ toolbox
class ToolboxWidget(QWidget):
    def __init__(self, parent=None):
        super(ToolboxWidget, self).__init__(parent)
        self.natives = {}
        self.buttons = []
        self._signature = None
        self._current = self._load_current()
        self.on_width = None          # called with the width the buttons need
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(2, 2, 2, 2)
        self.grid.setSpacing(1)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.sync)
        self.timer.start()
        QTimer.singleShot(0, self.sync)

    # -- settings
    def _load_current(self):
        try:
            return dict(json.loads(_get("toolboxCurrent", "{}")))
        except Exception:
            return {}

    def saved_current(self, name, ids):
        t = self._current.get(name)
        return t if t in ids else ids[0]

    def save_current(self, name, tid):
        self._current[name] = tid
        _set("toolboxCurrent", json.dumps(self._current))

    def tool_text(self, tid, nb):
        name, key = split_tooltip(nb.toolTip())
        try:
            a = Krita.instance().action(tid)
            if a is not None:
                sc = a.shortcut().toString()
                if sc:
                    key = sc
        except Exception:
            pass
        return (name or tid), key

    # -- build
    def rebuild(self):
        for b in self.buttons:
            b.setParent(None)
            b.deleteLater()
        self.buttons = []
        while self.grid.count():
            it = self.grid.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for r in range(self.grid.rowCount()):
            self.grid.setRowStretch(r, 0)
        cols = 2 if _get("toolboxColumns", "1") == "2" else 1
        side, icon = SIZES.get(_get("toolboxSize", "medium"), SIZES["medium"])
        groups = build_groups(list(self.natives))
        row, col, last_section = 0, 0, None
        for section, name, ids in groups:
            if last_section is not None and section != last_section:
                if col:
                    row, col = row + 1, 0
                line = QFrame()
                line.setFrameShape(QFrame.HLine)
                line.setFrameShadow(QFrame.Sunken)
                self.grid.addWidget(line, row, 0, 1, cols)
                row += 1
            last_section = section
            b = GroupButton(self, name, ids)
            b.setFixedSize(side, side)
            b.setIconSize(QSize(icon, icon))
            self.grid.addWidget(b, row, col, Qt.AlignHCenter)
            self.buttons.append(b)
            col += 1
            if col >= cols:
                row, col = row + 1, 0
        self.grid.setRowStretch(row + 1, 1)
        m = self.grid.contentsMargins()
        need = cols * side + (cols - 1) * self.grid.horizontalSpacing() + m.left() + m.right()
        if self.on_width is not None:
            self.on_width(need)

    # -- sync with Krita
    def sync(self):
        if not self.isVisible() and self.buttons:
            return
        qwin = main_window(self)
        natives = native_buttons(qwin)
        sig = tuple(sorted(natives))
        if sig != self._signature:
            self.natives = natives
            self._signature = sig
            self.rebuild()
        self.natives = natives
        active = next((t for t, b in natives.items() if b.isChecked()), None)
        for b in self.buttons:
            try:
                b.sync(natives, active)
            except RuntimeError:
                pass

    def activate(self, tid):
        nb = self.natives.get(tid)
        if nb is not None and not sip.isdeleted(nb):
            nb.click()               # same as clicking Krita's own toolbox button
        else:
            a = Krita.instance().action(tid)
            if a is not None:
                a.trigger()
        QTimer.singleShot(50, self.sync)

    # -- options
    def contextMenuEvent(self, e):
        m = QMenu(self)
        a2 = m.addAction(u"Two columns")
        a2.setCheckable(True)
        a2.setChecked(_get("toolboxColumns", "1") == "2")
        sm = m.addMenu(u"Button size")
        cur = _get("toolboxSize", "medium")
        size_acts = {}
        for key, label in (("small", u"Small"), ("medium", u"Medium"), ("large", u"Large")):
            a = sm.addAction(label)
            a.setCheckable(True)
            a.setChecked(cur == key)
            size_acts[a] = key
        m.addSeparator()
        dock = None
        qwin = main_window(self)
        if qwin is not None:
            dock = qwin.findChild(QDockWidget, "ToolBox")
        ak = m.addAction(u"Show Krita's Toolbox")
        ak.setCheckable(True)
        ak.setChecked(dock is not None and dock.isVisible())
        ak.setEnabled(dock is not None)
        from . import axislock
        al = m.addAction(u"Shift + drag: brush strokes along X / Y")
        al.setCheckable(True)
        al.setChecked(axislock.enabled())
        chosen = m.exec_(e.globalPos())
        if chosen is a2:
            _set("toolboxColumns", "2" if a2.isChecked() else "1")
            self.rebuild()
            self.sync()
        elif chosen in size_acts:
            _set("toolboxSize", size_acts[chosen])
            self.rebuild()
            self.sync()
        elif chosen is ak and dock is not None:
            dock.setVisible(ak.isChecked())
        elif chosen is al:
            axislock.set_enabled(al.isChecked())
            act = Krita.instance().action("ps_tools_shift_axis_lock")
            if act is not None and act.isChecked() != al.isChecked():
                act.setChecked(al.isChecked())


class ToolboxDocker(DockWidget):
    def __init__(self):
        super(ToolboxDocker, self).__init__()
        self.setWindowTitle(u"Tools (Photoshop-style)")
        # thin grab handle instead of the title bar - like Krita's own Toolbox
        # (Krita keeps a title bar widget that a docker sets itself)
        handle = QLabel(u" ", self)
        handle.setFrameStyle(QFrame.Panel | QFrame.Raised)
        f = QFont(QApplication.font())
        f.setPointSizeF(f.pointSizeF() * 0.9)
        t = QFontMetrics(f).height()
        handle.setMinimumSize(t, t // 2)
        handle.setMaximumHeight(t // 2 + 2)
        handle.setToolTip(u"Tools (Photoshop-style) - drag to move")
        self.setTitleBarWidget(handle)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # wheel still scrolls
        self.box = ToolboxWidget()
        self.box.on_width = self._fit
        self.scroll.setWidget(self.box)
        self.setWidget(self.scroll)

    def _fit(self, width):
        """The docker is exactly as wide as its buttons."""
        self.scroll.setFixedWidth(width)
        self.box.setFixedWidth(width)

    def canvasChanged(self, canvas):
        pass
