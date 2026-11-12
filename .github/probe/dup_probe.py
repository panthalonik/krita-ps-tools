# -*- coding: utf-8 -*-
"""Why does Duplicate Layer lag on a big, fully painted layer?
Run inside Krita by kritarunner:  kritarunner -s dup_probe -f main <out dir>
Writes <out dir>/probe.json."""
import json
import os
import time
import traceback

from krita import Krita
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

N = int(os.environ.get("PROBE_SIZE", "4096"))
pc = time.perf_counter


def log(*a):
    print("[dup_probe]", *a, flush=True)


def fill(node, seed):
    row = bytearray(N * 4)
    for x in range(N):
        row[x * 4:x * 4 + 4] = bytes(((x * 3 + seed) & 255, (x * 5 + seed) & 255, (x + seed * 7) & 255, 255))
    data = bytes(row) * N
    node.setPixelData(data, 0, 0, N, N)


def make_doc(k):
    doc = k.createDocument(N, N, "dup", "RGBA", "U8", "", 300.0)
    root = doc.rootNode()
    bg = root.childNodes()[0]
    fill(bg, 1)
    lay = doc.createNode("Obraz", "paintlayer")
    root.addChildNode(lay, bg)
    fill(lay, 3)
    doc.waitForDone()
    doc.refreshProjection()
    doc.waitForDone()
    return doc, lay


def engine(k):
    doc, lay = make_doc(k)
    root = doc.rootNode()
    out = []
    for _ in range(3):
        t0 = pc()
        d = lay.duplicate()
        t1 = pc()
        root.addChildNode(d, lay)
        t2 = pc()
        doc.waitForDone()
        t3 = pc()
        out.append({"clone": round(t1 - t0, 3), "add": round(t2 - t1, 3), "render": round(t3 - t2, 3)})
    t0 = pc()
    doc.refreshProjection()
    doc.waitForDone()
    full = round(pc() - t0, 3)
    doc.close()
    return {"duplicate": out, "full_rerender": full}


class Sampler(object):
    """Longest time the GUI thread did not get to run a 5 ms timer."""
    def __init__(self):
        self.timer = QTimer()
        self.timer.setInterval(5)
        self.timer.timeout.connect(self.tick)
        self.last = pc()
        self.gaps = []

    def tick(self):
        now = pc()
        self.gaps.append(now - self.last)
        self.last = now

    def start(self):
        self.gaps = []
        self.last = pc()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        return round(max(self.gaps or [0]), 3)


def pump(seconds):
    end = pc() + seconds
    longest = 0.0
    while pc() < end:
        t = pc()
        QApplication.processEvents()
        longest = max(longest, pc() - t)
        time.sleep(0.002)
    return longest


def gui_duplicate(k, doc, lay, sampler):
    root = doc.rootNode()
    before = len(root.childNodes())
    old = set(n.uniqueId().toString() for n in root.childNodes())
    doc.setActiveNode(lay)
    pump(0.5)
    act = k.action("duplicatelayer")
    sampler.start()
    t0 = pc()
    act.trigger()
    t_trigger = pc() - t0
    longest = 0.0
    added = None
    idle = None
    while pc() - t0 < 30:
        t = pc()
        QApplication.processEvents()
        longest = max(longest, pc() - t)
        if added is None and len(root.childNodes()) > before:
            added = pc() - t0
        if added is not None and doc.tryBarrierLock():
            doc.unlock()
            idle = pc() - t0
            break
        time.sleep(0.002)
    # whatever runs right after (thumbnails, dockers reacting...)
    longest_after = pump(1.5)
    gap = sampler.stop()
    for n in root.childNodes():
        if n.uniqueId().toString() not in old:
            n.remove()
    doc.waitForDone()
    pump(0.5)
    return {"trigger_call": round(t_trigger, 3), "layer_added_after": added and round(added, 3),
            "image_idle_after": idle and round(idle, 3), "longest_event_batch": round(longest, 3),
            "longest_event_batch_later": round(longest_after, 3), "longest_gui_freeze": gap}


def gui(k, res):
    doc, lay = make_doc(k)
    win = k.openWindow()
    win.qwindow().resize(1600, 1000)
    win.qwindow().show()
    view = win.addView(doc)
    pump(3)
    res["dockers"] = [(d.objectName(), d.isVisible()) for d in win.dockers()]
    res["extensions"] = [type(e).__module__ for e in k.extensions()]
    try:
        res["instant_preview"] = bool(k.action("level_of_detail_mode").isChecked())
    except Exception:
        res["instant_preview"] = None
    sampler = Sampler()
    runs = {}
    runs["default"] = [gui_duplicate(k, doc, lay, sampler) for _ in range(3)]

    # Instant Preview toggled
    try:
        k.action("level_of_detail_mode").trigger()
        pump(1)
        runs["instant_preview_%s" % k.action("level_of_detail_mode").isChecked()] = \
            [gui_duplicate(k, doc, lay, sampler) for _ in range(2)]
        k.action("level_of_detail_mode").trigger()
        pump(1)
    except Exception:
        runs["lod_error"] = traceback.format_exc()

    # every docker hidden (layer thumbnails, overview, plugin panels...)
    shown = [d for d in win.dockers() if d.isVisible()]
    for d in shown:
        d.hide()
    pump(1)
    runs["no_dockers"] = [gui_duplicate(k, doc, lay, sampler) for _ in range(2)]
    # then each visible docker alone
    for d in shown:
        d.show()
        pump(1)
        runs["only_" + (d.objectName() or "?")] = [gui_duplicate(k, doc, lay, sampler)]
        d.hide()
        pump(0.5)
    for d in shown:
        d.show()
    res["gui"] = runs
    try:
        doc.setModified(False)
        view.close()
    except Exception:
        pass


def main(args):
    out_dir = args[0] if args else os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    res = {"size": N, "errors": []}
    k = Krita.instance()
    res["version"] = k.version()
    try:
        res["engine_level"] = QApplication.instance().property("ps_tools_engine")
    except Exception:
        pass
    for name, fn in (("engine", lambda: res.__setitem__("engine", engine(k))),
                     ("gui", lambda: gui(k, res))):
        try:
            log("start", name)
            fn()
            log("done", name)
        except Exception:
            res["errors"].append(name + ": " + traceback.format_exc())
        with open(os.path.join(out_dir, "probe.json"), "w") as f:
            json.dump(res, f, indent=1)
    log(json.dumps(res))
