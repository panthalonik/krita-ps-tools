"""Krita PS paths of the plugin (engine level 2: C++ Color Lookup / Selective
Color filters, Blend If in the compositing engine), run against
verification/fake_krita. These are not tests of a running Krita: they check
that the plugin hands the work to the engine and computes nothing itself."""
import base64
import itertools
import json
import pathlib
import struct
import sys
import types
import unittest
import zlib
from array import array

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verification"))
import fake_krita                                # noqa: E402
fake_krita.install()
_pkg = types.ModuleType("ps_tools")
_pkg.__path__ = [str(ROOT / "ps_tools")]
sys.modules["ps_tools"] = _pkg
from fake_krita import Document, Node, Uid      # noqa: E402

krita = sys.modules["krita"]
_uids = itertools.count(100000)


# ---------------------------------------------------- fakes for this test
class InfoObject(object):
    def __init__(self, props=None):
        self.props = dict(props or {})

    def setProperty(self, k, v):
        self.props[k] = v

    def properties(self):
        return dict(self.props)


class Filter(object):
    def __init__(self, name):
        self._name, self._cfg = name, InfoObject()

    def name(self):
        return self._name

    def configuration(self):
        return InfoObject(self._cfg.props)

    def setConfiguration(self, cfg):
        self._cfg = InfoObject(cfg.props)


class Selection(object):
    def select(self, x, y, w, h, v):
        self.rect = (x, y, w, h, v)


FILTERS = ["levels", "perchannel", "pscolorlookup", "psselectivecolor", "psblendif"]
krita.Krita.filters = staticmethod(lambda: list(FILTERS))
krita.Krita.filter = staticmethod(lambda name: Filter(name))
krita.Selection = Selection
krita.InfoObject = InfoObject


def _filter(self):
    return self._filter


def _set_filter(self, f):
    self._filter = Filter(f.name())
    self._filter.setConfiguration(f.configuration())
    self.filter_sets = getattr(self, "filter_sets", 0) + 1


def _opacity(self):
    return getattr(self, "_opacity", 255)


def _set_opacity(self, v):
    self._opacity = v


def _duplicate(self):
    n = Node(self.doc, self._name, self._type, Uid(next(_uids)))
    n._visible, n._blend, n.pixels = self._visible, self._blend, self.pixels
    for k in self.kids:
        c = k.duplicate()
        c._parent = n
        n.kids.append(c)
    return n


def _set_selection(self, sel):
    self.selection_set = sel


_orig_set_pixels = Node.setPixelData


def _set_pixels(self, data, x, y, w, h):
    self.pixel_writes = getattr(self, "pixel_writes", 0) + 1
    _orig_set_pixels(self, data, x, y, w, h)


for name, fn in (("filter", _filter), ("setFilter", _set_filter), ("opacity", _opacity),
                 ("setOpacity", _set_opacity), ("duplicate", _duplicate),
                 ("setSelection", _set_selection), ("setPixelData", _set_pixels)):
    setattr(Node, name, fn)


def _create_filter_layer(self, name, f, sel):
    n = Node(self, name, "filterlayer")
    n.setFilter(f)
    n.filter_sets = 0
    return n


Document.createFilterLayer = _create_filter_layer

from ps_tools import adjustments as A           # noqa: E402
from ps_tools import autorefresh, core, dialog, lutfile, pixelops  # noqa: E402

core.engine_level = lambda: 2                   # Krita PS
core.idle = lambda doc: True
core.capture_this = core.capture_under = core.compute_mask = None   # must not be used

BI = "Blend If (Gray | 10/20 200/255 | 0/0 255/255)"


def make(doc, name, kind, parent=None, above=None):
    n = Node(doc, name, kind)
    (parent or doc.root).addChildNode(n, above)
    return n


