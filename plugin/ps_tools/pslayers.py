# -*- coding: utf-8 -*-
"""Photoshop-like look of Krita's Layers docker.

  * clipping groups made by the plugin ("<base> (clip)") are not shown as a
    row: their layers sit at the group's level, the base layer is underlined
    and the clipped layers above it get an indent with a clipping arrow;
  * a layer's mask (its transparency mask, or the "Layer Mask" of an
    adjustment layer) is not a separate row but a second thumbnail next to
    the layer thumbnail. Click it to edit the mask, click the layer thumbnail
    to edit the layer, Shift+click the mask thumbnail = disable/enable it,
    Alt+click = show the mask alone.

Only the *painting* of the rows is changed: the plugin wraps Krita's own
row delegate, calls it to draw each row (with a shifted rectangle) and adds
its own bits. Clicks on eye / lock / alpha icons, drag & drop, renaming and
the context menu are still handled by Krita itself. The geometry used below
mirrors Krita's NodeDelegate / KisNodeViewColorScheme (Krita 5.2 - 6.0)."""
import traceback

from krita import Krita
from PyQt5 import sip
from PyQt5.QtCore import QEvent, QModelIndex, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PyQt5.QtGui import QBrush, QColor, QFontMetrics, QImage, QPainter, QPalette, QPen, QPolygonF
from PyQt5.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from . import clip, core

GROUP = "ps_tools"
KEY_ON = "psLayersLook"

ROLE_GRAY_OUT = int(Qt.UserRole) + 6
ROLE_INFO_TEXT = int(Qt.UserRole) + 12
ROLE_THUMB = int(Qt.UserRole) + 100         # KisNodeModel::BeginThumbnailRole

THUMB_MARGIN, BORDER, DECO_W, TEXT_MARGIN = 3, 1, 16, 2
CLIP_W = 16


def enabled():
    return Krita.instance().readSetting(GROUP, KEY_ON, "true") == "true"


def set_enabled(on):
    Krita.instance().writeSetting(GROUP, KEY_ON, "true" if on else "false")
    for s in list(STYLERS):
        s.apply_enabled()


def _log(text):
    try:
        from .extension import log
        log("ps layers: " + text)
    except Exception:
        pass


def path_of(index):
    p = []
    while index.isValid():
        p.append(index.row())
        index = index.parent()
    return tuple(reversed(p))


def index_of(model, path):
    idx = QModelIndex()
    for r in path:
        idx = model.index(r, 0, idx)
    return idx


def _kcolor(key, default):
    try:
        v = Krita.instance().readSetting("", key, "")
        parts = [int(x) for x in v.split(",")[:3]]
        if len(parts) == 3:
            return QColor(*parts)
    except Exception:
        pass
    return QColor(default)


class RowInfo(object):
    __slots__ = ("hide", "kind", "unindent", "mask", "uid")

    def __init__(self):
        self.hide = False
        self.kind = None          # None, "base", "clipped"
        self.unindent = 0         # clipping-group levels to take back
        self.mask = None          # folded mask: dict(path, uid, visible)
        self.uid = None


