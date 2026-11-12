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


def fill(node, seed, depth=8):
    if depth == 8:
        row = bytearray(N * 4)
        for x in range(N):
            row[x * 4:x * 4 + 4] = bytes(((x * 3 + seed) & 255, (x * 5 + seed) & 255, (x + seed * 7) & 255, 255))
    else:
        row = bytearray(N * 8)
        for x in range(N):
            v = (((x * 3 + seed) & 255) * 257, ((x * 5 + seed) & 255) * 257, ((x + seed * 7) & 255) * 257, 65535)
            row[x * 8:x * 8 + 8] = b"".join(c.to_bytes(2, "little") for c in v)
    node.setPixelData(bytes(row) * N, 0, 0, N, N)


def filter_layer(k, doc, name, fid, props):
    from krita import Selection
    f = k.filter(fid)
    cfg = f.configuration()
    for key, v in props.items():
        cfg.setProperty(key, v)
    f.setConfiguration(cfg)
    sel = Selection()
    sel.select(0, 0, N, N, 255)
    return doc.createFilterLayer(name, f, sel)


CAMERA_RAW = {"exposure": 0.3, "contrast": 10, "highlights": -30, "shadows": 25, "clarity": 30,
              "texture": 20, "dehaze": 15, "vibrance": 15, "sharp_amount": 40, "nr_lum": 20}


def build(k, scenario):
    if scenario.startswith("png"):
        k.setBatchmode(True)                 # no import dialog, whatever the file
        try:
            doc = k.openDocument(os.environ["PROBE_PNG"])
        finally:
            k.setBatchmode(False)
        doc.waitForDone()
        if scenario.endswith("to_rgba16"):
            doc.setColorSpace("RGBA", "U16", "sRGB-elle-V2-srgbtrc.icc")
        elif scenario.endswith("to_rgba8"):
            doc.setColorSpace("RGBA", "U8", "sRGB-elle-V2-srgbtrc.icc")
        doc.waitForDone()
        RES.setdefault("png_color", {})[scenario] = (doc.colorModel(), doc.colorDepth(), doc.colorProfile())
        lay = [n for n in doc.rootNode().childNodes() if n.type() == "paintlayer"][-1]
        return doc, lay
    depth = 16 if "u16" in scenario else 8
    doc = k.createDocument(N, N, scenario, "RGBA", "U16" if depth == 16 else "U8", "", 300.0)
    root = doc.rootNode()
    bg = root.childNodes()[0]
    fill(bg, 1, depth)
    parent = root
    if "group" in scenario:
        g = doc.createGroupLayer("Grupa")
        root.addChildNode(g, bg)
        parent = g
        if "blendif" in scenario:
            g.addChildNode(doc.createTransparencyMask("Blend If (Gray | 20/60 255/255 | 0/0 255/255)"), None)
    lay = doc.createNode("Obraz", "paintlayer")
    parent.addChildNode(lay, None if parent is not root else bg)
    fill(lay, 3, depth)
    above = lay
    if "layers10" in scenario:
        for i in range(8):
            n = doc.createNode("L%d" % i, "paintlayer")
            parent.addChildNode(n, above)
            fill(n, 10 + i, depth)
            n.setOpacity(128)
            above = n
    if "adjust" in scenario:
        for name, fid, props in (("Curves", "perchannel", {"curve0": "0,0;0.5,0.6;1,1;"}),
                                 ("Hue/Saturation", "hsvadjustment", {"s": 20}),
                                 ("Levels", "levels", {})):
            n = filter_layer(k, doc, name, fid, props)
            parent.addChildNode(n, above)
            above = n
    if "cameraraw" in scenario:
        from krita import Selection
        f = k.filter("pscameraraw")
        cfg = f.configuration()
        for key, v in CAMERA_RAW.items():
            cfg.setProperty(key, float(v))
        f.setConfiguration(cfg)
        sel = Selection()
        sel.select(0, 0, N, N, 255)
        lay.addChildNode(doc.createFilterMask("Camera Raw Filter", f, sel), None)
    doc.waitForDone()
    return doc, lay


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
        root = lay.parentNode()
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
        t_end = pc() + 30
        while pc() < t_end and self.watch.get("idle") is None:
            yield 0.1
        yield 1.0
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
        RES["canvas"] = {key: k.readSetting("", key, "?") for key in
                         ("canvasState", "OpenGLRenderer", "useOpenGL", "levelOfDetailEnabled")}
        win = k.activeWindow()
        win.qwindow().showMaximized()
        if os.environ.get("PROBE_OPEN"):
            yield from self.open_test(k, win)
            if not os.environ.get("PROBE_SCENARIOS"):
                return
        scenarios = os.environ.get("PROBE_SCENARIOS", "u8_plain,u16_plain,u8_layers10,u8_adjust,"
                                   "u8_group_blendif,u8_cameraraw,u8_layers10_adjust_group").split(",")
        if "pscameraraw" not in k.filters() and "u8_cameraraw" in scenarios:
            scenarios.remove("u8_cameraraw")
        for sc in scenarios:
            try:
                t = pc()
                doc, lay = build(k, sc)
                view = win.addView(doc)
                k.setActiveDocument(doc)
                self.doc, self.lay = doc, lay
                yield 4.0
                RES.setdefault("build", {})[sc] = round(pc() - t, 1)
                RES["dockers"] = [d.objectName() for d in win.dockers() if d.isVisible()]
                for i in range(2):
                    yield from self.duplicate(sc, profile=(i == 0 and sc == "u8_plain"))
                if sc == os.environ.get("PROBE_SWEEP"):
                    # every docker Krita has, one at a time (the user's
                    # workspace may show some that the default one hides)
                    allD = list(win.dockers())
                    was = [d.isVisible() for d in allD]
                    for d in allD:
                        d.hide()
                    yield 1.0
                    yield from self.duplicate("sweep_none")
                    for d in allD:
                        d.show()
                        d.raise_()
                        yield 1.5
                        yield from self.duplicate("sweep_" + (d.objectName() or d.windowTitle()))
                        d.hide()
                        yield 0.5
                    for d in allD:
                        d.show()
                    yield 2.0
                    yield from self.duplicate("sweep_all")
                    for d, v in zip(allD, was):
                        d.setVisible(v)
                doc.setModified(False)
                doc.close()                  # closes its view too
                yield 1.0
            except Exception:
                RES["errors"].append(sc + ": " + traceback.format_exc())
                save()