class NativeBlendIf(unittest.TestCase):
    def setUp(self):
        self.doc = Document()
        self.bg = make(self.doc, "Background", "paintlayer")
        self.layer = make(self.doc, "Layer", "paintlayer", above=self.bg)

    def test_settings_only_in_the_mask_name(self):
        s = core.BlendIfSettings.from_name(BI)
        core.apply_settings(self.doc, self.layer, s)
        mask = core.find_blendif_mask(self.layer)
        self.assertIsNotNone(mask)
        self.assertEqual(mask.name(), BI)
        self.assertEqual(getattr(mask, "pixel_writes", 0), 0)     # nothing computed
        self.assertEqual(self.doc.refreshes, 0)                    # the engine redraws
        s2 = core.BlendIfSettings("Red", (0, 0, 128, 200), (0, 0, 255, 255))
        core.apply_settings(self.doc, self.layer, s2)
        self.assertIs(core.find_blendif_mask(self.layer), mask)    # renamed, not re-made
        self.assertEqual(mask.name(), s2.to_name())
        core.apply_settings(self.doc, self.layer, core.BlendIfSettings())
        self.assertIsNone(core.find_blendif_mask(self.layer))

    def test_filter_layer_leaves_copy_mode(self):
        f = make(self.doc, "Levels 1", "filterlayer", above=self.layer)
        self.assertEqual(f.blendingMode(), "copy")
        core.apply_settings(self.doc, f, core.BlendIfSettings.from_name(BI))
        self.assertEqual(f.blendingMode(), "normal")

    def test_refresh_all_computes_nothing(self):
        core.apply_settings(self.doc, self.layer, core.BlendIfSettings.from_name(BI))
        mask = core.find_blendif_mask(self.layer)
        self.assertEqual(core.refresh_all(self.doc), 1)
        self.assertEqual(getattr(mask, "pixel_writes", 0), 0)

    def test_auto_refresh_has_nothing_to_do(self):
        core.apply_settings(self.doc, self.layer, core.BlendIfSettings.from_name(BI))
        self.assertEqual(autorefresh.dependents(self.doc), [])

    def test_dialog_session_preview_cancel_accept(self):
        s = dialog.BlendIfSession(self.doc, self.layer)
        self.assertTrue(s.native)
        s.apply(core.BlendIfSettings.from_name(BI))
        mask = core.find_blendif_mask(self.layer)
        self.assertEqual(mask.name(), BI)
        s.finish(False, s.initial)                                 # Cancel: new mask goes
        self.assertIsNone(core.find_blendif_mask(self.layer))

        core.apply_settings(self.doc, self.layer, core.BlendIfSettings.from_name(BI))
        s = dialog.BlendIfSession(self.doc, self.layer)
        other = core.BlendIfSettings("Blue", (0, 0, 255, 255), (30, 60, 255, 255))
        s.apply(other)
        self.assertEqual(core.find_blendif_mask(self.layer).name(), other.to_name())
        s.finish(False, s.initial)                                 # Cancel: old name back
        self.assertEqual(core.find_blendif_mask(self.layer).name(), BI)

        s = dialog.BlendIfSession(self.doc, self.layer)
        s.apply(other)
        s.finish(True, other)
        self.assertEqual(core.find_blendif_mask(self.layer).name(), other.to_name())
        s = dialog.BlendIfSession(self.doc, self.layer)
        s.apply(core.BlendIfSettings())
        s.finish(True, core.BlendIfSettings())                     # neutral: removed
        self.assertIsNone(core.find_blendif_mask(self.layer))
        self.assertEqual(self.doc.refreshes, 0)


class LutWireFormat(unittest.TestCase):
    def test_filter_property_round_trip(self):
        data1d = array("f", [0.0, 0.1, 0.2, 0.5, 0.5, 0.5, 1.0, 0.9, 0.8])
        lut = lutfile.Lut(2, array("f", [i / 24.0 for i in range(24)]),
                          dmin=(0.0, 0.1, 0.2), dmax=(1.0, 0.9, 0.8),
                          data1d=data1d, size1d=3)
        text, lut_id = lutfile.filter_property(lut)
        self.assertEqual(len(lut_id), 40)
        packed = base64.b64decode(text)
        raw = zlib.decompress(packed[4:])
        self.assertEqual(struct.unpack(">I", packed[:4])[0], len(raw))   # qUncompress
        self.assertEqual(raw[:4], b"PSL1")
        n3, n1 = struct.unpack("<ii", raw[4:12])
        self.assertEqual((n3, n1), (2, 3))
        dom = struct.unpack("<6f", raw[12:36])
        self.assertAlmostEqual(dom[1], 0.1, places=6)
        self.assertAlmostEqual(dom[5], 0.8, places=6)
        self.assertEqual(len(raw), 36 + (9 + 24) * 4)
        self.assertEqual(array("f", raw[36:72]).tolist(), data1d.tolist())

    def test_3d_only(self):
        lut = lutfile.Lut(2, array("f", [0.5] * 24))
        raw = zlib.decompress(base64.b64decode(lutfile.filter_property(lut)[0])[4:])
        self.assertEqual(struct.unpack("<ii", raw[4:12]), (2, 0))
        self.assertEqual(len(raw), 36 + 24 * 4)


def _store(doc):
    return json.loads(bytes(doc.annotation(A.ANNOTATION)).decode("utf-8"))


