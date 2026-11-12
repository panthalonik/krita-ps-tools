# -*- coding: utf-8 -*-
"""
Photoshop-style adjustment layers on top of Krita filter / fill layers.

Every PS adjustment is mapped to a Krita filter layer (or fill layer) and
described by simple PS-like parameters ("params"). params <-> Krita filter
configuration conversion lives here. The PS kind + params of each layer are
also stored in a document annotation, so they survive saving to .kra.
"""
import json
import math
import re

from krita import InfoObject, Krita, Selection
from PyQt5.QtCore import QByteArray
from PyQt5.QtGui import QColor

from . import core

ANNOTATION = "ps_tools_adjustments"

# Photoshop menu (None = separator). Disabled entries have no Krita equivalent.
MENU = [
    ("camera_raw", u"Camera Raw…"),
    None,
    ("clarity", u"Clarity…"),
    ("grain", u"Grain…"),
    None,
    ("brightness_contrast", u"Brightness/Contrast…"),
    ("levels", u"Levels…"),
    ("curves", u"Curves…"),
    ("exposure", u"Exposure…"),
    None,
    ("hue_saturation", u"Hue/Saturation…"),
    ("color_balance", u"Color Balance…"),
    ("black_white", u"Black & White…"),
    ("photo_filter", u"Photo Filter…"),
    ("color_lookup", u"Color Lookup…"),
    ("selective_color", u"Selective Color…"),
    None,
    ("invert", u"Invert"),
    ("posterize", u"Posterize…"),
    ("threshold", u"Threshold…"),
    ("gradient_map", u"Gradient Map…"),
    None,
    ("solid_color", u"Solid Color…"),
    ("gradient", u"Gradient…"),
    ("pattern", u"Pattern…"),
]

UNAVAILABLE = {}


def refresh_unavailable():
    """Camera Raw needs the C++ filter of Krita PS."""
    try:
        if "pscameraraw" not in Krita.instance().filters():
            UNAVAILABLE["camera_raw"] = u"Needs Krita PS (this repository's build of Krita)"
        else:
            UNAVAILABLE.pop("camera_raw", None)
    except Exception:
        pass

# kind -> (PS title / layer base name, "filter"|"fill", krita filter/generator id)
KINDS = {
    "camera_raw": (u"Camera Raw", "filter", "pscameraraw"),
    "clarity": (u"Clarity", "filter", "unsharp"),
    "grain": (u"Grain", "filter", "noise"),
    "brightness_contrast": (u"Brightness/Contrast", "filter", "perchannel"),
    "levels": (u"Levels", "filter", "levels"),
    "curves": (u"Curves", "filter", "perchannel"),
    "exposure": (u"Exposure", "filter", "perchannel"),
    "hue_saturation": (u"Hue/Saturation", "filter", "hsvadjustment"),
    "color_balance": (u"Color Balance", "filter", "colorbalance"),
    "black_white": (u"Black & White", "filter", "desaturate"),
    "photo_filter": (u"Photo Filter", "fill", "color"),
    "invert": (u"Invert", "filter", "invert"),
    "posterize": (u"Posterize", "filter", "posterize"),
    "threshold": (u"Threshold", "filter", "threshold"),
    "gradient_map": (u"Gradient Map", "filter", "gradientmap"),
    "solid_color": (u"Color Fill", "fill", "color"),
    "gradient": (u"Gradient Fill", "fill", "gradient"),
    "pattern": (u"Pattern Fill", "fill", "pattern"),
    # Krita PS: real filter layers (C++ filters, see NATIVE_FILTERS).
    # Official Krita: computed by the plugin (a paint layer re-rendered from
    # the image below, see baked.py)
    "color_lookup": (u"Color Lookup", "baked", None),
    "selective_color": (u"Selective Color", "baked", None),
}

BAKED_KINDS = ("color_lookup", "selective_color")

# kind -> filter of krita/plugins/filters/psfilters (only in Krita PS)
NATIVE_FILTERS = {"color_lookup": "pscolorlookup", "selective_color": "psselectivecolor"}
SELECTIVE_RANGES = ["reds", "yellows", "greens", "cyans", "blues", "magentas",
                    "whites", "neutrals", "blacks"]

