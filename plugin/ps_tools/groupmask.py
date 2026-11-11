# -*- coding: utf-8 -*-
"""Transparency masks on pass-through groups.

Krita ignores the masks of a pass-through group (the group has no pixels of
its own: its layers are composited straight into the image below). In
Photoshop such a mask limits where the group's adjustment layers work.

That is reproduced with the adjustment (filter) layers' built-in mask, which
the plugin does not use for anything else: every adjustment layer inside a
masked pass-through group gets "the group's mask" (times the masks of outer
pass-through groups) written into its built-in mask. The mask the user
paints stays an ordinary transparency mask on the group.

In Krita PS (core.engine_level() >= 1) the engine itself honours masks of
pass-through groups, live and for every layer; then this module only undoes
what it wrote in documents edited with the official Krita.

Limits (Krita's engine would have to change to lift them):
  * the copy is made when a stroke on the mask has ended, not during it;
  * only adjustment layers follow the group's mask - paint layers and
    isolated groups inside the pass-through group do not;
  * with several adjustment layers in the group, soft mask edges are applied
    once per adjustment instead of once for the group."""
import json

from krita import Krita
from PyQt5.QtCore import QByteArray
from PyQt5.QtGui import QPainter

from . import core

ANNOTATION = "ps_tools_group_masks"


def group_masks(group):
    """The group's own visible transparency masks (not Blend If)."""
    return [c for c in group.childNodes()
            if c.type() == "transparencymask" and c.visible()
            and not core.is_blendif_mask(c)]


def plan(doc):
    """{layer key: (adjustment layer, [mask nodes that apply to it])}"""
    out = {}

    def walk(node, chain):
        for c in node.childNodes():
            if not core.is_layer(c):
                continue
            t = c.type()
            if t == "grouplayer":
                if c.passThroughMode():
                    walk(c, chain + group_masks(c))
                else:
                    walk(c, [])            # an isolated group: its mask works natively
            elif t == "filterlayer" and chain:
                out[core.node_key(c)] = (c, chain)
    walk(doc.rootNode(), [])
    return out


# ---- which layers' built-in masks are ours (kept in the .kra)
def controlled(doc):
    try:
        if ANNOTATION in doc.annotationTypes():
            return set(json.loads(bytes(doc.annotation(ANNOTATION)).decode("utf-8")))
    except Exception:
        pass
    return set()


def _save_controlled(doc, keys):
    data = json.dumps(sorted(keys)).encode("utf-8")
    doc.setAnnotation(ANNOTATION, "Adjustment layers masked by their pass-through "
                      "group (ps_tools plugin)", QByteArray(data))


def multiply(a, b, w, h):
    """a * b / 255 per pixel (8-bit gray, no padding)."""
    full = b"\xff" * (w * h)
    if a == full:
        return b
    if b == full:
        return a
    ia = core.gray_rgb32(core.pad_rows(a, w, h), w, h)
    ib = core.gray_rgb32(core.pad_rows(b, w, h), w, h)
    p = QPainter(ia)
    p.setCompositionMode(QPainter.CompositionMode_Multiply)
    p.drawImage(0, 0, ib)
    p.end()
    return core.rgb32_to_gray_bytes(ia, w, h)


def _mask_bytes(doc, mask, full):
    data = bytes(mask.pixelData(0, 0, doc.width(), doc.height()))
    return data if len(data) == len(full) else full


class _State(object):
    def __init__(self):
        self.sig = None           # structure at the last look
        self.applied = {}         # layer key -> hash of the mask we wrote
        self.dirty = True         # Krita worked since the last look


_states = {}


def _doc_key(doc):
    try:
        return core.node_key(doc.rootNode())
    except Exception:
        return str(id(doc))


def _find(doc, key):
    for layer in core.iter_layers(doc.rootNode()):
        if core.node_key(layer) == key:
            return layer
    return None


def release_all(doc):
    """Gives the adjustment layers back their full built-in mask (the
    engine does the masking itself). Returns True when something changed."""
    mine = controlled(doc)
    if not mine:
        return False
    w, h = doc.width(), doc.height()
    full = b"\xff" * (w * h)
    for k in mine:
        layer = _find(doc, k)
        if layer is not None and core.is_filter_layer(layer):
            layer.setPixelData(full, 0, 0, w, h)
    _save_controlled(doc, set())
    _states.pop(_doc_key(doc), None)
    doc.refreshProjection()
    return True


def sync(doc, active=None):
    """Called from the plugin's poll. Returns True when something was written."""
    if core.engine_level() >= 1:
        if core.idle(doc):
            return release_all(doc)
        return False
    key = _doc_key(doc)
    st = _states.get(key)
    if st is None:
        if len(_states) > 32:
            _states.clear()
        st = _states[key] = _State()
    if not core.idle(doc):
        st.dirty = True                    # a stroke is running: look again after it
        return False
    todo = plan(doc)
    mine = controlled(doc)
    if not todo and not mine:
        st.sig, st.dirty = None, False
        st.applied.clear()
        return False
    sig = tuple(sorted((k, tuple(core.node_key(m) for m in chain))
                       for k, (_l, chain) in todo.items()))
    structure_changed = sig != st.sig
    # mask pixels can only have changed while Krita was working, and then
    # (normally) on the active node
    mask_keys = set(core.node_key(m) for _l, chain in todo.values() for m in chain)
    active_is_mask = active is not None and core.node_key(active) in mask_keys
    if not structure_changed and not (st.dirty and (active_is_mask or not st.applied)):
        st.dirty = False
        return False
    st.sig, st.dirty = sig, False

    w, h = doc.width(), doc.height()
    full = b"\xff" * (w * h)
    products = {}
    changed = False
    for k, (layer, chain) in todo.items():
        ck = tuple(core.node_key(m) for m in chain)
        data = products.get(ck)
        if data is None:
            data = full
            for m in chain:
                data = multiply(data, _mask_bytes(doc, m, full), w, h)
            products[ck] = data
        digest = hash(data)
        if st.applied.get(k) != digest:
            layer.setPixelData(data, 0, 0, w, h)
            st.applied[k] = digest
            changed = True
    for k in mine - set(todo):             # left the group / mask removed or disabled
        layer = _find(doc, k)
        if layer is not None and core.is_filter_layer(layer):
            layer.setPixelData(full, 0, 0, w, h)
            changed = True
        st.applied.pop(k, None)
    if set(todo) != mine:
        _save_controlled(doc, set(todo))
    if changed:
        doc.refreshProjection()
    return changed
