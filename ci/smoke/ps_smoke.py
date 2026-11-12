# -*- coding: utf-8 -*-
"""Smoke test of a Krita PS build, run INSIDE Krita by kritarunner:

    kritarunner -s ps_smoke -f main <output folder>

Builds a small document with the Photoshop features of this repository
(Blend If, clipping, adjustment layers), checks a few rendered pixels and
exports it as smoke.psd + smoke.kra. ci/psd_inspect.py then checks the PSD.
Results go to <output folder>/smoke.json."""
import json
import os
import traceback

from krita import InfoObject, Krita, Selection

W = H = 64


def log(*a):
    print("[ps_smoke]", *a, flush=True)


def solid(node, r, g, b, a=255, rect=None):
    x0, y0, x1, y1 = rect or (0, 0, W, H)
    data = bytearray(W * H * 4)
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * W + x) * 4
            data[i:i + 4] = bytes((b, g, r, a))          # Krita RGBA U8 = B G R A
    node.setPixelData(bytes(data), 0, 0, W, H)


def pixel(doc, x, y):
    b, g, r, a = bytes(doc.pixelData(x, y, 1, 1))[:4]
    return [r, g, b, a]


def filter_layer(k, doc, name, fid, props):
    f = k.filter(fid)
    cfg = f.configuration()
    for key, v in props.items():
        cfg.setProperty(key, v)
    f.setConfiguration(cfg)
    sel = Selection()
    sel.select(0, 0, W, H, 255)
    return doc.createFilterLayer(name, f, sel)