# Krita filter -> kind, for layers made without the plugin
FILTER_TO_KIND = {
    "levels": "levels", "perchannel": "curves", "hsvadjustment": "hue_saturation",
    "colorbalance": "color_balance", "desaturate": "black_white", "invert": "invert",
    "posterize": "posterize", "threshold": "threshold", "gradientmap": "gradient_map",
    "noise": "grain", "unsharp": "clarity",
    "pscolorlookup": "color_lookup", "psselectivecolor": "selective_color",
    "pscameraraw": "camera_raw",
}

PHOTO_FILTERS = [
    (u"Warming Filter (85)", "#EC8A00"), (u"Warming Filter (LBA)", "#FA9600"),
    (u"Warming Filter (81)", "#EBB113"), (u"Cooling Filter (80)", "#006DFF"),
    (u"Cooling Filter (LBB)", "#005DFF"), (u"Cooling Filter (82)", "#00B5FF"),
    (u"Red", "#EA1A1A"), (u"Orange", "#F38417"), (u"Yellow", "#F9E31C"),
    (u"Green", "#19C919"), (u"Cyan", "#1DCBEA"), (u"Blue", "#1D35EA"),
    (u"Violet", "#9B1DEA"), (u"Magenta", "#E318E3"), (u"Sepia", "#AC7A33"),
    (u"Deep Red", "#FF0000"), (u"Deep Blue", "#0022CD"), (u"Deep Emerald", "#008C00"),
    (u"Deep Yellow", "#FFD500"), (u"Underwater", "#00C1B1"),
]

IDENTITY_CURVE = [[0.0, 0.0], [1.0, 1.0]]


def defaults(kind):
    if kind == "camera_raw":
        from . import camera_raw
        return camera_raw.defaults()
    d = {
        "clarity": {"amount": 25, "radius": 0},
        "grain": {"amount": 25, "intensity": 50},
        "brightness_contrast": {"brightness": 0, "contrast": 0, "legacy": False},
        "levels": {"in_black": 0, "in_white": 255, "gamma": 1.0,
                   "out_black": 0, "out_white": 255},
        "curves": {"rgb": IDENTITY_CURVE, "red": IDENTITY_CURVE,
                   "green": IDENTITY_CURVE, "blue": IDENTITY_CURVE},
        "exposure": {"exposure": 0.0, "offset": 0.0, "gamma": 1.0},
        "hue_saturation": {"hue": 0, "saturation": 0, "lightness": 0, "colorize": False},
        "color_balance": {"shadows": [0, 0, 0], "midtones": [0, 0, 0],
                          "highlights": [0, 0, 0], "preserve": True},
        "black_white": {"mode": 1},
        "photo_filter": {"color": "#EC8A00", "density": 25, "preserve": True},
        "invert": {},
        "posterize": {"levels": 4},
        "threshold": {"level": 128},
        "gradient_map": {},
        "solid_color": {"color": "#808080"},
        "gradient": {},
        "pattern": {},
        "color_lookup": {"lut": "", "name": ""},
        "selective_color": dict([("method", "relative")] +
                                [(r, [0, 0, 0, 0]) for r in SELECTIVE_RANGES]),
    }
    return json.loads(json.dumps(d.get(kind, {})))


# ------------------------------------------------------- native PS filters
def native_filters():
    """True in Krita PS: Color Lookup and Selective Color are real filter
    layers there, rendered live by Krita like any other adjustment."""
    if core.engine_level() < 1:
        return False
    try:
        names = Krita.instance().filters()
    except Exception:
        return False
    return all(f in names for f in NATIVE_FILTERS.values())


def layer_kind(kind):
    """(layer type, krita filter id) a NEW layer of this kind is made of."""
    title, ltype, kid = KINDS[kind]
    if ltype == "baked" and native_filters():
        return "filter", NATIVE_FILTERS[kind]
    return ltype, kid


