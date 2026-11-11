# -*- coding: utf-8 -*-
"""
Photoshop-style clipping masks.

Krita clips layers with "Inherit Alpha" inside a group. The plugin builds
such a group itself: [base, clipped*, clipped*...] and names it "<base> (clip)".
Releasing moves the layer (and the ones above it) out of the group; when only
the base is left, the group is dissolved.

Undo: moving a layer through the scripting API is two undo steps (remove +
add), so one Ctrl+Z would leave the layer removed. Clip / release therefore
use Krita's own layer commands (Quick Group, Quick Ungroup, Move Layer Down)
on the layers selected in the Layers docker - each is ONE undo step. Only if
the docker cannot be found the old remove + add way is used.

Krita PS (core.native_clipping()): clipping is part of the engine. "Inherit
Alpha" IS the clipping mask: a layer with it is clipped to the nearest
unclipped layer below it, live, with no groups at all (see the "native"
section at the end). Clip / release just switch it (Krita's own undoable
"toggle_layer_inherit_alpha" command); old "(clip)" groups are taken apart
when a document is opened.
"""
import time

from krita import Krita
from PyQt5.QtCore import QItemSelection, QItemSelectionModel, QTimer
from PyQt5.QtWidgets import QDockWidget, QTreeView

from . import core

CLIP_SUFFIX = " (clip)"


class ClipError(core.BlendIfError):
    pass


# layers clipped through the plugin (to release them when dragged out)
_tracked = set()
# base layer uid -> its own blending mode, moved to the clipping group (PS:
# the base's mode applies to the whole stack); given back when it leaves
_base_modes = {}
# sync() must not fight a native command that is still being applied
_busy_until = [0.0]
_known = set()          # layer uids seen by the last sync (to spot new layers)
_empty_seen = set()     # empty "(clip)" groups seen by the last sync
_bases = {}             # "(clip)" group uid -> uid of its base at the last sync
_single_seen = set()    # "(clip)" groups with one layer left at the last sync
_dissolve_tries = {}    # "(clip)" group uid -> automatic dissolve attempts
_prev_active = [None]


def _key(node):
    return node.uniqueId().toString()


def _same(a, b):
    return a is not None and b is not None and a.uniqueId() == b.uniqueId()


def _layers(node):
    return [c for c in node.childNodes() if core.is_layer(c)]


def sibling_below(node):
    parent = node.parentNode()
    if parent is None:
        return None
    sibs = _layers(parent)
    for i, s in enumerate(sibs):
        if _same(s, node):
            return sibs[i - 1] if i > 0 else None
    return None


def is_clip_group(node):
    """Clipping group: a normal (not pass-through) group, base at the bottom,
    everything above it with Inherit Alpha."""
    if node is None or node.type() != "grouplayer":
        return False
    if node.passThroughMode():
        return False
    kids = _layers(node)
    if len(kids) < 2 or kids[0].inheritAlpha():
        return False
    return all(k.inheritAlpha() for k in kids[1:])


def is_clipped(node):
    if core.native_clipping():
        return native_is_clipped(node)
    p = node.parentNode() if node is not None else None
    return (node is not None and core.is_layer(node) and node.inheritAlpha()
            and p is not None and p.parentNode() is not None
            and p.type() == "grouplayer")


def is_clip_base(node):
    if core.native_clipping():
        return native_is_base(node)
    p = node.parentNode() if node is not None else None
    return p is not None and is_clip_group(p) and _same(_layers(p)[0], node)


def _snapshot(node):
    return [(c, _snapshot(c)) for c in node.childNodes()]


def _restore(node, snap):
    """Krita's node.remove() detaches the node's children (masks, layers of a
    group). Put them back in the same order."""
    present = set(_key(c) for c in node.childNodes())
    above = None
    for child, sub in snap:
        if _key(child) not in present:
            node.addChildNode(child, above)
        _restore(child, sub)
        above = child


def _move(node, new_parent, above):
    snap = _snapshot(node)
    node.remove()
    new_parent.addChildNode(node, above)
    _restore(node, snap)


def _refresh(doc, active):
    doc.waitForDone()
    if active is not None:
        doc.setActiveNode(active)
    core.refresh(doc)