# ----------------------------------------------------------- delegate
class PSDelegate(QStyledItemDelegate):
    def __init__(self, styler):
        super(PSDelegate, self).__init__(styler.view)
        self.s = styler
        self.orig = styler.orig

    # -- forwarded parts (editing, tooltips)
    def createEditor(self, parent, option, index):
        return self.orig.createEditor(parent, self._shifted(option, index), index)

    def setEditorData(self, editor, index):
        self.orig.setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        self.orig.setModelData(editor, model, index)

    def updateEditorGeometry(self, editor, option, index):
        self.orig.updateEditorGeometry(editor, self._shifted(option, index), index)

    def destroyEditor(self, editor, index):
        self.orig.destroyEditor(editor, index)

    def editorEvent(self, event, model, option, index):
        return self.orig.editorEvent(event, model, option, index)

    def helpEvent(self, event, view, option, index):
        return self.orig.helpEvent(event, view, option, index)

    def sizeHint(self, option, index):
        info = self.s.info_for(index)
        size = self.orig.sizeHint(option, index)
        if info is not None and info.hide:
            return QSize(size.width(), 0)
        return size

    # -- geometry
    def _shifted(self, option, index):
        info = self.s.info_for(index)
        if info is None or index.column() != 0:
            return option
        opt = QStyleOptionViewItem(option)
        left = option.rect.left() - info.unindent * self.s.view.indentation()
        if info.kind == "clipped":
            left += CLIP_W
        if info.mask is not None:
            left += self.s.slot_w()
        opt.rect = QRect(QPoint(left, option.rect.top()), option.rect.bottomRight())
        return opt

    # -- painting
    def paint(self, p, option, index):
        info = self.s.info_for(index)
        if info is None:
            self.orig.paint(p, option, index)
            return
        if info.hide:
            return
        if index.column() != 0:
            self.orig.paint(p, option, index)
            return
        try:
            self._paint(p, option, index, info)
        except Exception:
            self.s.error(traceback.format_exc())
            self.orig.paint(p, option, index)

    def _paint(self, p, option, index, info):
        s = self.s
        view = s.view
        r = option.rect
        content_left = r.left() - info.unindent * view.indentation()
        gutter = CLIP_W if info.kind == "clipped" else 0
        slot = s.slot_w() if info.mask is not None else 0
        mask_active = info.mask is not None and s.active_uid == info.mask["uid"]

        opt = QStyleOptionViewItem(option)
        if mask_active:
            opt.state |= QStyle.State_Selected
        full = QRect(QPoint(content_left, r.top()), r.bottomRight())

        p.save()
        if content_left < r.left():
            # area taken back from the indentation (branch lines are drawn there)
            p.fillRect(QRect(content_left, r.top(), r.left() - content_left, r.height()),
                       view.palette().color(QPalette.Base))
        bg = QStyleOptionViewItem(opt)
        bg.rect = full
        style = view.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, bg, p, view)
        p.restore()

        inner = QStyleOptionViewItem(opt)
        inner.rect = QRect(QPoint(content_left + gutter + slot, r.top()), r.bottomRight())
        self.orig.paint(p, inner, index)

        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        text_col = opt.palette.color(QPalette.Text)
        grid = QColor.fromRgba(view.style().styleHint(QStyle.SH_Table_GridLineColor, opt, view) & 0xffffffff)
        row_h = s.row_h
        thumb_bottom = r.top() + row_h - BORDER - 1

        # bottom grid line under the part Krita did not draw
        if gutter or slot or content_left < r.left():
            p.setRenderHint(QPainter.Antialiasing, False)
            p.setPen(grid)
            p.drawLine(content_left, thumb_bottom, inner.rect.left(), thumb_bottom)
            p.setRenderHint(QPainter.Antialiasing, True)

        if gutter:
            self._clip_arrow(p, content_left, r.top(), gutter, row_h, text_col)

        if info.mask is not None:
            layer_left = content_left + gutter
            model = index.model()
            dpr = p.device().devicePixelRatioF()
            role = ROLE_THUMB + int(s.thumb * dpr)
            gray = bool(index.data(ROLE_GRAY_OUT))
            limg = index.data(role)
            self._thumb(p, layer_left, r.top(), limg, dpr, grid, checkers=True, dim=gray)
            midx = index_of(model, info.mask["path"])
            mimg = midx.data(role) if midx.isValid() else None
            self._thumb(p, inner.rect.left(), r.top(), mimg, dpr, grid, checkers=False,
                        dim=gray or not info.mask["visible"], cover=True)
            if not info.mask["visible"]:
                self._disabled_x(p, inner.rect.left(), r.top())
            # Photoshop: the edited one (layer or mask) gets a frame
            frame_left = None
            if mask_active:
                frame_left = inner.rect.left()
            elif s.active_uid is not None and s.active_uid == info.uid:
                frame_left = layer_left
            if frame_left is not None:
                fr = QRectF(frame_left + 2.5, r.top() + 1.5, s.slot_w() - 5, row_h - 4)
                p.setPen(QPen(opt.palette.color(QPalette.HighlightedText)
                              if opt.state & QStyle.State_Selected else text_col, 1))
                p.setBrush(Qt.NoBrush)
                p.drawRect(fr)

        if info.kind == "base":
            self._underline(p, inner, index, text_col)
        p.restore()

    def _thumb(self, p, slot_left, top, img, dpr, grid, checkers, dim, cover=False):
        s = self.s
        fit = QRect(slot_left, top, s.slot_w(), s.row_h).adjusted(
            THUMB_MARGIN + BORDER, THUMB_MARGIN + BORDER, -(THUMB_MARGIN + BORDER), -(THUMB_MARGIN + BORDER))
        if not isinstance(img, QImage) or img.isNull():
            if cover:
                p.fillRect(fit, QColor(255, 255, 255))
            return
        w, h = img.width() / dpr, img.height() / dpr
        x = fit.left() + (fit.width() - w) / 2.0
        y = fit.top() + (fit.height() - h) / 2.0
        target = QRectF(x, y, w, h)
        if cover:
            # cover whatever Krita drew in this slot (the layer thumbnail)
            p.fillRect(QRectF(fit).adjusted(-1, -1, 1, 1), self.s.view.palette().color(QPalette.Base))
        old = p.opacity()
        if dim:
            p.setOpacity(0.35)
        if checkers:
            p.fillRect(target, QBrush(self.s.checkers()))
        p.drawImage(target, img)
        p.setOpacity(old)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(grid)
        p.setBrush(Qt.NoBrush)
        p.drawRect(target.adjusted(-1, -1, 0, 0))
        p.setRenderHint(QPainter.Antialiasing, True)

    def _disabled_x(self, p, slot_left, top):
        s = self.s
        fit = QRectF(slot_left, top, s.slot_w(), s.row_h).adjusted(5, 5, -5, -5)
        p.setPen(QPen(QColor(220, 40, 40), 2))
        p.drawLine(fit.topLeft(), fit.bottomRight())
        p.drawLine(fit.topRight(), fit.bottomLeft())

    def _clip_arrow(self, p, left, top, w, h, col):
        """Photoshop's clipping arrow: a short line bending down into an arrow."""
        c = QColor(col)
        c.setAlpha(200)
        p.setPen(QPen(c, 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x0 = left + w * 0.30
        x1 = left + w * 0.70
        y0 = top + h * 0.30
        y1 = top + h * 0.72
        p.drawLine(QPointF(x0, y0), QPointF(x1, y0))
        p.drawLine(QPointF(x1, y0), QPointF(x1, y1))
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(x1 - 3, y1 - 3.5), QPointF(x1 + 3, y1 - 3.5),
                                 QPointF(x1, y1 + 0.5)]))

    def _underline(self, p, inner, index, col):
        """Photoshop underlines the base layer of a clipping stack."""
        s = self.s
        name = index.data(Qt.DisplayRole) or u""
        font = index.data(Qt.FontRole)
        f = font if font is not None else inner.font
        fm = QFontMetrics(f)
        r = inner.rect
        x = r.left() + s.slot_w() + DECO_W - BORDER + TEXT_MARGIN
        avail = r.right() - x - 40
        if avail <= 0:
            return
        width = min(fm.horizontalAdvance(name), avail)
        two_lines = False
        if s.info_style != 0 and not s.info_inline and index.data(ROLE_INFO_TEXT):
            two_lines = r.height() >= fm.height() * 2
        if two_lines:
            base_y = r.top() + r.height() // 2 - fm.descent()
        else:
            base_y = r.top() + (r.height() - fm.height()) / 2.0 + fm.ascent()
        c = QColor(col)
        c.setAlpha(190)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(QPen(c, 1))
        y = int(base_y + 2)
        p.drawLine(int(x), y, int(x + width), y)


