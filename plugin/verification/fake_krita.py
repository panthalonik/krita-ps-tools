"""Minimal stand-in for Krita's Python API, enough to exercise the mask logic
of ps_tools outside Krita. Not a renderer: projections are not computed."""
import itertools
import sys
import types

_ids = itertools.count(1)


class Uid(object):
    def __init__(self, n):
        self.n = n

    def toString(self):
        return "{%08d}" % self.n

    def __eq__(self, other):
        return isinstance(other, Uid) and other.n == self.n

    def __hash__(self):
        return hash(self.n)


class Node(object):
    def __init__(self, doc, name, kind, uid=None):
        self.doc, self._name, self._type = doc, name, kind
        self._uid = uid or Uid(next(_ids))
        self._visible, self._blend, self._parent = True, "normal", None
        self.kids = []
        n = doc.w * doc.h
        if kind in ("transparencymask", "selectionmask", "filterlayer"):
            self.pixels = b"\xff" * n
        else:
            self.pixels = bytes(n * 4)
        if kind == "filterlayer":
            self._blend = "copy"
        self.log = []

    def type(self): return self._type
    def name(self): return self._name
    def setName(self, n): self._name = n
    def visible(self): return self._visible
    def setVisible(self, v): self._visible = bool(v)
    def uniqueId(self): return self._uid
    def blendingMode(self): return self._blend
    def setBlendingMode(self, m): self._blend = m
    def childNodes(self): return list(self.kids)
    def parentNode(self): return self._parent
    def colorModel(self): return "RGBA"
    def colorDepth(self): return "U8"
    def opacity(self): return 255
    def inheritAlpha(self): return False

    def addChildNode(self, child, above):
        child._parent = self
        if above is None:
            self.kids.insert(0, child)
        else:
            self.kids.insert(self.kids.index(above) + 1, child)
        return True

    def remove(self):
        if self._parent is not None:
            self._parent.kids.remove(self)
            self._parent = None

    def pixelData(self, x, y, w, h):
        return self.pixels

    def setPixelData(self, data, x, y, w, h):
        assert len(data) == len(self.pixels), (len(data), len(self.pixels))
        self.pixels = bytes(data)

    def projectionPixelData(self, x, y, w, h):
        # records what the layer looked like when its projection was read
        self.log.append({"selection": self.pixels,
                         "visible_masks": [k.name() for k in self.kids if k.visible()]})
        return bytes(w * h * 4)

    def copy_into(self, doc):
        n = Node(doc, self._name, self._type, self._uid)
        n._visible, n._blend, n.pixels = self._visible, self._blend, self.pixels
        n.log = self.log                      # share, so tests can see reads
        for k in self.kids:
            c = k.copy_into(doc)
            c._parent = n
            n.kids.append(c)
        return n


class Document(object):
    def __init__(self, w=4, h=3):
        self.w, self.h = w, h
        self.root = Node(self, "root", "grouplayer")
        self.refreshes = 0
        self._active = None

    def width(self): return self.w
    def height(self): return self.h
    def colorModel(self): return "RGBA"
    def colorDepth(self): return "U8"
    def rootNode(self): return self.root
    def refreshProjection(self): self.refreshes += 1
    def waitForDone(self): pass
    def activeNode(self): return self._active
    def setActiveNode(self, n): self._active = n
    def selection(self): return None
    def tryBarrierLock(self): return True
    def unlock(self): pass
    def setBatchmode(self, v): pass
    def close(self): pass
    def createTransparencyMask(self, name): return Node(self, name, "transparencymask")
    def createSelectionMask(self, name): return Node(self, name, "selectionmask")
    def createNode(self, name, kind): return Node(self, name, kind)

    def clone(self):
        d = Document(self.w, self.h)
        d.root = self.root.copy_into(d)
        return d


def install():
    m = types.ModuleType("krita")

    class Krita(object):
        @staticmethod
        def instance():
            return Krita
    m.Krita = Krita
    m.Selection = m.InfoObject = m.Extension = m.DockWidget = m.ManagedColor = object
    m.DockWidgetFactory = m.DockWidgetFactoryBase = object
    sys.modules["krita"] = m


# ---- extras used by the clipping / grouping tests
def _extend():
    def inheritAlpha(self): return getattr(self, "_inherit", False)
    def setInheritAlpha(self, v): self._inherit = bool(v)
    def passThroughMode(self): return getattr(self, "_pass", False)
    def setPassThroughMode(self, v): self._pass = bool(v)
    def setCollapsed(self, v): pass
    def colorLabel(self): return 0
    def setColorLabel(self, v): pass
    for f in (inheritAlpha, setInheritAlpha, passThroughMode, setPassThroughMode,
              setCollapsed, colorLabel, setColorLabel):
        setattr(Node, f.__name__, f)


_extend()


def _annotations():
    def annotationTypes(self): return list(getattr(self, "_ann", {}))
    def annotation(self, key): return getattr(self, "_ann", {})[key]
    def setAnnotation(self, key, description, data):
        if not hasattr(self, "_ann"):
            self._ann = {}
        self._ann[key] = bytes(data)
    for f in (annotationTypes, annotation, setAnnotation):
        setattr(Document, f.__name__, f)


_annotations()
