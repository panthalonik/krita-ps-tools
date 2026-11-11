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

        # adjustment layers (hidden, so they don't change the checks above)
        above = clipped
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
        res["ok_render"] = (c["blendif_dark_below_hidden"][:3] == [40, 40, 40]
                            and c["blendif_bright_below_red"][:3] == [255, 0, 0]
                            and c["clip_inside_base_blue"][:3] == [0, 0, 255]
                            and c["clip_outside_base_bg"][:3] == [40, 40, 40])

        doc.setBatchmode(True)
        res["export_psd"] = bool(doc.exportImage(os.path.join(out_dir, "smoke.psd"), InfoObject()))
        res["save_kra"] = bool(doc.saveAs(os.path.join(out_dir, "smoke.kra")))
        doc.close()
    except Exception:
        res["errors"].append(traceback.format_exc())
    with open(os.path.join(out_dir, "smoke.json"), "w") as f:
        json.dump(res, f, indent=1)
    log(json.dumps(res))