# ------------------------------------------------------------ styler
class MaskClickFilter(QObject):
    """Clicks on the mask thumbnail (it has no row of its own any more)."""

    def __init__(self, styler):
        super(MaskClickFilter, self).__init__(styler.view)
        self.s = styler
        self._eat_release = False

    def eventFilter(self, obj, ev):
        try:
            t = ev.type()
            if t == QEvent.MouseMove and self._eat_release:
                # the press was ours: the view must not see the drag either,
                # or Qt starts dragging the selected item (the mask we just
                # selected) with its stale press position
                return ev.buttons() != Qt.NoButton
            if t == QEvent.MouseButtonRelease and self._eat_release:
                self._eat_release = False
                return True
            if t in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop, QEvent.DragLeave):
                return self._drag_event(t, ev)
            if t == QEvent.MouseButtonDblClick and self._hit(ev.pos()) is not None:
                return True
            if t != QEvent.MouseButtonPress or ev.button() != Qt.LeftButton:
                return False
            if self._expand_hit(ev.pos()):
                self._eat_release = True
                return True
            hit = self._hit(ev.pos())
            if hit is None:
                return False
            self.s.mask_clicked(hit, ev.modifiers())
            self._eat_release = True
            return True
        except RuntimeError:
            return False
        except Exception:
            self.s.error(traceback.format_exc())
            return False

    # -- masks may only be dropped onto a layer (moved to it, like in PS)
    _mask_drag = False

    def _dragged_has_mask(self, ev):
        if ev.source() is not self.s.view:
            return False                     # from another document / app
        nodes = []
        try:
            nodes = list(Krita.instance().activeWindow().activeView().selectedNodes())
        except Exception:
            nodes = []
        if not nodes:
            doc = Krita.instance().activeDocument()
            n = doc.activeNode() if doc is not None else None
            nodes = [n] if n is not None else []
        return any(core.is_mask(n) for n in nodes)

    def _mask_drop_ok(self, pos):
        """A mask may only land ON a layer row (it then becomes that layer's
        mask), never between rows - there Krita would put it into the
        enclosing group, where it shows up as a stray row."""
        s = self.s
        view = s.view
        idx = view.indexAt(pos)
        if not idx.isValid():
            return False
        idx = idx.sibling(idx.row(), 0)
        info = s.info_for(idx)
        if info is not None and info.hide:
            return False
        rect = view.visualRect(idx)
        margin = max(2, min(12, int(round(rect.height() / 5.5))))
        if not (rect.top() + margin <= pos.y() <= rect.bottom() - margin):
            return False                     # Qt would insert above / below
        doc = Krita.instance().activeDocument()
        target = clip.node_for_index(doc, idx) if doc is not None else None
        if target is None or not core.is_layer(target) or clip._is_plugin_group(target):
            return False
        try:
            dragged = set(core.node_key(n) for n in
                          Krita.instance().activeWindow().activeView().selectedNodes())
        except Exception:
            dragged = set()
        for c in target.childNodes():        # one mask per layer, like PS
            if (c.type() == "transparencymask" and not core.is_blendif_mask(c)
                    and core.node_key(c) not in dragged):
                return False
        return True

    def _drag_event(self, t, ev):
        if not self.s.active:
            return False
        if t == QEvent.DragEnter:
            self._mask_drag = self._dragged_has_mask(ev)
            return False                     # Krita prepares its drop flags here
        if t == QEvent.DragLeave:
            self._mask_drag = False
            return False
        if not self._mask_drag:
            return False
        if self._mask_drop_ok(ev.pos()):
            if t == QEvent.Drop:
                self._mask_drag = False
            return False
        ev.ignore()
        if t == QEvent.Drop:
            self._mask_drag = False
        self.s.view.viewport().update()      # no stale drop indicator
        return True

    def _expand_hit(self, pos):
        """Group arrow: toggled here for every group row (rows can be drawn
        shifted, and Krita's own click area then misses the arrow)."""
        s = self.s
        if not s.active:
            return False
        idx = s.view.indexAt(pos)
        if not idx.isValid():
            return False
        idx = idx.sibling(idx.row(), 0)
        info = s.info_for(idx)
        if info is None or info.hide:
            return False
        # rows under this one, not counting the mask folded into its thumbnail
        rows = s.view.model().rowCount(idx) - (1 if info.mask is not None else 0)
        if rows <= 0:
            return False
        shift = -info.unindent * s.view.indentation() + (CLIP_W if info.kind == "clipped" else 0)
        # the arrow follows the thumbnail(s): layer, and mask when there is one
        thumbs = 2 if info.mask is not None else 1
        left = s.view.visualRect(idx).left() + shift + thumbs * s.slot_w()
        if left <= pos.x() < left + DECO_W:
            s.view.setExpanded(idx, not s.view.isExpanded(idx))
            return True
        return False

    def _hit(self, pos):
        s = self.s
        if not s.active:
            return None
        idx = s.view.indexAt(pos)
        if not idx.isValid():
            return None
        idx = idx.sibling(idx.row(), 0)
        info = s.info_for(idx)
        if info is None or info.mask is None or info.hide:
            return None
        r = s.view.visualRect(idx)
        left = r.left() - info.unindent * s.view.indentation()
        if info.kind == "clipped":
            left += CLIP_W
        mask_left = left + s.slot_w()
        if mask_left <= pos.x() < mask_left + s.slot_w():
            return info
        return None