# --------------------------------------------- native (one undo step)
def _layers_view():
    w = Krita.instance().activeWindow()
    qwin = w.qwindow() if w is not None else None
    if qwin is None:
        return None
    dock = qwin.findChild(QDockWidget, "KisLayerBox")
    if dock is None:
        return None
    return dock.findChild(QTreeView, "listLayers") or dock.findChild(QTreeView)


def _index_for(view, node):
    """Layers docker index of a node (rows = children top first)."""
    chain = []
    n = node
    while n is not None and n.parentNode() is not None:
        chain.append(n)
        n = n.parentNode()
    model = view.model()
    idx = None
    from PyQt5.QtCore import QModelIndex
    idx = QModelIndex()
    for n in reversed(chain):
        kids = list(reversed(n.parentNode().childNodes()))
        row = next((i for i, k in enumerate(kids) if _same(k, n)), None)
        if row is None:
            return None
        idx = model.index(row, 0, idx)
        if not idx.isValid() or idx.data() != n.name():
            return None
    return idx


def _select(doc, nodes, current):
    """Make `nodes` the selected layers (and `current` the active one) the
    way the Layers docker does, so Krita's layer commands act on them."""
    view = _layers_view()
    if view is None:
        return False
    doc.setActiveNode(current)
    idxs = [_index_for(view, n) for n in nodes]
    if any(i is None for i in idxs):
        return False
    sel = QItemSelection()
    for i in idxs:
        sel.select(i, i)
    view.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
    return True


def _run(action_name):
    a = Krita.instance().action(action_name)
    if a is None:
        return False
    _busy_until[0] = time.time() + 1.5
    a.trigger()
    return True


def _after(fn, check, tries=30):
    """Run fn() once check() is true (Krita applies layer commands a moment
    later, in the background)."""
    def step(n=[0]):
        n[0] += 1
        try:
            if check():
                fn()
                return
        except Exception:
            pass
        if n[0] < tries:
            QTimer.singleShot(50, step)
    QTimer.singleShot(30, step)


def _remember_base_mode(base, group):
    mode = base.blendingMode()
    group.setBlendingMode(mode)
    if mode != "normal":
        _base_modes[_key(base)] = mode
        base.setBlendingMode("normal")


def clip(doc, layer):
    """Clips the layer to the layer directly below it (one undo step)."""
    below = sibling_below(layer)
    if below is None:
        raise ClipError(u"There is no layer below this one to clip it to.")
    if _is_plugin_group(below):
        # join the clipping group below: Krita's "Move Layer Down" puts the
        # layer on top inside an (expanded) group
        below.setCollapsed(False)
        if _select(doc, [layer], layer) and _run("move_layer_down"):
            def done():
                layer.setInheritAlpha(True)
                _tracked.add(_key(layer))
                doc.setActiveNode(layer)
            _after(done, lambda: _same(layer.parentNode(), below))
            return
        return _clip_fallback(doc, layer, below)
    parent = layer.parentNode()
    if _select(doc, [below, layer], layer) and _run("create_quick_group"):
        def done():
            group = layer.parentNode()
            group.setName(below.name() + CLIP_SUFFIX)
            group.setColorLabel(below.colorLabel())
            _remember_base_mode(below, group)
            layer.setInheritAlpha(True)
            _tracked.add(_key(layer))
            doc.setActiveNode(layer)
            doc.refreshProjection()
        _after(done, lambda: (layer.parentNode() is not None
                              and not _same(layer.parentNode(), parent)
                              and _same(below.parentNode(), layer.parentNode())))
        return
    _clip_fallback(doc, layer, below)


