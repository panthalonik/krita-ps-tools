"""Masks on pass-through groups (groupmask.py) and "Remove Transparency
Mask", run against verification/fake_krita. Not tests of a running Krita."""
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verification"))
import fake_krita
fake_krita.install()
_pkg = types.ModuleType("ps_tools")
_pkg.__path__ = [str(ROOT / "ps_tools")]
sys.modules["ps_tools"] = _pkg
from fake_krita import Document, Node                 # noqa: E402
from ps_tools import core, groupmask, masks            # noqa: E402

BI = "Blend If (Gray | 10/20 200/255 | 0/0 255/255)"


def make(doc, name, kind, parent=None):
    n = Node(doc, name, kind)
    parent = parent or doc.root
    parent.addChildNode(n, parent.kids[-1] if parent.kids else None)
    return n


class Base(unittest.TestCase):
    def setUp(self):
        groupmask._states.clear()
        masks._migrated.clear()
        self.idle = True
        core.idle = lambda doc: self.idle
        self.doc = Document(4, 2)
        self.n = 8
        self.full = b"\xff" * self.n
        self.bg = make(self.doc, "Background", "paintlayer")
        self.group = make(self.doc, "Group 2", "grouplayer")
        self.group.setPassThroughMode(True)
        self.bc = make(self.doc, "Brightness/Contrast 1", "filterlayer", self.group)
        self.lv = make(self.doc, "Levels 1", "filterlayer", self.group)
        self.paint = make(self.doc, "Paint", "paintlayer", self.group)
        self.M = bytes([0, 64, 128, 255, 255, 255, 0, 0])

    def mask(self, parent, data=None, name="Transparency Mask"):
        m = make(self.doc, name, "transparencymask", parent)
        m.pixels = data if data is not None else self.M
        return m


