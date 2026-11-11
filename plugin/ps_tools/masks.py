# -*- coding: utf-8 -*-
"""Photoshop-like masks: pasting an image into a mask, adding a mask."""
from krita import Krita
from PyQt5.QtCore import QRect, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QApplication, QMessageBox

from . import core

# masks that are grayscale images in Krita
PIXEL_MASKS = ("transparencymask", "filtermask", "selectionmask")


def _window_parent():
    w = Krita.instance().activeWindow()
    return w.qwindow() if w else None


def _info(text):
    QMessageBox.information(_window_parent(), u"Masks", text)


def is_pixel_mask(node):
    return node is not None and node.type() in PIXEL_MASKS


def clipboard_image():
    img = QApplication.clipboard().image()
    return None if img is None or img.isNull() else img


def _target_rect(doc, iw, ih):
    """Where to paste: full-size image -> 0,0; active selection -> centered
    on the selection (like PS); otherwise -> centered on the image."""
    W, H = doc.width(), doc.height()
    if (iw, ih) == (W, H):
        return QRect(0, 0, iw, ih)
    sel = doc.selection()
    if sel is not None and sel.width() > 0 and sel.height() > 0:
        cx = sel.x() + sel.width() // 2
        cy = sel.y() + sel.height() // 2
        return QRect(cx - iw // 2, cy - ih // 2, iw, ih)
    return QRect((W - iw) // 2, (H - ih) // 2, iw, ih)


def paste_image_into_mask(doc, mask, img):
    """Pastes an image into the mask (as grayscale, respecting its transparency).
    Returns the rectangle it was pasted into."""
    img = img.convertToFormat(QImage.Format_ARGB32)
    dest = _target_rect(doc, img.width(), img.height())
    r = dest.intersected(QRect(0, 0, doc.width(), doc.height()))
    if r.isEmpty():
        return None
    src = img.copy(r.x() - dest.x(), r.y() - dest.y(), r.width(), r.height())
    w, h = r.width(), r.height()

    old = bytes(mask.pixelData(r.x(), r.y(), w, h))
    if len(old) != w * h:
        raise core.BlendIfError(u"Unsupported mask format.")
    base = core.gray_rgb32(core.pad_rows(old, w, h), w, h)

    # grayscale of the pasted image with its alpha channel
    gray = src.convertToFormat(QImage.Format_Grayscale8).convertToFormat(QImage.Format_RGB32)
    overlay = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    overlay.fill(Qt.transparent)
    p = QPainter(overlay)
    p.drawImage(0, 0, gray)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawImage(0, 0, src)
    p.end()

    p = QPainter(base)
    p.drawImage(0, 0, overlay)
    p.end()
    mask.setPixelData(core.rgb32_to_gray_bytes(base, w, h), r.x(), r.y(), w, h)
    core.refresh(doc)
    return r


def _qt_paste_into_mask(doc, node):
    """Fallback (not undoable) when Krita has no 'Paste into Active Layer'."""
    img = clipboard_image()
    if img is None:
        _info(u"There is no image in the clipboard.")
        return
    try:
        if paste_image_into_mask(doc, node, img) is None:
            _info(u"The pasted image is outside the document.")
    except core.BlendIfError as e:
        _info(str(e))
    doc.setActiveNode(node)


def paste_into_active_mask():
    """Ctrl+Shift+V: paste into the selected mask (or into the active layer).
    Uses Krita's native 'Paste into Active Layer': grayscale in masks, centered,
    with Transform handles (Enter = apply), undoable with Ctrl+Z."""
    k = Krita.instance()
    doc = k.activeDocument()
    if doc is None:
        return
    node = doc.activeNode()
    native = k.action("paste_into")
    if native is not None:
        native.trigger()
    elif is_pixel_mask(node):
        _qt_paste_into_mask(doc, node)
    else:
        k.action("edit_paste").trigger()


def smart_paste():
    """Ctrl+V: into the mask if a mask is active, otherwise the regular paste."""
    doc = Krita.instance().activeDocument()
    node = doc.activeNode() if doc else None
    if is_pixel_mask(node):
        paste_into_active_mask()
    else:
        Krita.instance().action("edit_paste").trigger()


def add_mask(hide_all=False):
    """Adds a mask to the active layer (like the PS button). Krita uses the
    active selection itself. hide_all (Alt) = inverted mask.
    Works the same on adjustment (filter) layers: they get a native
    transparency mask too (see core.ensure_mask_blending)."""
    k = Krita.instance()
    doc = k.activeDocument()
    if doc is None:
        return
    before = doc.activeNode()
    if before is None:
        return
    layer = before.parentNode() if core.is_mask(before) else before
    core.ensure_mask_blending(layer)          # before the mask exists: no hole
    k.action("add_new_transparency_mask").trigger()
    doc.waitForDone()
    node = doc.activeNode()
    if not hide_all or node is None or node.type() != "transparencymask":
        return
    if before is not None and node.uniqueId() == before.uniqueId():
        return
    W, H = doc.width(), doc.height()
    data = bytes(node.pixelData(0, 0, W, H))
    inv = bytes(range(255, -1, -1))
    node.setPixelData(data.translate(inv), 0, 0, W, H)
    core.refresh(doc)


def layer_mask(node):
    """The mask the Layers docker shows as the layer's second thumbnail: the
    node itself when it is such a mask, else the layer's topmost
    transparency mask (not Blend If). None when there is none."""
    if node is None:
        return None
    if node.type() == "transparencymask" and not core.is_blendif_mask(node):
        return node
    if not core.is_layer(node):
        return None
    for c in reversed(node.childNodes()):
        if c.type() == "transparencymask" and not core.is_blendif_mask(c):
            return c
    return None


def remove_mask():
    """Removes the transparency mask of the active layer / group."""
    doc = Krita.instance().activeDocument()
    if doc is None:
        return
    mask = layer_mask(doc.activeNode())
    if mask is None:
        return
    layer = mask.parentNode()
    mask.remove()
    doc.waitForDone()
    if layer is not None:
        doc.setActiveNode(layer)
    core.refresh(doc)


# ------------------------------------------- masks on adjustment layers
_migrated = set()        # documents already checked for old-scheme masks
_ticks = [0]
FULL_SCAN_EVERY = 10     # polls (~3 s): masks dropped / pasted onto other layers


def _doc_key(doc):
    try:
        return core.node_key(doc.rootNode())
    except Exception:
        return str(id(doc))


def _fix_layer(layer):
    return (core.is_filter_layer(layer) and layer.blendingMode() == "copy"
            and core.has_visible_transparency_mask(layer)
            and core.ensure_mask_blending(layer))


def maintain(doc, active=None):
    """Called from the plugin's poll. Keeps transparency masks on adjustment
    layers working (the layer must not be on the "Copy" blending mode) and
    converts documents saved by older versions of the plugin. Normally it
    only looks at the active layer; the whole tree is walked now and then."""
    key = _doc_key(doc)
    changed = False
    first = key not in _migrated
    if first:
        if not core.idle(doc):
            return False                  # try again on the next poll
        _migrated.add(key)
        if len(_migrated) > 64:
            _migrated.clear()
            _migrated.add(key)
        from . import groupmask
        if core.migrate_legacy_masks(doc, skip=groupmask.controlled(doc)):
            changed = True
    _ticks[0] += 1
    if first or _ticks[0] % FULL_SCAN_EVERY == 0:
        for layer in core.iter_layers(doc.rootNode()):
            if _fix_layer(layer):
                changed = True
    else:
        layer = active
        if layer is not None and core.is_mask(layer):
            layer = layer.parentNode()
        if layer is not None and _fix_layer(layer):
            changed = True
    if changed:
        doc.refreshProjection()
    return changed


def mask_icon(size, color, dpr=1.0):
    """PS-like icon: a rectangle with a hollow circle inside."""
    px = int(round(size * dpr))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(dpr, dpr)
    s = float(size)
    rect = QRectF(s * 0.08, s * 0.2, s * 0.84, s * 0.6)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(rect, s * 0.06, s * 0.06)
    p.setCompositionMode(QPainter.CompositionMode_Clear)
    r = rect.height() * 0.32
    p.drawEllipse(rect.center(), r, r)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def make_mask_qicon(size, color):
    icon = QIcon()
    for dpr in (1.0, 1.25, 1.5, 2.0):
        icon.addPixmap(mask_icon(size, color, dpr))
    return icon