def is_baked_node(node, kind=None):
    """A computed paint layer (official Krita, or a document from it)."""
    if node is None or node.type() != "paintlayer":
        return False
    return kind in BAKED_KINDS if kind is not None else True


def _lut_props(p):
    """Properties of the C++ Color Lookup filter for params."""
    from . import baked, lutfile
    if not (p.get("lut") or p.get("name")):
        return {"lut": "", "lut_id": "", "linear": False, "name": ""}
    path = baked.resolve_lut(p)
    if not path:
        raise baked.BakeError(u"LUT file not found: %s\nChoose it again in the Properties "
                              u"panel (or add its folder with \"Choose LUT folder…\")."
                              % (p.get("name") or p.get("lut")))
    try:
        lut = lutfile.load(path)
        text, lut_id = lutfile.filter_property(lut)
        return {"lut": text, "lut_id": lut_id,
                "linear": bool(lutfile.is_linear(lut, path)),
                "name": p.get("name") or lut.title}
    except lutfile.LutError as e:
        raise baked.BakeError(str(e))


def _selective_props(p):
    from . import pixelops
    values = pixelops.adj_vector(p)                       # -1..1
    return {"values": ",".join(_f(round(v * 100.0, 4)) for v in values),
            "relative": p.get("method", "relative") == "relative"}


def _native_params(node, kind):
    """Params read back from a native filter layer's own configuration
    (a layer without plugin data, e.g. duplicated)."""
    p = defaults(kind)
    try:
        props = node.filter().configuration().properties()
    except Exception:
        return p
    if kind == "color_lookup":
        p["name"] = props.get("name") or ""
    else:
        try:
            vals = [float(x) for x in str(props.get("values") or "").split(",")]
        except ValueError:
            vals = []
        if len(vals) == 36:
            from . import pixelops
            for i, r in enumerate(pixelops.RANGES):
                p[r] = [int(round(v)) for v in vals[i * 4:i * 4 + 4]]
        rel = props.get("relative", True)
        if isinstance(rel, str):
            rel = rel.lower() not in ("false", "0")
        p["method"] = "relative" if rel else "absolute"
    return p


# ------------------------------------------------------------ annotation store
def _load_store(doc):
    try:
        if ANNOTATION in doc.annotationTypes():
            return json.loads(bytes(doc.annotation(ANNOTATION)).decode("utf-8"))
    except Exception:
        pass
    return {}


def _save_store(doc, store):
    data = json.dumps(store).encode("utf-8")
    doc.setAnnotation(ANNOTATION, "PS-style adjustment layer data (ps_tools plugin)",
                      QByteArray(data))


def remember(doc, node, kind, params):
    store = _load_store(doc)
    store[core.node_key(node)] = {"kind": kind, "params": params}
    _save_store(doc, store)


def recall(doc, node):
    return _load_store(doc).get(core.node_key(node))


# ------------------------------------------------------------- helpers
def _f(x):
    return ("%.6f" % x).rstrip("0").rstrip(".") if x != int(x) else str(int(x))


def _levels_curve_str(ib, iw, g, ob, ow):
    """Krita levels curve: in black;in white;gamma;out black;out white (0..1)."""
    return "%s;%s;%s;%s;%s" % (_f(ib), _f(iw), _f(g), _f(ob), _f(ow))


def _parse_levels_str(s):
    try:
        v = [float(x) for x in s.split(";")[:5]]
        if len(v) == 5:
            return v
    except Exception:
        pass
    return None


def _virtual_channels(doc):
    """Number of Krita 'virtual channels' (same layout Krita's own Levels and
    Curves dialogs use, identical in Krita 5.2 and 5.3):
    RGBA -> RGB(all), R, G, B, A, Hue, Saturation, Lightness = 8
    GRAYA -> Gray, Alpha = 2.  Channel 0 is the composite (PS "RGB")."""
    model = doc.colorModel()
    if model == "RGBA":
        return 8
    if model == "GRAYA":
        return 2
    return None


