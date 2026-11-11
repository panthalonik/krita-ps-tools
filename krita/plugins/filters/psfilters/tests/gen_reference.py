"""Writes test vectors for test_kernels.cpp: random pixels, random LUTs /
Selective Color settings, and the result of the Python plugin's NumPy
reference implementation (ps_tools/pixelops.py)."""
import importlib.util
import pathlib
import struct
import sys
from array import array

import numpy as np

plugin = pathlib.Path(sys.argv[1])          # .../ps_tools
out = pathlib.Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)


def load(name):
    spec = importlib.util.spec_from_file_location(name, plugin / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


px = load("pixelops")
luts = load("lutfile")
rng = np.random.default_rng(20261111)
N = 20000
DEPTHS = [("U8", np.uint8, 255), ("U16", np.uint16, 65535), ("F32", np.float32, 1)]


def pixels(dtype, scale):
    a = rng.random((N, 4))
    a[:64] = rng.integers(0, 2, (64, 4))            # corners of the cube
    a[64:128, :3] = a[64:128, :1]                    # grays
    if scale == 1:
        a[128:192, :3] = a[128:192, :3] * 3 - 1      # out-of-range floats
    arr = (a * scale).astype(dtype)
    arr[::7, 3] = 0
    return arr


def lut_blob(lut):
    # the plugin's own packer (what it hands to the C++ filter in Krita PS),
    # so test_kernels.cpp also checks the wire format end to end
    return luts.psl1_blob(lut)


cases = []
lut17 = luts.Lut(17, array("f", rng.random(17 ** 3 * 3)))
lut2 = luts.Lut(2, array("f", rng.random(24)))
lut1 = luts.Lut(0, None, data1d=array("f", rng.random(33 * 3)), size1d=33)
shaper = luts.Lut(9, array("f", rng.random(9 ** 3 * 3)),
                  data1d=array("f", np.sort(rng.random((16, 3)), 0).ravel()), size1d=16)
domain = luts.Lut(5, array("f", rng.random(375)), dmin=(0.1, 0.0, 0.2), dmax=(0.9, 1.0, 0.7))
idx = 0
for depth, dtype, scale in DEPTHS:
    size, pos = px.layout("RGBA", depth)
    src = pixels(dtype, scale)
    for name, lut in [("lut17", lut17), ("lut2", lut2), ("lut1d", lut1),
                      ("shaper", shaper), ("domain", domain)]:
        for linear in (0, 1):
            buf = bytearray(src.tobytes())
            px._lut_numpy(np, buf, size, pos, lut, bool(linear))
            base = "c%02d" % idx
            idx += 1
            (out / (base + ".src")).write_bytes(src.tobytes())
            (out / (base + ".ref")).write_bytes(bytes(buf))
            (out / (base + ".lut")).write_bytes(lut_blob(lut))
            cases.append("lut %s %s %d %s %s" % (base, depth, linear, name, "-"))
    for k in range(4):
        adj = rng.uniform(-1, 1, 36)
        if k == 1:
            adj[4:] = 0                                # only reds
        if k == 2:
            adj[:] = np.round(adj)                     # extremes
        for relative in (0, 1):
            buf = bytearray(src.tobytes())
            px._sel_numpy(np, buf, size, pos, [float(x) for x in adj], bool(relative))
            base = "c%02d" % idx
            idx += 1
            (out / (base + ".src")).write_bytes(src.tobytes())
            (out / (base + ".ref")).write_bytes(bytes(buf))
            (out / (base + ".adj")).write_bytes(array("f", adj).tobytes())
            cases.append("sel %s %s %d %s %s" % (base, depth, relative, "adj%d" % k, "-"))
(out / "cases.txt").write_text("%d\n" % N + "\n".join(cases) + "\n")
print(len(cases), "cases")
