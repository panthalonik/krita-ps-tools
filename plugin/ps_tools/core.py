# -*- coding: utf-8 -*-
"""
Blend If core: computes a mask from the brightness / channel of the
layer ("This Layer") and of the composite below it ("Underlying Layer").

The result goes into a regular transparency mask whose name starts with
"Blend If (...)" - the layer pixels are never modified. This is the same for
paint, group and adjustment (filter) layers.
Settings are stored in the mask name, so they survive saving to .kra.
"""
import re

from krita import Krita
from PyQt5.QtGui import QImage, QPainter

CHANNELS = ["Gray", "Red", "Green", "Blue"]
MASK_PREFIX = "Blend If"
NEUTRAL = (0, 0, 255, 255)

_NAME_RE = re.compile(
    r"^Blend If \((Gray|Red|Green|Blue) \| (\d+)/(\d+) (\d+)/(\d+) \| (\d+)/(\d+) (\d+)/(\d+)\)"
)

_CHANNELS_PER_MODEL = {"RGBA": 4, "GRAYA": 2, "CMYKA": 5, "LABA": 4,
                       "XYZA": 4, "YCbCrA": 4, "A": 1}
_BYTES_PER_DEPTH = {"U8": 1, "U16": 2, "F16": 2, "F32": 4}


class BlendIfError(Exception):
    pass


# ------------------------------------------------------------------ settings
class BlendIfSettings(object):
    def __init__(self, channel="Gray", this=NEUTRAL, under=NEUTRAL):
        self.channel = channel if channel in CHANNELS else "Gray"
        self.this = tuple(int(v) for v in this)
        self.under = tuple(int(v) for v in under)

    def copy(self):
        return BlendIfSettings(self.channel, self.this, self.under)

    def is_neutral(self):
        return self.this == NEUTRAL and self.under == NEUTRAL

    def uses_under(self):
        return self.under != NEUTRAL

    def to_name(self):
        t, u = self.this, self.under
        return "Blend If (%s | %d/%d %d/%d | %d/%d %d/%d)" % (
            self.channel, t[0], t[1], t[2], t[3], u[0], u[1], u[2], u[3])

    @classmethod
    def from_name(cls, name):
        m = _NAME_RE.match(name or "")
        if not m:
            return None
        v = [min(255, max(0, int(x))) for x in m.groups()[1:]]
        return cls(m.group(1), v[0:4], v[4:8])

    def __eq__(self, other):
        return (isinstance(other, BlendIfSettings) and self.channel == other.channel
                and self.this == other.this and self.under == other.under)

    def __ne__(self, other):
        return not self.__eq__(other)


def make_lut(vals):
    """Trapezoid curve 0..255 -> opacity 0..255 (like the PS Blend If sliders)."""
    b0, b1, w0, w1 = vals
    lut = bytearray(256)
    for v in range(256):
        if v < b0:
            a = 0.0
        elif v < b1:
            a = (v - b0) / float(b1 - b0)
        elif v <= w0:
            a = 1.0
        elif v <= w1:
            a = (w1 - v) / float(w1 - w0)
        else:
            a = 0.0
        lut[v] = int(round(a * 255))
    return bytes(lut)


# ----------------------------------------------------------- QImage helpers
def _stride(w):
    return (w + 3) & ~3


def pad_rows(data, w, h):
    """Pads each row to 4 bytes (QImage requirement for 8-bit formats)."""
    s = _stride(w)
    if s == w:
        return data
    pad = b"\0" * (s - w)
    return b"".join(data[i * w:(i + 1) * w] + pad for i in range(h))


def qimage_bytes(img, w, h, bpp=1):
    """Raw QImage bytes without row padding."""
    ptr = img.constBits()
    total = img.bytesPerLine() * h
    ptr.setsize(total)
    raw = ptr.asstring(total)
    bpl = img.bytesPerLine()
    row = w * bpp
    if bpl == row:
        return raw[:row * h]
    return b"".join(raw[i * bpl:i * bpl + row] for i in range(h))


def gray_rgb32(padded, w, h, lut=None):
    """8-bit gray (padded) -> RGB32, optionally through a LUT (done in C++)."""
    img = QImage(padded, w, h, _stride(w), QImage.Format_Indexed8)
    table = lut if lut is not None else bytes(range(256))
    img.setColorTable([0xff000000 | (v << 16) | (v << 8) | v for v in table])
    return img.convertToFormat(QImage.Format_RGB32)


