# -*- coding: utf-8 -*-
"""Photoshop-like brush picker in the tool options bar.

A button with the current preset's thumbnail and size replaces Krita's
"Edit brush settings" and "Choose brush preset" buttons (and the toolbar
"Size" slider); they can be shown again from the panel's gear menu. The
brush editor stays available from the panel and with F5.
Clicking it opens a panel like Photoshop's Brush Preset Picker:
  * Size (px) and Hardness (%) sliders with a tip preview,
  * search field and tag filter (Krita's own preset tags),
  * row of recently used brushes (each remembers its size),
  * list / thumbnail view of all presets with a thumbnail-size slider.

Hardness = "Fade" of an auto-brush (round / square) tip, the same value as
Krita's Brush Editor -> Brush Tip -> Fade. Like in Photoshop it is disabled
for image (predefined) tips and for brush engines without a tip."""
import json
import os
import traceback
import xml.etree.ElementTree as ET

from krita import Krita
from PyQt5 import sip
from PyQt5.QtCore import QMetaObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PyQt5.QtGui import (QBrush, QColor, QCursor, QFont, QIcon, QImage, QPainter,
                         QPalette, QPen, QPixmap, QPolygonF, QRadialGradient)
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QComboBox,
                             QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListView, QListWidget,
                             QListWidgetItem, QMenu, QSizeGrip, QSlider,
                             QToolBar, QToolButton, QVBoxLayout, QWidget)

from .jumpslider import JumpSlider

GROUP = "ps_tools"
RECENT_MAX = 7
SLIDER_STEPS = 1000


# ------------------------------------------------------------- settings
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


def _get_bool(key, default):
    return _get(key, "true" if default else "false") == "true"


def _set_bool(key, value):
    _set(key, "true" if value else "false")


def _get_int(key, default):
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default


def _log(text):
    try:
        from .extension import log
        log("brush picker: " + text)
    except Exception:
        pass


# ---------------------------------------------------------- Krita state
def active_view():
    w = Krita.instance().activeWindow()
    return w.activeView() if w is not None else None


def max_size():
    """Krita's maximum brush size (Settings -> General -> Tools)."""
    try:
        return max(10.0, float(Krita.instance().readSetting("", "maximumBrushSize", "1000")))
    except Exception:
        return 1000.0


def size_text(size):
    if size < 10:
        return ("%.1f" % size).rstrip("0").rstrip(".")
    return "%d" % round(size)


class Presets(object):
    """All brush presets (name -> Resource), thumbnails and tags, cached."""

    def __init__(self):
        self.by_name = {}
        self.names = []           # sorted
        self.icons = {}           # name -> QIcon
        self.files = set()        # filenames known to the resource database
        self.tags = None          # [(tag name, set(preset names))]

    def reload(self):
        try:
            res = Krita.instance().resources("preset")
        except Exception:
            res = {}
        self.by_name = dict(res)
        self.names = sorted(self.by_name, key=lambda n: n.lower())
        self.files = set(r.filename() for r in self.by_name.values())
        for n in list(self.icons):
            if n not in self.by_name:
                del self.icons[n]

    def icon(self, name):
        ic = self.icons.get(name)
        if ic is None:
            r = self.by_name.get(name)
            img = r.image() if r is not None else QImage()
            ic = QIcon(QPixmap.fromImage(img)) if not img.isNull() else QIcon()
            self.icons[name] = ic
        return ic

    def load_tags(self):
        """Krita's preset tags, read (read-only) from its resource database."""
        self.tags = []
        try:
            import sqlite3
            from urllib.request import pathname2url
            path = os.path.join(Krita.instance().getAppDataLocation(), "resourcecache.sqlite")
            if not os.path.exists(path):
                return
            con = sqlite3.connect("file:%s?mode=ro" % pathname2url(path), uri=True, timeout=0.5)
            try:
                rows = con.execute(
                    "SELECT t.name, r.name FROM tags t "
                    "JOIN resource_types ty ON ty.id = t.resource_type_id "
                    "JOIN resource_tags rt ON rt.tag_id = t.id "
                    "JOIN resources r ON r.id = rt.resource_id "
                    "WHERE ty.name = 'paintoppresets' AND t.active = 1 "
                    "AND rt.active = 1 AND r.status = 1").fetchall()
            finally:
                con.close()
            tags = {}
            for tag, name in rows:
                if tag:
                    tags.setdefault(tag, set()).add(name)
            self.tags = sorted(tags.items(), key=lambda t: t[0].lower())
        except Exception:
            _log("tags not available: " + traceback.format_exc().splitlines()[-1])


PRESETS = Presets()