def main(args):
    out_dir = args[0] if args else os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "started.txt"), "w") as f:
        f.write("ps_smoke started\n")
    res = {"checks": {}, "errors": []}
    try:
        from PyQt5.QtWidgets import QApplication
        res["engine_level"] = QApplication.instance().property("ps_tools_engine")
        k = Krita.instance()
        res["filters"] = [f for f in k.filters() if f.startswith("ps")]
        doc = k.createDocument(W, H, "smoke", "RGBA", "U8", "", 72.0)
        root = doc.rootNode()

        # background: dark left half, bright right half
        bg = doc.createNode("Background", "paintlayer")
        root.addChildNode(bg, None)
        data = bytearray(W * H * 4)
        for y in range(H):
            for x in range(W):
                v = 40 if x < W // 2 else 220
                data[(y * W + x) * 4:(y * W + x) * 4 + 4] = bytes((v, v, v, 255))
        bg.setPixelData(bytes(data), 0, 0, W, H)

        # Blend If: red layer shown only where the image below is bright
        bi = doc.createNode("BlendIf red", "paintlayer")
        root.addChildNode(bi, bg)
        solid(bi, 255, 0, 0, rect=(0, 0, W, H // 4))
        mask = doc.createTransparencyMask("Blend If (Gray | 0/0 255/255 | 128/128 255/255)")
        bi.addChildNode(mask, None)

        # clipping: blue clipped to a green square in the middle
        base = doc.createNode("Base square", "paintlayer")
        root.addChildNode(base, bi)
        solid(base, 0, 255, 0, rect=(24, 24, 40, 40))
        clipped = doc.createNode("Clipped blue", "paintlayer")
        root.addChildNode(clipped, base)
        solid(clipped, 0, 0, 255, rect=(0, 20, W, 44))
        clipped.setInheritAlpha(True)

        # a Pass Through group with Blend If on the group itself (for the PSD)
        gbi = doc.createGroupLayer("Group BlendIf")
        root.addChildNode(gbi, clipped)
        gbi.setPassThroughMode(True)
        gc = doc.createNode("Group content", "paintlayer")
        gbi.addChildNode(gc, None)
        solid(gc, 255, 0, 255, rect=(0, 48, W, H))
        gbi.addChildNode(doc.createTransparencyMask("Blend If (Gray | 0/0 255/255 | 128/128 255/255)"), None)

        # adjustment layers (hidden, so they don't change the checks above)
        above = gbi
        adj = [
            ("Levels", "levels", {"mode": "channels", "channel_0": "0.1;0.9;1.2;0;1"}),
            ("Curves", "perchannel", {"curve0": "0,0;0.5,0.7;1,1;"}),
            ("Hue/Saturation", "hsvadjustment", {"h": 30, "s": -20, "v": 10, "type": 1}),
            ("Color Balance", "colorbalance", {"cyan_red_midtones": 20, "yellow_blue_shadows": -15}),
            ("Selective Color", "psselectivecolor",
             {"values": ",".join(["10", "-20", "0", "5"] + ["0"] * 32), "relative": True}),
            ("Invert", "invert", {}),
            ("Posterize", "posterize", {"steps": 5}),
            ("Threshold", "threshold", {"threshold": 100}),
        ]
        for name, fid, props in adj:
            try:
                node = filter_layer(k, doc, name, fid, props)
                root.addChildNode(node, above)
                node.setVisible(False)
                above = node
            except Exception as e:
                res["errors"].append("filter %s: %s" % (fid, e))

        doc.refreshProjection()
        doc.waitForDone()

        c = res["checks"]
        c["blendif_dark_below_hidden"] = pixel(doc, 5, 5)          # expect background 40
        c["blendif_bright_below_red"] = pixel(doc, W - 5, 5)       # expect red
        c["clip_inside_base_blue"] = pixel(doc, 32, 32)            # expect blue
        c["clip_outside_base_bg"] = pixel(doc, 10, 32)             # expect background 40
        c["group_bi_dark_below_hidden"] = pixel(doc, 5, 56)        # expect background 40
        c["group_bi_bright_below_magenta"] = pixel(doc, W - 5, 56)  # expect magenta
        res["ok_render"] = (c["blendif_dark_below_hidden"][:3] == [40, 40, 40]
                            and c["blendif_bright_below_red"][:3] == [255, 0, 0]
                            and c["clip_inside_base_blue"][:3] == [0, 0, 255]
                            and c["clip_outside_base_bg"][:3] == [40, 40, 40]
                            and c["group_bi_dark_below_hidden"][:3] == [40, 40, 40]
                            and c["group_bi_bright_below_magenta"][:3] == [255, 0, 255])

        res["ok_groups"] = groups_check(k, res)

        doc.setBatchmode(True)
        res["export_psd"] = bool(doc.exportImage(os.path.join(out_dir, "smoke.psd"), InfoObject()))
        res["save_kra"] = bool(doc.saveAs(os.path.join(out_dir, "smoke.kra")))
        doc.close()

        # import the PSD again: adjustment layers, clipping and Blend If back
        back = k.openDocument(os.path.join(out_dir, "smoke.psd"))
        if back is None:
            res["errors"].append("could not open smoke.psd again")
        else:
            back.waitForDone()
            layers = {}
            for n in back.rootNode().childNodes():
                info = {"type": n.type(), "clip": bool(n.inheritAlpha()),
                        "masks": [c.name() for c in n.childNodes()]}
                if n.type() == "filterlayer":
                    info["filter"] = n.filter().name()
                layers[n.name()] = info
            res["import"] = layers
            want = {"Levels": "levels", "Curves": "perchannel", "Hue/Saturation": "hsvadjustment",
                    "Color Balance": "colorbalance", "Selective Color": "psselectivecolor",
                    "Invert": "invert", "Posterize": "posterize", "Threshold": "threshold"}
            res["ok_import"] = (all(layers.get(n, {}).get("filter") == f for n, f in want.items())
                                and layers.get("Clipped blue", {}).get("clip") is True
                                and any(m.startswith("Blend If (")
                                        for m in layers.get("BlendIf red", {}).get("masks", []))
                                and sum(m.startswith("Blend If (") for m in
                                        layers.get("Group BlendIf", {}).get("masks", [])) == 1)
            back.close()
    except Exception:
        res["errors"].append(traceback.format_exc())
    try:
        res["perf"] = benchmark(Krita.instance())
    except Exception:
        res["errors"].append("benchmark: " + traceback.format_exc())
    # engine speed-ups (engine level 6): same pixels with and without them,
    # then how much faster painting / adjustment changes are
    try:
        if (res.get("engine_level") or 0) >= 6:
            res["cache"] = cache_check(Krita.instance())
            res["ok_cache"] = res["cache"]["ok"]
            res["perf_paint"] = paint_benchmark(Krita.instance())
    except Exception:
        res["ok_cache"] = False
        res["errors"].append("cache check: " + traceback.format_exc())
    with open(os.path.join(out_dir, "smoke.json"), "w") as f:
        json.dump(res, f, indent=1)
    log(json.dumps(res))


def groups_check(k, res):
    """Photoshop "Pass Through" groups: Blend If, clipping and adjustment
    layers inside a pass-through group work as without the group."""
    doc = k.createDocument(W, H, "groups", "RGBA", "U8", "", 72.0)
    root = doc.rootNode()
    bg = doc.createNode("Background", "paintlayer")
    root.addChildNode(bg, None)
    data = bytearray(W * H * 4)
    for y in range(H):
        for x in range(W):
            v = 40 if x < W // 2 else 220
            data[(y * W + x) * 4:(y * W + x) * 4 + 4] = bytes((v, v, v, 255))
    bg.setPixelData(bytes(data), 0, 0, W, H)

    def group(name, above):
        g = doc.createGroupLayer(name)
        root.addChildNode(g, above)
        g.setPassThroughMode(True)
        return g

    g1 = group("Group Blend If", bg)
    bi = doc.createNode("BlendIf red", "paintlayer")
    g1.addChildNode(bi, None)
    solid(bi, 255, 0, 0, rect=(0, 0, W, H // 4))
    bi.addChildNode(doc.createTransparencyMask("Blend If (Gray | 0/0 255/255 | 128/128 255/255)"), None)

    g2 = group("Group clipping", g1)
    base = doc.createNode("Base square", "paintlayer")
    g2.addChildNode(base, None)
    solid(base, 0, 255, 0, rect=(24, 24, 40, 40))
    clipped = doc.createNode("Clipped blue", "paintlayer")
    g2.addChildNode(clipped, base)
    solid(clipped, 0, 0, 255, rect=(0, 20, W, 44))
    clipped.setInheritAlpha(True)

    g3 = group("Group adjustment", g2)
    f = k.filter("invert")
    sel = Selection()
    sel.select(0, 48, W, H - 48, 255)
    inv = doc.createFilterLayer("Invert", f, sel)
    g3.addChildNode(inv, None)

    doc.refreshProjection()
    doc.waitForDone()
    c = res["checks"]
    c["group_blendif_dark_below_hidden"] = pixel(doc, 5, 5)       # 40
    c["group_blendif_bright_below_red"] = pixel(doc, W - 5, 5)    # red
    c["group_clip_inside_base_blue"] = pixel(doc, 32, 32)         # blue
    c["group_clip_outside_base_bg"] = pixel(doc, 10, 32)          # 40
    c["group_invert_below_dark"] = pixel(doc, 5, 56)              # 215
    c["group_invert_below_bright"] = pixel(doc, W - 5, 56)        # 35
    ok = (c["group_blendif_dark_below_hidden"][:3] == [40, 40, 40]
          and c["group_blendif_bright_below_red"][:3] == [255, 0, 0]
          and c["group_clip_inside_base_blue"][:3] == [0, 0, 255]
          and c["group_clip_outside_base_bg"][:3] == [40, 40, 40]
          and c["group_invert_below_dark"][:3] == [215, 215, 215]
          and c["group_invert_below_bright"][:3] == [35, 35, 35])
    doc.close()
    return ok and clip_to_groups_check(k, res)


def clip_to_groups_check(k, res):
    """Clipping to a (nested) Pass Through group, like Photoshop: the group
    is drawn isolated and the clipped layer clips to its content. Also the
    switch back and forth (no full refresh in between)."""
    doc = k.createDocument(W, H, "clipgroups", "RGBA", "U8", "", 72.0)
    root = doc.rootNode()
    bg = doc.createNode("Background", "paintlayer")
    root.addChildNode(bg, None)
    data = bytearray(W * H * 4)
    for y in range(H):
        for x in range(W):
            v = 40 if x < W // 2 else 220
            data[(y * W + x) * 4:(y * W + x) * 4 + 4] = bytes((v, v, v, 255))
    bg.setPixelData(bytes(data), 0, 0, W, H)

    outer = doc.createGroupLayer("Outer")
    root.addChildNode(outer, bg)
    outer.setPassThroughMode(True)
    inner = doc.createGroupLayer("Inner")
    outer.addChildNode(inner, None)
    inner.setPassThroughMode(True)
    sq = doc.createNode("Square", "paintlayer")
    inner.addChildNode(sq, None)
    solid(sq, 0, 255, 0, rect=(24, 24, 40, 40))
    blue = doc.createNode("Clip to inner", "paintlayer")
    outer.addChildNode(blue, inner)
    solid(blue, 0, 0, 255, rect=(0, 0, 32, H))
    blue.setInheritAlpha(True)
    red = doc.createNode("Clip to outer", "paintlayer")
    root.addChildNode(red, outer)
    solid(red, 255, 0, 0, rect=(32, 0, W, H))
    red.setInheritAlpha(True)
    doc.refreshProjection()
    doc.waitForDone()

    c = res["checks"]
    pts = {"inner_clip_blue": (28, 32), "outer_clip_red": (36, 32),
           "outside_right_bg": (50, 32), "outside_left_bg": (10, 10), "above_square_bg": (36, 10)}
    want = {"inner_clip_blue": [0, 0, 255], "outer_clip_red": [255, 0, 0],
            "outside_right_bg": [220, 220, 220], "outside_left_bg": [40, 40, 40],
            "above_square_bg": [220, 220, 220]}
    ok = True
    for key, (x, y) in pts.items():
        c["clipgroup_" + key] = pixel(doc, x, y)
        ok &= c["clipgroup_" + key][:3] == want[key]

    red.setInheritAlpha(False)                   # release: red over the right half
    doc.waitForDone()
    c["clipgroup_released_red"] = pixel(doc, 50, 32)
    ok &= c["clipgroup_released_red"][:3] == [255, 0, 0]
    red.setInheritAlpha(True)                    # clip again
    doc.waitForDone()
    c["clipgroup_reclipped_bg"] = pixel(doc, 50, 32)
    c["clipgroup_reclipped_red"] = pixel(doc, 36, 32)
    ok &= c["clipgroup_reclipped_bg"][:3] == [220, 220, 220]
    ok &= c["clipgroup_reclipped_red"][:3] == [255, 0, 0]
    g = doc.createGroupLayer("Group BlendIf")         # Blend If on a (nested) group
    root.addChildNode(g, red)
    g.setPassThroughMode(True)
    gi = doc.createGroupLayer("Inner")
    g.addChildNode(gi, None)
    gi.setPassThroughMode(True)
    mag = doc.createNode("Magenta", "paintlayer")
    gi.addChildNode(mag, None)
    solid(mag, 255, 0, 255, rect=(0, 48, W, H))
    m = doc.createTransparencyMask("Blend If (Gray | 0/0 255/255 | 128/128 255/255)")
    g.addChildNode(m, None)
    doc.waitForDone()
    c["groupbi_dark_below_hidden"] = pixel(doc, 5, 56)
    c["groupbi_bright_below_magenta"] = pixel(doc, W - 5, 56)
    ok &= c["groupbi_dark_below_hidden"][:3] == [40, 40, 40]
    ok &= c["groupbi_bright_below_magenta"][:3] == [255, 0, 255]
    m.setVisible(False)                          # Blend If off: magenta everywhere
    doc.waitForDone()
    c["groupbi_off_magenta"] = pixel(doc, 5, 56)
    ok &= c["groupbi_off_magenta"][:3] == [255, 0, 255]
    m.setVisible(True)
    doc.waitForDone()
    c["groupbi_on_again_bg"] = pixel(doc, 5, 56)
    ok &= c["groupbi_on_again_bg"][:3] == [40, 40, 40]

    red.remove()                                 # base back to pass-through
    doc.waitForDone()
    c["clipgroup_removed_green"] = pixel(doc, 36, 32)
    ok &= c["clipgroup_removed_green"][:3] == [0, 255, 0]
    res["ok_clip_to_groups"] = bool(ok)
    doc.close()
    return ok


def benchmark(k, w=3000, h=2000, stacks=6):
    # variants: plain layers, clipping only, Blend If only, both
    """Seconds for a full re-render of a photobash-like document: the same
    layers plain, then as clipping stacks with Blend If (the cost of the
    engine patches compared with Krita's own compositing)."""
    import time

    def build(clip, blendif):
        d = k.createDocument(w, h, "bench", "RGBA", "U8", "", 72.0)
        root = d.rootNode()
        data = bytes(range(256)) * (w * h * 4 // 256)
        above = None
        for i in range(stacks):
            base = d.createNode("base %d" % i, "paintlayer")
            root.addChildNode(base, above)
            base.setPixelData(data, 0, 0, w, h)
            top = d.createNode("top %d" % i, "paintlayer")
            root.addChildNode(top, base)
            top.setPixelData(data[::-1], 0, 0, w, h)
            if clip:
                top.setInheritAlpha(True)
            if blendif:
                m = d.createTransparencyMask("Blend If (Gray | 20/60 255/255 | 0/0 200/240)")
                top.addChildNode(m, None)
            curves = filter_layer(k, d, "curves %d" % i, "perchannel",
                                  {"curve0": "0,0;0.5,0.6;1,1;"})
            root.addChildNode(curves, top)
            if clip:
                curves.setInheritAlpha(True)
            above = curves
        d.waitForDone()
        return d

    out = {}
    for label, clip, blendif in (("plain", False, False), ("clip", True, False),
                                 ("blendif", False, True), ("clip_blendif", True, True)):
        d = build(clip, blendif)
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            d.refreshProjection()
            d.waitForDone()
            times.append(round(time.perf_counter() - t0, 3))
        out[label] = times
        d.close()
    out["size"] = "%dx%d, %d stacks (base + clipped layer + clipped curves)" % (w, h, stacks)
    return out


# ---------------------------------------------------------------------------
# Engine speed-ups (kis_ps_perf.h): composite caches + visible area first
# ---------------------------------------------------------------------------

class Engine(object):
    """Commands of the psfilters plugin (ps_engine_control.h)."""

    def __init__(self):
        from PyQt5.QtWidgets import QApplication
        self.app = QApplication.instance()
        self.n = 0

    def cmd(self, text):
        self.n += 1
        self.app.setProperty("ps_tools_test", "#%d %s" % (self.n, text))
        out = self.app.property("ps_tools_test_result")
        out = str(out) if out is not None else "error: no answer"
        if out.startswith("error"):
            raise RuntimeError("%s -> %s" % (text, out))
        return out

    def uid(self, node):
        return node.uniqueId().toString()

    def dab(self, node, x, y, w, h, seed):
        return self.cmd("dab %s %d %d %d %d %d" % (self.uid(node), x, y, w, h, seed))

    def dabs(self, node, count, x, y, w, h, dx, dy, seed):
        return self.cmd("dabs %s %d %d %d %d %d %d %d %d" % (self.uid(node), count, x, y, w, h, dx, dy, seed))

    def dirty(self, node):
        return self.cmd("dirty %s" % self.uid(node))

    def stats(self):
        out = {}
        for part in self.cmd("stats").split():
            k, _, v = part.partition("=")
            try:
                out[k] = int(v)
            except ValueError:
                out[k] = v
        return out


CW, CH = 700, 520          # larger than one 512 px update patch: parallel jobs


def build_complex(k, eng):
    """A photobash-like document with everything the caches must respect:
    blend modes, opacity, hidden layers, isolated / pass-through / nested
    groups, clipping stacks, Blend If, adjustment layers, masks."""
    doc = k.createDocument(CW, CH, "cache", "RGBA", "U8", "", 72.0)
    root = doc.rootNode()
    N = {}
    seed = [100]

    def paint(name, parent=None, above=None, mode=None, opacity=None, rects=3):
        n = doc.createNode(name, "paintlayer")
        (parent or root).addChildNode(n, above)
        if mode:
            n.setBlendingMode(mode)
        if opacity is not None:
            n.setOpacity(opacity)
        for i in range(rects):
            seed[0] += 7
            s = seed[0]
            eng.dab(n, (s * 37) % (CW - 200), (s * 53) % (CH - 160), 120 + s % 180, 90 + s % 140, s)
        N[name] = n
        return n

    def group(name, parent=None, above=None, passthrough=False):
        g = doc.createGroupLayer(name)
        (parent or root).addChildNode(g, above)
        g.setPassThroughMode(passthrough)
        N[name] = g
        return g

    def curves(name, parent=None, above=None, pts="0,0;0.5,0.7;1,1;"):
        f = k.filter("perchannel")
        cfg = f.configuration()
        cfg.setProperty("curve0", pts)
        f.setConfiguration(cfg)
        sel = Selection()
        sel.select(0, 0, CW, CH, 255)
        n = doc.createFilterLayer(name, f, sel)
        (parent or root).addChildNode(n, above)
        N[name] = n
        return n

    bg = paint("Background", rects=0)
    eng.dab(bg, 0, 0, CW, CH, 1)
    a = paint("Multiply", above=bg, mode="multiply", opacity=200)
    ga = group("Group A", above=a)
    a1 = paint("A1", parent=ga)
    a2 = paint("A2", parent=ga, above=a1, mode="screen")
    ab = paint("A base", parent=ga, above=a2)
    ac = paint("A clipped", parent=ga, above=ab)
    ac.setInheritAlpha(True)
    curves("A curves", parent=ga, above=ac)
    gp = group("Group P", above=ga, passthrough=True)
    p1 = paint("P1", parent=gp)
    p2 = paint("P2 blendif", parent=gp, above=p1)
    p2.addChildNode(doc.createTransparencyMask("Blend If (Gray | 0/0 255/255 | 60/120 255/255)"), None)
    gi = group("Group PI", parent=gp, above=p2, passthrough=True)
    pi1 = paint("PI1", parent=gi)
    paint("PI2", parent=gi, above=pi1, mode="overlay")
    lv = curves("Root curves", above=gp, pts="0,0;0.4,0.6;1,1;")
    mid = paint("Mid", above=lv)
    ov = paint("Overlay", above=mid, mode="overlay")
    base = paint("Base", above=ov)
    c1 = paint("C1", above=base)
    c1.setInheritAlpha(True)
    c2 = paint("C2", above=c1, mode="multiply")
    c2.setInheritAlpha(True)
    hid = paint("Hidden", above=c2)
    hid.setVisible(False)
    t1 = paint("Top1", above=hid)
    t2 = paint("Top2", above=t1, opacity=180)
    gt = group("Group T", above=t2)
    paint("T1", parent=gt)
    paint("Top3", above=gt)
    doc.refreshProjection()
    doc.waitForDone()
    return doc, N


def cache_script(N):
    """(action, layer name, args...) - painting several times on the same
    layer (the caches fill, then are used), changes elsewhere in between
    (they must be dropped), every kind of layer as the painted one."""
    S = []

    def strokes(name, n, x, y, seed):
        for i in range(n):
            S.append(("dab", name, x + i * 23, y + i * 17, 140, 110, seed + i))

    strokes("Mid", 4, 60, 60, 500)                  # middle of the root level
    strokes("Top3", 3, 300, 200, 520)               # topmost layer
    strokes("Background", 3, 100, 300, 540)         # bottom layer
    strokes("A1", 3, 200, 100, 560)                 # inside an isolated group
    strokes("PI1", 3, 350, 250, 580)                # nested pass-through groups
    strokes("P2 blendif", 3, 150, 220, 600)         # Blend If layer
    strokes("C1", 3, 420, 120, 620)                 # clipped layer
    strokes("Base", 3, 380, 160, 640)               # base of a stack
    strokes("T1", 3, 260, 330, 660)                 # inside a group of the top run
    strokes("Top1", 3, 90, 380, 680)                # inside the top run
    S.append(("visible", "Multiply", False))        # a change below "Mid"
    strokes("Mid", 3, 80, 90, 700)
    S.append(("opacity", "Top2", 90))               # a change above "Mid"
    strokes("Mid", 3, 120, 110, 720)
    S.append(("dab", "Overlay", 140, 140, 200, 150, 740))   # another layer
    strokes("Mid", 2, 130, 130, 760)
    S.append(("blend", "Top1", "multiply"))         # the run is not plain any more
    strokes("Mid", 3, 160, 150, 780)
    S.append(("blend", "Top1", "normal"))
    strokes("Mid", 2, 170, 160, 800)
    S.append(("curves", "Root curves", "0,0;0.3,0.8;1,1;"))  # adjustment changed
    strokes("Mid", 2, 40, 260, 820)
    S.append(("clip", "Mid", True))                 # the painted layer gets clipped
    strokes("Mid", 2, 60, 280, 840)
    S.append(("clip", "Mid", False))
    strokes("Mid", 2, 70, 290, 860)
    S.append(("passthrough", "Group A", True))      # group switches mode
    strokes("A2", 3, 230, 150, 880)
    S.append(("visible", "Hidden", True))
    strokes("Top3", 2, 320, 210, 900)
    S.append(("move", "Mid", "Top2"))               # the painted layer moves up
    strokes("Mid", 3, 100, 120, 920)
    S.append(("remove", "Overlay"))                 # a layer below goes away
    strokes("Mid", 2, 110, 140, 940)
    S.append(("dabs", "Mid", 12, 20, 20, 160, 120, 45, 30, 960))   # a long stroke, no waits
    S.append(("dabs", "Top3", 10, 600, 20, 100, 100, -50, 40, 990))
    return S


def run_script(k, eng, flags, priority=None):
    """Fresh document, the script, the projection after every step."""
    eng.cmd("perf %d" % flags)
    eng.cmd("drop")
    doc, N = build_complex(k, eng)
    # (building sets clipping while the first dabs are still being drawn: a
    # state change in the middle of an update, which the cache detects and
    # handles by dropping itself - counted from here on, only the script)
    eng.cmd("reset_stats")
    if priority:
        eng.cmd("priority %d %d %d %d" % priority)
    shots = []
    for step in cache_script(N):
        act, name = step[0], step[1]
        n = N.get(name)
        if act == "dab":
            eng.dab(n, *step[2:])
        elif act == "dabs":
            eng.dabs(n, *step[2:])
        elif act == "visible":
            n.setVisible(step[2])
            eng.dirty(n)
        elif act == "opacity":
            n.setOpacity(step[2])
            eng.dirty(n)
        elif act == "blend":
            n.setBlendingMode(step[2])
            eng.dirty(n)
        elif act == "clip":
            n.setInheritAlpha(step[2])
            eng.dirty(n)
        elif act == "passthrough":
            n.setPassThroughMode(step[2])
            doc.refreshProjection()
        elif act == "curves":
            f = k.filter("perchannel")
            cfg = f.configuration()
            cfg.setProperty("curve0", step[2])
            f.setConfiguration(cfg)
            n.setFilter(f)
        elif act == "move":
            target = N[step[2]]
            parent = n.parentNode()
            n.remove()
            doc.waitForDone()
            parent.addChildNode(n, target)
            eng.dirty(n)
        elif act == "remove":
            n.remove()
            del N[name]
        doc.waitForDone()
        shots.append(bytes(doc.pixelData(0, 0, CW, CH)))
    stats = eng.stats()
    stats["notes"] = eng.cmd("notes")
    # the cached result must equal a full recomputation
    before = shots[-1]
    doc.refreshProjection()
    doc.waitForDone()
    full = bytes(doc.pixelData(0, 0, CW, CH))
    doc.close()
    eng.cmd("drop")
    return shots, stats, diff(before, full)


def diff(a, b):
    """(pixels that differ, largest channel difference)."""
    if a == b:
        return (0, 0)
    if len(a) != len(b):
        return (-1, 255)
    import array
    x, y = array.array("B", a), array.array("B", b)
    worst, count = 0, 0
    for i in range(0, len(x), 4):
        d = max(abs(x[i] - y[i]), abs(x[i + 1] - y[i + 1]),
                abs(x[i + 2] - y[i + 2]), abs(x[i + 3] - y[i + 3]))
        if d:
            count += 1
            worst = max(worst, d)
    return (count, worst)


def cache_check(k):
    """The same script without speed-ups, with the below cache only (must
    be exact) and with everything (the merged top layers may differ by
    rounding: at most a few levels)."""
    eng = Engine()
    out = {}
    ref, st0, full0 = run_script(k, eng, 0)
    below, st1, full1 = run_script(k, eng, 0x1 | 0x4 | 0x8, priority=(200, 100, 300, 200))
    allf, st2, full2 = run_script(k, eng, 0xF, priority=(0, 0, 250, 250))
    out["steps"] = len(ref)
    out["stats_off"], out["stats_below"], out["stats_all"] = st0, st1, st2
    out["full_refresh_diff"] = [full0, full1, full2]
    bad_below = [(i, diff(r, b)) for i, (r, b) in enumerate(zip(ref, below)) if r != b]
    diffs_all = [diff(r, a) for r, a in zip(ref, allf)]
    bad_all = [(i, d) for i, d in enumerate(diffs_all) if d[1] > 3]
    out["below_mismatch_steps"] = bad_below[:10]
    out["all_mismatch_steps"] = bad_all[:10]
    out["all_max_diff"] = max([d[1] for d in diffs_all] or [0])
    out["ok"] = (not bad_below and not bad_all
                 and full0 == (0, 0) and full1 == (0, 0) and full2[1] <= 3
                 and st1.get("belowHit", 0) > 0 and st2.get("aboveHit", 0) > 0
                 and st1.get("inconsistent", 0) == 0 and st2.get("inconsistent", 0) == 0
                 and st0.get("belowHit", 0) == 0)
    eng.cmd("perf 15")
    return out


def paint_benchmark(k, w=3000, h=2000, layers=30):
    """A photobash-like document (every layer covers the canvas, a few
    blend modes, Normal layers on top, a curves layer) without / with the
    speed-ups:
      dab_ms_*   - milliseconds per dab when every dab is waited for (what
                   you feel while painting), on a layer in the middle, at
                   the top and near the bottom of the stack
      stroke_*   - seconds for 120 dabs sent at once (throughput)
      adjust_x4  - seconds for 4 changes of the curves layer"""
    import time
    eng = Engine()
    out = {}
    for label, flags in (("off", 0), ("on", 0xF)):
        eng.cmd("perf %d" % flags)
        eng.cmd("drop")
        eng.cmd("reset_stats")
        doc = k.createDocument(w, h, "bench", "RGBA", "U8", "", 72.0)
        root = doc.rootNode()
        above = None
        nodes = []
        modes = ["normal", "multiply", "normal", "screen", "normal", "overlay", "normal", "soft_light"]
        for i in range(layers):
            n = doc.createNode("L%d" % i, "paintlayer")
            root.addChildNode(n, above)
            if 0 < i < layers - 6:
                n.setBlendingMode(modes[i % len(modes)])
            if i:
                n.setOpacity(150 + (i * 37) % 100)
            eng.cmd("dab %s 0 0 %d %d %d" % (eng.uid(n), w, h, i * 11 + 3))   # covers the canvas
            for j in range(4):
                s = i * 13 + j * 7 + 1
                eng.dab(n, (s * 397) % (w - 900), (s * 211) % (h - 700), 600 + s % 300, 400 + s % 300, s)
            nodes.append(n)
            above = n
            if i == layers // 2:
                f = k.filter("perchannel")
                cfg = f.configuration()
                cfg.setProperty("curve0", "0,0;0.5,0.6;1,1;")
                f.setConfiguration(cfg)
                sel = Selection()
                sel.select(0, 0, w, h, 255)
                adj = doc.createFilterLayer("Curves", f, sel)
                root.addChildNode(adj, above)
                above = adj
        doc.refreshProjection()
        doc.waitForDone()
        res = {}
        for name, node in (("mid", nodes[layers // 2 + 3]), ("top", nodes[-1])):
            # a very big brush: every dab covers a large area painted before
            uid = eng.uid(node)
            t0 = time.perf_counter()
            for i in range(20):
                eng.cmd("dab %s %d %d 1400 1400 %d" % (uid, 500 + i * 9, 300 + i * 7, 5000 + i))
                doc.waitForDone()
            res["bigdab_ms_" + name] = round((time.perf_counter() - t0) * 1000.0 / 20, 1)
        for name, node in (("mid", nodes[layers // 2 + 3]), ("top", nodes[-1]), ("bottom", nodes[2])):
            uid = eng.uid(node)
            t0 = time.perf_counter()
            for i in range(60):
                eng.cmd("dab %s %d %d 220 220 %d" % (uid, 400 + i * 21, 300 + i * 13, 3000 + i))
                doc.waitForDone()
            res["dab_ms_" + name] = round((time.perf_counter() - t0) * 1000.0 / 60, 2)
            t0 = time.perf_counter()
            eng.dabs(node, 120, 300, 900, 220, 220, 19, -5, 4000)
            doc.waitForDone()
            res["stroke_" + name] = round(time.perf_counter() - t0, 3)
        t0 = time.perf_counter()
        for i in range(4):
            f = k.filter("perchannel")
            cfg = f.configuration()
            cfg.setProperty("curve0", "0,0;0.5,%.2f;1,1;" % (0.55 + i * 0.05))
            f.setConfiguration(cfg)
            adj.setFilter(f)
            doc.waitForDone()
        res["adjust_x4"] = round(time.perf_counter() - t0, 3)
        res["stats"] = eng.cmd("stats")
        out[label] = res
        doc.close()
    eng.cmd("perf 15")
    eng.cmd("drop")
    out["size"] = "%dx%d, %d full layers + curves; dabs of 220 px" % (w, h, layers)
    return out
