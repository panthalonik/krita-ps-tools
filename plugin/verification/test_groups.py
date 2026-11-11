"""Grouping, clipping stacks and auto refresh decisions, run against
verification/fake_krita. These are not tests of a running Krita."""
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
from fake_krita import Document, Node           # noqa: E402
from ps_tools import autorefresh, clip, core     # noqa: E402

BI_THIS = core.BlendIfSettings("Gray", (10, 20, 200, 255), core.NEUTRAL)
BI_UNDER = core.BlendIfSettings("Gray", core.NEUTRAL, (10, 20, 200, 255))


def make(doc, name, kind, parent=None, above=None):
    n = Node(doc, name, kind)
    parent = parent or doc.root
    if above is None and parent.kids:
        above = parent.kids[-1]                 # on top, like a new layer
    parent.addChildNode(n, above)
    return n


class Base(unittest.TestCase):
    def setUp(self):
        for s in (clip._tracked, clip._known, clip._empty_seen, clip._single_seen):
            s.clear()
        clip._base_modes.clear()
        clip._bases.clear()
        clip._dissolve_tries.clear()
        self.released = []
        if not hasattr(clip, "_real_release"):
            clip._real_release = clip.release
        clip.release = lambda doc, layer: self.released.append(layer.name())
        clip._busy_until[0] = 0.0
        clip._prev_active[0] = None
        self.doc = Document()
        self.bg = make(self.doc, "Background", "paintlayer")

    def clip_stack(self, base_name="Layer 1", parent=None):
        g = make(self.doc, base_name + clip.CLIP_SUFFIX, "grouplayer", parent)
        base = make(self.doc, base_name, "paintlayer", g)
        adj = make(self.doc, "Levels 1", "filterlayer", g)
        adj.setInheritAlpha(True)
        clip._tracked.add(clip._key(adj))
        return g, base, adj


class Widen(Base):
    def test_members_of_a_stack_become_the_stack(self):
        g, base, adj = self.clip_stack()
        other = make(self.doc, "Curves 1", "filterlayer")
        out, changed = clip.widen_to_stacks([base, adj, other])
        self.assertTrue(changed)
        self.assertEqual([n.name() for n in out], [g.name(), "Curves 1"])

    def test_plain_selection_is_untouched(self):
        a = make(self.doc, "A", "paintlayer")
        b = make(self.doc, "B", "filterlayer")
        out, changed = clip.widen_to_stacks([a, b])
        self.assertFalse(changed)
        self.assertEqual(out, [a, b])

    def test_stack_inside_users_own_group_is_untouched(self):
        g = make(self.doc, "Group 1", "grouplayer")
        base = make(self.doc, "L", "paintlayer", g)
        c = make(self.doc, "C", "paintlayer", g)
        c.setInheritAlpha(True)
        self.assertEqual(clip.widen_to_stacks([base, c]), ([base, c], False))


class PassThrough(Base):
    def group(self, *kinds):
        g = make(self.doc, "Group 1", "grouplayer")
        return g, [make(self.doc, "%s %d" % (k, i), k, g) for i, k in enumerate(kinds)]

    def test_new_group_with_adjustment_layers(self):
        clip.sync(self.doc)                               # baseline
        g, _ = self.group("filterlayer", "filterlayer")
        self.assertTrue(clip.sync(self.doc))
        self.assertTrue(g.passThroughMode())

    def test_user_can_switch_it_off_again(self):
        clip.sync(self.doc)
        g, _ = self.group("filterlayer")
        clip.sync(self.doc)
        g.setPassThroughMode(False)
        for _ in range(5):
            clip.sync(self.doc)
        self.assertFalse(g.passThroughMode())

    def test_existing_groups_are_not_changed(self):
        g, _ = self.group("filterlayer")                  # there before the plugin looked
        for _ in range(3):
            clip.sync(self.doc)
        self.assertFalse(g.passThroughMode())

    def test_not_for_plain_groups_masks_or_stacks(self):
        clip.sync(self.doc)
        g1, _ = self.group("paintlayer", "paintlayer")
        g2, _ = self.group("filterlayer")
        make(self.doc, "Transparency Mask", "transparencymask", g2)
        g3, kids = self.group("paintlayer", "filterlayer")
        kids[1].setInheritAlpha(True)
        g4, _, _ = self.clip_stack("Layer 9")
        clip.sync(self.doc)
        self.assertEqual([g.passThroughMode() for g in (g1, g2, g3, g4)], [False] * 4)

    def test_stack_nested_in_new_group_stays_clipped(self):
        clip.sync(self.doc)
        outer = make(self.doc, "Group 1", "grouplayer")
        g, base, adj = self.clip_stack(parent=outer)
        free = make(self.doc, "Curves 1", "filterlayer", outer)
        for _ in range(3):
            clip.sync(self.doc)
        self.assertTrue(outer.passThroughMode())          # free adjustment inside
        self.assertFalse(g.passThroughMode())
        self.assertTrue(adj.inheritAlpha())
        self.assertFalse(base.inheritAlpha())
        self.assertFalse(free.inheritAlpha())