def _in_database(res):
    """A preset made from scratch is not in the resource database yet -
    Preset() must not be used on it."""
    if res is None or not res.filename():
        return False
    if res.filename() in PRESETS.files:
        return True
    PRESETS.reload()
    return res.filename() in PRESETS.files


# ------------------------------------------------------------ hardness
def _brush_definition(xml):
    """(preset root, brush_definition param, Brush element, MaskGenerator)
    for an auto-brush tip, else None."""
    root = ET.fromstring(xml)
    for p in root.iter("param"):
        if p.get("name") != "brush_definition":
            continue
        try:
            brush = ET.fromstring(p.text or "")
        except ET.ParseError:
            return None
        if brush.get("type") != "auto_brush":
            return None
        mg = brush.find("MaskGenerator")
        if mg is None or mg.get("hfade") is None:
            return None
        return root, p, brush, mg
    return None


def hardness_from_xml(xml):
    d = _brush_definition(xml)
    if d is None:
        return None
    mg = d[3]
    try:
        h = max(float(mg.get("hfade", "1")), float(mg.get("vfade", "1")))
    except ValueError:
        return None
    return min(1.0, max(0.0, h))


def xml_with_hardness(xml, value):
    d = _brush_definition(xml)
    if d is None:
        return None
    root, param, brush, mg = d
    v = "%.4f" % min(1.0, max(0.0, value))
    mg.set("hfade", v)
    mg.set("vfade", v)
    param.text = ET.tostring(brush, encoding="unicode")
    return ET.tostring(root, encoding="unicode")


def get_hardness(view):
    """0..1, or None when the current brush has no round/square tip."""
    try:
        from krita import Preset
        res = view.currentBrushPreset()
        if not _in_database(res):
            return None
        h = hardness_from_xml(Preset(res).toXML())
        if h is not None and hasattr(view, "brushFade"):
            h = min(1.0, max(0.0, view.brushFade()))      # newer Krita
        return h
    except Exception:
        _log("read hardness: " + traceback.format_exc().splitlines()[-1])
        return None


def set_hardness(view, value):
    try:
        if hasattr(view, "setBrushFade"):                 # newer Krita
            view.setBrushFade(value)
            return True
        from krita import Preset
        res = view.currentBrushPreset()
        if not _in_database(res):
            return False
        p = Preset(res)
        xml = xml_with_hardness(p.toXML(), value)
        if xml is None:
            return False
        size = view.brushSize()
        p.fromXML(xml)                 # edits the active preset (marks it changed)
        if abs(view.brushSize() - size) > 0.01:
            view.setBrushSize(size)
        return True
    except Exception:
        _log("set hardness: " + traceback.format_exc())
        return False


# ------------------------------------------------------------ recent
def load_recent():
    try:
        data = json.loads(_get("brushRecent", "[]"))
        return [(str(d["name"]), float(d["size"])) for d in data][:RECENT_MAX]
    except Exception:
        return []


def save_recent(items):
    _set("brushRecent", json.dumps([{"name": n, "size": s} for n, s in items[:RECENT_MAX]]))


# ------------------------------------------------------------- drawing
def _text_color(widget):
    return widget.palette().color(QPalette.WindowText)