def rgb32_to_gray_bytes(img, w, h):
    g = img.convertToFormat(QImage.Format_Grayscale8)
    return qimage_bytes(g, w, h, 1)


# ------------------------------------------------------------ pixel sources
def bytes_per_pixel(model, depth):
    return _CHANNELS_PER_MODEL.get(model, 4) * _BYTES_PER_DEPTH.get(depth, 1)


class PixelSource(object):
    """8-bit pixels; channels are computed lazily and cached."""

    def __init__(self, data, model, depth, w, h):
        self.w, self.h = w, h
        self._cache = {}
        self._padded = {}
        data = bytes(data)
        if depth == "U16":
            data = data[1::2]          # high byte (little endian)
        elif depth != "U8":
            raise BlendIfError(
                u"Blend If supports 8- and 16-bit integer images. "
                u"This image has depth %s." % depth)
        if model == "RGBA":
            self._bgra = data
            self._gray_only = None
        elif model == "GRAYA":
            self._bgra = None
            self._gray_only = data[0::2]
        else:
            raise BlendIfError(
                u"Blend If supports RGB and Grayscale color models. "
                u"This image/layer is %s." % model)

    def channel(self, name):
        if name in self._cache:
            return self._cache[name]
        if self._gray_only is not None:
            res = self._gray_only
        elif name == "Blue":
            res = self._bgra[0::4]
        elif name == "Green":
            res = self._bgra[1::4]
        elif name == "Red":
            res = self._bgra[2::4]
        else:
            img = QImage(self._bgra, self.w, self.h, self.w * 4, QImage.Format_RGB32)
            res = rgb32_to_gray_bytes(img, self.w, self.h)
        self._cache[name] = res
        return res

    def padded(self, name):
        if name not in self._padded:
            self._padded[name] = pad_rows(self.channel(name), self.w, self.h)
        return self._padded[name]


def compute_mask(settings, this_src, under_src):
    """Returns the mask bytes (1 byte per pixel)."""
    ch = settings.channel
    w, h = this_src.w, this_src.h
    lt, lu = make_lut(settings.this), make_lut(settings.under)
    t_id = settings.this == NEUTRAL
    u_id = settings.under == NEUTRAL or under_src is None
    if t_id and u_id:
        return b"\xff" * (w * h)
    if u_id:
        return this_src.channel(ch).translate(lt)
    if t_id:
        return under_src.channel(ch).translate(lu)
    a = gray_rgb32(this_src.padded(ch), w, h, lt)
    b = gray_rgb32(under_src.padded(ch), w, h, lu)
    p = QPainter(a)
    p.setCompositionMode(QPainter.CompositionMode_Multiply)
    p.drawImage(0, 0, b)
    p.end()
    return rgb32_to_gray_bytes(a, w, h)


# --------------------------------------------------------------- Krita nodes
def is_layer(node):
    return node is not None and node.type().endswith("layer")


def is_mask(node):
    return node is not None and node.type().endswith("mask")


def is_blendif_mask(node):
    return (node is not None and node.type() == "transparencymask"
            and node.name().startswith(MASK_PREFIX))


def find_blendif_mask(layer):
    for c in layer.childNodes():
        if is_blendif_mask(c):
            return c
    return None


def refresh(doc):
    doc.refreshProjection()
    doc.waitForDone()


def _same(a, b):
    return a is not None and b is not None and a.uniqueId() == b.uniqueId()


def nodes_at_and_above(layer):
    """The layer + everything above it in compositing order."""
    res = [layer]
    node = layer
    while True:
        parent = node.parentNode()
        if parent is None:
            break
        sibs = parent.childNodes()          # bottom to top
        idx = next((i for i, s in enumerate(sibs) if _same(s, node)), None)
        if idx is not None:
            res.extend(s for s in sibs[idx + 1:] if is_layer(s))
        node = parent
    return res


def engine_level():
    """0 in the official Krita; in Krita PS (this repository's build) the
    level of the C++ additions (see krita/plugins/filters/psfilters):
    1 = C++ filters + native masks on pass-through groups."""
    try:
        from PyQt5.QtWidgets import QApplication
        v = QApplication.instance().property("ps_tools_engine")
        return int(v) if v is not None else 0
    except Exception:
        return 0