def _clip_fallback(doc, layer, below):
    """Scripting-API way (several undo steps)."""
    _busy_until[0] = time.time() + 1.5      # sync() must not see half-built groups
    below = sibling_below(layer)
    if below is None:
        raise ClipError(u"There is no layer below this one to clip it to.")
    if _is_plugin_group(below):
        # join an existing clipping group (at the very top)
        top = _layers(below)[-1]
        _move(layer, below, top)
        layer.setInheritAlpha(True)
        _tracked.add(_key(layer))
        _refresh(doc, layer)
        return
    parent = layer.parentNode()
    group = doc.createGroupLayer(below.name() + CLIP_SUFFIX)
    parent.addChildNode(group, below)
    # in PS the base's blending mode applies to the whole clipping group
    mode = below.blendingMode()
    group.setBlendingMode(mode)
    if mode != "normal":
        below.setBlendingMode("normal")
    group.setColorLabel(below.colorLabel())
    if mode != "normal":
        _base_modes[_key(below)] = mode
    _move(below, group, None)
    _move(layer, group, below)
    layer.setInheritAlpha(True)
    _tracked.add(_key(layer))
    group.setCollapsed(False)
    _refresh(doc, layer)


def release(doc, layer):
    """Releases the layer and all clipped layers above it (like PS) - one
    undo step, with Krita's "Quick Ungroup" on the selected layers."""
    group = layer.parentNode()
    kids = _layers(group)
    idx = next(i for i, k in enumerate(kids) if _same(k, layer))
    to_release = kids[idx:]
    if not _is_plugin_group(group):
        # a stack inside the user's own group (e.g. base + clipped grouped
        # with Ctrl+G): release in place, like PS
        for n in to_release:
            n.setInheritAlpha(False)
            _tracked.discard(_key(n))
        _refresh(doc, layer)
        return
    dissolve = idx <= 1 and group.name().endswith(CLIP_SUFFIX)
    # Quick Ungroup moves the selected layers above the group; when all of
    # the group's children are selected it also removes the group
    nodes = list(group.childNodes()) if dissolve else to_release
    # Quick Ungroup looks at the ACTIVE node first: when that is a group it
    # dissolves that very group (its layers would spill into the clipping
    # group and get clipped one by one). So a clipped group must not be the
    # active node while the command runs; done() makes it active again.
    anchor = layer
    if anchor.type() == "grouplayer":
        anchor = next((n for n in nodes if n.type() != "grouplayer"), None)
    if anchor is not None and _select(doc, nodes, anchor):
        for n in to_release:
            n.setInheritAlpha(False)
            _tracked.discard(_key(n))
        base = kids[0] if dissolve else None
        if base is not None:
            mode = group.blendingMode()
            _base_modes.pop(_key(base), None)
            if mode != "normal":
                base.setBlendingMode(mode)
        if _run("quick_ungroup"):
            def done():
                for n in to_release:
                    n.setInheritAlpha(False)
                doc.setActiveNode(layer)
                doc.refreshProjection()
            _after(done, lambda: not _same(layer.parentNode(), group))
            return
    _release_fallback(doc, layer)


def _release_fallback(doc, layer):
    _busy_until[0] = time.time() + 1.5
    group = layer.parentNode()
    outer = group.parentNode()
    kids = _layers(group)
    idx = next(i for i, k in enumerate(kids) if _same(k, layer))
    to_release = kids[idx:]
    anchor = group
    for n in to_release:
        _move(n, outer, anchor)
        n.setInheritAlpha(False)
        _tracked.discard(_key(n))
        anchor = n
    rest = _layers(group)
    if len(rest) <= 1 and group.name().endswith(CLIP_SUFFIX):
        # only the base is left -> dissolve the group, give the blending mode back to the base
        if rest:
            base = rest[0]
            mode = group.blendingMode()
            _move(base, outer, group)
            if mode != "normal":
                base.setBlendingMode(mode)
        if not [c for c in group.childNodes()]:
            group.remove()
    _refresh(doc, layer)


def toggle(doc, layer):
    if layer is None or not core.is_layer(layer) or layer.parentNode() is None:
        raise ClipError(u"Select a layer.")
    if core.native_clipping():
        return native_toggle(doc, layer)
    if is_clipped(layer):
        release(doc, layer)
    elif is_clip_base(layer):
        raise ClipError(u"This is the base layer of a clipping group. "
                        u"Select a clipped layer to release it.")
    else:
        clip(doc, layer)


# ------------------------------------------------ grouping (Ctrl+G)
# The Layers docker does not show the "(clip)" group as a row, so the user
# can only select the layers inside it. Krita's Quick Group would pull just
# those out of the clipping group: the stack falls apart (clipped adjustment
# layers suddenly affect everything) and an empty "<base> (clip)" group is
# left behind. So grouping any layer of a clipping stack groups the whole
# stack: the selection is widened to the "(clip)" group first.