def tip_pixmap(side, hardness, color, image=None, dpr=1.0):
    """Round tip preview like Photoshop's (soft edge = low hardness)."""
    pm = QPixmap(int(side * dpr), int(side * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    if hardness is None:
        if image is not None and not image.isNull():
            img = image.scaled(int(side * dpr), int(side * dpr), Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
            img.setDevicePixelRatio(dpr)
            p.drawImage(QRectF((side - img.width() / dpr) / 2, (side - img.height() / dpr) / 2,
                               img.width() / dpr, img.height() / dpr), img)
        p.end()
        return pm
    r = side / 2.0 - 1
    c = QColor(color)
    g = QRadialGradient(side / 2.0, side / 2.0, r)
    g.setColorAt(0.0, c)
    g.setColorAt(max(0.0, min(0.999, hardness)), c)
    c0 = QColor(c)
    c0.setAlpha(0)
    g.setColorAt(1.0, c0)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(QRectF(1, 1, 2 * r, 2 * r))
    p.end()
    return pm


class TipPreview(QWidget):
    def __init__(self, parent=None):
        super(TipPreview, self).__init__(parent)
        self.setFixedSize(84, 84)
        self.hardness = 1.0
        self.image = None

    def set_state(self, hardness, image):
        self.hardness, self.image = hardness, image
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        p.setPen(QPen(pal.color(QPalette.Mid)))
        p.setBrush(pal.color(QPalette.Base))
        p.drawRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1))
        side = self.width() - 16
        pm = tip_pixmap(side, self.hardness, pal.color(QPalette.Text), self.image,
                        self.devicePixelRatioF())
        p.drawPixmap(8, 8, pm)
        if self.hardness is not None:            # Photoshop-like crosshair
            col = QColor(pal.color(QPalette.Mid))
            p.setPen(QPen(col, 1))
            c = self.width() / 2.0
            p.drawLine(int(c), 10, int(c), self.height() - 10)
            p.drawLine(10, int(c), self.width() - 10, int(c))
        p.end()


# -------------------------------------------------------------- slider
class ValueSlider(QWidget):
    """Label + spin box on one row, slider below (Photoshop layout)."""

    def __init__(self, label, suffix, lo, hi, decimals, exponent, parent=None):
        super(ValueSlider, self).__init__(parent)
        self.lo, self.hi, self.exp = lo, hi, exponent
        self._busy = False
        self.on_change = None
        self.on_release = None
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(6)
        lay.setVerticalSpacing(2)
        self.label = QLabel(label)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        self.spin.setRange(lo, hi)
        self.spin.setSuffix(suffix)
        self.spin.setKeyboardTracking(False)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignRight)
        self.spin.setMinimumWidth(78)
        self.slider = JumpSlider(Qt.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        lay.addWidget(self.label, 0, 0)
        lay.addWidget(self.spin, 0, 1, Qt.AlignRight)
        lay.addWidget(self.slider, 1, 0, 1, 2)
        self.slider.valueChanged.connect(self._slid)
        self.slider.sliderReleased.connect(self._released)
        self.spin.valueChanged.connect(self._typed)

    def set_range(self, lo, hi):
        self.lo, self.hi = lo, hi
        self._busy = True
        self.spin.setRange(lo, hi)
        self._busy = False

    def _to_pos(self, v):
        t = (v - self.lo) / float(self.hi - self.lo) if self.hi > self.lo else 0.0
        t = min(1.0, max(0.0, t)) ** (1.0 / self.exp)
        return int(round(t * SLIDER_STEPS))

    def _from_pos(self, pos):
        t = (pos / float(SLIDER_STEPS)) ** self.exp
        return self.lo + t * (self.hi - self.lo)

    def value(self):
        return self.spin.value()

    def set_value(self, v):
        self._busy = True
        try:
            self.spin.setValue(v)
            self.slider.setValue(self._to_pos(v))
        finally:
            self._busy = False

    def _slid(self, pos):
        if self._busy:
            return
        v = self._from_pos(pos)
        self._busy = True
        self.spin.setValue(v)
        self._busy = False
        if self.on_change:
            self.on_change(self.spin.value(), self.slider.isSliderDown())

    def _released(self):
        if self.on_release:
            self.on_release(self.spin.value())

    def _typed(self, v):
        if self._busy:
            return
        self._busy = True
        self.slider.setValue(self._to_pos(v))
        self._busy = False
        if self.on_change:
            self.on_change(v, False)
        if self.on_release:
            self.on_release(v)


# -------------------------------------------------------- recent button
class RecentButton(QToolButton):
    def __init__(self, parent=None):
        super(RecentButton, self).__init__(parent)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(30, 30))
        self.setFixedSize(40, 50)
        f = self.font()
        f.setPointSizeF(max(6.0, f.pointSizeF() - 1.5))
        self.setFont(f)
        self.entry = None


