"""Krita PS clipping (engine level 3: "inherit alpha" is a Photoshop clipping
mask, handled by the engine), run against verification/fake_krita. Checks
the plugin's side: which layer is clipped to which base, clip / release,
Ctrl+G widening, taking old "(clip)" groups apart and the Photoshop habits
kept by native_sync(). Not a test of a running Krita."""
import itertools
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verification"))
import fake_krita                                # noqa: E402
fake_krita.install()
_pkg = types.ModuleType("ps_tools")
_pkg.__path__ = [str(ROOT / "ps_tools")]
sys.modules["ps_tools"] = _pkg
from fake_krita import Document, Node, Uid      # noqa: E402

try:                                             # real Qt (CI): native_sync asks for mouse buttons
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(["test"])
except Exception:
    _app = None

krita = sys.modules["krita"]
krita.Krita.activeWindow = staticmethod(lambda: None)     # no Layers docker: fallback paths
_uids = itertools.count(200000)


def _opacity(self):
    return getattr(self, "_opacity", 255)


def _set_opacity(self, v):
    self._opacity = v


def _duplicate(self):
    n = Node(self.doc, self._name, self._type, Uid(next(_uids)))
    n._visible, n._blend, n.pixels = self._visible, self._blend, self.pixels
    return n


Node.opacity, Node.setOpacity, Node.duplicate = _opacity, _set_opacity, _duplicate

from ps_tools import clip, core                 # noqa: E402

core.engine_level = lambda: 3
core.idle = lambda doc: True


def make(doc, name, kind="paintlayer", parent=None, above=None, clipped=False):
    n = Node(doc, name, kind)
    (parent or doc.root).addChildNode(n, above)
    if clipped:
        n.setInheritAlpha(True)
    return n


def names(parent):
    return [n.name() for n in clip._layers(parent)]


def flags(parent):
    return [bool(n.inheritAlpha()) for n in clip._layers(parent)]


class Base(unittest.TestCase):
    def setUp(self):
        clip._nat_neighbours.clear()
        clip._nat_migrated.clear()
        clip._busy_until[0] = 0
        clip._prev_active[0] = None
        self.doc = Document()
        self.bg = make(self.doc, "Background")
        self.base = make(self.doc, "Base", above=self.bg)
        self.c1 = make(self.doc, "C1", above=self.base, clipped=True)
        self.c2 = make(self.doc, "C2", above=self.c1, clipped=True)
        self.top = make(self.doc, "Top", above=self.c2)

    def sync(self):
        clip._nat_dirty[0] = True
        clip._busy_until[0] = 0
        return clip.sync(self.doc)


class Structure(Base):
    def test_base_and_clipped(self):
        self.assertTrue(clip.is_clipped(self.c1))
        self.assertTrue(clip.is_clipped(self.c2))
        self.assertIs(clip.native_base(self.c2), self.base)
        self.assertTrue(clip.is_clip_base(self.base))
        self.assertFalse(clip.is_clipped(self.base))
        self.assertFalse(clip.is_clip_base(self.top))
        self.assertEqual([n.name() for n in clip.native_stack(self.c1)], ["Base", "C1", "C2"])

    def test_bottom_layer_cannot_be_clipped(self):
        self.bg.setInheritAlpha(True)
        self.assertFalse(clip.is_clipped(self.bg))

    def test_clip_and_release_like_photoshop(self):
        clip.toggle(self.doc, self.top)                       # clip Top to the stack
        self.assertTrue(clip.is_clipped(self.top))
        self.assertIs(clip.native_base(self.top), self.base)
        clip.toggle(self.doc, self.c1)                        # release C1 -> C1 and above
        self.assertEqual(flags(self.doc.root), [False, False, False, False, False])
        clip.toggle(self.doc, self.base)                      # a base can be clipped too
        self.assertIs(clip.native_base(self.base), self.bg)

    def test_nothing_to_clip_to(self):
        with self.assertRaises(clip.ClipError):
            clip.toggle(self.doc, self.bg)

    def test_clipping_a_group_makes_it_isolated(self):
        g = make(self.doc, "Group", "grouplayer", above=self.top)
        g.setPassThroughMode(True)
        clip.toggle(self.doc, g)
        self.assertTrue(g.inheritAlpha())
        self.assertFalse(g.passThroughMode())

    def test_ctrl_g_takes_the_whole_stack(self):
        wide, changed = clip.widen_to_stacks([self.c1])
        self.assertTrue(changed)
        self.assertEqual([n.name() for n in wide], ["Base", "C1", "C2"])
        wide, changed = clip.widen_to_stacks([self.top])
        self.assertFalse(changed)

    def test_pass_through_allowed_around_stacks(self):
        g = make(self.doc, "Group", "grouplayer", above=self.top)
        b = make(self.doc, "b", parent=g)
        make(self.doc, "c", parent=g, above=b, clipped=True)
        make(self.doc, "Levels", "filterlayer", parent=g)
        self.assertTrue(clip.wants_pass_through(g))