class EmptyClipGroup(Base):
    def test_removed_on_second_look_only(self):
        g, base, adj = self.clip_stack()
        clip.sync(self.doc)
        base.remove()
        adj.remove()
        self.doc.root.addChildNode(base, self.bg)
        self.doc.root.addChildNode(adj, base)
        clip.sync(self.doc)
        self.assertIn(g, self.doc.root.kids)              # might be mid-operation
        clip.sync(self.doc)
        self.assertNotIn(g, self.doc.root.kids)

    def test_refilled_group_survives(self):
        g = make(self.doc, "X" + clip.CLIP_SUFFIX, "grouplayer")
        clip.sync(self.doc)
        make(self.doc, "X", "paintlayer", g)
        clip.sync(self.doc)
        clip.sync(self.doc)
        self.assertIn(g, self.doc.root.kids)

    def test_users_empty_group_is_kept(self):
        g = make(self.doc, "Group 1", "grouplayer")
        for _ in range(3):
            clip.sync(self.doc)
        self.assertIn(g, self.doc.root.kids)


class ReleaseGroup(Base):
    """Krita's Quick Ungroup dissolves the ACTIVE node when it is a group."""

    def setUp(self):
        Base.setUp(self)
        self.calls = []
        clip._select = lambda doc, nodes, cur: self.calls.append(
            ("select", [n.name() for n in nodes], cur.name())) or True
        clip._run = lambda name: self.calls.append(("run", name)) or True
        clip._after = lambda fn, check, tries=30: None
        clip._release_fallback = lambda doc, layer: self.calls.append(("fallback", layer.name()))

    def stack(self, base_kind="paintlayer", clipped_kind="grouplayer", extra=None):
        g = make(self.doc, "Layer 1" + clip.CLIP_SUFFIX, "grouplayer")
        base = make(self.doc, "Layer 1", base_kind, g)
        c = make(self.doc, "Group 1", clipped_kind, g)
        c.setInheritAlpha(True)
        make(self.doc, "Inside A", "paintlayer", c)
        make(self.doc, "Inside B", "filterlayer", c)
        if extra:
            make(self.doc, "Top", extra, g).setInheritAlpha(True)
        return g, base, c

    def test_clipped_group_is_never_the_active_node(self):
        g, base, grp = self.stack()
        clip._real_release(self.doc, grp)
        sel = [c for c in self.calls if c[0] == "select"][0]
        self.assertEqual(sel[1], ["Layer 1", "Group 1"])      # whole stack: group dissolves
        self.assertEqual(sel[2], "Layer 1")                    # active: the base, not the group
        self.assertIn(("run", "quick_ungroup"), self.calls)
        self.assertFalse(grp.inheritAlpha())
        self.assertEqual([k.name() for k in grp.kids], ["Inside A", "Inside B"])

    def test_plain_layer_stays_the_active_node(self):
        g, base, c = self.stack(clipped_kind="paintlayer")
        clip._real_release(self.doc, c)
        sel = [x for x in self.calls if x[0] == "select"][0]
        self.assertEqual(sel[2], "Group 1")

    def test_group_released_from_the_middle_uses_a_layer_above(self):
        g, base, grp = self.stack(extra="paintlayer")
        mid = make(self.doc, "Mid", "paintlayer", g, above=base)
        mid.setInheritAlpha(True)                              # base, Mid, Group 1, Top
        clip._real_release(self.doc, grp)
        sel = [x for x in self.calls if x[0] == "select"][0]
        self.assertEqual(sel[1], ["Group 1", "Top"])
        self.assertEqual(sel[2], "Top")

    def test_only_groups_to_move_falls_back_to_scripting(self):
        g, base, grp = self.stack(base_kind="grouplayer")
        clip._real_release(self.doc, grp)
        self.assertEqual([c for c in self.calls if c[0] in ("run", "select")], [])
        self.assertIn(("fallback", "Group 1"), self.calls)