def levels_props(doc, ib, iw, g, ob, ow):
    """Levels config on the composite channel (like PS RGB levels)."""
    n = _virtual_channels(doc)
    curve = _levels_curve_str(ib, iw, g, ob, ow)
    if n is None:  # Lab / CMYK ...: Krita's lightness mode
        return {"mode": "lightness", "lightness": curve}
    p = {"mode": "channels", "number_of_channels": n}
    for i in range(n):
        p["channel_%d" % i] = curve if i == 0 else "0;1;1;0;1"
    # Krita applies properties in alphabetical order; "number_of_channels"
    # comes after "channel_N", so re-trigger the channel transfer update
    # with a key that sorts last but still matches /channel_\d+/
    p["zchannel_0"] = "0;1;1;0;1"
    return p


def linear_to_levels(a, k):
    """y = a*x + k (clipped) -> levels (ib, iw, ob, ow) in 0..1."""
    a = max(a, 1e-4)
    ib, ob = 0.0, k
    if k < 0:
        ib, ob = -k / a, 0.0
    top = a + k
    iw, ow = 1.0, top
    if top > 1:
        iw, ow = (1.0 - k) / a, 1.0
    ib = min(max(ib, 0.0), 0.999)
    iw = min(max(iw, ib + 0.001), 1.0)
    return ib, iw, min(max(ob, 0.0), 1.0), min(max(ow, 0.0), 1.0)


def bc_to_levels(brightness, contrast):
    # contrast -50..100 -> slope 0.5..2, brightness -150..150 -> offset
    a = 1.0 + contrast / 100.0
    off = brightness / 300.0
    k = 0.5 - 0.5 * a + off
    return linear_to_levels(a, k)


def exposure_to_levels(exposure, offset, gamma):
    a = 2.0 ** exposure
    ib, iw, ob, ow = linear_to_levels(a, offset)
    g = max(0.01, gamma)
    return ib, iw, g, ob ** (1.0 / g) if ob > 0 else 0.0, ow ** (1.0 / g)


def curve_str(points):
    pts = sorted((min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0))
                 for x, y in points)
    return "".join("%s,%s;" % (_f(x), _f(y)) for x, y in pts)


def parse_curve(s):
    pts = []
    for part in (s or "").split(";"):
        if "," in part:
            try:
                x, y = part.split(",")[:2]
                pts.append([float(x), float(y)])
            except ValueError:
                pass
    return pts if len(pts) >= 2 else [list(p) for p in IDENTITY_CURVE]


def color_from(value, default="#808080"):
    if isinstance(value, QColor):
        return value.name()
    if isinstance(value, str):
        if value.startswith("#"):
            return value
        m = re.search(r'r="([\d.eE+-]+)"\s+g="([\d.eE+-]+)"\s+b="([\d.eE+-]+)"', value)
        if not m:
            m = re.search(r'g="([\d.eE+-]+)"\s+r="([\d.eE+-]+)"\s+b="([\d.eE+-]+)"', value)
            if m:
                g, r, b = (float(x) for x in m.groups())
                return QColor.fromRgbF(min(r, 1), min(g, 1), min(b, 1)).name()
        if m:
            r, g, b = (float(x) for x in m.groups())
            return QColor.fromRgbF(min(r, 1), min(g, 1), min(b, 1)).name()
    return default


def bw_gradient_xml():
    def stop(off, v):
        return ('<stop offset="%s" bitdepth="U8" alpha="1" stoptype="0">'
                '<RGB r="%s" g="%s" b="%s" space="sRGB-elle-V2-srgbtrc.icc"/></stop>'
                % (off, v, v, v))
    return '<gradient type="stop" name="Black to White">%s%s</gradient>' % (stop(0, 0), stop(1, 1))


def _curve_props(doc, rgb, red, green, blue):
    n = _virtual_channels(doc)
    if n == 8:
        # RGBA, R, G, B, A, Hue, Saturation, Lightness (Krita 5.1+ layout)
        curves = [rgb, red, green, blue] + [IDENTITY_CURVE] * 4
    else:
        curves = [rgb, IDENTITY_CURVE]
    props = {"nTransfers": len(curves)}
    for i, c in enumerate(curves):
        # "zcurveN": sorts after "nTransfers" but Krita still reads it as curve N
        props["zcurve%d" % i] = curve_str(c)
    return props


