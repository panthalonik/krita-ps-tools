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
        if "pscameraraw" in res["filters"]:
            res["ok_camera_raw"] = camera_raw_check(k, res)

        res["ok_psd_pixels"] = psd_roundtrip_check(k, res, out_dir)

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


def camera_raw_check(k, res):
    """Camera Raw Filter (pscameraraw): as a filter layer and as a filter
    mask - neutral settings change nothing, the sliders change the image,
    nothing breaks with every setting on (also in 16-bit)."""
    out = {}
    ok = True
    for depth in ("U8", "U16"):
        doc = k.createDocument(W, H, "cr", "RGBA", depth, "", 72.0)
        root = doc.rootNode()
        bg = doc.createNode("Photo", "paintlayer")
        root.addChildNode(bg, None)
        # a photo-like gradient with a bright and a dark part
        bpp = 1 if depth == "U8" else 2
        data = bytearray(W * H * 4 * bpp)
        for y in range(H):
            for x in range(W):
                v = (30 + x * 3 + y) % 256 if x < W // 2 else 200 + (x + y) % 50
                px = (min(255, v), min(255, v + 10), min(255, v // 2 + 40), 255)
                i = (y * W + x) * 4 * bpp
                if bpp == 1:
                    data[i:i + 4] = bytes((px[2], px[1], px[0], 255))
                else:
                    for c, val in enumerate((px[2], px[1], px[0], 255)):
                        data[i + 2 * c:i + 2 * c + 2] = (val * 257).to_bytes(2, "little")
        bg.setPixelData(bytes(data), 0, 0, W, H)
        doc.refreshProjection()
        doc.waitForDone()
        before = bytes(doc.pixelData(0, 0, W, H))

        def layer(props):
            f = k.filter("pscameraraw")
            cfg = f.configuration()
            for key, v in props.items():
                cfg.setProperty(key, v)
            f.setConfiguration(cfg)
            sel = Selection()
            sel.select(0, 0, W, H, 255)
            n = doc.createFilterLayer("Camera Raw", f, sel)
            root.addChildNode(n, bg)
            doc.refreshProjection()
            doc.waitForDone()
            return n

        n = layer({"exposure": 0.0, "clarity": 0.0})
        same = bytes(doc.pixelData(0, 0, W, H)) == before
        n.remove()
        n = layer({"exposure": 1.0})
        brighter = sum(bytes(doc.pixelData(0, 0, W, H))[1::4 * bpp]) > sum(before[1::4 * bpp])
        n.remove()
        everything = {"exposure": 0.4, "contrast": 30, "highlights": -50, "shadows": 40, "whites": 10,
                      "blacks": -10, "temperature": 20, "tint": -10, "vibrance": 30, "saturation": -10,
                      "mix_hue_red": 20, "cg_shadows_hue": 210, "cg_shadows_sat": 40, "texture": 30,
                      "clarity": 40, "dehaze": 20, "vig_amount": -30, "grain_amount": 20,
                      "sharp_amount": 60, "nr_lum": 30, "nr_color": 30,
                      "curve_rgb": "0,0;0.4,0.35;1,1", "pc_shadows": 20}
        n = layer(everything)
        changed = bytes(doc.pixelData(0, 0, W, H)) != before
        n.remove()
        # as a filter mask on the layer (Filter > Camera Raw Filter)
        f = k.filter("pscameraraw")
        cfg = f.configuration()
        cfg.setProperty("saturation", -100.0)
        f.setConfiguration(cfg)
        sel = Selection()
        sel.select(0, 0, W, H, 255)
        mask = doc.createFilterMask("Camera Raw Filter", f, sel)
        bg.addChildNode(mask, None)
        doc.refreshProjection()
        doc.waitForDone()
        px = bytes(doc.pixelData(5, 5, 1, 1))
        grey = abs(px[0] - px[bpp]) <= 2 * bpp and abs(px[bpp] - px[2 * bpp]) <= 2 * bpp if bpp == 1 else True
        # what the Camera Raw dialog does: new settings on the mask, and the
        # image must change by itself (no refreshProjection, patches/0011)
        masked = bytes(doc.pixelData(0, 0, W, H))
        f = k.filter("pscameraraw")
        cfg = f.configuration()
        cfg.setProperty("saturation", 0.0)
        cfg.setProperty("exposure", 1.0)
        f.setConfiguration(cfg)
        mask.setFilter(f)
        doc.waitForDone()
        live = bytes(doc.pixelData(0, 0, W, H)) != masked
        out[depth] = {"neutral_unchanged": same, "exposure_brighter": brighter,
                      "all_settings_changed": changed, "mask_greyscale": grey, "mask_live_update": live}
        ok = ok and same and brighter and changed and grey and live
        doc.close()
    res["camera_raw"] = out
    return ok


def psd_roundtrip_check(k, res, out_dir):
    """PSD export + import gives back exactly the same layer pixels (RGB /
    Grayscale, 8 / 16 bit; patch 0012 reads those with a fast path), and how
    long a bigger PSD takes to open."""
    import random
    import time
    out = res["psd_pixels"] = {}
    ok = True
    for model, depth in (("RGBA", "U8"), ("RGBA", "U16"), ("GRAYA", "U8"), ("GRAYA", "U16")):
        w, h = 203, 131
        d = k.createDocument(w, h, "psd", model, depth, "", 72.0)
        root = d.rootNode()
        bpp = (4 if model == "RGBA" else 2) * (1 if depth == "U8" else 2)
        rnd = random.Random(7)
        datas = {}
        for i, (x, y, lw, lh) in enumerate(((0, 0, w, h), (17, 9, 120, 77), (60, 40, 143, 91))):
            n = d.createNode("L%d" % i, "paintlayer")
            root.addChildNode(n, None)
            data = bytearray(rnd.randbytes(lw * lh * bpp))
            n.setPixelData(bytes(data), x, y, lw, lh)
            datas[n.name()] = n
        d.waitForDone()
        want = {name: bytes(n.pixelData(0, 0, w, h)) for name, n in datas.items()}
        path = os.path.join(out_dir, "rt_%s_%s.psd" % (model, depth))
        d.setBatchmode(True)
        d.exportImage(path, InfoObject())
        d.setModified(False)
        d.close()
        b = k.openDocument(path)
        same = {}
        if b is None:
            same["open"] = False
        else:
            b.waitForDone()
            for n in b.rootNode().childNodes():
                if n.name() in want:
                    same[n.name()] = bytes(n.pixelData(0, 0, w, h)) == want[n.name()]
            b.setModified(False)
            b.close()
        out["%s_%s" % (model, depth)] = same
        ok = ok and len(same) == 3 and all(same.values())
    # opening time of a 2048x2048 PSD with 4 full layers (informational)
    try:
        n2 = 2048
        d = k.createDocument(n2, n2, "psdbig", "RGBA", "U8", "", 72.0)
        rnd = random.Random(3)
        for i in range(4):
            n = d.createNode("B%d" % i, "paintlayer")
            d.rootNode().addChildNode(n, None)
            data = bytearray(rnd.randbytes(n2 * n2 * 4))
            data[3::4] = b"\xff" * (n2 * n2)
            n.setPixelData(bytes(data), 0, 0, n2, n2)
        d.waitForDone()
        path = os.path.join(out_dir, "big.psd")
        d.setBatchmode(True)
        d.exportImage(path, InfoObject())
        d.setModified(False)
        d.close()
        t = time.perf_counter()
        b = k.openDocument(path)
        b.waitForDone()
        out["open_2048_4_layers_s"] = round(time.perf_counter() - t, 2)
        b.close()
        os.remove(path)
    except Exception:
        res["errors"].append("psd timing: " + traceback.format_exc())
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