# --------------------------------------------------------------- panel
class BrushPanel(QFrame):
    """The drop-down panel (a popup window, closes on click outside)."""

    def __init__(self, parent=None):
        super(BrushPanel, self).__init__(parent, Qt.Popup)
        self.setFrameShape(QFrame.StyledPanel)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.view = None
        self._hard_pending = None
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 6)
        root.setSpacing(6)

        # -- tip preview + Size / Hardness
        top = QHBoxLayout()
        top.setSpacing(10)
        self.preview = TipPreview()
        top.addWidget(self.preview, 0, Qt.AlignTop)
        sliders = QVBoxLayout()
        sliders.setSpacing(6)
        self.size = ValueSlider(u"Size:", u" px", 1.0, max_size(), 1, 3.0)
        self.hard = ValueSlider(u"Hardness:", u"%", 0.0, 100.0, 0, 1.0)
        sliders.addWidget(self.size)
        sliders.addWidget(self.hard)
        sliders.addStretch(1)
        top.addLayout(sliders, 1)
        self.gear = QToolButton()
        self.gear.setText(u"⚙")
        self.gear.setAutoRaise(True)
        self.gear.setPopupMode(QToolButton.InstantPopup)
        self.gear.setToolTip(u"Options")
        self.editor_btn = QToolButton()
        self.editor_btn.setAutoRaise(True)
        self.editor_btn.setToolTip(u"Brush Editor (F5)")
        try:
            ic = Krita.instance().icon("paintop_settings_02")
        except Exception:
            ic = QIcon()
        if ic.isNull():
            self.editor_btn.setText(u"≡")
        else:
            self.editor_btn.setIcon(ic)
        self.editor_btn.clicked.connect(self._open_editor)
        side = QVBoxLayout()
        side.setSpacing(2)
        side.addWidget(self.gear)
        side.addWidget(self.editor_btn)
        side.addStretch(1)
        top.addLayout(side)
        root.addLayout(top)

        # -- search + tags
        self.search = QLineEdit()
        self.search.setPlaceholderText(u"Search Brushes")
        self.search.setClearButtonEnabled(True)
        root.addWidget(self.search)
        self.tag = QComboBox()
        root.addWidget(self.tag)

        # -- recent
        self.recent_row = QHBoxLayout()
        self.recent_row.setSpacing(2)
        self.recent_buttons = []
        for _i in range(RECENT_MAX):
            b = RecentButton()
            b.clicked.connect(lambda _c=False, btn=b: self._recent_clicked(btn))
            self.recent_buttons.append(b)
            self.recent_row.addWidget(b)
        self.recent_row.addStretch(1)
        self.recent_frame = QFrame()
        self.recent_frame.setFrameShape(QFrame.StyledPanel)
        self.recent_frame.setLayout(self.recent_row)
        self.recent_row.setContentsMargins(2, 2, 2, 2)
        root.addWidget(self.recent_frame)

        # -- presets
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setUniformItemSizes(True)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        root.addWidget(self.list, 1)

        # -- thumbnail size + resize grip
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        self.thumb = JumpSlider(Qt.Horizontal)
        self.thumb.setRange(20, 96)
        self.thumb.setToolTip(u"Thumbnail size")
        bottom.addWidget(self.thumb, 1)
        bottom.addWidget(QSizeGrip(self), 0, Qt.AlignBottom | Qt.AlignRight)
        root.addLayout(bottom)

        self.resize(_get_int("brushPanelW", 330), _get_int("brushPanelH", 560))
        self.setMinimumSize(280, 380)

        self.hard_timer = QTimer(self)
        self.hard_timer.setSingleShot(True)
        self.hard_timer.setInterval(60)
        self.hard_timer.timeout.connect(self._apply_hardness)

        self.size.on_change = self._size_changed
        self.hard.on_change = self._hard_changed
        self.hard.on_release = self._hard_released
        self.search.textChanged.connect(self._filter)
        self.tag.currentIndexChanged.connect(self._tag_changed)
        self.list.itemClicked.connect(self._item_clicked)
        self.list.itemDoubleClicked.connect(lambda _i: self.close())
        self.thumb.valueChanged.connect(self._thumb_changed)
        self._build_gear()

    # -------------------------------------------------------- options
    def _build_gear(self):
        m = QMenu(self.gear)
        self.a_list = m.addAction(u"Name and thumbnail")
        self.a_grid = m.addAction(u"Thumbnails only")
        for a in (self.a_list, self.a_grid):
            a.setCheckable(True)
        self.a_list.triggered.connect(lambda: self._set_mode("list"))
        self.a_grid.triggered.connect(lambda: self._set_mode("grid"))
        m.addSeparator()
        self.a_tags = m.addAction(u"Show tag filter")
        self.a_tags.setCheckable(True)
        self.a_tags.toggled.connect(self._toggle_tags)
        self.a_recent = m.addAction(u"Show recent brushes")
        self.a_recent.setCheckable(True)
        self.a_recent.toggled.connect(self._toggle_recent)
        m.addSeparator()
        self.a_native = m.addAction(u"Hide Krita's Size slider in the toolbar")
        self.a_native.setCheckable(True)
        self.a_native.toggled.connect(self._toggle_native)
        self.a_kbtn = m.addAction(u"Show Krita's brush preset / editor buttons")
        self.a_kbtn.setCheckable(True)
        self.a_kbtn.toggled.connect(self._toggle_krita_buttons)
        m.addSeparator()
        a = m.addAction(u"Open Brush Editor (F5)")
        a.triggered.connect(self._open_editor)
        a = m.addAction(u"Clear recent brushes")
        a.triggered.connect(self._clear_recent)
        self.gear.setMenu(m)

    def _sync_options(self):
        mode = _get("brushViewMode", "list")
        self.a_list.setChecked(mode == "list")
        self.a_grid.setChecked(mode == "grid")
        for a, key, default in ((self.a_tags, "brushShowTags", True),
                                (self.a_recent, "brushShowRecent", True),
                                (self.a_native, "brushHideSizeSlider", True),
                                (self.a_kbtn, "brushShowKritaButtons", False)):
            a.blockSignals(True)
            a.setChecked(_get_bool(key, default))
            a.blockSignals(False)
        self.recent_frame.setVisible(self.a_recent.isChecked())
        self.tag.setVisible(self.a_tags.isChecked() and self.tag.count() > 1)

    def _set_mode(self, mode):
        _set("brushViewMode", mode)
        self.a_list.setChecked(mode == "list")
        self.a_grid.setChecked(mode == "grid")
        self._apply_view_mode()

    def _toggle_tags(self, on):
        _set_bool("brushShowTags", on)
        self.tag.setVisible(on and self.tag.count() > 1)
        if not on:
            self.tag.setCurrentIndex(0)

    def _toggle_recent(self, on):
        _set_bool("brushShowRecent", on)
        self.recent_frame.setVisible(on)

    def _toggle_native(self, on):
        _set_bool("brushHideSizeSlider", on)
        BAR.apply_native_visibility()

    def _open_editor(self):
        self.close()
        QTimer.singleShot(0, BAR.open_brush_editor)

    def _toggle_krita_buttons(self, on):
        _set_bool("brushShowKritaButtons", on)
        BAR.apply_native_visibility()

    def _clear_recent(self):
        BAR.recent = []
        save_recent([])
        self._fill_recent()

    # -------------------------------------------------------- opening
    def open_at(self, global_pos, above_widget=None):
        self.view = active_view()
        self._loading = True
        try:
            PRESETS.reload()
            if PRESETS.tags is None:
                PRESETS.load_tags()
            self._fill_tags()
            self._sync_options()
            self.thumb.blockSignals(True)
            self.thumb.setValue(_get_int("brushThumbSize", 40))
            self.thumb.blockSignals(False)
            self._fill_list()
            self._fill_recent()
            self._read_brush()
        finally:
            self._loading = False
        # keep the panel on the screen
        scr = QApplication.desktop().availableGeometry(global_pos)
        x = min(max(scr.left(), global_pos.x()), scr.right() - self.width())
        y = global_pos.y()
        if y + self.height() > scr.bottom():
            y = max(scr.top(), (above_widget.mapToGlobal(QPoint(0, 0)).y() - self.height())
                    if above_widget is not None else scr.bottom() - self.height())
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.list.setFocus()

    def hideEvent(self, e):
        _set("brushPanelW", str(self.width()))
        _set("brushPanelH", str(self.height()))
        if self.hard_timer.isActive():
            self.hard_timer.stop()
            self._apply_hardness()
        BAR.refresh_buttons(force=True)
        super(BrushPanel, self).hideEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            it = self.list.currentItem()
            if self.search.hasFocus():          # Enter in search = first match
                it = next((self.list.item(i) for i in range(self.list.count())
                           if not self.list.item(i).isHidden()), None)
                if it is not None:
                    self._activate(it.data(Qt.UserRole))
            elif self.list.hasFocus() and it is not None:
                self._activate(it.data(Qt.UserRole))
            self.close()
            return
        super(BrushPanel, self).keyPressEvent(e)

    # --------------------------------------------------------- brush
    def _read_brush(self):
        v = self.view
        self.size.set_range(1.0 if v is None or v.brushSize() >= 1 else 0.01, max_size())
        if v is None:
            self.size.setEnabled(False)
            self.hard.setEnabled(False)
            self.preview.set_state(None, None)
            return
        self.size.setEnabled(True)
        self.size.set_value(v.brushSize())
        h = get_hardness(v)
        res = v.currentBrushPreset()
        img = res.image() if res is not None else None
        self.hard.setEnabled(h is not None)
        self.hard.set_value(round((h if h is not None else 1.0) * 100))
        self.preview.set_state(h, img)
        self._select_current(res.name() if res is not None else None)

    def _size_changed(self, v, dragging):
        if self.view is None:
            return
        self.view.setBrushSize(v)
        BAR.note_preset()
        BAR.refresh_buttons(force=True)

    def _hard_changed(self, v, dragging):
        self._hard_pending = v / 100.0
        self.preview.set_state(self._hard_pending, None)
        if not self.hard_timer.isActive():
            self.hard_timer.start()

    def _hard_released(self, v):
        self._hard_pending = v / 100.0
        self.hard_timer.stop()
        self._apply_hardness()

    def _apply_hardness(self):
        if self._hard_pending is None or self.view is None:
            return
        val, self._hard_pending = self._hard_pending, None
        if not set_hardness(self.view, val):
            self.hard.setEnabled(False)

    # --------------------------------------------------------- tags
    def _fill_tags(self):
        cur = _get("brushTag", "")
        self.tag.blockSignals(True)
        self.tag.clear()
        self.tag.addItem(u"All brushes", "")
        for name, _members in (PRESETS.tags or []):
            self.tag.addItem(name, name)
        idx = max(0, self.tag.findData(cur))
        self.tag.setCurrentIndex(idx)
        self.tag.blockSignals(False)

    def _tag_members(self):
        name = self.tag.currentData()
        if not name:
            return None
        for n, members in (PRESETS.tags or []):
            if n == name:
                return members
        return None

    def _tag_changed(self, _i):
        if not self._loading:
            _set("brushTag", self.tag.currentData() or "")
        self._filter()

    # --------------------------------------------------------- list
    def _fill_list(self):
        self.list.clear()
        for name in PRESETS.names:
            it = QListWidgetItem(PRESETS.icon(name), name)
            it.setData(Qt.UserRole, name)
            it.setToolTip(name)
            self.list.addItem(it)
        self._apply_view_mode()
        self._filter()

    def _apply_view_mode(self):
        s = self.thumb.value()
        grid = _get("brushViewMode", "list") == "grid"
        self.list.setIconSize(QSize(s, s))
        if grid:
            self.list.setViewMode(QListView.IconMode)
            self.list.setWrapping(True)
            self.list.setGridSize(QSize(s + 8, s + 8))
            self.list.setSpacing(0)
        else:
            self.list.setViewMode(QListView.ListMode)
            self.list.setWrapping(False)
            self.list.setGridSize(QSize())
            self.list.setSpacing(1)
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setText(u"" if grid else it.data(Qt.UserRole))
        cur = self.list.currentItem()
        if cur is not None:
            self.list.scrollToItem(cur, QAbstractItemView.PositionAtCenter)

    def _thumb_changed(self, v):
        _set("brushThumbSize", str(v))
        self._apply_view_mode()

    def _filter(self, *_a):
        text = self.search.text().strip().lower()
        members = self._tag_members()
        for i in range(self.list.count()):
            it = self.list.item(i)
            name = it.data(Qt.UserRole)
            ok = (not text or text in name.lower()) and (members is None or name in members)
            it.setHidden(not ok)

    def _select_current(self, name):
        self.list.blockSignals(True)
        try:
            for i in range(self.list.count()):
                it = self.list.item(i)
                if it.data(Qt.UserRole) == name:
                    self.list.setCurrentItem(it)
                    self.list.scrollToItem(it, QAbstractItemView.PositionAtCenter)
                    return
            self.list.clearSelection()
        finally:
            self.list.blockSignals(False)

    def _activate(self, name, size=None):
        v = self.view or active_view()
        res = PRESETS.by_name.get(name)
        if v is None or res is None:
            return
        self.view = v
        BAR.note_preset()              # remember the size used with the old brush
        v.setCurrentBrushPreset(res)
        if size is not None:
            v.setBrushSize(size)
        BAR.note_preset(force=True)
        self._loading = True
        try:
            self._read_brush()
            self._fill_recent()
        finally:
            self._loading = False

    def _item_clicked(self, it):
        self._activate(it.data(Qt.UserRole))

    # -------------------------------------------------------- recent
    def _fill_recent(self):
        items = [(n, s) for n, s in BAR.recent if n in PRESETS.by_name]
        for i, b in enumerate(self.recent_buttons):
            if i < len(items):
                n, s = items[i]
                b.entry = (n, s)
                b.setIcon(PRESETS.icon(n))
                b.setText(size_text(s))
                b.setToolTip(u"%s\n%s px" % (n, size_text(s)))
                b.setVisible(True)
            else:
                b.entry = None
                b.setVisible(False)

    def _recent_clicked(self, btn):
        if btn.entry is not None:
            self._activate(btn.entry[0], btn.entry[1])