_tone_cache = {}


def tone_curve_points(kind, p):
    from . import tonecurves as T
    if kind == "exposure":
        key = (kind, round(float(p["exposure"]), 3), round(float(p["offset"]), 5),
               round(float(p["gamma"]), 3))
        fn = lambda: T.exposure_fn(key[1], key[2], key[3])
    else:
        key = (kind, int(p["brightness"]), int(p["contrast"]), bool(p.get("legacy")))
        fn = lambda: T.bc_fn(key[1], key[2], key[3])
    pts = _tone_cache.get(key)
    if pts is None:
        if len(_tone_cache) > 200:
            _tone_cache.clear()
        pts = _tone_cache[key] = T.fit_points(fn())[0]
    return pts


def _levels_fallback_props(doc, kind, p):
    """Krita < 5.3 ignores curve settings from scripts: approximate with Levels."""
    if kind == "brightness_contrast":
        ib, iw, ob, ow = bc_to_levels(p["brightness"], p["contrast"])
        return levels_props(doc, ib, iw, 1.0, ob, ow)
    ib, iw, g, ob, ow = exposure_to_levels(p["exposure"], p["offset"], p["gamma"])
    return levels_props(doc, ib, iw, g, ob, ow)


# ------------------------------------------------------- params -> krita
def build_props(doc, kind, p):
    """Krita filter/generator properties + layer settings for given params."""
    layer = {}
    if kind == "levels":
        props = levels_props(doc, p["in_black"] / 255.0, p["in_white"] / 255.0,
                             p["gamma"], p["out_black"] / 255.0, p["out_white"] / 255.0)
    elif kind in ("brightness_contrast", "exposure"):
        # the real Photoshop tone curve, applied to R, G and B (composite curve)
        props = _curve_props(doc, tone_curve_points(kind, p), IDENTITY_CURVE,
                             IDENTITY_CURVE, IDENTITY_CURVE)
    elif kind == "curves":
        props = _curve_props(doc, p["rgb"], p["red"], p["green"], p["blue"])
    elif kind == "hue_saturation":
        props = {"h": int(p["hue"]), "s": int(p["saturation"]), "v": int(p["lightness"]),
                 "type": 1, "colorize": bool(p["colorize"]), "compatibilityMode": False}
    elif kind == "color_balance":
        props = {"preserve_luminosity": bool(p["preserve"])}
        for tone in ("shadows", "midtones", "highlights"):
            cr, mg, yb = p[tone]
            props["cyan_red_" + tone] = int(cr)
            props["magenta_green_" + tone] = int(mg)
            props["yellow_blue_" + tone] = int(yb)
    elif kind == "black_white":
        props = {"type": int(p["mode"])}
    elif kind == "posterize":
        props = {"steps": int(p["levels"])}
    elif kind == "threshold":
        props = {"threshold": int(p["level"])}
    elif kind == "grain":
        props = {"level": int(p["amount"]), "opacity": int(p["intensity"])}
    elif kind == "clarity":
        radius = int(p.get("radius") or 0)
        if radius <= 0:
            radius = max(8, int(max(doc.width(), doc.height()) / 120))
        props = {"halfSize": radius, "amount": p["amount"] / 50.0,
                 "threshold": 0, "lightnessOnly": True}
    elif kind == "gradient_map":
        props = {"gradientXML": bw_gradient_xml()} if not p.get("_keep") else {}
    elif kind == "color_lookup":
        props = _lut_props(p)
    elif kind == "selective_color":
        props = _selective_props(p)
    elif kind == "camera_raw":
        from . import camera_raw
        props = camera_raw.build_props(p)
    elif kind == "solid_color":
        props = {"color": QColor(p["color"])}
    elif kind == "photo_filter":
        props = {"color": QColor(p["color"])}
        layer = {"blend": "color" if p["preserve"] else "multiply",
                 "opacity": int(round(p["density"] * 2.55))}
    else:
        props = {}
    return props, layer