def widen_to_stacks(nodes):
    """-> (nodes with members of plugin clipping groups replaced by their
    group, True when something was replaced)."""
    if core.native_clipping():
        return native_widen(nodes)
    out, seen, changed = [], set(), False
    for n in nodes:
        p = n.parentNode()
        if core.is_layer(n) and _is_plugin_group(p):
            n, changed = p, True
        k = _key(n)
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out, changed


def group_selected():
    """Quick Group that keeps clipping stacks together (one undo step)."""
    k = Krita.instance()
    doc = k.activeDocument()
    if doc is None:
        return
    try:
        nodes = list(k.activeWindow().activeView().selectedNodes())
    except Exception:
        nodes = []
    act = doc.activeNode()
    if not nodes and act is not None:
        nodes = [act]
    wide, changed = widen_to_stacks(nodes)
    if changed:
        cur = widen_to_stacks([act])[0][0] if act is not None else wide[-1]
        if not any(_same(cur, n) for n in wide):
            cur = wide[-1]
        _select(doc, wide, cur)
    _run("create_quick_group")


def _adjustment_keys(doc):
    """Keys of the computed (Color Lookup / Selective Color) layers."""
    try:
        from . import adjustments as A
        store = A._load_store(doc) or {}
        return set(k for k, rec in store.items()
                   if rec and rec.get("kind") in A.BAKED_KINDS)
    except Exception:
        return set()


def wants_pass_through(group, baked_keys=()):
    """Photoshop groups are "Pass Through": adjustment layers inside them
    work on everything below the group. A Krita group is isolated (the
    adjustment layers would see only the group's own layers), so a new
    group that directly holds adjustment layers is switched to pass-through.
    Not when it has a mask (Krita ignores masks of pass-through groups) or
    is / holds a clipping stack."""
    if (group is None or group.type() != "grouplayer" or _is_plugin_group(group)
            or group.passThroughMode() or group.inheritAlpha()):
        return False
    # Krita PS: masks of pass-through groups and clipping stacks inside them
    # are handled by the engine
    native = core.native_clipping()
    found = False
    for c in group.childNodes():
        if core.is_mask(c) and not native:
            return False
        if not core.is_layer(c):
            continue
        if c.inheritAlpha() and not native:
            return False
        if c.type() == "filterlayer" or _key(c) in baked_keys:
            found = True
    return found


def toggle_active():
    doc = Krita.instance().activeDocument()
    if doc is None:
        return
    toggle(doc, doc.activeNode())


def node_for_index(doc, index):
    """Layers docker index -> Krita node (verified by name)."""
    path = []
    i = index
    while i.isValid():
        path.append(i)
        i = i.parent()
    node = doc.rootNode()
    for idx in reversed(path):
        kids = list(reversed(node.childNodes()))
        if idx.row() >= len(kids):
            return None
        node = kids[idx.row()]
        if node.name() != idx.data():
            return None
    return node


def _is_plugin_group(node):
    return (node is not None and node.type() == "grouplayer"
            and node.name().endswith(CLIP_SUFFIX) and not node.passThroughMode())


def in_clip_stack(layer):
    """Is the layer still above a base inside a group (e.g. after the user
    grouped the base together with its clipped layers)?"""
    parent = layer.parentNode()
    if parent is None or parent.parentNode() is None or parent.type() != "grouplayer":
        return False
    kids = _layers(parent)
    i = next((k for k, n in enumerate(kids) if _same(n, layer)), None)
    if not i:
        return False
    if kids[0].inheritAlpha():
        return False
    return all(n.inheritAlpha() for n in kids[1:i])