def idle(doc):
    """True when nothing is being painted / transformed right now."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication
    if QApplication.mouseButtons() != Qt.NoButton:
        return False
    try:
        if not doc.tryBarrierLock():
            return False
        doc.unlock()
    except Exception:
        pass
    return True


# ------------------------------------------------------- off-screen captures
# To read "what is below a layer" the layer and everything above it must be
# hidden and the image re-rendered. Doing that in the visible document makes
# the canvas flash (waitForDone() processes events, so the view really paints
# the image without those layers for a moment). Instead all such tricks are
# done on a hidden copy of the document (Document.clone(): no view, tiles are
# copy-on-write, so it is cheap) - the canvas never sees them.

_offscreen = {"ok": True}


def _log(text):
    try:
        from .extension import log
        log(text)
    except Exception:
        pass


def node_path(node):
    """Child indices from the root to the node."""
    path = []
    while node is not None and node.parentNode() is not None:
        parent = node.parentNode()
        sibs = parent.childNodes()
        idx = next((i for i, s in enumerate(sibs) if _same(s, node)), None)
        if idx is None:
            raise BlendIfError(u"Layer not found in the document.")
        path.append(idx)
        node = parent
    return path[::-1]


def node_at(root, path):
    node = root
    for i in path:
        node = node.childNodes()[i]
    return node


class Offscreen(object):
    """Lazily created hidden copy of a document. Use as a context manager.
    .doc is None when a copy could not be made (then callers fall back to
    working on the visible document)."""

    def __init__(self, doc):
        self.src = doc
        self._copy = None
        self._tried = False

    @property
    def doc(self):
        if not self._tried:
            self._tried = True
            if _offscreen["ok"]:
                try:
                    c = self.src.clone()
                    if c is None:
                        raise RuntimeError("Document.clone() returned None")
                    try:
                        c.setBatchmode(True)
                    except Exception:
                        pass
                    self._copy = c
                except Exception as e:
                    _offscreen["ok"] = False
                    _log("off-screen copy not available, falling back "
                         "to the visible document: %s" % e)
        return self._copy

    def find(self, node):
        """The node in the copy that corresponds to `node` of the source."""
        return node_at(self.doc.rootNode(), node_path(node))

    def close(self):
        if self._copy is not None:
            try:
                self._copy.close()
            except Exception:
                pass
            self._copy = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class below_only(object):
    """Context manager: yields a document whose projection is the composite
    of everything below `layer` (the layer and what is above it hidden).
    Works on an off-screen copy, so the visible canvas does not change.
        with below_only(doc, layer) as d:
            data = d.pixelData(0, 0, d.width(), d.height())"""

    def __init__(self, doc, layer, off=None, restore_refresh=True):
        self.doc, self.layer = doc, layer
        self.own = off is None
        self.off = Offscreen(doc) if off is None else off
        self.restore_refresh = restore_refresh
        self.hidden = []
        self.target = None

    def __enter__(self):
        copy = self.off.doc
        if copy is not None:
            self.target, node = copy, self.off.find(self.layer)
        else:
            self.target, node = self.doc, self.layer
        self.hidden = [n for n in nodes_at_and_above(node) if n.visible()]
        try:
            for n in self.hidden:
                n.setVisible(False)
            refresh(self.target)
        except Exception:
            self._restore()
            raise
        return self.target

    def _restore(self):
        for n in self.hidden:
            n.setVisible(True)
        self.hidden = []
        if self.target is self.doc and self.restore_refresh:
            refresh(self.doc)            # only the fallback touches the canvas

    def __exit__(self, *exc):
        try:
            self._restore()
        finally:
            if self.own:
                self.off.close()
        return False


def under_bytes(doc, layer, off=None, restore_refresh=True):
    """Composite of everything below the layer (bytes in the image format)."""
    w, h = doc.width(), doc.height()
    with below_only(doc, layer, off, restore_refresh) as d:
        return bytes(d.pixelData(0, 0, w, h))


# ------------------------------------------------- adjustment (filter) layers
# Krita creates filter layers with the "Copy" blending mode: the layer's
# result replaces what is below it, alpha included. A transparency mask
# lowers that alpha, so with "Copy" it cuts a hole through the whole image.
# With "Normal" the masked areas simply show the (unadjusted) image below -
# exactly a Photoshop layer mask on an adjustment layer. So masks on filter
# layers are ordinary Krita transparency masks (painted live, undoable,
# saved in the .kra); the plugin only makes sure the layer is not on "Copy".

LAYER_MASK_NAME = "Layer Mask"          # selection mask used by older versions
USER_MASK_NAME = "Transparency Mask"


def is_filter_layer(node):
    return node is not None and node.type() == "filterlayer"


def node_key(node):
    return node.uniqueId().toString()


def read_selection(doc, layer):
    """The filter layer's own built-in mask (1 byte per pixel)."""
    return bytes(layer.pixelData(0, 0, doc.width(), doc.height()))


