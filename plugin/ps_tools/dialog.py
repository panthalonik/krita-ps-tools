# -*- coding: utf-8 -*-
"""Blend If dialog with Photoshop-style sliders (Alt = split a slider)."""
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                             QGridLayout, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import core

_CHANNEL_LABELS = [("Gray", u"Gray"), ("Red", u"Red"),
                   ("Green", u"Green"), ("Blue", u"Blue")]
_CHANNEL_COLORS = {"Gray": QColor(255, 255, 255), "Red": QColor(255, 0, 0),
                   "Green": QColor(0, 255, 0), "Blue": QColor(0, 0, 255)}


class BlendSlider(QWidget):
    """Gradient bar with two sliders (black/white), each one split with Alt."""
    changed = pyqtSignal()
    M = 9            # side margin
    BAR_H = 14
    HANDLE = 9

    def __init__(self, parent=None):
        super(BlendSlider, self).__init__(parent)
        self.v = list(core.NEUTRAL)
        self.color = QColor(255, 255, 255)
        self._drag = None
        self.setMinimumSize(320, self.BAR_H + self.HANDLE + 8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    # -- API
    def values(self):
        return tuple(self.v)

    def setValues(self, vals):
        self.v = [int(x) for x in vals]
        self.update()

    def setColor(self, c):
        self.color = QColor(c)
        self.update()

    # -- geometry
    def _span(self):
        return max(1.0, self.width() - 2 * self.M - 1)

    def _x(self, val):
        return self.M + val / 255.0 * self._span()

    def _val(self, x):
        return (x - self.M) / self._span() * 255.0

    # -- painting
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        bar = QRectF(self.M, 2, self._span(), self.BAR_H)
        g = QLinearGradient(bar.topLeft(), bar.topRight())
        g.setColorAt(0, QColor(0, 0, 0))
        g.setColorAt(1, self.color)
        p.fillRect(bar, g)
        p.setPen(QPen(self.palette().mid().color(), 1))
        p.drawRect(bar.adjusted(-0.5, -0.5, 0.5, 0.5))
        y = bar.bottom() + 2
        b0, b1, w0, w1 = self.v
        self._handle(p, b0, b1, y, QColor(20, 20, 20))
        self._handle(p, w0, w1, y, QColor(245, 245, 245))
        p.end()

    def _handle(self, p, a, b, y, fill):
        hs = self.HANDLE
        p.setPen(QPen(QColor(128, 128, 128), 1))
        p.setBrush(fill)
        if a == b:
            x = self._x(a)
            p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x + hs / 1.6, y + hs),
                                     QPointF(x - hs / 1.6, y + hs)]))
        else:   # split: two halves
            xa, xb = self._x(a), self._x(b)
            p.drawPolygon(QPolygonF([QPointF(xa, y), QPointF(xa, y + hs),
                                     QPointF(xa - hs / 1.6, y + hs)]))
            p.drawPolygon(QPolygonF([QPointF(xb, y), QPointF(xb + hs / 1.6, y + hs),
                                     QPointF(xb, y + hs)]))

    # -- mouse
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        x = e.pos().x()
        b0, b1, w0, w1 = self.v
        db = min(abs(x - self._x(b0)), abs(x - self._x(b1)))
        dw = min(abs(x - self._x(w0)), abs(x - self._x(w1)))
        if db == dw:      # overlapping - pick by click side
            which = "b" if x <= self._x(b1) else "w"
        else:
            which = "b" if db < dw else "w"
        alt = bool(e.modifiers() & Qt.AltModifier)
        lo, hi = (b0, b1) if which == "b" else (w0, w1)
        if not alt:
            part = "both"
        elif lo != hi:
            part = 0 if abs(x - self._x(lo)) <= abs(x - self._x(hi)) else 1
        else:
            part = "split"      # direction decided on first move
        self._drag = (which, part, x, list(self.v))

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        which, part, sx, sv = self._drag
        d = int(round(self._val(e.pos().x()) - self._val(sx)))
        if part == "split":
            if d == 0:
                return
            part = 1 if d > 0 else 0
            self._drag = (which, part, sx, sv)
        b0, b1, w0, w1 = sv
        if which == "b":
            if part == "both":
                d = max(-b0, min(d, w0 - b1))
                b0, b1 = b0 + d, b1 + d
            elif part == 0:
                b0 = max(0, min(b0 + d, b1))
            else:
                b1 = max(b0, min(b1 + d, w0))
        else:
            if part == "both":
                d = max(b1 - w0, min(d, 255 - w1))
                w0, w1 = w0 + d, w1 + d
            elif part == 0:
                w0 = max(b1, min(w0 + d, w1))
            else:
                w1 = max(w0, min(w1 + d, 255))
        nv = [b0, b1, w0, w1]
        if nv != self.v:
            self.v = nv
            self.update()
            self.changed.emit()

    def mouseReleaseEvent(self, _e):
        self._drag = None