def sync(doc):
    """Keeps "(clip)" groups behaving like Photoshop clipping stacks:
    - a layer dragged/created inside the group (above the base) gets clipped,
    - the bottom layer is always the (unclipped) base,
    - a clipped layer dragged out of its stack is released (but one grouped
      together with its base stays clipped, like in PS),
    - the base gets its own blending mode back when it leaves the group."""
    if core.native_clipping():
        return native_sync(doc)
    if time.time() < _busy_until[0]:
        return False
    changed = False
    layers = list(core.iter_layers(doc.rootNode()))
    keys = set(_key(n) for n in layers)
    act = doc.activeNode()
    baked_keys = None
    # a clipping group that lost all its layers (they were dragged / grouped
    # away) is only an empty row named like its old base layer: remove it
    empty = set()
    for layer in list(layers):
        if _is_plugin_group(layer) and not layer.childNodes():
            key = _key(layer)
            if key in _empty_seen:        # still empty on the second look
                layer.remove()
                layers.remove(layer)
                keys.discard(key)
                changed = True
            else:
                empty.add(key)
    _empty_seen.clear()
    _empty_seen.update(empty)
    # A clipping group whose base is gone (deleted, dragged or grouped away)
    # or that has a single layer left is no clipping stack any more. Left
    # alone, a formerly clipped adjustment layer would become the "base" of
    # an isolated group and stop doing anything. Photoshop releases the
    # clipped layers in that case - so the group is dissolved.
    single = set()
    live = set()
    for group in layers:
        if not _is_plugin_group(group):
            continue
        kids = _layers(group)
        if not kids:
            continue
        gkey = _key(group)
        live.add(gkey)
        kid_keys = [_key(n) for n in kids]
        prev = _bases.get(gkey)
        lost = prev is not None and prev not in kid_keys
        lonely = len(kids) == 1 and gkey in _single_seen
        if len(kids) == 1:
            single.add(gkey)
        if (lost or lonely) and _dissolve_tries.get(gkey, 0) < 2:
            _dissolve_tries[gkey] = _dissolve_tries.get(gkey, 0) + 1
            _bases.pop(gkey, None)
            _single_seen.clear()
            _single_seen.update(single)
            release(doc, kids[0])          # releases everything, removes the group
            _busy_until[0] = max(_busy_until[0], time.time() + 1.5)
            return True
        if not lost:
            _bases[gkey] = kid_keys[0]
    _single_seen.clear()
    _single_seen.update(single)
    for gkey in list(_bases):
        if gkey not in live:
            del _bases[gkey]
            _dissolve_tries.pop(gkey, None)
    if _known:
        for layer in layers:
            key = _key(layer)
            if key in _known:
                continue
            # PS: a group is "Pass Through" - adjustment layers grouped by the
            # user keep working on what is below the group
            if layer.type() == "grouplayer":
                if baked_keys is None:
                    baked_keys = _adjustment_keys(doc)
                if wants_pass_through(layer, baked_keys):
                    layer.setPassThroughMode(True)
                    changed = True
                    continue
            parent = layer.parentNode()
            if parent is None or parent.parentNode() is None:
                continue
            kids = _layers(parent)
            # PS: a new layer made while a group is selected goes above the group
            if (_prev_active[0] == _key(parent) and not _is_plugin_group(parent)
                    and kids and _same(kids[-1], layer) and _same(act, layer)):
                if _select(doc, [layer], layer) and _run("move_layer_up"):
                    _known.update(keys)
                    return False
            # PS: a new layer inside a clipping stack is clipped too
            i = next((k for k, n in enumerate(kids) if _same(n, layer)), 0)
            if not layer.inheritAlpha() and i > 0 and (
                    kids[i - 1].inheritAlpha()
                    or (i < len(kids) - 1 and kids[i + 1].inheritAlpha())):
                layer.setInheritAlpha(True)
                _tracked.add(key)
                changed = True
    _known.clear()
    _known.update(keys)
    _prev_active[0] = _key(act) if act is not None else None
    for layer in layers:
        parent = layer.parentNode()
        key = _key(layer)
        if _is_plugin_group(parent):
            kids = _layers(parent)
            if kids and _same(kids[0], layer):
                if layer.inheritAlpha():
                    layer.setInheritAlpha(False)
                    changed = True
                _tracked.discard(key)
                # base inside its group: the group has the mode (also after redo)
                gm = parent.blendingMode()
                if gm != "normal" and layer.blendingMode() == gm:
                    _base_modes[key] = gm
                    layer.setBlendingMode("normal")
                    changed = True
            else:
                if not layer.inheritAlpha():
                    layer.setInheritAlpha(True)
                    changed = True
                _tracked.add(key)
        elif key in _tracked:
            if not in_clip_stack(layer):
                _tracked.discard(key)
                if layer.inheritAlpha():
                    layer.setInheritAlpha(False)
                    changed = True
        if key in _base_modes and not _is_plugin_group(parent):
            # a base that left its clipping group (release, undo...) gets its mode back
            mode = _base_modes.pop(key)
            if layer.blendingMode() == "normal":
                layer.setBlendingMode(mode)
                changed = True
    if changed:
        doc.refreshProjection()
    return changed