def write_selection(doc, layer, data, do_refresh=True):
    layer.setPixelData(data, 0, 0, doc.width(), doc.height())
    if do_refresh:
        refresh(doc)


def has_visible_transparency_mask(layer):
    for c in layer.childNodes():
        if c.type() == "transparencymask" and c.visible():
            return True
    return False


def ensure_mask_blending(layer):
    """A filter layer that carries a transparency mask must not use "Copy".
    Returns True when the blending mode was changed."""
    if is_filter_layer(layer) and layer.blendingMode() == "copy":
        layer.setBlendingMode("normal")
        return True
    return False


# ---- documents saved by older versions of the plugin
# Old scheme on a filter layer: the user's mask lived in a "Layer Mask"
# selection mask (or, without one, in a hidden "Blend If (...)" store) and
# the plugin kept copying it (x Blend If) into the layer's built-in mask.

def find_layer_mask(layer):
    for c in layer.childNodes():
        if c.type() == "selectionmask" and c.name() == LAYER_MASK_NAME:
            return c
    return None


def _legacy_state(doc, layer, full):
    """(layer mask, hidden store) of a filter layer still in the old scheme,
    or None."""
    lm = find_layer_mask(layer)
    store = find_blendif_mask(layer)
    if store is not None and store.visible():
        store = None                      # a regular (new scheme) Blend If mask
    if store is not None and lm is None and read_selection(doc, layer) == full:
        store = None                      # hidden by the user, nothing baked in
    if lm is None and store is None:
        return None
    return lm, store


def migrate_legacy_masks(doc, skip=()):
    """Turns old-scheme masks of filter layers into native transparency
    masks. Returns the number of converted layers. skip: keys of layers
    whose built-in mask is written by groupmask.py (not old-scheme)."""
    w, h = doc.width(), doc.height()
    full = b"\xff" * (w * h)
    todo = []
    for layer in list(iter_layers(doc.rootNode())):
        if not is_filter_layer(layer) or node_key(layer) in skip:
            continue
        state = _legacy_state(doc, layer, full)
        if state is not None:
            todo.append((layer, state[0], state[1]))
    pending = []
    for layer, lm, store in todo:
        src = lm if lm is not None else store
        base = bytes(src.pixelData(0, 0, w, h))
        if len(base) != w * h:
            base = full
        ensure_mask_blending(layer)
        layer.setPixelData(full, 0, 0, w, h)
        if lm is not None or base != full:   # an explicit mask stays, even white
            mask = doc.createTransparencyMask(USER_MASK_NAME)
            layer.addChildNode(mask, None)
            mask.setPixelData(base, 0, 0, w, h)
        if lm is not None:
            lm.remove()
        if store is not None:
            s = BlendIfSettings.from_name(store.name())
            if s is None or s.is_neutral():
                store.remove()
            else:
                store.setVisible(True)
                pending.append((layer, s))
    if todo:
        refresh(doc)
        for layer, s in pending:
            apply_settings(doc, layer, s)
        _log("converted %d adjustment layer mask(s) to native transparency masks"
             % len(todo))
    return len(todo)