def params_from_node(doc, node, kind):
    """Read params back from the layer (annotation first, then the config)."""
    if kind == "camera_raw":
        from . import camera_raw
        p = camera_raw.params_from_node(node)       # the filter itself is the truth
        rec = recall(doc, node)
        if rec and isinstance(rec.get("params"), dict) and rec["params"].get("wb_mode"):
            p["wb_mode"] = rec["params"]["wb_mode"]
        return p
    rec = recall(doc, node)
    if rec and rec.get("kind") == kind and isinstance(rec.get("params"), dict):
        p = defaults(kind)
        p.update(rec["params"])
        if kind not in ("levels", "curves"):
            return p
    p = defaults(kind)
    if kind in BAKED_KINDS:
        return _native_params(node, kind) if node.type() == "filterlayer" else p
    try:
        props = (node.filter().configuration().properties() if node.type() == "filterlayer"
                 else node.filterConfig().properties())
    except Exception:
        return p
    if kind == "levels":
        s = props.get("channel_0") if props.get("mode") == "channels" else props.get("lightness")
        v = _parse_levels_str(s or "")
        if v:
            p.update(in_black=int(round(v[0] * 255)), in_white=int(round(v[1] * 255)),
                     gamma=v[2], out_black=int(round(v[3] * 255)),
                     out_white=int(round(v[4] * 255)))
    elif kind == "curves":
        keys = ["rgb", "red", "green", "blue"]
        for i, key in enumerate(keys):
            s = props.get("zcurve%d" % i) or props.get("curve%d" % i)
            if s:
                p[key] = parse_curve(s)
    elif kind == "hue_saturation":
        p.update(hue=props.get("h", 0), saturation=props.get("s", 0),
                 lightness=props.get("v", 0), colorize=bool(props.get("colorize", False)))
    elif kind == "color_balance":
        for tone in ("shadows", "midtones", "highlights"):
            p[tone] = [props.get("cyan_red_" + tone, 0), props.get("magenta_green_" + tone, 0),
                       props.get("yellow_blue_" + tone, 0)]
        p["preserve"] = bool(props.get("preserve_luminosity", True))
    elif kind == "black_white":
        p["mode"] = int(props.get("type", 1))
    elif kind == "posterize":
        p["levels"] = int(props.get("steps", 4))
    elif kind == "threshold":
        p["level"] = int(props.get("threshold", 128))
    elif kind == "grain":
        p.update(amount=int(props.get("level", 25)), intensity=int(props.get("opacity", 50)))
    elif kind == "clarity":
        p.update(amount=int(round(float(props.get("amount", 0.5)) * 50)),
                 radius=int(props.get("halfSize", 0)))
    elif kind in ("solid_color", "photo_filter"):
        p["color"] = color_from(props.get("color"), p["color"])
        if kind == "photo_filter":
            p["density"] = int(round(node.opacity() / 2.55))
            p["preserve"] = node.blendingMode() == "color"
    return p


def kind_of(doc, node):
    if node is None:
        return None
    t = node.type()
    if t == "filtermask":
        try:
            return "camera_raw" if node.filter().name() == "pscameraraw" else None
        except Exception:
            return None
    if t == "paintlayer":
        rec = recall(doc, node)
        if rec and rec.get("kind") in BAKED_KINDS:
            return rec["kind"]
        return None
    if t not in ("filterlayer", "filllayer"):
        return None
    rec = recall(doc, node)
    if rec and rec.get("kind") in KINDS:
        return rec["kind"]
    try:
        if t == "filterlayer":
            return FILTER_TO_KIND.get(node.filter().name(), "generic")
        gen = node.generatorName()
        return {"color": "solid_color", "gradient": "gradient", "pattern": "pattern"}.get(gen, "generic_fill")
    except Exception:
        return "generic"


# ----------------------------------------------------------- apply / create
def _info(props):
    io = InfoObject()
    for k, v in props.items():
        io.setProperty(k, v)
    return io