# ================================================================ native
# Krita PS (core.native_clipping()): the engine does Photoshop clipping
# itself. A layer with "Inherit Alpha" is clipped to the nearest layer below
# it that does not have it (the base), in the same group - always, live,
# wherever the layers are moved. The plugin only switches the flag (with
# Krita's own undoable command) and keeps a few Photoshop habits:
#   - a new layer made inside a clipping stack is clipped too;
#   - a layer dragged into a stack is clipped, a clipped layer dragged out of
#     one is released;
#   - a clipped group is not pass-through (it is drawn like one layer);
#   - "(clip)" groups of older documents are taken apart once.

_nat_neighbours = {}      # layer key -> (parent key, below key, above key)
_nat_dirty = [True]
_nat_ticks = [0]
_nat_migrated = set()     # documents whose "(clip)" groups were taken apart
NATIVE_FULL_EVERY = 10    # polls: look even when nothing said "changed"


def mark_dirty(*_a):
    """Called when the Layers docker reports a change."""
    _nat_dirty[0] = True


def sibling_above(node):
    parent = node.parentNode() if node is not None else None
    if parent is None:
        return None
    sibs = _layers(parent)
    for i, s in enumerate(sibs):
        if _same(s, node):
            return sibs[i + 1] if i + 1 < len(sibs) else None
    return None


def native_base(node):
    """The base a clipped layer is clipped to (None: not clipped)."""
    if node is None or not core.is_layer(node) or not node.inheritAlpha():
        return None
    parent = node.parentNode()
    if parent is None:
        return None
    sibs = _layers(parent)
    i = next((k for k, s in enumerate(sibs) if _same(s, node)), None)
    if i is None:
        return None
    for s in reversed(sibs[:i]):
        if not s.inheritAlpha():
            return s
    return None


def native_is_clipped(node):
    return native_base(node) is not None


def native_is_base(node):
    if node is None or not core.is_layer(node) or node.inheritAlpha():
        return False
    up = sibling_above(node)
    return up is not None and up.inheritAlpha()


def native_stack(node):
    """[base, clipped...] (bottom to top) the layer belongs to, or [node]."""
    base = node if native_is_base(node) else native_base(node)
    if base is None:
        return [node]
    sibs = _layers(base.parentNode())
    i = next(k for k, s in enumerate(sibs) if _same(s, base))
    out = [base]
    for s in sibs[i + 1:]:
        if not s.inheritAlpha():
            break
        out.append(s)
    return out


def _set_clip(doc, nodes, on, active=None):
    """Switches clipping of the layers - one undoable Krita command (the
    Layers docker's own "inherit alpha" toggle on the selected layers)."""
    todo = [n for n in nodes if core.is_layer(n) and bool(n.inheritAlpha()) != on]
    if not todo:
        return False
    if on:
        for n in todo:
            # a pass-through group has no pixels of its own to clip
            if n.type() == "grouplayer" and n.passThroughMode():
                n.setPassThroughMode(False)
    active = active or todo[-1]
    if _select(doc, todo, todo[-1]) and _run("toggle_layer_inherit_alpha"):
        def done():
            doc.setActiveNode(active)
        _after(done, lambda: all(bool(n.inheritAlpha()) == on for n in todo))
        return True
    for n in todo:                       # no Layers docker: no undo, but works
        n.setInheritAlpha(on)
    doc.setActiveNode(active)
    doc.refreshProjection()
    return True