# ------------------------------------------------------- toolbar button
class BrushButton(QWidget):
    """Toolbar button: preset thumbnail, size under it, drop-down arrow."""

    def __init__(self, parent=None):
        super(BrushButton, self).__init__(parent)
        self.setObjectName("psToolsBrushButton")
        self.setToolTip(u"Brush preset picker (click)\nMouse wheel: brush size")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover)
        self.name = None
        self.size_ = 0.0
        self.image = None
        self.hover = False
        self.setFixedWidth(52)
        self.setMinimumHeight(26)

    def sizeHint(self):
        return QSize(52, 30)

    def set_state(self, name, size, image):
        if name == self.name and abs(size - self.size_) < 0.005 and image is self.image:
            return
        self.name, self.size_ = name, size
        if image is not None:
            self.image = image
        self.setToolTip(u"%s\n%s px\n\nClick: brush preset picker\nMouse wheel: size"
                        % (name or u"", size_text(size)))
        self.update()

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        pal = self.palette()
        h = self.height()
        if self.hover or BAR.panel_visible():
            c = QColor(pal.color(QPalette.Highlight))
            c.setAlpha(60)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, h - 1), 3, 3)
        left = 36
        f = QFont(self.font())
        f.setPixelSize(max(8, min(10, h // 3)))
        text_h = f.pixelSize() + 1
        side = max(10, h - text_h - 3)
        if self.image is not None and not self.image.isNull():
            dpr = self.devicePixelRatioF()
            img = self.image.scaled(int(side * dpr), int(side * dpr), Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation)
            img.setDevicePixelRatio(dpr)
            p.drawImage(QRectF((left - side) / 2.0, 1, side, side), img)
        else:
            pm = tip_pixmap(side, 0.5, _text_color(self), None, self.devicePixelRatioF())
            p.drawPixmap(int((left - side) / 2), 1, pm)
        p.setFont(f)
        p.setPen(_text_color(self) if self.isEnabled() else pal.color(QPalette.Mid))
        p.drawText(QRectF(0, h - text_h - 1, left, text_h), Qt.AlignCenter,
                   size_text(self.size_) if self.name is not None else u"–")
        # drop-down arrow
        ax, ay = left + 7, h / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(_text_color(self))
        p.drawPolygon(QPolygonF([QPointF(ax - 3.5, ay - 2), QPointF(ax + 3.5, ay - 2),
                                 QPointF(ax, ay + 2.5)]))
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if BAR.panel_visible():
                BAR.panel.close()
            else:
                BAR.open_panel(self.mapToGlobal(QPoint(0, self.height())), self)
            e.accept()
            return
        super(BrushButton, self).mousePressEvent(e)

    def wheelEvent(self, e):
        v = active_view()
        if v is None:
            return
        steps = e.angleDelta().y() / 120.0
        if not steps:
            return
        s = v.brushSize() * (1.1 ** steps)
        v.setBrushSize(min(max_size(), max(1.0, s)))
        BAR.refresh_buttons(force=True)
        e.accept()


# --------------------------------------------------------- controller
def _is_size_box(sb, maxs):
    try:
        if sb.suffix().strip().lower() == "px":
            return True
        return abs(sb.maximum() - maxs) < 0.5 and sb.minimum() <= 0.011
    except Exception:
        return False


def _find_size_slot(qwin):
    """(toolbar, action) of Krita's Size slider in the tool options bar."""
    maxs = max_size()
    for tb in qwin.findChildren(QToolBar):
        for a in tb.actions():
            w = tb.widgetForAction(a)
            if w is None or isinstance(w, BrushButton):
                continue
            for sb in w.findChildren(QDoubleSpinBox):
                # skip the unchosen sliders living in the chooser's popup
                if sb.window() != tb.window():
                    continue
                if _is_size_box(sb, maxs):
                    return tb, a
    return None, None


def _find_preset_buttons(qwin):
    """(layout, [Edit brush settings, Choose brush preset]) - the two
    buttons at the start of Krita's paintop box ("paintopbox") in the bar."""
    box = qwin.findChild(QWidget, "paintopbox")
    if box is None or box.layout() is None or box.layout().count() == 0:
        return None, []
    inner = box.layout().itemAt(0).widget()
    if inner is None or inner.layout() is None:
        return None, []
    lay = inner.layout()
    btns = [lay.itemAt(i).widget() for i in range(lay.count())]
    btns = [b for b in btns if isinstance(b, QToolButton)]
    if len(btns) < 2:
        return None, []
    return lay, btns[-2:]          # (a Tool Settings button may come first)


def _fallback_toolbar(qwin):
    for tb in qwin.findChildren(QToolBar):
        if tb.objectName() == "BrushesAndStuff":
            return tb
    return None


def _same_obj(a, b):
    try:
        return a is not None and b is not None and sip.unwrapinstance(a) == sip.unwrapinstance(b)
    except Exception:
        return a is b


class _Slot(object):
    def __init__(self, qwin):
        self.qwin = qwin
        self.button = None
        self.action = None
        self.native = None        # Krita's Size slider action (hidden)
        self.krita_buttons = []   # Krita's "Edit brush settings", "Choose brush preset"


class BrushBar(object):
    def __init__(self):
        self.slots = []
        self.panel = None
        self.recent = load_recent()
        self._cur = None
        self._cur_image = None
        self._ticks = 0
        self._recent_dirty = 0.0

    # ---------------------------------------------------- windows
    def watch(self, qwin):
        if qwin is None:
            return
        for s in self.slots:
            if s.qwin is qwin:
                return
        self.slots.append(_Slot(qwin))

    def _alive(self, obj):
        try:
            return obj is not None and not sip.isdeleted(obj)
        except Exception:
            return False

    def _install(self, slot):
        if not self._alive(slot.qwin):
            return False
        tb, act = _find_size_slot(slot.qwin)
        lay, kbtns = _find_preset_buttons(slot.qwin)
        btn = BrushButton()
        if lay is not None:
            # where Krita's "Edit brush settings" + "Choose brush preset" are
            h = kbtns[0].minimumHeight() or kbtns[0].sizeHint().height() or 32
            btn.setFixedHeight(max(24, h))
            lay.insertWidget(lay.indexOf(kbtns[0]), btn)
            slot.action = None
            where = "paintop box"
        elif tb is not None:
            slot.action = tb.insertWidget(act, btn)
            where = "toolbar '%s' (Size slider)" % tb.objectName()
        else:
            tb = _fallback_toolbar(slot.qwin)
            if tb is None:
                btn.deleteLater()
                return False
            slot.action = tb.addWidget(btn)
            where = "toolbar '%s' (end)" % tb.objectName()
        slot.button = btn
        slot.native = act
        slot.krita_buttons = kbtns
        self.apply_native_visibility()
        self.refresh_buttons(force=True)
        _log("installed in " + where)
        return True

    def apply_native_visibility(self):
        hide = _get_bool("brushHideSizeSlider", True)
        show_k = _get_bool("brushShowKritaButtons", False)
        for s in self.slots:
            if self._alive(s.native):
                if s.native.isVisible() == hide:
                    s.native.setVisible(not hide)
            for b in s.krita_buttons:
                if self._alive(b) and b.isHidden() == show_k:
                    b.setVisible(show_k)

    def open_brush_editor(self):
        """Krita's brush editor, opened under our toolbar button."""
        qwin = None
        w = Krita.instance().activeWindow()
        if w is not None:
            qwin = w.qwindow()
        for s in self.slots:
            if not _same_obj(s.qwin, qwin) or len(s.krita_buttons) < 2:
                continue
            ed = s.krita_buttons[0]
            if not self._alive(ed):
                break
            if ed.isHidden() and self._alive(s.button) and s.button.parentWidget() is ed.parentWidget():
                ed.setGeometry(s.button.geometry())     # popup is placed below this button
            if QMetaObject.invokeMethod(ed, "showPopupWidget"):
                return
            break
        a = Krita.instance().action("show_brush_editor")
        if a is not None:
            if a.isCheckable():
                a.toggle()
            else:
                a.trigger()

    # ------------------------------------------------------- panel
    def panel_visible(self):
        return self._alive(self.panel) and self.panel.isVisible()

    def open_panel(self, pos, anchor=None):
        if not self._alive(self.panel):
            self.panel = BrushPanel()
        self.panel.open_at(pos, anchor)

    def open_at_cursor(self):
        self.open_panel(QCursor.pos() + QPoint(-30, -30))

    # -------------------------------------------------------- tick
    def tick(self):
        """Called from the extension's poll timer (~300 ms)."""
        self._ticks += 1
        if self._ticks % 4 == 0:
            for s in self.slots:
                if not self._alive(s.qwin):
                    continue
                if not self._alive(s.button) or s.button.parentWidget() is None:
                    try:
                        self._install(s)
                    except Exception:
                        _log("install error: " + traceback.format_exc())
            self.slots = [s for s in self.slots if self._alive(s.qwin)]
            self.apply_native_visibility()   # Krita may re-show it after a toolbar rebuild
        self.note_preset()
        self.refresh_buttons()

    def note_preset(self, force=False):
        v = active_view()
        if v is None:
            return
        try:
            res = v.currentBrushPreset()
        except Exception:
            return
        if res is None:
            return
        name, size = res.name(), v.brushSize()
        if name != self._cur or force:
            if name != self._cur:
                self._cur_image = res.image()
            self._cur = name
            self.recent = [(n, s) for n, s in self.recent if n != name]
            self.recent.insert(0, (name, size))
            del self.recent[RECENT_MAX:]
            save_recent(self.recent)
        elif self.recent and self.recent[0][0] == name and abs(self.recent[0][1] - size) > 0.005:
            self.recent[0] = (name, size)
            self._recent_dirty += 1
            if self._recent_dirty > 10:          # don't write kritarc every tick
                self._recent_dirty = 0
                save_recent(self.recent)

    def refresh_buttons(self, force=False):
        v = active_view()
        name, size, img = None, 0.0, None
        if v is not None:
            try:
                res = v.currentBrushPreset()
                size = v.brushSize()
                if res is not None:
                    name = res.name()
                    img = self._cur_image if (name == self._cur and not force) else res.image()
            except Exception:
                pass
        for s in self.slots:
            if self._alive(s.button):
                s.button.setEnabled(v is not None)
                s.button.set_state(name, size, img)

    def shutdown(self):
        save_recent(self.recent)


BAR = BrushBar()
