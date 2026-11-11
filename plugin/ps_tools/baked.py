# -*- coding: utf-8 -*-
"""Photoshop adjustments Krita has no filter for (Color Lookup, Selective
Color), done as "baked" adjustment layers.

The layer is a normal paint layer whose pixels are computed from the image
below it (exactly what a PS adjustment layer sees). Opacity, blend modes,
transparency masks, clipping and Blend If then work like on any layer.
When the image below changes the layer is re-computed (automatically - see
autorefresh.py - or with "Refresh Blend If / Adjustments")."""
import glob
import os
import sys

from krita import Krita, Selection

from . import core, lutfile, pixelops

BAKED_KINDS = ("color_lookup", "selective_color")

SETTINGS_GROUP = "ps_tools"
FOLDERS_KEY = "lutFolders"


class BakeError(Exception):
    pass


# ------------------------------------------------------------- LUT folders
def photoshop_lut_dirs():
    pats = []
    if sys.platform == "win32":
        for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                pats.append(os.path.join(base, "Adobe", "Adobe Photoshop*", "Presets", "3DLUTs"))
                pats.append(os.path.join(base, "Adobe", "Adobe Photoshop*", "Presets", "3DLUTs", "*"))
        appdata = os.environ.get("APPDATA")
        if appdata:
            pats.append(os.path.join(appdata, "Adobe", "Adobe Photoshop*", "Presets", "3DLUTs"))
    elif sys.platform == "darwin":
        pats.append("/Applications/Adobe Photoshop*/Presets/3DLUTs")
    out = []
    for p in pats:
        for d in sorted(glob.glob(p), reverse=True):     # newest PS first
            if os.path.isdir(d) and d not in out:
                out.append(d)
    return out


def user_lut_dirs():
    raw = Krita.instance().readSetting(SETTINGS_GROUP, FOLDERS_KEY, "")
    return [d for d in raw.split("|") if d]


def set_user_lut_dirs(dirs):
    seen = []
    for d in dirs:
        if d and d not in seen:
            seen.append(d)
    Krita.instance().writeSetting(SETTINGS_GROUP, FOLDERS_KEY, "|".join(seen))


def lut_dirs():
    plugin_luts = os.path.join(os.path.dirname(os.path.abspath(__file__)), "luts")
    res_luts = os.path.join(Krita.instance().getAppDataLocation(), "luts")
    dirs = user_lut_dirs() + [res_luts, plugin_luts] + photoshop_lut_dirs()
    return [d for d in dirs if os.path.isdir(d)]


def available_luts():
    return lutfile.list_luts(lut_dirs())


def resolve_lut(params):
    """Path of the LUT file of params (falls back to a file with the same name
    in the LUT folders, e.g. after moving the .kra to another computer)."""
    path = params.get("lut") or ""
    if path and os.path.isfile(path):
        return path
    name = params.get("name") or (os.path.splitext(os.path.basename(path))[0] if path else "")
    if not name:
        return None
    for n, p in available_luts():
        if n.lower() == name.lower():
            return p
    return None


# ------------------------------------------------------------- rendering
def is_neutral(kind, params):
    if kind == "color_lookup":
        return not (params.get("lut") or params.get("name"))
    if kind == "selective_color":
        return not any(pixelops.adj_vector(params))
    return True


def capture_under_raw(doc, layer, restore_refresh=True):
    """Composite of everything below the layer (bytes in the image format).
    Rendered on an off-screen copy of the document, so the canvas does not flash."""
    return core.under_bytes(doc, layer, restore_refresh=restore_refresh)


_under_cache = {}     # at most one interactive input buffer
CACHE_LIMIT = 256 * 1024 * 1024


def forget(node=None):
    if node is None:
        _under_cache.clear()
    else:
        _under_cache.pop(core.node_key(node), None)