class ValueLabels(QWidget):
    """Numbers above the slider: left (black) and right (white), like PS."""

    def __init__(self, title, parent=None):
        super(ValueLabels, self).__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(title)
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        self.left = QLabel()
        self.right = QLabel()
        lay.addWidget(self.title)
        lay.addStretch(1)
        lay.addWidget(self.left)
        lay.addSpacing(18)
        lay.addWidget(self.right)

    def setValues(self, v):
        b0, b1, w0, w1 = v
        self.left.setText(str(b0) if b0 == b1 else "%d / %d" % (b0, b1))
        self.right.setText(str(w0) if w0 == w1 else "%d / %d" % (w0, w1))


class BlendIfSession(object):
    """Blend If editing state of one layer (live preview + cancel)."""

    def __init__(self, doc, layer):
        self.doc, self.layer = doc, layer
        self.mask = core.find_blendif_mask(layer)
        self.created = False
        self.initial = (core.BlendIfSettings.from_name(self.mask.name())
                        if self.mask is not None else None) or core.BlendIfSettings()
        # Krita PS: the engine renders Blend If live - no pixels to capture
        self.native = core.native_blend_if()
        self.initial_visible = self.mask.visible() if self.mask is not None else True
        w, h = doc.width(), doc.height()
        self.backup = (self.mask.pixelData(0, 0, w, h)
                       if self.mask is not None and not self.native else None)
        self.this_src = None if self.native else core.capture_this(doc, layer, self.mask)
        self.under_src = None

    def recapture(self):
        if self.native:
            return
        self.this_src = core.capture_this(self.doc, self.layer, self.mask)
        if self.under_src is not None:
            self.under_src = core.capture_under(self.doc, self.layer)

    def _apply_native(self, s):
        if self.mask is None:
            if s.is_neutral():
                return
            self.mask = core.create_blendif_mask(self.doc, self.layer, s)
            self.created = True
            return
        core.ensure_mask_blending(self.layer)
        if self.mask.name() != s.to_name():
            self.mask.setName(s.to_name())     # the engine redraws the layer
        if not self.mask.visible():
            self.mask.setVisible(True)

    def apply(self, s):
        if self.native:
            self._apply_native(s)
            return
        if s.is_neutral() and self.mask is None:
            return
        if s.uses_under() and self.under_src is None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.under_src = core.capture_under(self.doc, self.layer)
            finally:
                QApplication.restoreOverrideCursor()
        data = core.compute_mask(s, self.this_src, self.under_src)
        if self.mask is None:
            self.mask = core.create_blendif_mask(self.doc, self.layer, s)
            self.created = True
        core.write_mask(self.doc, self.mask, data)

    def finish(self, accepted, s):
        doc = self.doc
        if self.native:
            if accepted:
                if self.mask is not None and s.is_neutral():
                    self.mask.remove()
                elif self.mask is not None:
                    self._apply_native(s)
            elif self.created and self.mask is not None:
                self.mask.remove()
            elif self.mask is not None:
                self.mask.setName(self.initial.to_name())
                self.mask.setVisible(self.initial_visible)
            doc.setActiveNode(self.layer)
            return
        if accepted:
            if self.mask is not None:
                if s.is_neutral():
                    self.mask.remove()
                else:
                    self.mask.setName(s.to_name())
        else:
            if self.created and self.mask is not None:
                self.mask.remove()
            elif self.mask is not None and self.backup is not None:
                core.write_mask(doc, self.mask, self.backup, do_refresh=False)
        doc.setActiveNode(self.layer)
        core.refresh(doc)

    def remove(self):
        if self.mask is not None:
            self.mask.remove()
            self.mask = None
        self.doc.setActiveNode(self.layer)
        if not self.native:
            core.refresh(self.doc)