class NativeAdjustments(unittest.TestCase):
    def setUp(self):
        self.doc = Document()
        self.bg = make(self.doc, "Background", "paintlayer")
        krita.Krita.activeDocument = staticmethod(lambda: self.doc)
        self.doc.setActiveNode(self.bg)

    def test_new_layers_are_filter_layers(self):
        self.assertTrue(A.native_filters())
        self.assertEqual(A.layer_kind("color_lookup"), ("filter", "pscolorlookup"))
        node = A.create("selective_color")
        self.assertEqual(node.type(), "filterlayer")
        self.assertEqual(node.filter().name(), "psselectivecolor")
        self.assertEqual(A.kind_of(self.doc, node), "selective_color")

    def test_selective_color_properties(self):
        p = A.defaults("selective_color")
        p["reds"] = [10, -20, 0, 100]
        p["blacks"] = [0, 0, 0, -35]
        p["method"] = "absolute"
        props, _layer = A.build_props(self.doc, "selective_color", p)
        vals = [float(v) for v in props["values"].split(",")]
        self.assertEqual(len(vals), 36)
        self.assertEqual(vals[:4], [10, -20, 0, 100])
        self.assertEqual(vals[-4:], [0, 0, 0, -35])
        self.assertFalse(props["relative"])
        node = A.create("selective_color", p)
        self.assertEqual(node.filter().configuration().properties()["values"], props["values"])
        # read back without the plugin's own record (e.g. a duplicated layer)
        back = A._native_params(node, "selective_color")
        self.assertEqual(back["reds"], [10, -20, 0, 100])
        self.assertEqual(back["method"], "absolute")

    def test_apply_does_not_block_on_a_full_refresh(self):
        node = A.create("selective_color")
        before = self.doc.refreshes
        p = A.defaults("selective_color")
        p["greens"] = [5, 5, 5, 5]
        A.apply(self.doc, node, "selective_color", p)
        self.assertEqual(node.filter_sets, 1)
        self.assertEqual(self.doc.refreshes, before)

    def test_empty_color_lookup_is_neutral(self):
        props, _ = A.build_props(self.doc, "color_lookup", A.defaults("color_lookup"))
        self.assertEqual(props["lut"], "")

    def test_old_computed_layer_becomes_live(self):
        group = make(self.doc, "Group", "grouplayer", above=self.bg)
        below = make(self.doc, "Below", "paintlayer", parent=group)
        old = make(self.doc, "Selective Color 1", "paintlayer", parent=group, above=below)
        above = make(self.doc, "Above", "paintlayer", parent=group, above=old)
        old.setBlendingMode("multiply")
        old.setOpacity(128)
        old.setInheritAlpha(True)
        m1 = make(self.doc, "Transparency Mask", "transparencymask", parent=old)
        m2 = make(self.doc, BI, "transparencymask", parent=old, above=m1)
        p = A.defaults("selective_color")
        p["reds"] = [0, 0, 0, 50]
        A.remember(self.doc, old, "selective_color", p)
        self.doc.setActiveNode(m2)

        self.assertEqual(A.convert_baked_layers(self.doc), 1)
        kids = group.childNodes()
        self.assertEqual([k.name() for k in kids], ["Below", "Selective Color 1", "Above"])
        new = kids[1]
        self.assertEqual(new.type(), "filterlayer")
        self.assertEqual(new.filter().name(), "psselectivecolor")
        self.assertEqual((new.blendingMode(), new.opacity(), new.inheritAlpha()),
                         ("multiply", 128, True))
        self.assertEqual([c.name() for c in new.childNodes()], ["Transparency Mask", BI])
        self.assertIs(self.doc.activeNode(), new)
        store = _store(self.doc)
        self.assertNotIn(core.node_key(old), store)
        self.assertEqual(store[core.node_key(new)]["params"]["reds"], [0, 0, 0, 50])
        self.assertEqual(A.kind_of(self.doc, new), "selective_color")
        self.assertEqual(A.convert_baked_layers(self.doc), 0)     # nothing left

    def test_layer_with_missing_lut_stays_computed(self):
        old = make(self.doc, "Color Lookup 1", "paintlayer", above=self.bg)
        A.remember(self.doc, old, "color_lookup", {"lut": "/nowhere/x.cube", "name": "zz-none"})
        self.assertEqual(A.convert_baked_layers(self.doc), 0)
        self.assertIs(self.doc.root.childNodes()[-1], old)


class OfficialKrita(unittest.TestCase):
    def test_no_engine_no_native_paths(self):
        saved = core.engine_level
        core.engine_level = lambda: 0
        try:
            self.assertFalse(A.native_filters())
            self.assertFalse(core.native_blend_if())
            self.assertEqual(A.layer_kind("color_lookup"), ("baked", None))
            self.assertEqual(A.convert_baked_layers(Document()), 0)
        finally:
            core.engine_level = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
