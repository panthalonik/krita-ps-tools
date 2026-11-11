# -*- coding: utf-8 -*-
"""Photoshop-like Smart Objects built from Krita parts:

  Convert to Smart Object
      the selected layers are saved into their own .kra (in a
      "<document> Smart Objects" folder next to the document) and replaced
      by a File Layer that shows that file, plus a Transform Mask, so
      Ctrl+T scales / rotates it without losing quality (Krita's Transform
      tool automatically edits a layer's transform mask).

  Edit Contents (double-click the thumbnail, or right-click)
      opens that .kra in a new tab. Save it (Ctrl+S) and the smart object in
      the main document updates by itself (Krita reloads file layers when
      their file changes)."""
import os
import re

from krita import Krita
from PyQt5.QtWidgets import QMessageBox

from . import core

FOLDER_SUFFIX = u" Smart Objects"
TRANSFORM_MASK = u"Transform"


class SmartError(Exception):
    pass


def _parent_widget():
    w = Krita.instance().activeWindow()
    return w.qwindow() if w else None


def is_smart(node):
    return node is not None and node.type() == "filelayer"


def selected_layers(doc):
    """Layers selected in the Layers docker (top-level of the selection only),
    bottom to top. Falls back to the active layer."""
    nodes = []
    try:
        view = Krita.instance().activeWindow().activeView()
        nodes = [n for n in view.selectedNodes() if core.is_layer(n)]
    except Exception:
        nodes = []
    if not nodes:
        n = doc.activeNode()
        if n is not None and core.is_mask(n):
            n = n.parentNode()
        nodes = [n] if core.is_layer(n) and n.parentNode() is not None else []
    keys = set(core.node_key(n) for n in nodes)

    def has_selected_ancestor(n):
        p = n.parentNode()
        while p is not None:
            if core.node_key(p) in keys:
                return True
            p = p.parentNode()
        return False
    nodes = [n for n in nodes if not has_selected_ancestor(n)]
    # compositing order (bottom to top), as in the document
    order = {}
    for i, n in enumerate(_flat(doc.rootNode())):
        order[core.node_key(n)] = i
    nodes.sort(key=lambda n: order.get(core.node_key(n), 0))
    return nodes


def _flat(node):
    out = []
    for c in node.childNodes():
        out.extend(_flat(c))
        out.append(c)
    return out


def _safe_name(name):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return s[:80] or "Smart Object"


def smart_folder(doc):
    path = doc.fileName()
    base = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(os.path.dirname(path), base + FOLDER_SUFFIX)


def _unique_path(folder, name):
    p = os.path.join(folder, name + ".kra")
    i = 2
    while os.path.exists(p):
        p = os.path.join(folder, "%s %d.kra" % (name, i))
        i += 1
    return p


def _ensure_saved(doc):
    if doc.fileName():
        return True
    r = QMessageBox.question(
        _parent_widget(), u"Convert to Smart Object",
        u"Save the document first?\n\nThe smart object's contents are stored as a .kra file "
        u"in a folder next to your document, so the document needs a location.",
        QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Save)
    if r != QMessageBox.Save:
        return False
    Krita.instance().action("file_save_as").trigger()
    return bool(doc.fileName())


def _bounds(nodes, doc):
    rect = None
    for n in nodes:
        try:
            b = n.bounds()
        except Exception:
            continue
        if b.isEmpty():
            continue
        rect = b if rect is None else rect.united(b)
    if rect is None or rect.isEmpty():
        from PyQt5.QtCore import QRect
        rect = QRect(0, 0, doc.width(), doc.height())
    return rect


def convert(doc=None):
    k = Krita.instance()
    doc = doc or k.activeDocument()
    if doc is None:
        return None
    nodes = selected_layers(doc)
    if not nodes:
        raise SmartError(u"Select one or more layers first.")
    if not _ensure_saved(doc):
        return None
    top = nodes[-1]
    parent = top.parentNode()
    name = top.name() if len(nodes) == 1 else top.name()
    rect = _bounds(nodes, doc)

    # 1. the contents document
    sub = k.createDocument(doc.width(), doc.height(), name, doc.colorModel(),
                           doc.colorDepth(), doc.colorProfile(), doc.resolution())
    if sub is None:
        raise SmartError(u"Could not create the smart object document.")
    try:
        sub.setBatchmode(True)
        sroot = sub.rootNode()
        defaults = list(sroot.childNodes())
        for n in nodes:                      # bottom to top
            sroot.addChildNode(n.clone(), None)
        for d in defaults:
            d.remove()
        sub.refreshProjection()
        sub.waitForDone()
        sub.crop(rect.x(), rect.y(), rect.width(), rect.height())
        sub.waitForDone()
        sub.refreshProjection()
        sub.waitForDone()
        folder = smart_folder(doc)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        path = _unique_path(folder, _safe_name(name))
        if not sub.saveAs(path):
            raise SmartError(u"Could not save the smart object file:\n%s" % path)
    finally:
        try:
            sub.close()
        except Exception:
            pass

    # 2. the file layer in the main document
    fl = doc.createFileLayer(name, path, "None")
    if fl is None:
        raise SmartError(u"Could not create the file layer.")
    parent.addChildNode(fl, top)
    if rect.x() or rect.y():
        fl.move(rect.x(), rect.y())
    tm = doc.createTransformMask(TRANSFORM_MASK)
    if tm is not None:
        fl.addChildNode(tm, None)
    for n in nodes:
        n.remove()
    doc.waitForDone()
    doc.setActiveNode(fl)
    doc.refreshProjection()
    return fl


# ------------------------------------------------------------ edit contents
def edit_contents(node=None):
    k = Krita.instance()
    doc = k.activeDocument()
    if node is None and doc is not None:
        node = doc.activeNode()
        if node is not None and core.is_mask(node):
            node = node.parentNode()
    if not is_smart(node):
        raise SmartError(u"Select a smart object (file layer) first.")
    path = node.path()
    if not path or not os.path.isfile(path):
        raise SmartError(u"The smart object's file is missing:\n%s" % path)
    norm = os.path.normcase(os.path.abspath(path))
    win = k.activeWindow()
    # already open? switch to it
    for w in k.windows():
        for v in w.views():
            d = v.document()
            if d is not None and d.fileName() and \
                    os.path.normcase(os.path.abspath(d.fileName())) == norm:
                w.activate()
                w.showView(v)
                return d
    d = k.openDocument(path)
    if d is None:
        raise SmartError(u"Krita could not open:\n%s" % path)
    win.addView(d)
    return d