def compute(doc, kind, params, under):
    """Returns the adjusted pixels (bytearray), or None when neutral."""
    if is_neutral(kind, params):
        return None
    model, depth = doc.colorModel(), doc.colorDepth()
    buf = bytearray(under)
    try:
        if kind == "color_lookup":
            path = resolve_lut(params)
            if not path:
                raise BakeError(u"LUT file not found: %s\nChoose it again in the Properties "
                                u"panel (or add its folder with \"Choose LUT folder…\")."
                                % (params.get("name") or params.get("lut")))
            lut = lutfile.load(path)
            linear = lutfile.is_linear(lut, path)
            pixelops.apply_lut(buf, model, depth, lut, linear)
        elif kind == "selective_color":
            pixelops.selective_color(buf, model, depth, params,
                                     relative=params.get("method", "relative") == "relative")
    except pixelops.PixelFormatError as e:
        raise BakeError(str(e))
    except lutfile.LutError as e:
        raise BakeError(str(e))
    return buf


def render(doc, node, kind, params, use_cache=False, under=None):
    """(Re)computes the layer pixels from the image below."""
    key = core.node_key(node)
    w, h = doc.width(), doc.height()
    if under is None and use_cache:
        under = _under_cache.get(key)
        if under is not None and len(under) != w * h * core.bytes_per_pixel(doc.colorModel(), doc.colorDepth()):
            under = None
    if under is None:
        if is_neutral(kind, params):
            under = b""
        else:
            # no projection refresh after showing the layers again - the
            # setPixelData + refresh below re-renders everything once
            under = capture_under_raw(doc, node, restore_refresh=False)
            _under_cache.clear()
            if use_cache and len(under) <= CACHE_LIMIT:
                _under_cache[key] = under
    try:
        buf = None if is_neutral(kind, params) else compute(doc, kind, params, under)
    except Exception:
        core.refresh(doc)
        raise
    bpp = core.bytes_per_pixel(doc.colorModel(), doc.colorDepth())
    if buf is None:
        node.setPixelData(bytes(w * h * bpp), 0, 0, w, h)     # empty = no effect
    else:
        node.setPixelData(bytes(buf), 0, 0, w, h)
    core.refresh(doc)
    # computed layer (Color Lookup, Selective Color): locked, so it cannot be
    # painted on by accident - the plugin still writes its pixels
    try:
        if not node.locked():
            node.setLocked(True)
    except Exception:
        pass
    # Blend If on this layer depends on both its pixels and what is below
    mask = core.find_blendif_mask(node)
    if mask is not None:
        s = core.BlendIfSettings.from_name(mask.name())
        if s is not None and not s.is_neutral():
            under_src = None
            if s.uses_under() and under:
                under_src = core.PixelSource(under, doc.colorModel(), doc.colorDepth(), w, h)
            core.apply_settings(doc, node, s, under_src=under_src)


def is_baked(doc, node):
    if node is None or node.type() != "paintlayer":
        return False
    from . import adjustments as A
    rec = A.recall(doc, node)
    return bool(rec and rec.get("kind") in BAKED_KINDS)


def iter_baked(doc):
    from . import adjustments as A
    store = A._load_store(doc)
    if not store:
        return
    for layer in core.iter_layers(doc.rootNode()):
        if layer.type() != "paintlayer":
            continue
        rec = store.get(core.node_key(layer))
        if rec and rec.get("kind") in BAKED_KINDS:
            yield layer, rec["kind"], rec.get("params") or {}


def create(doc, kind, title, params):
    from . import adjustments as A
    active = doc.activeNode()
    if active is not None and core.is_mask(active):
        active = active.parentNode()
    if active is None or active.parentNode() is None:
        parent, above = doc.rootNode(), None
    else:
        parent, above = active.parentNode(), active
    pixelops.layout(doc.colorModel(), doc.colorDepth())     # raises if unsupported
    node = doc.createNode(A._next_name(doc, title), "paintlayer")
    parent.addChildNode(node, above)
    A.remember(doc, node, kind, params)
    sel = doc.selection()
    if sel is not None and sel.width() > 0:
        mask = doc.createTransparencyMask(u"Transparency Mask")
        node.addChildNode(mask, None)
        try:
            mask.setSelection(sel)
        except Exception:
            pass
    render(doc, node, kind, params)
    doc.waitForDone()
    doc.setActiveNode(node)
    return node