class LostBase(Base):
    """A stack without its base is released (the group dissolved)."""

    def sync(self, times=1):
        for _ in range(times):
            clip._busy_until[0] = 0.0
            clip.sync(self.doc)

    def test_intact_stack_is_left_alone(self):
        self.clip_stack()
        self.sync(6)
        self.assertEqual(self.released, [])

    def test_base_deleted(self):
        g, base, adj = self.clip_stack()
        self.sync(2)
        base.remove()
        self.sync()
        self.assertEqual(self.released, ["Levels 1"])

    def test_base_moved_out_others_stay(self):
        g, base, adj = self.clip_stack()
        adj2 = make(self.doc, "Curves 1", "filterlayer", g)
        adj2.setInheritAlpha(True)
        self.sync(2)
        base.remove()
        self.doc.root.addChildNode(base, g)
        self.sync()
        self.assertEqual(self.released, ["Levels 1"])      # release() takes the whole rest

    def test_reordering_inside_the_stack_is_not_a_lost_base(self):
        g, base, adj = self.clip_stack()
        make(self.doc, "Curves 1", "filterlayer", g).setInheritAlpha(True)
        self.sync(2)
        g.kids.remove(adj)
        g.kids.insert(0, adj)                              # dragged below the base
        self.sync(3)
        self.assertEqual(self.released, [])
        self.assertFalse(adj.inheritAlpha())               # it is the base now
        self.assertTrue(base.inheritAlpha())

    def test_single_layer_left_needs_two_looks(self):
        g, base, adj = self.clip_stack()
        self.sync(2)
        adj.remove()
        self.sync()
        self.assertEqual(self.released, [])
        self.sync()
        self.assertEqual(self.released, ["Layer 1"])

    def test_gives_up_after_two_attempts(self):
        g, base, adj = self.clip_stack()
        self.sync(2)
        adj.remove()
        self.sync(10)                                      # release() is a stub: nothing changes
        self.assertEqual(len(self.released), 2)

    def test_stack_opened_from_file_with_one_adjustment_only(self):
        g = make(self.doc, "Gone" + clip.CLIP_SUFFIX, "grouplayer")
        make(self.doc, "Levels 1", "filterlayer", g)
        self.sync(2)
        self.assertEqual(self.released, ["Levels 1"])

    def test_whole_stack_moved_into_a_group_survives(self):
        g, base, adj = self.clip_stack()
        self.sync(2)
        outer = make(self.doc, "Group 1", "grouplayer")
        g.remove()
        outer.addChildNode(g, None)
        self.sync(4)
        self.assertEqual(self.released, [])
        self.assertTrue(adj.inheritAlpha())


class Touches(Base):
    """Which dependent layers a finished stroke on the active node refreshes."""

    def setUp(self):
        Base.setUp(self)
        self.paint = make(self.doc, "Paint", "paintlayer")
        self.pmask = make(self.doc, "Transparency Mask", "transparencymask", self.paint)
        self.adj = make(self.doc, "Levels 1", "filterlayer")
        self.amask = make(self.doc, "Transparency Mask", "transparencymask", self.adj)
        self.top = make(self.doc, "Top", "paintlayer")
        autorefresh._thumb_hash = lambda node: 0
        self.sig, self.start = autorefresh.scan(self.doc)
        self.index = {k: i for i, (k, _s) in enumerate(self.sig)}

    def t(self, layer, dep, active):
        apos = None if active is None else self.index[core.node_key(active)]
        return autorefresh.touches(self.sig, self.start, self.index[core.node_key(layer)],
                                   layer, dep, apos)

    def test_underlying_layer_follows_painting_below(self):
        dep = ("blendif", BI_UNDER)
        self.assertTrue(self.t(self.top, dep, self.bg))
        self.assertTrue(self.t(self.top, dep, self.pmask))
        self.assertTrue(self.t(self.adj, dep, self.bg))
        self.assertFalse(self.t(self.paint, dep, self.top))      # painted above it
        self.assertFalse(self.t(self.paint, dep, self.paint))

    def test_this_layer_follows_own_pixels_not_own_mask(self):
        dep = ("blendif", BI_THIS)
        self.assertTrue(self.t(self.paint, dep, self.paint))
        self.assertFalse(self.t(self.paint, dep, self.pmask))
        self.assertFalse(self.t(self.paint, dep, self.bg))

    def test_adjustment_layer_always_sees_what_is_below(self):
        dep = ("blendif", BI_THIS)
        self.assertTrue(self.t(self.adj, dep, self.bg))
        self.assertTrue(self.t(self.adj, dep, self.paint))
        self.assertFalse(self.t(self.adj, dep, self.amask))
        self.assertFalse(self.t(self.adj, dep, self.top))

    def test_computed_layer(self):
        dep = ("baked", ("selective_color", {}))
        self.assertTrue(self.t(self.top, dep, self.bg))
        self.assertFalse(self.t(self.top, dep, self.top))

    def test_unknown_active_node_refreshes(self):
        self.assertTrue(self.t(self.paint, ("blendif", BI_THIS), None))

    def test_blend_if_mask_maps_to_its_layer(self):
        bi = make(self.doc, BI_UNDER.to_name(), "transparencymask", self.bg)
        self.doc.setActiveNode(bi)                 # not part of the signature
        self.assertEqual(autorefresh._active_pos(self.doc, self.sig),
                         self.index[core.node_key(self.bg)])


if __name__ == "__main__":
    unittest.main(verbosity=1)
