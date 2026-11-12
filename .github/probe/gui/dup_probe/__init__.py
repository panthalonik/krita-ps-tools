# -*- coding: utf-8 -*-
"""CI only: once the Krita window is up, times Duplicate Layer on a big fully
painted layer (GUI freezes, Python time) and quits. Results: $PROBE_OUT."""
import cProfile
import io
import json
import os
import pstats
import time
import traceback

from krita import Extension, Krita
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

N = int(os.environ.get("PROBE_SIZE", "4096"))
OUT = os.environ.get("PROBE_OUT", os.path.expanduser("~/probe.json"))
pc = time.perf_counter
RES = {"size": N, "errors": [], "runs": {}}


def save():
    with open(OUT, "w") as f:
        json.dump(RES, f, indent=1)


def fill(node, seed):
    row = bytearray(N * 4)
    for x in range(N):
        row[x * 4:x * 4 + 4] = bytes(((x * 3 + seed) & 255, (x * 5 + seed) & 255, (x + seed * 7) & 255, 255))
    node.setPixelData(bytes(row) * N, 0, 0, N, N)


class Probe(object):
    def __init__(self):
        self.steps = self.script()
        self.timer = QTimer()
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.tick)
        self.gaps = []
        self.last = pc()
        self.watch = None

    # ------------------------------------------------ driver: generator of waits
    def go(self, *_):
        try:
            wait = next(self.steps)
        except StopIteration:
            return self.finish()
        except Exception:
            RES["errors"].append(traceback.format_exc())
            return self.finish()
        QTimer.singleShot(int(wait * 1000), self.go)

    def finish(self):
        save()
        os._exit(0)

    def tick(self):
        now = pc()
        self.gaps.append(now - self.last)
        self.last = now
        w = self.watch
        if w is not None:
            if w.get("added") is None and len(w["root"].childNodes()) > w["before"]:
                w["added"] = now - w["t0"]
            if w.get("added") is not None and w.get("idle") is None:
                if w["doc"].tryBarrierLock():
                    w["doc"].unlock()
                    w["idle"] = now - w["t0"]

    # ------------------------------------------------ one measured duplicate
    def duplicate(self, label, profile=False):
        k, doc, lay = self.k, self.doc, self.lay
        root = doc.rootNode()
        old = set(n.uniqueId().toString() for n in root.childNodes())
        doc.setActiveNode(lay)
        yield 0.7
        prof = cProfile.Profile() if profile else None
        self.gaps = []
        self.last = pc()
        self.timer.start()
        self.watch = {"root": root, "doc": doc, "before": len(old), "t0": pc()}
        if prof:
            prof.enable()
        t = pc()
        k.action("duplicatelayer").trigger()
        trig = pc() - t
        yield 3.0
        if prof:
            prof.disable()
        self.timer.stop()
        w, self.watch = self.watch, None
        gaps = sorted(self.gaps, reverse=True)
        r = {"trigger_call": round(trig, 3),
             "layer_added_after": w.get("added") and round(w["added"], 3),
             "image_idle_after": w.get("idle") and round(w["idle"], 3),
             "longest_gui_freezes": [round(g, 3) for g in gaps[:5]],
             "frozen_total": round(sum(g - 0.005 for g in self.gaps if g > 0.05), 3)}
        if prof:
            s = io.StringIO()
            pstats.Stats(prof, stream=s).sort_stats("cumulative").print_stats(30)
            r["python_profile"] = s.getvalue().splitlines()[:70]
        RES["runs"].setdefault(label, []).append(r)
        save()
        for n in root.childNodes():
            if n.uniqueId().toString() not in old:
                n.remove()
        yield 1.0

    # ------------------------------------------------ the whole test
    def script(self):
        self.k = k = Krita.instance()
        RES["version"] = k.version()
        RES["engine_level"] = QApplication.instance().property("ps_tools_engine")
        RES["extensions"] = [type(e).__module__ for e in k.extensions()]
        win = k.activeWindow()
        win.qwindow().showMaximized()
        doc = k.createDocument(N, N, "dup", "RGBA", "U8", "", 300.0)
        root = doc.rootNode()
        fill(root.childNodes()[0], 1)
        lay = doc.createNode("Obraz", "paintlayer")
        root.addChildNode(lay, root.childNodes()[0])
        fill(lay, 3)
        doc.waitForDone()
        win.addView(doc)
        k.setActiveDocument(doc)
        self.doc, self.lay = doc, lay
        yield 5.0
        RES["dockers"] = [d.objectName() for d in win.dockers() if d.isVisible()]
        try:
            RES["instant_preview"] = k.action("level_of_detail_mode").isChecked()
        except Exception:
            pass
        for i in range(3):
            yield from self.duplicate("default", profile=(i == 0))
        try:
            k.action("level_of_detail_mode").trigger()
            yield 1.0
            lbl = "instant_preview_%s" % k.action("level_of_detail_mode").isChecked()
            for _ in range(2):
                yield from self.duplicate(lbl)
            k.action("level_of_detail_mode").trigger()
            yield 1.0
        except Exception:
            RES["errors"].append(traceback.format_exc())
        shown = [d for d in win.dockers() if d.isVisible()]
        for d in shown:
            d.hide()
        yield 1.0
        for _ in range(2):
            yield from self.duplicate("no_dockers")
        for d in shown:
            d.show()
            yield 1.0
            yield from self.duplicate("only_" + (d.objectName() or "?"))
            d.hide()
            yield 0.5
        for d in shown:
            d.show()
        doc.setModified(False)


class DupProbe(Extension):
    def setup(self):
        self.probe = Probe()
        Krita.instance().notifier().windowCreated.connect(
            lambda: QTimer.singleShot(8000, self.probe.go))
        QTimer.singleShot(600000, lambda: (RES["errors"].append("timeout"), save(), os._exit(1)))

    def createActions(self, window):
        pass


Krita.instance().addExtension(DupProbe(Krita.instance()))