def photo(seed):
    import random
    rnd = random.Random(seed)
    data = bytearray(rnd.randbytes(N * N * 4))
    data[3::4] = b"\xff" * (N * N)
    return bytes(data)


def big_document(k, path_kra, path_psd):
    """A photobash-like 4096x4096 document: photos, partly painted layers,
    a group, adjustment layers, clipping, Blend If."""
    from krita import InfoObject
    doc = k.createDocument(N, N, "big", "RGBA", "U8", "", 300.0)
    root = doc.rootNode()
    fill(root.childNodes()[0], 1)
    above = root.childNodes()[0]
    for i in range(3):
        n = doc.createNode("Photo %d" % i, "paintlayer")
        root.addChildNode(n, above)
        n.setPixelData(photo(i), 0, 0, N, N)
        above = n
    g = doc.createGroupLayer("Group")
    root.addChildNode(g, above)
    for i in range(4):
        n = doc.createNode("Painted %d" % i, "paintlayer")
        g.addChildNode(n, None)
        h = N // 4
        part = bytearray(N * h * 4)
        part[i * 4::16] = b"\x80" * (N * h // 4)
        part[3::4] = b"\xff" * (N * h)
        n.setPixelData(bytes(part), 0, i * h, N, h)
        if i == 1:
            n.setInheritAlpha(True)
        if i == 2:
            n.addChildNode(doc.createTransparencyMask("Blend If (Gray | 20/60 255/255 | 0/0 255/255)"), None)
    for name, fid, props in (("Curves", "perchannel", {"curve0": "0,0;0.5,0.6;1,1;"}),
                             ("Hue/Saturation", "hsvadjustment", {"s": 20})):
        root.addChildNode(filter_layer(k, doc, name, fid, props), g)
    doc.waitForDone()
    k.setBatchmode(True)
    try:
        doc.setBatchmode(True)
        doc.saveAs(path_kra)
        doc.exportImage(path_psd, InfoObject())
    finally:
        k.setBatchmode(False)
    doc.setModified(False)
    doc.close()


class OpenMixin(object):
    def open_test(self, k, win):
        base = os.path.dirname(OUT)
        kra, psd = os.path.join(base, "big.kra"), os.path.join(base, "big.psd")
        t = pc()
        modes = os.environ.get("PROBE_OPEN", "").split(",")
        files = []
        if "png" in modes:
            from krita import InfoObject
            rgb8 = os.path.join(base, "photo8.png")
            d = k.createDocument(N, N, "photo", "RGBA", "U8", "", 300.0)
            n = d.rootNode().childNodes()[0]
            n.setPixelData(photo(5), 0, 0, N, N)
            d.waitForDone()
            k.setBatchmode(True)
            try:
                d.setBatchmode(True)
                info = InfoObject()
                info.setProperty("alpha", False)
                d.exportImage(rgb8, info)
            finally:
                k.setBatchmode(False)
            d.setModified(False)
            d.close()
            files += [("png_rgb8", rgb8), ("png_grey16", os.environ["PROBE_PNG"]), ("png_rgb8_again", rgb8)]
        if "big" in modes:
            big_document(k, kra, psd)
            files += [("kra", kra), ("psd", psd), ("kra_again", kra)]
        RES["make_files"] = round(pc() - t, 1)
        RES["file_mb"] = {p: round(os.path.getsize(p) / 1e6, 1) for _l, p in files if os.path.exists(p)}
        yield 3.0
        for label, path in files:
            if not os.path.exists(path):
                continue
            prof = cProfile.Profile()
            self.gaps = []
            self.last = pc()
            self.timer.start()
            t0 = pc()
            k.setBatchmode(True)
            try:
                doc = k.openDocument(path)
            finally:
                k.setBatchmode(False)
            t_load = pc() - t0
            win.addView(doc)
            t_view = pc() - t0
            prof.enable()
            idle = None
            while pc() - t0 < 120:
                yield 0.1
                if doc.tryBarrierLock():
                    doc.unlock()
                    idle = pc() - t0
                    break
            yield 4.0                    # what runs after (thumbnails, plugin...)
            prof.disable()
            self.timer.stop()
            gaps = sorted(self.gaps, reverse=True)
            st = io.StringIO()
            pstats.Stats(prof, stream=st).sort_stats("cumulative").print_stats(25)
            RES["runs"][label] = {
                "load_call": round(t_load, 2), "view_added": round(t_view, 2),
                "image_idle": idle and round(idle, 2),
                "longest_gui_freezes": [round(g, 3) for g in gaps[:5]],
                "frozen_total": round(sum(g for g in self.gaps if g > 0.05), 2),
                "layers": len(doc.topLevelNodes()),
                "python_profile": st.getvalue().splitlines()[:45]}
            save()
            doc.setModified(False)
            doc.close()
            yield 2.0


Probe.open_test = OpenMixin.open_test


class DupProbe(Extension):
    def setup(self):
        self.probe = Probe()
        Krita.instance().notifier().windowCreated.connect(
            lambda: QTimer.singleShot(8000, self.probe.go))
        QTimer.singleShot(1200000, lambda: (RES["errors"].append("timeout"), save(), os._exit(1)))

    def createActions(self, window):
        pass


Krita.instance().addExtension(DupProbe(Krita.instance()))