def apply(doc, node, kind, params, do_refresh=True):
    """Apply params to an existing adjustment layer. Returns False when Krita
    ignored the settings (Curves on Krita < 5.3)."""
    k = Krita.instance()
    title, ltype, kid = KINDS[kind]
    if ltype == "baked":
        from . import baked
        if is_baked_node(node, kind):
            remember(doc, node, kind, params)
            baked.render(doc, node, kind, params, use_cache=True)
            return True
        if not native_filters():
            raise baked.BakeError(u"This %s layer was made in Krita PS - it can only be "
                                  u"edited there." % title)
        ltype, kid = "filter", NATIVE_FILTERS[kind]
    props, layer = build_props(doc, kind, params)
    if ltype == "filter":
        f = k.filter(kid)
        cfg = f.configuration()
        for key, v in props.items():
            cfg.setProperty(key, v)
        f.setConfiguration(cfg)
        node.setFilter(f)
    else:
        node.setGenerator(kid, _info(props))
    layer_changed = False
    if "blend" in layer and node.blendingMode() != layer["blend"]:
        node.setBlendingMode(layer["blend"])
        layer_changed = True
    if "opacity" in layer and node.opacity() != layer["opacity"]:
        node.setOpacity(layer["opacity"])
        layer_changed = True
    remember(doc, node, kind, params)
    # Krita PS (patches/0003): setFilter() itself redraws the layer, live and
    # asynchronously - no blocking re-render of the whole image (scripted
    # opacity / blending mode changes still need one)
    if core.engine_level() >= 2 and not layer_changed:
        # (a filter mask redraws by itself only from engine level 6)
        if node.type() != "filtermask" or core.engine_level() >= 6:
            do_refresh = False
    if kind in ("curves", "brightness_contrast", "exposure"):
        try:
            ok = int(node.filter().configuration().properties().get("nTransfers", 0)) > 0
        except Exception:
            ok = False
        if not ok and kind != "curves":
            f = k.filter("levels")
            cfg = f.configuration()
            for key, v in _levels_fallback_props(doc, kind, params).items():
                cfg.setProperty(key, v)
            f.setConfiguration(cfg)
            node.setFilter(f)
            ok = True
        if do_refresh:
            doc.refreshProjection()
        return ok
    if do_refresh:
        doc.refreshProjection()
    return True


def _next_name(doc, base):
    used = set()
    def walk(n):
        for c in n.childNodes():
            used.add(c.name())
            walk(c)
    walk(doc.rootNode())
    i = 1
    while "%s %d" % (base, i) in used:
        i += 1
    return "%s %d" % (base, i)


def create(kind, params=None):
    """Creates the adjustment layer above the active layer (like PS)."""
    k = Krita.instance()
    doc = k.activeDocument()
    if doc is None:
        return None
    title = KINDS[kind][0]
    ltype, kid = layer_kind(kind)
    params = params or defaults(kind)
    if ltype == "baked":
        from . import baked
        return baked.create(doc, kind, title, params)
    active = doc.activeNode()
    if active is not None and core.is_mask(active):
        active = active.parentNode()
    if active is None or active.parentNode() is None:
        parent, above = doc.rootNode(), None
    else:
        parent, above = active.parentNode(), active
    # PS: an active selection becomes the new layer's mask - a native
    # transparency mask, added below once the layer is in the document
    W, H = doc.width(), doc.height()
    mask_data = None
    user_sel = doc.selection()
    if user_sel is not None and user_sel.width() > 0:
        mask_data = bytes(user_sel.pixelData(0, 0, W, H))
        if len(mask_data) != W * H:
            mask_data = None
    sel = Selection()
    sel.select(0, 0, W, H, 255)
    props, layer = build_props(doc, kind, params)
    name = _next_name(doc, title)
    if ltype == "filter":
        f = k.filter(kid)
        cfg = f.configuration()
        for key, v in props.items():
            cfg.setProperty(key, v)
        f.setConfiguration(cfg)
        node = doc.createFilterLayer(name, f, sel)
    else:
        node = doc.createFillLayer(name, kid, _info(props), sel)
    if node is None:
        return None
    parent.addChildNode(node, above)
    if "blend" in layer:
        node.setBlendingMode(layer["blend"])
    if "opacity" in layer:
        node.setOpacity(layer["opacity"])
    remember(doc, node, kind, params)
    if mask_data is not None:
        core.ensure_mask_blending(node)
        mask = doc.createTransparencyMask(core.USER_MASK_NAME)
        node.addChildNode(mask, None)
        mask.setPixelData(mask_data, 0, 0, W, H)
    doc.waitForDone()
    doc.setActiveNode(node)
    doc.refreshProjection()
    return node


