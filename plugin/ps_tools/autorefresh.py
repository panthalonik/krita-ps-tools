# -*- coding: utf-8 -*-
"""Automatic refresh of everything that depends on other pixels:
Blend If masks and baked adjustment layers (Color Lookup, Selective Color).

Only needed in the official Krita. In Krita PS the engine renders Blend If,
Color Lookup and Selective Color live (C++ filters + patches/0002), so there
is nothing to refresh - only baked layers of old documents that could not be
converted to native ones (LUT file missing) still go through here.

How it stays cheap and out of the way:
  * every poll it builds a signature of the layer stack from Krita's cached
    layer thumbnails (Krita only re-renders a thumbnail when the layer really
    changed), plus visibility / opacity / blend mode / order;
  * a small thumbnail can miss a small change, so it also notices that Krita
    was working at all (a stroke / image update was running, or a mouse
    button was down over the canvas): once that is over, the dependent
    layers that can see the active layer are refreshed even when no
    thumbnail changed;
  * nothing happens while you paint: it waits until Krita has no stroke
    running, no mouse button is down, no dialog is open and the signature has
    been stable for a moment (default 0.25 s);
  * each dependent layer is refreshed only when what its result is computed
    from really changed: "This Layer" -> the layer's own pixels, "Underlying
    Layer" -> everything below it. Painting a mask of the layer, or anything
    above it, does not trigger a refresh. A refreshed layer also refreshes the
    dependent layers above it that look at what is below them;
  * the "what is below" composite is rendered on a hidden copy of the
    document (core.Offscreen), so the canvas never flashes."""
import time
import traceback

from krita import Krita
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QApplication

from . import core

GROUP = "ps_tools"
KEY_ON = "autoRefresh"
KEY_DELAY = "autoRefreshDelay"
THUMB = 96


def enabled():
    return Krita.instance().readSetting(GROUP, KEY_ON, "true") == "true"


def set_enabled(on):
    Krita.instance().writeSetting(GROUP, KEY_ON, "true" if on else "false")
    _state.clear()


def delay():
    try:
        return max(0.1, float(Krita.instance().readSetting(GROUP, KEY_DELAY, "0.25")))
    except ValueError:
        return 0.25


# ------------------------------------------------------------- signature
def _thumb_hash(node):
    try:
        img = node.thumbnail(THUMB, THUMB)
        if img is None or img.isNull():
            return 0
        bits = img.constBits()
        bits.setsize(img.byteCount())
        return hash(bytes(bits))
    except Exception:
        return 0


def scan(doc):
    """(sig, start): sig = [(node key, node signature)] in compositing order
    (children first); start[key] = index of the first entry of the node's
    subtree (its masks / children come right before the node itself)."""
    out = [("doc", (doc.width(), doc.height(), doc.colorModel(), doc.colorDepth()))]
    start = {}

    def walk(node):
        for c in node.childNodes():
            if core.is_blendif_mask(c):
                continue                      # written by us
            try:
                key = core.node_key(c)
            except Exception:
                continue
            first = len(out)
            walk(c)
            try:
                out.append((key, (c.type(), c.visible(), c.opacity(),
                                  c.blendingMode(), _thumb_hash(c))))
                start[key] = first
            except Exception:
                pass
    walk(doc.rootNode())
    return out, start


def signature(doc):
    return scan(doc)[0]


# ----------------------------------------------------- dependent layers
def dependents(doc):
    """[(layer, kind)] that need refreshing when something below changes.
    kind = ("baked", (adjustment kind, params)) or ("blendif", settings)."""
    from . import baked
    out = []
    baked_keys = {}
    for layer, kind, params in baked.iter_baked(doc):
        baked_keys[core.node_key(layer)] = (layer, ("baked", (kind, params)))
    # Krita PS: Blend If is rendered live by the engine, never refreshed here
    native = core.native_blend_if()
    if native and not baked_keys:
        return out
    for layer in core.iter_layers(doc.rootNode()):
        key = core.node_key(layer)
        if key in baked_keys:
            out.append(baked_keys[key])
            continue
        if native:
            continue
        mask = core.find_blendif_mask(layer)
        if mask is None:
            continue
        s = core.BlendIfSettings.from_name(mask.name())
        if s is not None and not s.is_neutral():
            out.append((layer, ("blendif", s)))
    return out


def _visible(node):
    while node is not None and node.parentNode() is not None:
        if not node.visible():
            return False
        node = node.parentNode()
    return True


# ------------------------------------------------------- what a layer uses
def _looks_below(layer, dep):
    """Does the result depend on the composite below the layer?"""
    what, data = dep
    if what == "baked" or core.is_filter_layer(layer):
        return True
    if data.uses_under():
        return True
    # clone / file / vector... layers: be conservative
    return data.this != core.NEUTRAL and layer.type() not in ("paintlayer", "grouplayer")