class Migration(unittest.TestCase):
    def setUp(self):
        clip._nat_neighbours.clear()
        clip._nat_migrated.clear()
        clip._busy_until[0] = 0
        self.doc = Document()
        self.bg = make(self.doc, "Background")
        self.g = make(self.doc, "Photo (clip)", "grouplayer", above=self.bg)
        self.g.setBlendingMode("multiply")
        self.g.setOpacity(128)
        self.base = make(self.doc, "Photo", parent=self.g)
        self.c1 = make(self.doc, "Curves 1", "filterlayer", parent=self.g, above=self.base,
                       clipped=True)
        self.c2 = make(self.doc, "Paint", parent=self.g, above=self.c1, clipped=True)
        self.mask = make(self.doc, "Transparency Mask", "transparencymask", parent=self.g)
        self.top = make(self.doc, "Top", above=self.g)

    def test_groups_are_taken_apart(self):
        self.assertEqual(clip.migrate_clip_groups(self.doc), 1)
        self.assertEqual(names(self.doc.root), ["Background", "Photo", "Curves 1", "Paint", "Top"])
        self.assertEqual(flags(self.doc.root), [False, False, True, True, False])
        self.assertEqual(self.base.blendingMode(), "multiply")
        self.assertEqual(self.base.opacity(), 128)
        self.assertEqual([c.name() for c in self.base.childNodes()], ["Transparency Mask"])
        self.assertIs(clip.native_base(self.c2), self.base)
        self.assertEqual(clip.migrate_clip_groups(self.doc), 0)

    def test_hidden_group_hides_the_stack(self):
        self.g.setVisible(False)
        clip.migrate_clip_groups(self.doc)
        self.assertFalse(self.base.visible())

    def test_base_with_its_own_mask_keeps_the_group(self):
        make(self.doc, "Own Mask", "transparencymask", parent=self.base)
        self.assertEqual(clip.migrate_clip_groups(self.doc), 0)
        self.assertIs(self.base.parentNode(), self.g)

    def test_sync_migrates_once_per_document(self):
        self.assertTrue(clip.sync(self.doc))
        self.assertNotIn("Photo (clip)", names(self.doc.root))


class PhotoshopHabits(Base):
    def test_new_layer_inside_a_stack_is_clipped(self):
        self.sync()                                          # first look: baseline
        n = make(self.doc, "New", above=self.c1)             # between C1 and C2
        self.doc.setActiveNode(n)
        self.sync()
        self.assertTrue(n.inheritAlpha())

    def test_new_layer_over_a_selected_clipped_layer_is_clipped(self):
        self.doc.setActiveNode(self.c2)
        self.sync()
        n = make(self.doc, "New", above=self.c2)             # top of the stack
        self.doc.setActiveNode(n)
        self.sync()
        self.assertTrue(n.inheritAlpha())

    def test_new_layer_above_a_plain_layer_stays_unclipped(self):
        self.doc.setActiveNode(self.top)
        self.sync()
        n = make(self.doc, "New", above=self.top)
        self.doc.setActiveNode(n)
        self.sync()
        self.assertFalse(n.inheritAlpha())

    def test_clipped_layer_dragged_out_is_released(self):
        self.sync()
        clip._move(self.c2, self.doc.root, self.top)        # above Top
        self.sync()
        self.assertFalse(self.c2.inheritAlpha())
        self.assertTrue(self.c1.inheritAlpha())

    def test_layer_dragged_into_a_stack_is_clipped(self):
        self.sync()
        clip._move(self.top, self.doc.root, self.base)      # between Base and C1
        self.sync()
        self.assertTrue(self.top.inheritAlpha())

    def test_whole_stack_moved_together_stays_as_it_is(self):
        g = make(self.doc, "Group", "grouplayer", above=self.top)
        self.sync()
        prev = None
        for n in (self.base, self.c1, self.c2):
            clip._move(n, g, prev)
            prev = n
        self.sync()
        self.assertEqual(flags(g), [False, True, True])
        self.assertIs(clip.native_base(self.c2), self.base)

    def test_quiet_without_changes(self):
        self.sync()
        clip._nat_dirty[0] = False
        clip._nat_ticks[0] = 1
        self.assertFalse(clip.sync(self.doc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