class GroupMask(Base):
    def test_group_mask_reaches_every_adjustment_layer(self):
        m = self.mask(self.group)
        self.assertTrue(groupmask.sync(self.doc, m))
        self.assertEqual(self.bc.pixels, self.M)
        self.assertEqual(self.lv.pixels, self.M)
        self.assertEqual(self.paint.pixels, bytes(self.n * 4))        # not an adjustment
        self.assertEqual(m.pixels, self.M)                             # the user's mask is untouched
        self.assertEqual(groupmask.controlled(self.doc),
                         {core.node_key(self.bc), core.node_key(self.lv)})
        self.assertFalse(groupmask.sync(self.doc, m))                  # nothing new

    def test_painting_updates_after_the_stroke(self):
        m = self.mask(self.group)
        groupmask.sync(self.doc, m)
        self.idle = False                                              # stroke running
        m.pixels = bytes(self.n)
        self.assertFalse(groupmask.sync(self.doc, m))
        self.assertEqual(self.bc.pixels, self.M)
        self.idle = True                                               # pen lifted
        self.assertTrue(groupmask.sync(self.doc, m))
        self.assertEqual(self.bc.pixels, bytes(self.n))
        self.assertEqual(self.lv.pixels, bytes(self.n))

    def test_strokes_elsewhere_do_not_reread_masks(self):
        m = self.mask(self.group)
        groupmask.sync(self.doc, m)
        reads = []
        real = m.pixelData
        m.pixelData = lambda *a: reads.append(1) or real(*a)
        for _ in range(5):
            self.idle = False
            groupmask.sync(self.doc, self.bg)
            self.idle = True
            groupmask.sync(self.doc, self.bg)                          # painting on Background
        self.assertEqual(reads, [])

    def test_isolated_group_is_left_to_krita(self):
        self.group.setPassThroughMode(False)
        m = self.mask(self.group)
        self.assertFalse(groupmask.sync(self.doc, m))
        self.assertEqual(self.bc.pixels, self.full)
        self.assertEqual(groupmask.controlled(self.doc), set())

    def test_removing_or_disabling_the_mask_restores(self):
        m = self.mask(self.group)
        groupmask.sync(self.doc, m)
        m.setVisible(False)                                            # Shift+click
        self.assertTrue(groupmask.sync(self.doc, m))
        self.assertEqual(self.bc.pixels, self.full)
        m.setVisible(True)
        groupmask.sync(self.doc, m)
        self.assertEqual(self.bc.pixels, self.M)
        m.remove()
        groupmask.sync(self.doc, self.group)
        self.assertEqual(self.bc.pixels, self.full)
        self.assertEqual(self.lv.pixels, self.full)
        self.assertEqual(groupmask.controlled(self.doc), set())

    def test_layer_dragged_out_of_the_group_is_restored(self):
        m = self.mask(self.group)
        groupmask.sync(self.doc, m)
        self.bc.remove()
        self.doc.root.addChildNode(self.bc, self.group)
        groupmask.sync(self.doc, self.bc)
        self.assertEqual(self.bc.pixels, self.full)
        self.assertEqual(self.lv.pixels, self.M)

    def test_switching_pass_through_off_restores(self):
        m = self.mask(self.group)
        groupmask.sync(self.doc, m)
        self.group.setPassThroughMode(False)
        groupmask.sync(self.doc, m)
        self.assertEqual(self.bc.pixels, self.full)

    def test_nested_pass_through_groups_multiply(self):
        inner = make(self.doc, "Inner", "grouplayer", self.group)
        inner.setPassThroughMode(True)
        hue = make(self.doc, "Hue 1", "filterlayer", inner)
        self.mask(self.group, bytes([255, 255, 128, 128, 0, 0, 255, 255]))
        m2 = self.mask(inner, bytes([255, 128, 255, 128, 255, 0, 255, 0]))
        groupmask.sync(self.doc, m2)
        self.assertEqual(self.bc.pixels, bytes([255, 255, 128, 128, 0, 0, 255, 255]))
        got = hue.pixels
        want = [255, 128, 128, 64, 0, 0, 255, 0]
        self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(got, want)), list(got))

    def test_layers_painted_by_the_user_are_never_reset(self):
        other = make(self.doc, "Curves 1", "filterlayer")
        other.pixels = bytes([7]) * self.n            # painted directly on the layer
        m = self.mask(self.group)
        groupmask.sync(self.doc, m)
        m.remove()
        groupmask.sync(self.doc, self.group)
        self.assertEqual(other.pixels, bytes([7]) * self.n)

    def test_reopened_document_keeps_working(self):
        m = self.mask(self.group)
        groupmask.sync(self.doc, m)
        groupmask._states.clear()                     # new session, same .kra
        m.pixels = bytes(self.n)
        groupmask.sync(self.doc, self.bg)
        self.assertEqual(self.bc.pixels, bytes(self.n))
        groupmask._states.clear()
        m.remove()                                    # removed, then first look of a session
        groupmask.sync(self.doc, self.bg)
        self.assertEqual(self.bc.pixels, self.full)

    def test_blend_if_mask_of_the_group_is_not_a_group_mask(self):
        self.mask(self.group, name=BI)
        self.assertFalse(groupmask.sync(self.doc, self.group))
        self.assertEqual(self.bc.pixels, self.full)

    def test_group_masked_layer_is_not_taken_for_an_old_document(self):
        m = self.mask(self.group)
        bi = self.mask(self.bc, self.full, name=BI)
        bi.setVisible(False)                          # Blend If disabled by the user
        groupmask.sync(self.doc, m)
        self.assertEqual(self.bc.pixels, self.M)
        masks.maintain(self.doc, self.bc)             # first poll: old-document check
        self.assertEqual([k.name() for k in self.bc.kids], [BI])
        self.assertEqual(self.bc.pixels, self.M)


class RemoveMask(Base):
    def test_finds_the_thumbnail_mask(self):
        self.assertIsNone(masks.layer_mask(self.bg))
        low = self.mask(self.bg, name="Low")
        self.mask(self.bg, name=BI)
        top = self.mask(self.bg, name="Top")
        self.assertIs(masks.layer_mask(self.bg), top)
        self.assertIs(masks.layer_mask(low), low)
        self.assertIsNone(masks.layer_mask(core.find_blendif_mask(self.bg)))
        self.assertIsNone(masks.layer_mask(None))

    def test_remove_on_layer_group_and_mask(self):
        from krita import Krita
        Krita.activeDocument = staticmethod(lambda: self.doc)
        for target in (self.bg, self.group, self.lv):
            m = self.mask(target)
            self.doc.setActiveNode(target)
            masks.remove_mask()
            self.assertNotIn(m, target.kids)
            self.assertIs(self.doc.activeNode(), target)
        m = self.mask(self.bg)
        bi = self.mask(self.bg, name=BI)
        self.doc.setActiveNode(m)                     # the mask itself is selected
        masks.remove_mask()
        self.assertEqual(self.bg.kids, [bi])          # Blend If stays
        self.assertIs(self.doc.activeNode(), self.bg)
        masks.remove_mask()                           # nothing left to remove: no error
        self.assertEqual(self.bg.kids, [bi])


if __name__ == "__main__":
    unittest.main(verbosity=1)
