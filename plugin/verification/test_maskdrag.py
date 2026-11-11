"""Mask drag & drop guard in the Layers docker (pslayers.MaskClickFilter),
with real Qt widgets and verification/fake_krita for the nodes.
Not a test of a running Krita."""
import os
import pathlib
import sys
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verification"))
import fake_krita
fake_krita.install()
_pkg = types.ModuleType("ps_tools")
_pkg.__path__ = [str(ROOT / "ps_tools")]
sys.modules["ps_tools"] = _pkg
from fake_krita import Document, Node                          # noqa: E402
from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt            # noqa: E402
from PyQt5.QtGui import QMouseEvent, QStandardItem, QStandardItemModel  # noqa: E402
from PyQt5.QtWidgets import QApplication, QTreeView             # noqa: E402
from ps_tools import clip, core, pslayers                       # noqa: E402

app = QApplication.instance() or QApplication([])


def make(doc, name, kind, parent=None):
    n = Node(doc, name, kind)
    parent = parent or doc.root
    parent.addChildNode(n, parent.kids[-1] if parent.kids else None)
    return n


class FakeEvent(object):
    def __init__(self, pos, source):
        self._pos, self._source, self.ignored = QPoint(*pos), source, False

    def pos(self): return self._pos
    def source(self): return self._source
    def ignore(self): self.ignored = True


class Base(unittest.TestCase):
    def setUp(self):
        from krita import Krita
        self.doc = Document(4, 2)
        d = self.doc
        self.bg = make(d, "Background", "paintlayer")
        self.paint = make(d, "Paint", "paintlayer")
        self.pmask = make(d, "Transparency Mask", "transparencymask", self.paint)
        self.group = make(d, "Group 2", "grouplayer")
        self.adj = make(d, "Levels 1", "filterlayer", self.group)
        self.selected = [self.pmask]
        Krita.activeDocument = staticmethod(lambda: d)
        sel = self.selected

        class _View(object):
            def selectedNodes(self_): return list(sel)

        class _Win(object):
            def activeView(self_): return _View()
        Krita.activeWindow = staticmethod(lambda: _Win())

        # a model shaped like Krita's (rows top first, masks as child rows)
        self.model = QStandardItemModel()

        def fill(parent_item, node):
            for c in reversed(node.childNodes()):
                it = QStandardItem(c.name())
                parent_item.appendRow(it)
                fill(it, c)
        fill(self.model.invisibleRootItem(), d.root)
        self.view = QTreeView()
        self.view.setModel(self.model)
        self.view.expandAll()
        self.view.resize(300, 400)
        self.view.show()
        app.processEvents()

        hidden = set()
        for r in range(self.model.rowCount()):           # fold the mask row of "Paint"
            idx = self.model.index(r, 0)
            if idx.data() == "Paint":
                hidden.add(self.model.index(0, 0, idx))

        class _Info(object):
            def __init__(self, hide): self.hide = hide

        class _Styler(object):
            pass
        st = _Styler()
        st.view, st.active = self.view, True
        st.info_for = lambda idx: _Info(any(idx == h for h in hidden))
        st.error = lambda text: self.fail(text)
        self.f = pslayers.MaskClickFilter(st)

    def row_pos(self, name, where="mid"):
        it = self.model.findItems(name, Qt.MatchRecursive)[0]
        r = self.view.visualRect(it.index())
        y = {"mid": r.center().y(), "top": r.top() + 1, "bottom": r.bottom() - 1}[where]
        return (r.left() + 40, y)

    def drop(self, pos, source=None):
        src = self.view if source is None else source
        enter = FakeEvent(pos, src)
        self.f._drag_event(QEvent.DragEnter, enter)
        move = FakeEvent(pos, src)
        eaten = self.f._drag_event(QEvent.DragMove, move)
        return not (eaten and move.ignored)


class ClickDoesNotDrag(Base):
    def test_move_after_our_press_is_eaten(self):
        self.f._eat_release = True                       # we handled the press
        move = QMouseEvent(QEvent.MouseMove, QPointF(50, 10), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        self.assertTrue(self.f.eventFilter(self.view.viewport(), move))
        hover = QMouseEvent(QEvent.MouseMove, QPointF(50, 10), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        self.assertFalse(self.f.eventFilter(self.view.viewport(), hover))
        rel = QMouseEvent(QEvent.MouseButtonRelease, QPointF(50, 10), Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        self.assertTrue(self.f.eventFilter(self.view.viewport(), rel))
        self.assertFalse(self.f.eventFilter(self.view.viewport(), move))   # normal drags work again


class MaskDrop(Base):
    def test_onto_another_layer_is_allowed(self):
        self.assertTrue(self.drop(self.row_pos("Background")))
        self.assertTrue(self.drop(self.row_pos("Levels 1")))
        self.assertTrue(self.drop(self.row_pos("Group 2")))

    def test_between_rows_is_refused(self):
        self.assertFalse(self.drop(self.row_pos("Background", "top")))
        self.assertFalse(self.drop(self.row_pos("Levels 1", "bottom")))
        self.assertFalse(self.drop((40, 390)))           # empty area below the rows

    def test_onto_a_layer_that_has_a_mask_is_refused(self):
        make(self.doc, "Transparency Mask", "transparencymask", self.bg)
        self.assertFalse(self.drop(self.row_pos("Background")))

    def test_onto_its_own_layer_is_allowed(self):
        self.assertTrue(self.drop(self.row_pos("Paint")))

    def test_onto_a_hidden_or_mask_row_is_refused(self):
        it = self.model.findItems("Transparency Mask", Qt.MatchRecursive)[0]
        self.view.setRowHidden(0, it.index().parent(), False)
        r = self.view.visualRect(it.index())
        self.assertFalse(self.drop((r.left() + 40, r.center().y())))

    def test_layers_are_not_restricted(self):
        self.selected[:] = [self.paint]
        self.assertTrue(self.drop(self.row_pos("Background", "top")))

    def test_drags_from_elsewhere_are_not_restricted(self):
        self.assertTrue(self.drop(self.row_pos("Background", "top"), source=object()))

    def test_refused_drop_is_ignored(self):
        pos = self.row_pos("Background", "top")
        self.f._drag_event(QEvent.DragEnter, FakeEvent(pos, self.view))
        ev = FakeEvent(pos, self.view)
        self.assertTrue(self.f._drag_event(QEvent.Drop, ev))
        self.assertTrue(ev.ignored)

    def test_off_when_ps_look_is_off(self):
        self.f.s.active = False
        self.assertTrue(self.drop(self.row_pos("Background", "top")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
