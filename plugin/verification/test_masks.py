"""Mask logic for adjustment (filter) layers, run against verification/fake_krita.
These are not tests of a running Krita."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verification"))
import types
import fake_krita
fake_krita.install()
# load the plugin's modules without running its __init__ (needs real Krita)
_pkg = types.ModuleType("ps_tools")
_pkg.__path__ = [str(ROOT / "ps_tools")]
sys.modules["ps_tools"] = _pkg
from fake_krita import Document, Node          # noqa: E402
from ps_tools import core, masks                # noqa: E402

core.idle = lambda doc: True                    # no mouse / stroke in tests
BI = "Blend If (Gray | 10/20 200/255 | 0/0 255/255)"


def make(doc, name, kind, parent=None, above=None):
    n = Node(doc, name, kind)
    (parent or doc.root).addChildNode(n, above)
    return n


def top(parent):
    return parent.kids[-1] if parent.kids else None


class Base(unittest.TestCase):
    def setUp(self):
        masks._migrated.clear()
        masks._ticks[0] = 0
        self.doc = Document()
        self.n = self.doc.w * self.doc.h
        self.full = b"\xff" * self.n
        self.bg = make(self.doc, "Background", "paintlayer")
        self.adj = make(self.doc, "Levels 1", "filterlayer", above=self.bg)

    def kids(self, layer=None):
        return [(k.type(), k.name(), k.visible()) for k in (layer or self.adj).kids]


class Blending(Base):
    def test_copy_becomes_normal_only_for_filter_layers(self):
        self.assertTrue(core.ensure_mask_blending(self.adj))
        self.assertEqual(self.adj.blendingMode(), "normal")
        self.assertFalse(core.ensure_mask_blending(self.adj))
        self.bg.setBlendingMode("copy")
        self.assertFalse(core.ensure_mask_blending(self.bg))
        self.assertEqual(self.bg.blendingMode(), "copy")

    def test_user_blending_mode_is_kept(self):
        self.adj.setBlendingMode("luminize")
        make(self.doc, "Transparency Mask", "transparencymask", self.adj)
        masks.maintain(self.doc, self.adj)
        self.assertEqual(self.adj.blendingMode(), "luminize")

    def test_no_mask_keeps_copy(self):
        masks.maintain(self.doc, self.adj)
        self.assertEqual(self.adj.blendingMode(), "copy")

    def test_hidden_mask_keeps_copy(self):
        m = make(self.doc, "Transparency Mask", "transparencymask", self.adj)
        m.setVisible(False)
        masks.maintain(self.doc, self.adj)
        self.assertEqual(self.adj.blendingMode(), "copy")


class Maintain(Base):
    def test_active_mask_fixes_its_layer_on_next_poll(self):
        masks.maintain(self.doc, self.bg)                 # first poll: full scan
        m = make(self.doc, "Transparency Mask", "transparencymask", self.adj)
        r0 = self.doc.refreshes
        self.assertTrue(masks.maintain(self.doc, m))      # mask active
        self.assertEqual(self.adj.blendingMode(), "normal")
        self.assertEqual(self.doc.refreshes, r0 + 1)
        self.assertFalse(masks.maintain(self.doc, m))     # nothing left to do
        self.assertEqual(self.doc.refreshes, r0 + 1)

    def test_other_layer_is_found_by_periodic_scan(self):
        masks.maintain(self.doc, self.bg)
        make(self.doc, "Transparency Mask", "transparencymask", self.adj)
        polls = 0
        while self.adj.blendingMode() == "copy" and polls < 50:
            masks.maintain(self.doc, self.bg)             # another layer active
            polls += 1
        self.assertLessEqual(polls, masks.FULL_SCAN_EVERY)

    def test_mask_is_left_alone(self):
        m = make(self.doc, "Transparency Mask", "transparencymask", self.adj)
        m.pixels = bytes(range(self.n))
        for _ in range(25):
            masks.maintain(self.doc, m)
        self.assertEqual(self.kids(), [("transparencymask", "Transparency Mask", True)])
        self.assertEqual(m.pixels, bytes(range(self.n)))
        self.assertEqual(self.adj.pixels, self.full)

    def test_not_idle_postpones_first_scan(self):
        old = core.idle
        core.idle = lambda doc: False
        try:
            make(self.doc, core.LAYER_MASK_NAME, "selectionmask", self.adj)
            self.assertFalse(masks.maintain(self.doc, self.adj))
            self.assertEqual(self.kids()[0][0], "selectionmask")
        finally:
            core.idle = old
        self.assertTrue(masks.maintain(self.doc, self.adj))
        self.assertEqual(self.kids()[0][0], "transparencymask")


class Migration(Base):
    def test_layer_mask_becomes_transparency_mask(self):
        painted = bytes((i * 20) % 256 for i in range(self.n))
        lm = make(self.doc, core.LAYER_MASK_NAME, "selectionmask", self.adj)
        lm.pixels = painted
        self.adj.pixels = painted                 # old plugin copied it there
        self.assertEqual(core.migrate_legacy_masks(self.doc), 1)
        self.assertEqual(self.kids(), [("transparencymask", core.USER_MASK_NAME, True)])
        self.assertEqual(self.adj.kids[0].pixels, painted)
        self.assertEqual(self.adj.pixels, self.full)
        self.assertEqual(self.adj.blendingMode(), "normal")
        self.assertEqual(core.migrate_legacy_masks(self.doc), 0)      # idempotent

    def test_layer_mask_plus_blend_if_store(self):
        painted = bytes([0, 255] * (self.n // 2))
        lm = make(self.doc, core.LAYER_MASK_NAME, "selectionmask", self.adj)
        lm.pixels = painted
        store = make(self.doc, BI, "transparencymask", self.adj, above=lm)
        store.setVisible(False)
        store.pixels = b"\x33" * self.n            # stale copy in the old store
        self.adj.pixels = bytes(self.n)            # old product (mask x Blend If)
        self.assertEqual(core.migrate_legacy_masks(self.doc), 1)
        kinds = sorted(self.kids())
        self.assertEqual(kinds, [("transparencymask", BI, True),
                                 ("transparencymask", core.USER_MASK_NAME, True)])
        user = [k for k in self.adj.kids if k.name() == core.USER_MASK_NAME][0]
        self.assertEqual(user.pixels, painted)     # Layer Mask wins over the store
        self.assertEqual(self.adj.pixels, self.full)
        self.assertEqual(self.adj.blendingMode(), "normal")
        self.assertEqual(core.migrate_legacy_masks(self.doc), 0)

    def test_store_only(self):
        painted = bytes([255, 128] * (self.n // 2))
        store = make(self.doc, BI, "transparencymask", self.adj)
        store.setVisible(False)
        store.pixels = painted
        self.adj.pixels = bytes([200, 100] * (self.n // 2))
        self.assertEqual(core.migrate_legacy_masks(self.doc), 1)
        user = [k for k in self.adj.kids if k.name() == core.USER_MASK_NAME][0]
        self.assertEqual(user.pixels, painted)
        bi = core.find_blendif_mask(self.adj)
        self.assertTrue(bi.visible())
        self.assertEqual(self.adj.pixels, self.full)

    def test_white_store_makes_no_user_mask(self):
        store = make(self.doc, BI, "transparencymask", self.adj)
        store.setVisible(False)
        self.adj.pixels = bytes(self.n)
        core.migrate_legacy_masks(self.doc)
        self.assertEqual([k.name() for k in self.adj.kids], [BI])

    def test_new_scheme_hidden_blend_if_is_not_legacy(self):
        bi = make(self.doc, BI, "transparencymask", self.adj)
        bi.setVisible(False)                       # user disabled Blend If
        bi.pixels = b"\x40" * self.n
        self.adj.setBlendingMode("normal")
        self.assertEqual(core.migrate_legacy_masks(self.doc), 0)
        self.assertEqual(self.kids(), [("transparencymask", BI, False)])
        self.assertEqual(bi.pixels, b"\x40" * self.n)

    def test_other_layers_and_plain_filter_layers_untouched(self):
        m = make(self.doc, "Transparency Mask", "transparencymask", self.bg)
        sm = make(self.doc, "Selection Mask", "selectionmask", self.bg, above=m)
        self.adj.pixels = bytes([7]) * self.n      # painted natively on the layer
        self.assertEqual(core.migrate_legacy_masks(self.doc), 0)
        self.assertEqual(len(self.bg.kids), 2)
        self.assertEqual(self.adj.pixels, bytes([7]) * self.n)
        self.assertEqual(self.adj.blendingMode(), "copy")
        self.assertIs(sm.parentNode(), self.bg)


class BlendIf(Base):
    def test_blend_if_on_adjustment_layer_is_a_visible_mask(self):
        user = make(self.doc, "Transparency Mask", "transparencymask", self.adj)
        user.pixels = bytes(range(self.n))
        self.adj.pixels = bytes([9]) * self.n      # natively painted built-in mask
        s = core.BlendIfSettings.from_name(BI)
        core.apply_settings(self.doc, self.adj, s)
        bi = core.find_blendif_mask(self.adj)
        self.assertIsNotNone(bi)
        self.assertTrue(bi.visible())
        self.assertEqual(self.adj.blendingMode(), "normal")
        # "This Layer" was read with every mask off and the built-in mask full
        read = self.adj.log[-1]
        self.assertEqual(read["selection"], self.full)
        self.assertEqual(read["visible_masks"], [])
        # ...and the document itself was left as it was
        self.assertEqual(user.pixels, bytes(range(self.n)))
        self.assertTrue(user.visible())
        self.assertEqual(self.adj.pixels, bytes([9]) * self.n)
        self.assertEqual(len(bi.pixels), self.n)

    def test_capture_this_fallback_restores_visible_document(self):
        user = make(self.doc, "Transparency Mask", "transparencymask", self.adj)
        self.adj.pixels = bytes([9]) * self.n
        old = core._offscreen["ok"]
        core._offscreen["ok"] = False              # Document.clone() unavailable
        try:
            core.capture_this(self.doc, self.adj)
        finally:
            core._offscreen["ok"] = old
        self.assertEqual(self.adj.log[-1]["visible_masks"], [])
        self.assertTrue(user.visible())
        self.assertEqual(self.adj.pixels, bytes([9]) * self.n)

    def test_neutral_settings_remove_the_mask(self):
        s = core.BlendIfSettings.from_name(BI)
        core.apply_settings(self.doc, self.adj, s)
        core.apply_settings(self.doc, self.adj, core.BlendIfSettings())
        self.assertIsNone(core.find_blendif_mask(self.adj))


if __name__ == "__main__":
    unittest.main(verbosity=1)