def _inputs(sig, start, pos, layer, dep):
    """Hash of everything the dependent layer's result is computed from."""
    what, data = dep
    key = sig[pos][0]
    first = start.get(key, pos)
    below = tuple(sig[:first])
    if what == "baked":
        return hash(below)
    parts = []
    if data.this != core.NEUTRAL:
        t = layer.type()
        own = sig[pos][1]
        if t == "paintlayer":
            parts.append((own[0], own[4]))          # its pixels only
        elif t == "grouplayer":
            parts.append(tuple(sig[first:pos]) + ((own[0], own[4]),))
        else:
            parts.append(tuple(sig[:pos + 1]))
    if data.uses_under() or core.is_filter_layer(layer):
        parts.append(below)
    return hash(tuple(parts))


def _positions(sig, deps):
    index = {k: i for i, (k, _s) in enumerate(sig)}
    out = []
    for layer, dep in deps:
        k = core.node_key(layer)
        if k in index:
            out.append((index[k], k, layer, dep))
    out.sort(key=lambda t: t[0])
    return out


# ------------------------------------------------------------------ state
class _DocState(object):
    def __init__(self, sig):
        self.base = sig
        self.pending = False       # something changed, waiting to settle
        self.changed_at = 0.0
        self.inputs = {}           # layer key -> _inputs() at last refresh
        self.force = False         # Krita was seen working: do not trust thumbnails


_state = {}           # document key -> _DocState
_touched = {}         # document key -> True: Krita worked on it since the last look
_quiet_until = [0.0]  # our own writes also make Krita work: ignore that
QUIET_AFTER_RUN = 0.6


def _over_canvas():
    try:
        w = QApplication.widgetAt(QCursor.pos())
        while w is not None:
            n = w.metaObject().className()
            if n.startswith("KisOpenGLCanvas") or n == "KisQPainterCanvas":
                return True
            w = w.parentWidget()
    except Exception:
        pass
    return False


def _touch(key):
    if time.time() >= _quiet_until[0]:
        _touched[key] = True


def touches(sig, start, pos, layer, dep, apos):
    """Can a change of the node at index `apos` of the signature (None =
    unknown) change the result of the dependent layer at `pos`?"""
    if apos is None:
        return True
    first = start.get(sig[pos][0], pos)
    if apos < first:
        return _looks_below(layer, dep)
    if apos > pos:
        return False
    what, data = dep
    if what == "baked":
        return False                     # its own pixels are our output
    if core.is_filter_layer(layer):
        return apos == pos               # the filter's settings
    if data.this == core.NEUTRAL:
        return False
    if layer.type() == "grouplayer":
        return True                      # anything inside the group
    return apos == pos                   # the layer's pixels, not its masks


def _active_pos(doc, sig):
    try:
        node = doc.activeNode()
        index = {k: i for i, (k, _s) in enumerate(sig)}
        while node is not None and node.parentNode() is not None:
            i = index.get(core.node_key(node))
            if i is not None:
                return i
            node = node.parentNode()     # e.g. a Blend If mask (not scanned)
    except Exception:
        pass
    return None
_dialog_was_open = [False]


def _doc_key(doc):
    try:
        return core.node_key(doc.rootNode())     # stable for the document's lifetime
    except Exception:
        return str(id(doc))


def _store_inputs(st, sig, start, deps, keep=()):
    for pos, k, layer, dep in _positions(sig, deps):
        if k in keep and k in st.inputs:
            continue
        try:
            st.inputs[k] = _inputs(sig, start, pos, layer, dep)
        except Exception:
            st.inputs.pop(k, None)


def rebaseline(doc):
    """Call after the plugin itself changed pixels."""
    if doc is None:
        return
    key = _doc_key(doc)
    try:
        sig, start = scan(doc)
        old = _state.get(key)
        st = _DocState(sig)
        deps = dependents(doc)
        # hidden layers keep their old inputs, so they refresh once shown
        hidden = set(core.node_key(l) for l, _d in deps if not _visible(l))
        if old is not None:
            st.inputs = dict(old.inputs)
        _store_inputs(st, sig, start, deps, keep=hidden)
        _state[key] = st
    except Exception:
        _state.pop(key, None)


def _log(text):
    try:
        from .extension import log
        log("auto refresh: " + text)
    except Exception:
        pass


def _busy(doc):
    """Reason why we must not touch the document now (or None)."""
    if QApplication.mouseButtons() != Qt.NoButton:
        return "mouse button down"
    if QApplication.activeModalWidget() is not None:
        return "modal dialog open"
    if QApplication.activePopupWidget() is not None:
        return "popup open"
    from .dialog import BlendIfDialog
    for w in QApplication.topLevelWidgets():
        if isinstance(w, BlendIfDialog) and w.isVisible():
            return "Blend If dialog open"
    return None