def input_histogram(doc, layer, size=256):
    """Histogram of what is below the adjustment layer (like PS Levels/Curves).
    Returns dict channel -> 256 counts."""
    with core.below_only(doc, layer) as d:       # off-screen, no canvas flash
        img = d.thumbnail(size, size)
    from PyQt5.QtGui import QImage
    img = img.convertToFormat(QImage.Format_RGB32)
    w, h = img.width(), img.height()
    raw = core.qimage_bytes(img, w, h, 4)
    gray = core.rgb32_to_gray_bytes(img, w, h)
    out = {}
    for name, data in (("rgb", gray), ("blue", raw[0::4]), ("green", raw[1::4]),
                       ("red", raw[2::4])):
        out[name] = [data.count(bytes([i])) for i in range(256)]
    return out


# ----------------------------------------- old computed layers -> native
def convert_baked_layers(doc):
    """Krita PS: turns the computed Color Lookup / Selective Color paint
    layers of a document (made in the official Krita or by an older version
    of the plugin) into real filter layers, which Krita renders live.
    Name, opacity, blending mode, visibility, clipping and masks are kept.
    A layer whose LUT file cannot be found stays as it is (and is still
    refreshed the old way). Returns the number of converted layers."""
    if not native_filters():
        return 0
    from . import baked
    done = 0
    for layer, kind, params in list(baked.iter_baked(doc)):
        try:
            if _convert_baked(doc, layer, kind, params):
                done += 1
        except Exception as e:
            _log(u"could not convert %s to a live layer: %s" % (layer.name(), e))
    if done:
        _log(u"converted %d computed adjustment layer(s) to live filter layers" % done)
    return done


def _log(text):
    try:
        from .extension import log
        log(text)
    except Exception:
        pass


def _convert_baked(doc, layer, kind, params):
    parent = layer.parentNode()
    if parent is None:
        return False
    k = Krita.instance()
    props, _extra = build_props(doc, kind, params)      # BakeError: LUT missing
    f = k.filter(NATIVE_FILTERS[kind])
    cfg = f.configuration()
    for key, v in props.items():
        cfg.setProperty(key, v)
    f.setConfiguration(cfg)
    sel = Selection()
    sel.select(0, 0, doc.width(), doc.height(), 255)
    node = doc.createFilterLayer(layer.name(), f, sel)
    if node is None:
        return False
    active = doc.activeNode()
    was_active = active is not None and (
        core._same(active, layer) or (core.is_mask(active) and core._same(active.parentNode(), layer)))
    parent.addChildNode(node, layer)                    # right above the old one
    node.setBlendingMode(layer.blendingMode())
    node.setOpacity(layer.opacity())
    for get, put in (("inheritAlpha", "setInheritAlpha"), ("colorLabel", "setColorLabel")):
        try:
            getattr(node, put)(getattr(layer, get)())
        except Exception:
            pass
    prev = None
    for c in layer.childNodes():                        # bottom to top
        if not core.is_mask(c):
            continue
        copy = c.duplicate()
        if copy is None:
            continue
        node.addChildNode(copy, prev)
        prev = copy
    node.setVisible(layer.visible())
    store = _load_store(doc)
    store.pop(core.node_key(layer), None)
    store[core.node_key(node)] = {"kind": kind, "params": params}
    _save_store(doc, store)
    layer.remove()
    if was_active:
        doc.setActiveNode(node)
    return True