def native_toggle(doc, layer):
    if native_is_clipped(layer):
        # PS: releases the layer and the clipped layers above it
        stack = native_stack(layer)
        i = next(k for k, s in enumerate(stack) if _same(s, layer))
        _set_clip(doc, stack[i:], False, active=layer)
        return
    if layer.inheritAlpha():
        # flagged but nothing to clip to (bottom of its group)
        _set_clip(doc, [layer], False, active=layer)
        return
    if sibling_below(layer) is None:
        raise ClipError(u"There is no layer below this one to clip it to.")
    _set_clip(doc, [layer], True, active=layer)


def native_widen(nodes):
    """Ctrl+G keeps clipping stacks together: a selected member of a stack
    brings the whole stack (base + clipped layers) into the new group."""
    out, seen, changed = [], set(), False
    for n in nodes:
        group = native_stack(n) if core.is_layer(n) else [n]
        if len(group) > 1:
            changed = changed or len(group) > 1
        for m in group:
            k = _key(m)
            if k not in seen:
                seen.add(k)
                out.append(m)
    changed = changed and len(out) != len(nodes)
    return out, changed


def _levels(node):
    """(parent, [child layers bottom to top]) for every group, root first."""
    kids = _layers(node)
    yield node, kids
    for k in kids:
        if k.type() == "grouplayer":
            for x in _levels(k):
                yield x


def migrate_clip_groups(doc):
    """Takes apart the "<base> (clip)" groups of documents made before Krita
    PS clipped natively: the layers go back to the group's place, the base
    gets the group's blending mode / opacity / visibility. The result looks
    the same. A group the engine cannot replace exactly (its own mask while
    the base has one too, a layer style, clipped itself) is left as it is -
    it still renders correctly. Returns the number of groups taken apart."""
    groups = [n for n in core.iter_layers(doc.rootNode()) if _is_plugin_group(n)]
    done = 0
    for g in reversed(groups):                  # inner groups first
        try:
            if _migrate_group(doc, g):
                done += 1
        except Exception as e:
            _log(u"could not take apart %s: %s" % (g.name(), e))
    if done:
        _log(u"took apart %d clipping group(s) - clipping is native now" % done)
    return done


def _log(text):
    try:
        from .extension import log
        log(text)
    except Exception:
        pass


def _migrate_group(doc, g):
    parent = g.parentNode()
    layers = _layers(g)
    if parent is None or not layers or g.inheritAlpha():
        return False
    base = layers[0]
    gmasks = [c for c in g.childNodes() if core.is_mask(c)]
    if gmasks and any(core.is_mask(c) and c.type() == "transparencymask"
                      for c in base.childNodes()):
        return False
    _busy_until[0] = time.time() + 1.5
    active = doc.activeNode()
    mode = g.blendingMode()
    if mode != "normal" and base.blendingMode() == "normal":
        base.setBlendingMode(mode)
    _base_modes.pop(_key(base), None)
    try:
        op = g.opacity()
        if op < 255:
            base.setOpacity(int(round(base.opacity() * op / 255.0)))
    except Exception:
        pass
    if not g.visible():
        base.setVisible(False)
    anchor = g
    for n in layers:                            # bottom to top, above the group
        _move(n, parent, anchor)
        anchor = n
        if _same(n, base):
            if n.inheritAlpha():
                n.setInheritAlpha(False)
        elif not n.inheritAlpha():
            n.setInheritAlpha(True)
        _tracked.discard(_key(n))
    prev = None
    for m in gmasks:                            # a mask of the stack = of its base
        copy = m.duplicate()
        if copy is not None:
            base.addChildNode(copy, prev)
            prev = copy
    g.remove()
    if active is not None:
        try:
            doc.setActiveNode(active)
        except Exception:
            pass
    return True