class BlendIfDialog(QDialog):
    def __init__(self, session, parent=None):
        super(BlendIfDialog, self).__init__(parent)
        self.session = session
        self.setWindowTitle(u"Blend If — %s" % session.layer.name())
        self._busy = False
        self._applied = session.initial.copy()

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(u"Blend If:"))
        self.combo = QComboBox()
        for key, label in _CHANNEL_LABELS:
            self.combo.addItem(label, key)
        top.addWidget(self.combo)
        top.addStretch(1)
        root.addLayout(top)

        self.lbl_this = ValueLabels(u"This Layer:")
        self.sl_this = BlendSlider()
        self.lbl_under = ValueLabels(u"Underlying Layer:")
        self.sl_under = BlendSlider()
        for w in (self.lbl_this, self.sl_this):
            root.addWidget(w)
        root.addSpacing(8)
        for w in (self.lbl_under, self.sl_under):
            root.addWidget(w)

        hint = QLabel(u"Alt + drag a half of a slider to split it "
                      u"(smooth transition).")
        hint.setWordWrap(True)
        f = hint.font()
        f.setItalic(True)
        hint.setFont(f)
        root.addWidget(hint)

        opts = QHBoxLayout()
        self.preview = QCheckBox(u"Live preview")
        self.preview.setChecked(True)
        opts.addWidget(self.preview)
        opts.addStretch(1)
        btn_refresh = QPushButton(u"Refresh pixels")
        btn_refresh.setToolTip(u"Re-read the layer pixels (e.g. after painting).")
        btn_reset = QPushButton(u"Reset")
        if session.native:
            # rendered live by the engine: nothing to re-read
            btn_refresh.hide()
            self.preview.hide()
        opts.addWidget(btn_refresh)
        opts.addWidget(btn_reset)
        root.addLayout(opts)

        btns = QHBoxLayout()
        self.btn_remove = QPushButton(u"Remove Blend If")
        btns.addWidget(self.btn_remove)
        btns.addStretch(1)
        btn_cancel = QPushButton(u"Cancel")
        btn_ok = QPushButton(u"OK")
        btn_ok.setDefault(True)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        root.addLayout(btns)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._apply_now)

        self._load(session.initial)
        self.combo.currentIndexChanged.connect(self._channel_changed)
        self.sl_this.changed.connect(self._changed)
        self.sl_under.changed.connect(self._changed)
        self.preview.toggled.connect(self._changed)
        btn_reset.clicked.connect(self._reset)
        btn_refresh.clicked.connect(self._refresh)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        self.btn_remove.clicked.connect(self._remove)
        self.removed = False

    # -- state
    def settings(self):
        return core.BlendIfSettings(self.combo.currentData(),
                                    self.sl_this.values(), self.sl_under.values())

    def _load(self, s):
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(max(0, core.CHANNELS.index(s.channel)))
        self.combo.blockSignals(False)
        self.sl_this.setValues(s.this)
        self.sl_under.setValues(s.under)
        self._update_labels()

    def _update_labels(self):
        c = _CHANNEL_COLORS[self.combo.currentData()]
        self.sl_this.setColor(c)
        self.sl_under.setColor(c)
        self.lbl_this.setValues(self.sl_this.values())
        self.lbl_under.setValues(self.sl_under.values())

    def _channel_changed(self, _i):
        self._update_labels()
        self._changed()

    def _changed(self, *_a):
        self._update_labels()
        if self.preview.isChecked() and not self._timer.isActive():
            self._timer.start()

    def _apply_now(self):
        if self._busy:
            self._timer.start()
            return
        s = self.settings()
        if s == self._applied:
            return
        self._busy = True
        try:
            self.session.apply(s)
            self._applied = s
        except core.BlendIfError as e:
            QMessageBox.warning(self, u"Blend If", str(e))
        finally:
            self._busy = False
        if self.settings() != self._applied and self.preview.isChecked():
            self._timer.start()      # the slider moved in the meantime

    def _reset(self):
        self._load(core.BlendIfSettings(self.combo.currentData()))
        self._changed()

    def _refresh(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.session.recapture()
            self._applied = None
            self._apply_now()
        finally:
            QApplication.restoreOverrideCursor()

    def _remove(self):
        self._timer.stop()
        self.session.remove()
        self.removed = True
        QDialog.reject(self)

    def accept(self):
        self._timer.stop()
        s = self.settings()
        if s != self._applied:
            self._apply_now()
        self.session.finish(True, s)
        QDialog.accept(self)

    def reject(self):
        self._timer.stop()
        if not self.removed:
            self.session.finish(False, self.session.initial)
        QDialog.reject(self)