class Styler(QObject):
    def __init__(self, view, ext):
        super(Styler, self).__init__(view)
        self.view = view
        self.ext = ext
        self.orig = view.itemDelegate()
        self.delegate = None
        self.info = {}
        self.active_uid = None
        self.active = False
        self.row_h = 27
        self.thumb = 20
        self._checkers = None
        self._checkers_key = None
        self.info_style = 0
        self.info_inline = False
        self._errors = 0
        self._sig = None
        self.click_filter = MaskClickFilter(self)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)          # safety net; model signals do the rest
        self.timer.timeout.connect(self.rebuild)
        self.soon = QTimer(self)
        self.soon.setSingleShot(True)
        self.soon.setInterval(33)
        self.soon.timeout.connect(self.rebuild)
        self.timer.start()
        self.apply_enabled()

    # -------------------------------------------------- on / off
    def apply_enabled(self):
        want = enabled()
        if want and not self.active:
            self.install()
        elif not want and self.active:
            self.uninstall()

    def install(self):
        if self.delegate is None:
            self.delegate = PSDelegate(self)
            try:
                self.orig.commitData.connect(self.delegate.commitData)
                self.orig.closeEditor.connect(self.delegate.closeEditor)
                self.orig.sizeHintChanged.connect(self.delegate.sizeHintChanged)
            except Exception:
                self.error(traceback.format_exc())
        self.view.setItemDelegate(self.delegate)
        self.view.viewport().installEventFilter(self.click_filter)
        self._connect_model()
        self.active = True
        self._sig = None
        self.rebuild()
        _log("installed")

    def uninstall(self):
        self.active = False
        self.view.viewport().removeEventFilter(self.click_filter)
        self.view.setItemDelegate(self.orig)
        self.info = {}
        self.view.doItemsLayout()
        self.view.viewport().update()

    def _connect_model(self):
        m = self.view.model()
        if m is None or getattr(self, "_model", None) is m:
            return
        self._model = m
        for sig in (m.rowsInserted, m.rowsRemoved, m.rowsMoved, m.modelReset,
                    m.layoutChanged):
            try:
                sig.connect(self._schedule)
            except Exception:
                pass
        try:
            # thumbnails change on every stroke: those updates are batched
            m.dataChanged.connect(self._schedule_data)
        except Exception:
            pass

    def _schedule(self, *a):
        clip.mark_dirty()
        if not self.soon.isActive():
            self.soon.start(33)

    def _schedule_data(self, *a):
        clip.mark_dirty()
        if not self.soon.isActive():
            self.soon.start(250)

    def error(self, text):
        self._errors += 1
        if self._errors <= 5:
            _log(text)

    # ------------------------------------------------------ data
    def slot_w(self):
        return self.thumb + 2 * THUMB_MARGIN + 2 * BORDER

    def checkers(self):
        key = self.thumb
        if self._checkers is None or self._checkers_key != key:
            step = max(2, self.thumb // 6)
            c1 = _kcolor("checkerscolor", QColor(220, 220, 220))
            c2 = _kcolor("checkerscolor2", QColor(255, 255, 255))
            img = QImage(2 * step, 2 * step, QImage.Format_ARGB32)
            g = QPainter(img)
            g.fillRect(0, 0, step, step, c1)
            g.fillRect(step, 0, step, step, c2)
            g.fillRect(step, step, step, step, c1)
            g.fillRect(0, step, step, step, c2)
            g.end()
            self._checkers, self._checkers_key = img, key
        return self._checkers

    def info_for(self, index):
        if not self.active or not self.info:
            return None
        return self.info.get(path_of(index))

    def rebuild(self):
        try:
            if sip.isdeleted(self.view):
                self.timer.stop()
                return
        except Exception:
            return
        if not self.active or not self.view.isVisible():
            return
        # painting / transforming on the canvas: look again after it
        from PyQt5.QtWidgets import QApplication
        if QApplication.mouseButtons() != Qt.NoButton and not self.view.underMouse():
            self.soon.start(250)
            return
        try:
            self._rebuild()
        except Exception:
            self.error(traceback.format_exc())

    def _rebuild(self):
        view = self.view
        model = view.model()
        doc = Krita.instance().activeDocument()
        if model is None or doc is None:
            if self.info:
                self.info = {}
                self.delegate.sizeHintChanged.emit(QModelIndex())
            return
        self._connect_model()
        # Krita's row geometry (thumbnail size from the row height)
        try:
            idx0 = model.index(0, 0, QModelIndex())
            if idx0.isValid():
                h = self.orig.sizeHint(QStyleOptionViewItem(), idx0).height()   # = Krita's row height
                if h > 10:
                    self.row_h = h
                    self.thumb = h - (2 * THUMB_MARGIN + BORDER)
        except Exception:
            pass
        try:
            self.info_style = int(Krita.instance().readSetting("", "layerInfoTextStyle", "0"))
            self.info_inline = Krita.instance().readSetting("", "useInlineLayerInfoText", "false") == "true"
        except Exception:
            pass

        info = {}
        expand = []
        native = core.native_clipping()

        def native_kinds(layers_top_first):
            """Krita PS: uid -> "base" / "clipped" from the layer order (the
            engine clips "inherit alpha" layers to the unclipped one below)."""
            out = {}
            base = None
            for n in reversed(layers_top_first):          # bottom to top
                uid = n.uniqueId().toString()
                if n.inheritAlpha() and base is not None:
                    out[uid] = "clipped"
                    out[base] = "base"
                elif not n.inheritAlpha():
                    base = uid
            return out

        def walk(pidx, node, path, unindent, in_clip):
            kids = list(reversed(node.childNodes()))
            j = 0
            rows = model.rowCount(pidx)
            row_nodes = []
            for row in range(rows):
                idx = model.index(row, 0, pidx)
                name = idx.data(Qt.DisplayRole)
                while j < len(kids) and kids[j].name() != name:
                    j += 1
                if j >= len(kids):
                    return                       # model and nodes disagree (filter on)
                row_nodes.append((row, idx, kids[j]))
                j += 1
            layers_here = [n for _r, _i, n in row_nodes if core.is_layer(n)]
            kinds = native_kinds(layers_here) if native else None
            # the base is only marked when something is clipped to it
            base = None
            if in_clip and len(layers_here) > 1 and any(n.inheritAlpha() for n in layers_here[:-1]):
                base = layers_here[-1]
            for row, idx, n in row_nodes:
                rp = path + (row,)
                ri = RowInfo()
                ri.unindent = unindent
                try:
                    ri.uid = n.uniqueId().toString()
                except Exception:
                    pass
                info[rp] = ri
                is_layer = core.is_layer(n)
                if kinds is not None and is_layer:
                    ri.kind = kinds.get(ri.uid)
                elif in_clip and is_layer:
                    ri.kind = "base" if (base is not None and clip._same(n, base)) else (
                        "clipped" if n.inheritAlpha() else None)
                if is_layer and n.type() == "grouplayer" and clip._is_plugin_group(n) \
                        and model.rowCount(idx) > 0:
                    # the plugin's clipping group: no row, its layers at its level
                    ri.hide = True
                    if not view.isExpanded(idx):
                        expand.append(idx)
                    walk(idx, n, rp, unindent + 1, True)
                    continue
                gl = clip._layers(n) if (is_layer and n.type() == "grouplayer" and not native) else []
                if len(gl) > 1 and not gl[0].inheritAlpha() and any(k.inheritAlpha() for k in gl[1:]):
                    # the user's own group holding a clipping stack: keep the
                    # group row, mark base / clipped layers inside
                    walk(idx, n, rp, unindent, True)
                    continue
                if model.rowCount(idx) > 0:
                    folded = None
                    if is_layer:
                        folded = self._foldable_mask(model, idx, n)
                    walk(idx, n, rp, unindent, False)
                    if folded is not None:
                        mrow, mnode = folded
                        mp = rp + (mrow,)
                        mi = info.get(mp)
                        if mi is not None:
                            mi.hide = True
                            ri.mask = {"path": mp, "uid": mnode.uniqueId().toString(),
                                       "visible": mnode.visible()}

        walk(QModelIndex(), doc.rootNode(), (), 0, False)

        active = doc.activeNode()
        active_uid = active.uniqueId().toString() if active is not None else None

        sig = tuple(sorted((k, v.hide, v.kind, v.unindent,
                            None if v.mask is None else (v.mask["uid"], v.mask["visible"]))
                           for k, v in info.items()))
        layout_changed = sig != self._sig
        self.info = info
        self._sig = sig
        for idx in expand:
            view.expand(idx)
        if layout_changed:
            self.delegate.sizeHintChanged.emit(QModelIndex())
        if layout_changed or active_uid != self.active_uid:
            self.active_uid = active_uid
            view.viewport().update()

    def _foldable_mask(self, model, idx, layer):
        """(row, node) of the mask shown as the layer's second thumbnail."""
        kids = list(reversed(layer.childNodes()))
        rows = model.rowCount(idx)
        want = lambda n: n.type() == "transparencymask" and not core.is_blendif_mask(n)
        j = 0
        for row in range(rows):
            name = model.index(row, 0, idx).data(Qt.DisplayRole)
            while j < len(kids) and kids[j].name() != name:
                j += 1
            if j >= len(kids):
                return None
            n = kids[j]
            j += 1
            if want(n):
                return row, n
        return None

    # ------------------------------------------------------ clicks
    def mask_clicked(self, info, mods):
        doc = Krita.instance().activeDocument()
        if doc is None:
            return
        mask = self._node_by_uid(doc, info.mask["uid"])
        if mask is None:
            return
        if mods & Qt.ShiftModifier:
            mask.setVisible(not mask.visible())          # PS: Shift+click = disable mask
            doc.refreshProjection()
        elif mods & Qt.AltModifier and self.ext is not None:
            self.ext.toggle_mask_view(doc, mask)          # PS: Alt+click = show the mask
        else:
            doc.setActiveNode(mask)
        self.rebuild()
        self.view.viewport().update()

    @staticmethod
    def _node_by_uid(doc, uid):
        def walk(n):
            for c in n.childNodes():
                if c.uniqueId().toString() == uid:
                    return c
                r = walk(c)
                if r is not None:
                    return r
            return None
        return walk(doc.rootNode())


STYLERS = []


def install(view, ext):
    for s in STYLERS:
        if s.view is view:
            return s
    s = Styler(view, ext)
    STYLERS.append(s)
    return s