def native_sync(doc):
    """The few Photoshop habits on top of the engine's clipping (see above).
    Cheap: does nothing unless the Layers docker reported a change (or now
    and then), and never while a button is held (painting, dragging)."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication
    if time.time() < _busy_until[0]:
        return False
    if QApplication.mouseButtons() != Qt.NoButton:
        return False
    key = _key(doc.rootNode())
    if key not in _nat_migrated:
        if not core.idle(doc):
            return False
        _nat_migrated.add(key)
        if migrate_clip_groups(doc):
            doc.refreshProjection()
            _nat_dirty[0] = True
            return True
    _nat_ticks[0] += 1
    if not _nat_dirty[0] and _nat_ticks[0] % NATIVE_FULL_EVERY:
        return False
    # Krita applies layer commands (Remove Layer, Move, Group...) a moment
    # later, in the background. Looking in the middle of one would see a
    # half-done layer stack and could switch clipping - an extra undo step
    # that gets in the way of the user's own command. Wait until it is done.
    if not core.idle(doc):
        _nat_dirty[0] = True
        return False
    _nat_dirty[0] = False
    act = doc.activeNode()
    now = {}
    for parent, kids in _levels(doc.rootNode()):
        pkey = _key(parent)
        for i, n in enumerate(kids):
            below = _key(kids[i - 1]) if i > 0 else None
            above = _key(kids[i + 1]) if i + 1 < len(kids) else None
            now[_key(n)] = (pkey, below, above)
    first_look = not _nat_neighbours
    # the move rules only for a pure move: when layers were added or removed
    # their neighbours change too, and that must not clip / release anything
    pure_move = set(now) == set(_nat_neighbours)
    added = set(now) - set(_nat_neighbours)
    removed = set(_nat_neighbours) - set(now)
    # the new-layer rules only for one layer just made by the user (active,
    # and empty when it is a paint layer) - not for layers brought back by
    # Undo, which must come back exactly as they were
    one_new = len(added) == 1 and not removed
    clip_on, clip_off = [], []
    for parent, kids in _levels(doc.rootNode()):
        pkey = _key(parent)
        for i, n in enumerate(kids):
            k = _key(n)
            below, above = now[k][1], now[k][2]
            if first_look:
                continue
            old = _nat_neighbours.get(k)
            up = kids[i + 1] if i + 1 < len(kids) else None
            dn = kids[i - 1] if i > 0 else None
            if old is None:
                if not (one_new and _same(act, n) and _fresh(n)):
                    continue
                # PS: a new layer inside a clipping stack is clipped too, and
                # so is one made while a clipped layer was selected
                if not n.inheritAlpha() and dn is not None and (
                        (up is not None and up.inheritAlpha())
                        or (dn.inheritAlpha() and _prev_active[0] == _key(dn))):
                    clip_on.append(n)
                if n.type() == "grouplayer" and wants_pass_through(n):
                    n.setPassThroughMode(True)
                # PS: a new layer made while a group is selected goes above it
                if (_prev_active[0] == pkey and parent.parentNode() is not None
                        and up is None and _same(act, n)):
                    if _select(doc, [n], n) and _run("move_layer_up"):
                        _nat_dirty[0] = True
                continue
            moved = old[0] != pkey or (old[1] != below and old[2] != above)
            if not moved or not pure_move:
                continue
            if (up is not None and up.inheritAlpha() and dn is not None
                    and not n.inheritAlpha() and old[2] != _key(up)):
                clip_on.append(n)                  # dropped inside a stack
            elif n.inheritAlpha() and (dn is None or not (dn.inheritAlpha() or (
                    up is not None and up.inheritAlpha()))) and not _moved_with_base(n, dn, old):
                clip_off.append(n)                 # dragged out of its stack
    for parent, kids in _levels(doc.rootNode()):
        for n in kids:
            if n.type() == "grouplayer" and n.inheritAlpha() and n.passThroughMode():
                n.setPassThroughMode(False)        # a clipped group is one "layer"
    _nat_neighbours.clear()
    _nat_neighbours.update(now)
    _prev_active[0] = _key(act) if act is not None else None
    changed = False
    if clip_on:
        changed = _set_clip(doc, clip_on, True, active=act) or changed
    if clip_off:
        changed = _set_clip(doc, clip_off, False, active=act) or changed
    if changed:
        _nat_dirty[0] = True
    return changed


def _fresh(n):
    """A layer as Krita makes it new: an empty paint layer, or any other kind
    (adjustment, fill, group...)."""
    if n.type() != "paintlayer":
        return True
    try:
        return n.bounds().isEmpty()
    except Exception:
        return False


def _moved_with_base(n, dn, old):
    """The layer moved together with the layer it was clipped to (e.g. the
    whole stack was dragged): keep it clipped."""
    return dn is not None and old[1] == _key(dn)