def capture_this(doc, layer, own_mask=None, off=None):
    """Pixels of the layer itself. Anything that needs re-rendering is done
    on an off-screen copy (`off`, created here if not given)."""
    w, h = doc.width(), doc.height()
    if is_filter_layer(layer):
        # "This Layer" of an adjustment = the adjusted image everywhere:
        # its projection without any mask (transparency masks hidden, the
        # built-in mask full)
        full = b"\xff" * (w * h)
        with _maybe_own(doc, off) as o:
            on_copy = o.doc is not None
            target = o.doc if on_copy else doc
            node = o.find(layer) if on_copy else layer
            sel = read_selection(target, node)
            hidden = [c for c in node.childNodes()
                      if c.type() == "transparencymask" and c.visible()]
            changed = bool(hidden) or sel != full
            try:
                for c in hidden:
                    c.setVisible(False)
                if sel != full:
                    write_selection(target, node, full, do_refresh=False)
                if changed or on_copy:
                    refresh(target)
                data = node.projectionPixelData(0, 0, w, h)
            finally:
                for c in hidden:
                    c.setVisible(True)
                if sel != full:
                    write_selection(target, node, sel, do_refresh=False)
                if changed and not on_copy:
                    refresh(doc)             # only the fallback touches the canvas
        return PixelSource(data, doc.colorModel(), doc.colorDepth(), w, h)
    model, depth = layer.colorModel(), layer.colorDepth()
    need = w * h * bytes_per_pixel(model, depth)
    data = layer.pixelData(0, 0, w, h)
    if len(data) < need:
        # groups / file layers etc. - use the projection without our mask
        if own_mask is None or not own_mask.visible():
            data = layer.projectionPixelData(0, 0, w, h)
        else:
            with _maybe_own(doc, off) as o:
                if o.doc is not None:
                    node, m = o.find(layer), o.find(own_mask)
                    m.setVisible(False)
                    try:
                        refresh(o.doc)
                        data = node.projectionPixelData(0, 0, w, h)
                    finally:
                        m.setVisible(True)
                else:
                    own_mask.setVisible(False)
                    refresh(doc)
                    data = layer.projectionPixelData(0, 0, w, h)
                    own_mask.setVisible(True)
                    refresh(doc)
        if len(data) < need:
            raise BlendIfError(u"Could not read the layer pixels.")
    return PixelSource(data, model, depth, w, h)


class _maybe_own(object):
    """Use the given Offscreen, or make (and later close) a new one."""

    def __init__(self, doc, off):
        self.own = off is None
        self.off = Offscreen(doc) if off is None else off

    def __enter__(self):
        return self.off

    def __exit__(self, *exc):
        if self.own:
            self.off.close()
        return False


def capture_under(doc, layer, off=None):
    """Composite of everything below the layer (rendered off-screen)."""
    w, h = doc.width(), doc.height()
    data = under_bytes(doc, layer, off)
    return PixelSource(data, doc.colorModel(), doc.colorDepth(), w, h)


def write_mask(doc, mask, data, do_refresh=True):
    mask.setPixelData(data, 0, 0, doc.width(), doc.height())
    if do_refresh:
        refresh(doc)


def create_blendif_mask(doc, layer, settings):
    ensure_mask_blending(layer)             # before the mask exists: no hole
    mask = doc.createTransparencyMask(settings.to_name())
    layer.addChildNode(mask, None)
    return mask


def target_layer(node):
    """The layer Blend If applies to (for a mask -> its layer)."""
    if node is None:
        return None
    if is_mask(node):
        node = node.parentNode()
    if is_layer(node) and node.parentNode() is not None:
        return node
    return None


def remove_blendif(doc, layer):
    mask = find_blendif_mask(layer)
    if mask is None:
        return
    mask.remove()
    refresh(doc)


def apply_settings(doc, layer, settings, this_src=None, under_src=None):
    """One-off recompute (e.g. refresh) - without the dialog."""
    mask = find_blendif_mask(layer)
    if settings.is_neutral():
        remove_blendif(doc, layer)
        return
    with Offscreen(doc) as off:          # one hidden copy for both captures
        if this_src is None:
            this_src = capture_this(doc, layer, mask, off)
        if under_src is None and settings.uses_under():
            under_src = capture_under(doc, layer, off)
    data = compute_mask(settings, this_src, under_src)
    if mask is None:
        mask = create_blendif_mask(doc, layer, settings)
    else:
        ensure_mask_blending(layer)
    mask.setName(settings.to_name())
    write_mask(doc, mask, data)


def iter_layers(node):
    for c in node.childNodes():
        if is_layer(c):
            yield c
            for x in iter_layers(c):
                yield x


def refresh_all(doc):
    """Recomputes all Blend If masks in the document. Returns their count."""
    count = 0
    for layer in list(iter_layers(doc.rootNode())):
        mask = find_blendif_mask(layer)
        if mask is None:
            continue
        s = BlendIfSettings.from_name(mask.name())
        if s is None:
            continue
        apply_settings(doc, layer, s)
        count += 1
    return count