_blocked = {"since": None, "reason": None, "logged": False}


def _set_blocked(reason):
    b = _blocked
    if reason is None:
        if b["logged"]:
            _log("unblocked after %.1fs (%s)" % (time.time() - b["since"], b["reason"]))
        b.update(since=None, reason=None, logged=False)
        return
    if b["since"] is None or b["reason"] != reason:
        b.update(since=time.time(), reason=reason, logged=False)
    elif not b["logged"] and time.time() - b["since"] > 8:
        _log("waiting for more than 8 s: %s" % reason)
        b["logged"] = True


def tick(doc):
    """Called from the plugin's poll timer (every ~300 ms)."""
    if doc is None or getattr(core, "_preview_busy", 0) or not enabled():
        return
    key = _doc_key(doc)
    reason = _busy(doc)
    if reason:
        if reason == "mouse button down" and _over_canvas():
            _touch(key)
        if reason == "Blend If dialog open":
            _dialog_was_open[0] = True
        if _state.get(key) is not None and _state[key].pending:
            _set_blocked(reason)
        return
    if _dialog_was_open[0]:
        _dialog_was_open[0] = False
        _touched.pop(key, None)
        rebaseline(doc)                # the dialog already updated everything
        return
    deps = dependents(doc)
    if not deps:
        _state.pop(key, None)
        _touched.pop(key, None)
        return
    # Krita still running a stroke (painting, transform, filter preview...)?
    try:
        if not doc.tryBarrierLock():
            _touch(key)
            _set_blocked("Krita is busy (a stroke/transform is still running)")
            return
        doc.unlock()
    except Exception:
        pass
    _set_blocked(None)
    sig, start = scan(doc)
    st = _state.get(key)
    if st is None:
        _touched.pop(key, None)
        rebaseline(doc)
        return
    touched = _touched.pop(key, False)
    if sig != st.base or touched:
        from . import baked
        baked.forget()
        st.base = sig
        st.pending = True
        st.force = st.force or touched
        st.changed_at = time.time()
        return
    if not st.pending or time.time() - st.changed_at < delay():
        return
    st.pending = False
    force, st.force = st.force, False
    apos = _active_pos(doc, sig) if force else None
    todo = []
    refreshed = []                      # positions refreshed in this pass
    for pos, k, layer, dep in _positions(sig, deps):
        if not _visible(layer):
            continue                    # keeps its old inputs -> refreshes when shown
        try:
            inp = _inputs(sig, start, pos, layer, dep)
        except Exception:
            continue
        old = st.inputs.get(k)
        st.inputs[k] = inp
        first = start.get(k, pos)
        need = old is not None and inp != old
        if not need and force:
            need = touches(sig, start, pos, layer, dep, apos)
        if not need and refreshed:
            # a dependent layer below was refreshed -> its new result is part of
            # our "below" (or of our group's contents)
            if _looks_below(layer, dep) and any(p < first for p in refreshed):
                need = True
            elif (layer.type() == "grouplayer" and dep[0] == "blendif"
                  and dep[1].this != core.NEUTRAL
                  and any(first <= p < pos for p in refreshed)):
                need = True
        if need:
            todo.append((layer, dep))
            refreshed.append(pos)
    if not todo:
        return
    t0 = time.time()
    run(doc, todo, busy_cursor=False)
    rebaseline(doc)
    _touched.pop(key, None)
    _quiet_until[0] = time.time() + QUIET_AFTER_RUN
    _log("%d layer(s) in %.2fs: %s" % (len(todo), time.time() - t0,
                                       ", ".join(l.name() for l, _d in todo)))


def run(doc, todo, busy_cursor=True):
    from . import baked
    if busy_cursor:
        QApplication.setOverrideCursor(Qt.BusyCursor)
    try:
        for layer, (what, data) in todo:
            try:
                if what == "baked":
                    kind, params = data
                    baked.render(doc, layer, kind, params)
                else:
                    core.apply_settings(doc, layer, data)
            except Exception:
                from .extension import log
                log("auto refresh failed for %s:\n%s" % (layer.name(), traceback.format_exc()))
    finally:
        if busy_cursor:
            QApplication.restoreOverrideCursor()


def refresh_everything(doc):
    """Manual 'Refresh' - every dependent layer, bottom to top."""
    if doc is None:
        return 0
    deps = dependents(doc)
    sig = signature(doc)
    index = {k: i for i, (k, _s) in enumerate(sig)}
    deps.sort(key=lambda d: index.get(core.node_key(d[0]), -1))
    run(doc, deps)
    rebaseline(doc)
    return len(deps)
