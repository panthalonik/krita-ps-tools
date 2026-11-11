# -*- coding: utf-8 -*-
import traceback

from krita import Extension, Krita
from PyQt5 import sip
from PyQt5.QtCore import QEvent, QObject, QPointF, QTimer, Qt
from PyQt5.QtGui import (QColor, QCursor, QKeySequence, QPainter, QPainterPath,
                         QPen, QPixmap)
from PyQt5.QtWidgets import (QAction, QApplication, QColorDialog, QDockWidget, QMenu,
                             QMessageBox, QToolButton, QTreeView)

from . import (adjustments, axislock, brushbar, clip, core, groupmask, masks, optionsbar,
               pslayers, side_strip)
from .widgets import adjustment_icon, make_icon
from .dialog import BlendIfDialog, BlendIfSession

TITLE = u"Blend If / Masks"


def log(text):
    """Small diagnostic log: <Krita resource folder>/ps_tools.log"""
    try:
        import os, time
        path = os.path.join(Krita.instance().getAppDataLocation(), "ps_tools.log")
        if os.path.exists(path) and os.path.getsize(path) > 200000:
            os.remove(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except Exception:
        pass


def _parent():
    w = Krita.instance().activeWindow()
    return w.qwindow() if w else None


def _warn(text):
    QMessageBox.warning(_parent(), TITLE, text)


def _safe(fn):
    def wrapper(*_a):
        try:
            fn()
        except core.BlendIfError as e:
            _warn(str(e))
        except Exception:
            _warn(u"Plugin error:\n\n" + traceback.format_exc())
    return wrapper


# ------------------------------------------------------------- actions
def open_blend_if():
    doc = Krita.instance().activeDocument()
    if doc is None:
        return
    layer = core.target_layer(doc.activeNode())
    if layer is None:
        _warn(u"Select the layer you want to set Blend If for.")
        return
    QApplication.setOverrideCursor(Qt.WaitCursor)
    try:
        session = BlendIfSession(doc, layer)
    finally:
        QApplication.restoreOverrideCursor()
    dlg = BlendIfDialog(session, _parent())
    dlg.exec_()


def refresh_blend_if():
    doc = Krita.instance().activeDocument()
    if doc is None:
        return
    from . import autorefresh
    autorefresh.refresh_everything(doc)


def smart_convert():
    from . import smart
    try:
        smart.convert()
    except smart.SmartError as e:
        _warn(str(e))


def smart_edit():
    from . import smart
    try:
        smart.edit_contents()
    except smart.SmartError as e:
        _warn(str(e))


def show_properties_docker():
    w = Krita.instance().activeWindow()
    if w is None:
        return
    for d in w.qwindow().findChildren(QDockWidget):
        if d.objectName() == "ps_tools_properties":
            d.show()
            d.raise_()
            return


def create_adjustment(kind):
    params = None
    if kind == "solid_color":
        c = QColorDialog.getColor(QColor("#808080"), _parent(), u"Color Fill")
        if not c.isValid():
            return
        params = adjustments.defaults(kind)
        params["color"] = c.name()
    from . import baked, pixelops
    try:
        node = adjustments.create(kind, params)
    except (baked.BakeError, pixelops.PixelFormatError) as e:
        _warn(str(e))
        return
    if node is None:
        _warn(u"Could not create the layer (is a document open?).")
        return
    show_properties_docker()
    if kind in ("gradient", "pattern"):
        Krita.instance().action("layer_properties").trigger()


def adjustment_menu(parent):
    menu = QMenu(parent)
    menu.setToolTipsVisible(True)
    for item in adjustments.MENU:
        if item is None:
            menu.addSeparator()
            continue
        kind, label = item
        a = menu.addAction(label.replace("&", "&&"))
        if kind in adjustments.UNAVAILABLE:
            a.setEnabled(False)
            a.setToolTip(adjustments.UNAVAILABLE[kind])
        else:
            a.triggered.connect(_safe(lambda k=kind: create_adjustment(k)))
    return menu


# ------------------------------------------- hooking the layers menu
class MenuCatcher(QObject):
    """Enabled only for a split second (while the Layers docker context
    menu is opening) - appends our entries to that menu."""

    def __init__(self, ext):
        super(MenuCatcher, self).__init__()
        self.ext = ext
        self.armed = False

    def arm(self):
        if self.armed:
            return
        self.armed = True
        QApplication.instance().installEventFilter(self)
        QTimer.singleShot(0, self.disarm)   # safety net

    def disarm(self):
        if self.armed:
            self.armed = False
            QApplication.instance().removeEventFilter(self)

    def eventFilter(self, obj, ev):
        if not self.armed:
            return False
        if ev.type() in (QEvent.Polish, QEvent.Show) and isinstance(obj, QMenu):
            try:
                if self.ext.inject_into_menu(obj):
                    self.disarm()
            except Exception:
                self.disarm()
        return False


def _clip_cursor():
    pm = QPixmap(26, 26)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.moveTo(5, 2)
    path.lineTo(5, 13)
    path.lineTo(15, 13)
    for col, w in ((QColor(0, 0, 0), 4.0), (QColor(255, 255, 255), 2.0)):
        p.setPen(QPen(col, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.drawLine(QPointF(15, 13), QPointF(11, 9))
        p.drawLine(QPointF(15, 13), QPointF(11, 17))
        p.drawRect(13, 18, 10, 6)
    p.end()
    return QCursor(pm, 5, 13)


class LayerViewHook(QObject):
    """Layers docker event filter: context menu + Alt+click between
    layers (clipping mask like PS)."""
    EDGE = 4

    def __init__(self, catcher, view, ext):
        super(LayerViewHook, self).__init__()
        self.ext = ext
        self.vp = view.viewport()
        self.dead = False
        self.catcher = catcher
        self.view = view
        self._swallow_release = False
        self._cursor = None
        self._cursor_on = False

    def _boundary(self, pos):
        view = self.view
        idx = view.indexAt(pos)
        if not idx.isValid():
            return None
        rect = view.visualRect(idx)
        if pos.y() - rect.top() <= self.EDGE:
            upper, lower = view.indexAbove(idx), idx
        elif rect.bottom() - pos.y() <= self.EDGE:
            upper, lower = idx, view.indexBelow(idx)
        else:
            return None
        if not upper.isValid() or not lower.isValid():
            return None
        return upper, lower

    def _set_clip_cursor(self, on):
        if on == self._cursor_on:
            return
        self._cursor_on = on
        vp = self.view.viewport()
        if on:
            if self._cursor is None:
                self._cursor = _clip_cursor()
            vp.setCursor(self._cursor)
        else:
            vp.unsetCursor()

    def eventFilter(self, obj, ev):
        # Krita closing: the docker may already be deleted -> just ignore
        if self.dead:
            return False
        try:
            if sip.isdeleted(self.view) or sip.isdeleted(self.vp):
                self.dead = True
                return False
            return self._filter(obj, ev)
        except RuntimeError:
            self.dead = True
            return False

    def _filter(self, obj, ev):
        t = ev.type()
        if t == QEvent.ContextMenu:
            self.catcher.arm()
            return False
        if obj is not self.vp:
            return False
        if t == QEvent.MouseMove and self._swallow_release and ev.buttons() != Qt.NoButton:
            return True                     # our click: no drag of the selected item
        if t == QEvent.MouseMove:
            alt = bool(ev.modifiers() & Qt.AltModifier)
            self._set_clip_cursor(alt and ev.buttons() == Qt.NoButton
                                  and self._boundary(ev.pos()) is not None)
            return False
        if t == QEvent.Leave:
            self._set_clip_cursor(False)
            return False
        if (t in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick)
                and ev.button() == Qt.LeftButton and ev.modifiers() & Qt.AltModifier):
            doc = Krita.instance().activeDocument()
            if doc is None:
                return False
            b = self._boundary(ev.pos())
            if b is None:
                # Alt+click on a mask = show the mask on the canvas (like PS)
                node = clip.node_for_index(doc, self.view.indexAt(ev.pos()))
                if node is None or not core.is_mask(node):
                    return False
                self._swallow_release = True
                QTimer.singleShot(0, _safe(lambda: self.ext.toggle_mask_view(doc, node)))
                return True
            upper = clip.node_for_index(doc, b[0])
            if upper is None or not core.is_layer(upper):
                return False
            self._swallow_release = True
            QTimer.singleShot(0, _safe(lambda: clip.toggle(doc, upper)))
            return True
        if (t == QEvent.MouseButtonDblClick and ev.button() == Qt.LeftButton
                and not ev.modifiers()):
            doc = Krita.instance().activeDocument()
            idx = self.view.indexAt(ev.pos())
            if doc is not None and idx.isValid():
                node = clip.node_for_index(doc, idx)
                if node is not None and node.type() == "filelayer" and \
                        self._on_thumbnail(idx, ev.pos()):
                    self._swallow_release = True
                    QTimer.singleShot(0, _safe(lambda: _edit_node(node)))
                    return True
        if t == QEvent.MouseButtonRelease and self._swallow_release:
            self._swallow_release = False
            return True
        return False

    def _on_thumbnail(self, idx, pos):
        """True when pos is over the layer thumbnail (left part of the row,
        before the name)."""
        rect = self.view.visualRect(idx)
        x = pos.x() - rect.left()
        return -2 <= x <= rect.height() + 2      # the thumbnail is a square at the row start


def _edit_node(node):
    from . import smart
    try:
        smart.edit_contents(node)
    except smart.SmartError as e:
        _warn(str(e))


def _icon_color(button):
    """Krita icon color (sampled from a neighbouring button) - matches the theme."""
    try:
        img = button.icon().pixmap(button.iconSize()).toImage()
        best, best_a = None, 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = QColor.fromRgba(img.pixel(x, y))
                if c.alpha() > best_a:
                    best, best_a = c, c.alpha()
        if best is not None and best_a > 128:
            best.setAlpha(255)
            return best
    except Exception:
        pass
    return None


class MaskButton(QToolButton):
    def __init__(self, ref, parent):
        super(MaskButton, self).__init__(parent)
        self._ref = ref
        self.setObjectName("psToolsAddMask")
        self.setToolTip(u"Add transparency mask\n"
                        u"(from the active selection, if any)\n"
                        u"Alt + click: hide-all mask (black / inverted)")
        self.setAutoRaise(ref.autoRaise())
        self.setIconSize(ref.iconSize())
        self.setSizePolicy(ref.sizePolicy())
        self.setMinimumSize(ref.minimumSize())
        self.setMaximumSize(ref.maximumSize())
        self.setFocusPolicy(ref.focusPolicy())
        self._update_icon()
        self.clicked.connect(self._clicked)

    def _update_icon(self):
        col = _icon_color(self._ref) or self.palette().windowText().color()
        self.setIcon(masks.make_mask_qicon(self.iconSize().width(), col))

    def changeEvent(self, e):
        try:
            if (e.type() in (QEvent.PaletteChange, QEvent.StyleChange)
                    and not sip.isdeleted(self._ref)):
                self._update_icon()
        except RuntimeError:
            pass
        super(MaskButton, self).changeEvent(e)

    def _clicked(self):
        alt = bool(QApplication.keyboardModifiers() & Qt.AltModifier)
        _safe(lambda: masks.add_mask(hide_all=alt))()


class AdjustmentButton(QToolButton):
    def __init__(self, ref, parent):
        super(AdjustmentButton, self).__init__(parent)
        self._ref = ref
        self.setObjectName("psToolsAdjustment")
        self.setToolTip(u"Create new fill or adjustment layer")
        self.setAutoRaise(ref.autoRaise())
        self.setIconSize(ref.iconSize())
        self.setSizePolicy(ref.sizePolicy())
        self.setMinimumSize(ref.minimumSize())
        self.setMaximumSize(ref.maximumSize())
        self.setFocusPolicy(ref.focusPolicy())
        self.setPopupMode(QToolButton.InstantPopup)
        self.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")
        self.setMenu(adjustment_menu(self))
        self._update_icon()

    def _update_icon(self):
        col = _icon_color(self._ref) or self.palette().windowText().color()
        self.setIcon(make_icon(adjustment_icon, self.iconSize().width(), col))

    def changeEvent(self, e):
        try:
            if (e.type() in (QEvent.PaletteChange, QEvent.StyleChange)
                    and not sip.isdeleted(self._ref)):
                self._update_icon()
        except RuntimeError:
            pass
        super(AdjustmentButton, self).changeEvent(e)


def _find_layout(layout, widget):
    if layout is None:
        return None
    if layout.indexOf(widget) >= 0:
        return layout
    for i in range(layout.count()):
        r = _find_layout(layout.itemAt(i).layout(), widget)
        if r is not None:
            return r
    return None


class PSToolsExtension(Extension):
    def __init__(self, parent):
        super(PSToolsExtension, self).__init__(parent)
        self.catcher = MenuCatcher(self)
        self.hooks = []
        self.strips = []
        self.buttons = []
        self.my_actions = {}
        self.mask_view = None
        self._shortcut_ticks = 0
        self.poll = QTimer(self)
        self.poll.setInterval(300)
        self.poll.timeout.connect(self._poll)
        # auto refresh of Blend If / computed adjustments: own timer, so that
        # nothing else in the poll can hold it up
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(150)
        self.auto_timer.timeout.connect(self._auto_tick)
        self._auto_errors = 0
        QApplication.instance().aboutToQuit.connect(self._shutdown)

    def setup(self):
        pass

    def createActions(self, window):
        defs = [
            ("ps_tools_blend_if", u"Blend If…", open_blend_if),
            ("ps_tools_blend_if_refresh", u"Refresh Blend If / Adjustments (whole document)",
             refresh_blend_if),
            ("ps_tools_paste_into_mask", u"Paste into Mask / Active Layer", masks.paste_into_active_mask),
            ("ps_tools_smart_paste", u"Paste (also into masks)", masks.smart_paste),
            ("ps_tools_clip", u"Create / Release Clipping Mask",
             clip.toggle_active),
            ("ps_tools_group", u"Group Layers (keeps clipping stacks together)",
             clip.group_selected),
            ("ps_tools_add_mask", u"Add Transparency Mask (PS-style)",
             lambda: masks.add_mask(False)),
            ("ps_tools_remove_mask", u"Remove Transparency Mask", masks.remove_mask),
            ("ps_tools_smart_convert", u"Convert to Smart Object", smart_convert),
            ("ps_tools_smart_edit", u"Edit Smart Object Contents", smart_edit),
            ("ps_tools_brush_picker", u"Brush Preset Picker (at cursor)",
             brushbar.BAR.open_at_cursor),
        ]
        log("Krita %s, plugin loaded" % Krita.instance().version())
        acts = {}
        for aid, text, fn in defs:
            a = window.createAction(aid, text, "tools/scripts")
            a.triggered.connect(_safe(fn))
            acts[aid] = a
        from . import autorefresh
        a = window.createAction("ps_tools_auto_refresh",
                                u"Auto-refresh Blend If / Adjustments", "tools/scripts")
        a.setCheckable(True)
        a.setChecked(autorefresh.enabled())
        a.toggled.connect(autorefresh.set_enabled)
        acts["ps_tools_auto_refresh"] = a
        a = window.createAction("ps_tools_ps_layers",
                                u"Photoshop-style Layers docker look", "tools/scripts")
        a.setCheckable(True)
        a.setChecked(pslayers.enabled())
        a.toggled.connect(pslayers.set_enabled)
        acts["ps_tools_ps_layers"] = a
        a = window.createAction("ps_tools_shift_axis_lock",
                                u"Shift + drag: brush strokes along X / Y", "tools/scripts")
        a.setCheckable(True)
        a.setChecked(axislock.enabled())
        a.toggled.connect(axislock.set_enabled)
        acts["ps_tools_shift_axis_lock"] = a
        self.my_actions = acts
        self._try_hook(window, 0)
        try:
            brushbar.BAR.watch(window.qwindow())     # installed from the poll
            optionsbar.BAR.watch(window.qwindow())
        except Exception:
            log("brush picker error: " + traceback.format_exc())
        if not self.poll.isActive():
            self.poll.start()
        if not self.auto_timer.isActive():
            self.auto_timer.start()

    # -- layers docker
    def _try_hook(self, window, attempt):
        try:
            if self._hook(window):
                return
        except Exception:
            traceback.print_exc()
        if attempt < 40:
            QTimer.singleShot(500, lambda: self._try_hook(window, attempt + 1))
        else:
            log("could not find the Layers docker")

    def _hook(self, window):
        qwin = window.qwindow()
        if qwin is None:
            return False
        dock = qwin.findChild(QDockWidget, "KisLayerBox")
        if dock is None:
            return False
        view = dock.findChild(QTreeView, "listLayers") or dock.findChild(QTreeView)
        if view is None:
            return False
        hook = LayerViewHook(self.catcher, view, self)
        view.installEventFilter(hook)
        view.viewport().installEventFilter(hook)
        self.hooks.append(hook)
        log("layers docker hooked")
        try:
            pslayers.install(view, self)
        except Exception:
            log("ps layers error: " + traceback.format_exc())
        try:
            strip = side_strip.install(qwin)
            if strip is not None and strip not in self.strips:
                self.strips.append(strip)
                log("side strip installed")
        except Exception:
            log("side strip error: " + traceback.format_exc())
        try:
            self._add_button(dock)
        except Exception:
            log("button error: " + traceback.format_exc())
        return True

    # -- Photoshop shortcuts (Ctrl+V into masks, Ctrl+Alt+G = clipping mask)
    def _my_action(self, name):
        a = Krita.instance().action(name)
        if a is None:
            a = self.my_actions.get(name)
            if a is not None and sip.isdeleted(a):
                a = None                # its window was closed
        return a

    def _take_shortcut(self, mine_name, keys, other_name, must_take=False):
        """Gives our action Krita's shortcut `keys` of `other_name`. Only
        while our action has no shortcut and the other one still has that
        one, so a user's own choice in Keyboard Shortcuts wins. must_take:
        only when the shortcut can be taken from the other action."""
        mine = self._my_action(mine_name)
        if mine is None or not mine.shortcut().isEmpty():
            return
        ks = QKeySequence(keys)
        other = Krita.instance().action(other_name)
        if other is not None and other.shortcut() == ks:
            other.setShortcut(QKeySequence())
            mine.setShortcut(ks)
            log("%s taken from %s" % (keys, other_name))
        elif not must_take and (other is None or other.shortcut().isEmpty()):
            mine.setShortcut(ks)
            log("%s assigned" % keys)

    def _take_shortcuts(self):
        """Ctrl+Alt+G = clipping mask (like PS), taken from Krita's Quick
        Ungroup; Ctrl+G = our Quick Group that keeps clipping stacks whole."""
        self._take_shortcut("ps_tools_clip", "Ctrl+Alt+G", "quick_ungroup")
        self._take_shortcut("ps_tools_group", "Ctrl+G", "create_quick_group",
                            must_take=True)
        self._menu_ticks = getattr(self, "_menu_ticks", 0) + 1
        if self._menu_ticks % 10 == 1:          # main menu: now and then
            try:
                for w in Krita.instance().windows():
                    qwin = w.qwindow()
                    if qwin is not None and qwin.menuBar() is not None:
                        for m in qwin.menuBar().findChildren(QMenu):
                            self.swap_group_action(m, deep=False)
            except Exception:
                log("menu error: " + traceback.format_exc())

    def swap_group_action(self, menu, deep=True, depth=0):
        """Puts our "Group Layers" in place of Krita's Quick Group in a menu
        (and its submenus), so grouping from a menu keeps clipping stacks
        together as well."""
        mine = self._my_action("ps_tools_group")
        if mine is None or menu is None:
            return
        for a in list(menu.actions()):
            if a.objectName() == "create_quick_group":
                if mine not in menu.actions():
                    menu.insertAction(a, mine)
                menu.removeAction(a)
            elif deep and depth < 3 and a.menu() is not None:
                self.swap_group_action(a.menu(), deep, depth + 1)

    # -- mask view (Alt+click on a mask)
    def toggle_mask_view(self, doc, node):
        iso = Krita.instance().action("isolate_active_layer")
        if iso is None:
            return
        active = doc.activeNode()
        same = active is not None and active.uniqueId() == node.uniqueId()
        if iso.isChecked() and same:
            iso.trigger()                      # exit mask view
            self.mask_view = None
            return
        if not same:
            doc.setActiveNode(node)
        if not iso.isChecked():
            iso.trigger()
        self.mask_view = node.uniqueId()

    def _auto_tick(self):
        if getattr(self, "_auto_running", False):
            return
        self._auto_running = True
        try:
            from . import autorefresh
            doc = Krita.instance().activeDocument()
            if doc is not None:
                autorefresh.tick(doc)
        except Exception:
            self._auto_errors += 1
            if self._auto_errors <= 5:
                log("auto refresh error: " + traceback.format_exc())
        finally:
            self._auto_running = False

    def _shutdown(self):
        try:
            axislock.LOCK.shutdown()
        except Exception:
            pass
        try:
            brushbar.BAR.shutdown()
        except Exception:
            pass
        self.poll.stop()
        self.auto_timer.stop()
        for s in self.strips:
            try:
                s._watch_canvas(False)
            except RuntimeError:
                pass
        self.catcher.disarm()
        for h in self.hooks:
            h.dead = True

    def _poll(self):
        # Krita can re-apply its shortcut scheme later (e.g. when a view is
        # opened), so keep checking (cheap) for the whole session
        self._shortcut_ticks += 1
        if self._shortcut_ticks % 3 == 0:
            try:
                self._take_shortcuts()
            except Exception:
                log("shortcut error: " + traceback.format_exc())
        try:
            axislock.LOCK.tick()
        except Exception:
            self._lock_errors = getattr(self, "_lock_errors", 0) + 1
            if self._lock_errors <= 5:
                log("axis lock error: " + traceback.format_exc())
        try:
            optionsbar.BAR.tick()
        except Exception:
            self._opts_errors = getattr(self, "_opts_errors", 0) + 1
            if self._opts_errors <= 5:
                log("options bar error: " + traceback.format_exc())
        try:
            brushbar.BAR.tick()
        except Exception:
            self._brush_errors = getattr(self, "_brush_errors", 0) + 1
            if self._brush_errors <= 5:
                log("brush picker error: " + traceback.format_exc())
        for s in list(self.strips):
            try:
                if sip.isdeleted(s):
                    self.strips.remove(s)
                    continue
                s.update_visibility()
                if s.isVisible():
                    s.sync_buttons()
            except RuntimeError:
                self.strips.remove(s)
        try:
            doc = Krita.instance().activeDocument()
            if doc is None:
                return
            if self.mask_view is not None:
                iso = Krita.instance().action("isolate_active_layer")
                active = doc.activeNode()
                if iso is None or not iso.isChecked():
                    self.mask_view = None
                elif active is None or active.uniqueId() != self.mask_view:
                    iso.trigger()              # selected something else -> leave mask view
                    self.mask_view = None
            clip.sync(doc)
            masks.maintain(doc, doc.activeNode())
            groupmask.sync(doc, doc.activeNode())
        except Exception:
            self._poll_errors = getattr(self, "_poll_errors", 0) + 1
            if self._poll_errors <= 5:
                log("poll error: " + traceback.format_exc())

    def _add_button(self, dock):
        if dock.findChild(QToolButton, "psToolsAddMask") is not None:
            return
        ref = dock.findChild(QToolButton, "bnDuplicate") or dock.findChild(QToolButton, "bnAdd")
        anchor = (dock.findChild(QToolButton, "bnProperties")
                  or dock.findChild(QToolButton, "bnRaise") or ref)
        if ref is None or anchor is None:
            return
        container = anchor.parentWidget()
        lay = _find_layout(container.layout(), anchor)
        if lay is None or not hasattr(lay, "insertWidget"):
            return
        btn = MaskButton(ref, container)
        lay.insertWidget(lay.indexOf(anchor) + 1, btn)
        self.buttons.append(btn)
        adj = AdjustmentButton(ref, container)
        lay.insertWidget(lay.indexOf(btn) + 1, adj)
        self.buttons.append(adj)
        log("mask + adjustment buttons added")

    # -- context menu
    def inject_into_menu(self, menu):
        if menu.property("psToolsDone"):
            return True
        try:
            self.swap_group_action(menu)
        except Exception:
            log("menu error: " + traceback.format_exc())
        acts = menu.actions()
        names = [a.objectName() for a in acts]
        if "layer_properties" not in names and "layer_style" not in names:
            return False
        menu.setProperty("psToolsDone", True)

        doc = Krita.instance().activeDocument()
        node = doc.activeNode() if doc else None
        new = []
        a = QAction(u"Blend If…", menu)
        a.triggered.connect(_safe(open_blend_if))
        new.append(a)
        if core.is_layer(node) and node.parentNode() is not None:
            if clip.is_clipped(node):
                a = QAction(u"Release Clipping Mask", menu)
                new.append(a)
            elif not clip.is_clip_base(node) and clip.sibling_below(node) is not None:
                a = QAction(u"Create Clipping Mask", menu)
                new.append(a)
            else:
                a = None
            if a is not None:
                a.triggered.connect(_safe(clip.toggle_active))
        if core.is_layer(node) and node.parentNode() is not None:
            a = QAction(u"Convert to Smart Object", menu)
            a.triggered.connect(_safe(smart_convert))
            new.append(a)
        if node is not None and node.type() == "filelayer":
            a = QAction(u"Edit Contents", menu)
            a.triggered.connect(_safe(smart_edit))
            new.append(a)
        if masks.layer_mask(node) is not None:
            a = QAction(u"Remove Transparency Mask", menu)
            a.triggered.connect(_safe(masks.remove_mask))
            new.append(a)
        if masks.is_pixel_mask(node):
            a = QAction(u"Paste into Mask", menu)
            a.triggered.connect(_safe(masks.paste_into_active_mask))
            new.append(a)

        anchor = "layer_style" if "layer_style" in names else "layer_properties"
        idx = names.index(anchor) + 1
        before = acts[idx] if idx < len(acts) else None
        for a in new:
            if before is None:
                menu.addAction(a)
            else:
                menu.insertAction(before, a)
        return True
