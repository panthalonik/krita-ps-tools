# -*- coding: utf-8 -*-
"""Pixel kernels (3D LUT, Selective Color) for Krita pixel buffers.

Uses the bundled native library (ps_native.dll on Windows, libps_native.so on
Linux) through ctypes. Falls back to numpy, then to (slow) pure Python."""
import ctypes
import os
import sys
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))

RANGES = ["reds", "yellows", "greens", "cyans", "blues", "magentas",
          "whites", "neutrals", "blacks"]

_lib = None
_lib_error = None


def _private_copy(path):
    """Windows locks a loaded DLL; load a copy so that the plugin folder can
    be overwritten by a newer version while Krita is running."""
    if sys.platform != "win32":
        return path
    try:
        import hashlib
        import shutil
        import tempfile
        with open(path, "rb") as f:
            digest = hashlib.md5(f.read()).hexdigest()[:12]
        dst = os.path.join(tempfile.gettempdir(), "ps_tools_native_%s.dll" % digest)
        if not os.path.exists(dst):
            tmp = dst + ".%d.tmp" % os.getpid()
            shutil.copyfile(path, tmp)
            os.replace(tmp, dst)
        return dst
    except Exception:
        return path


def _load():
    global _lib, _lib_error
    if _lib is not None or _lib_error is not None:
        return _lib
    names = []
    if sys.platform == "win32":
        names = ["ps_native.dll"]
    elif sys.platform.startswith("linux"):
        names = ["libps_native.so"]
    elif sys.platform == "darwin":
        names = ["libps_native.dylib"]
    for n in names:
        p = os.path.join(HERE, n)
        if os.path.exists(p):
            try:
                lib = ctypes.CDLL(_private_copy(p))
                f = lib.ps_lut3d
                f.restype = ctypes.c_int
                f.argtypes = [ctypes.c_void_p, ctypes.c_int64] + [ctypes.c_int] * 5 + [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
                f = lib.ps_lut1d
                f.restype = ctypes.c_int
                f.argtypes = [ctypes.c_void_p, ctypes.c_int64] + [ctypes.c_int] * 5 + [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
                f = lib.ps_selective_color
                f.restype = ctypes.c_int
                f.argtypes = [ctypes.c_void_p, ctypes.c_int64] + [ctypes.c_int] * 5 + [
                    ctypes.c_void_p, ctypes.c_int]
                _lib = lib
                return _lib
            except Exception as e:
                _lib_error = str(e)
                return None
    _lib_error = "native library not found"
    return None


def backend():
    if _load() is not None:
        return "native"
    try:
        import numpy  # noqa
        return "numpy"
    except Exception:
        return "python"


class PixelFormatError(Exception):
    pass


def layout(model, depth):
    """(element size, (r, g, b, a) positions) for a Krita colour model/depth."""
    if model != "RGBA":
        raise PixelFormatError(u"Color Lookup and Selective Color work on RGB images "
                               u"(this image is %s)." % model)
    if depth == "U8":
        return 1, (2, 1, 0, 3)
    if depth == "U16":
        return 2, (2, 1, 0, 3)
    if depth == "F32":
        return 4, (0, 1, 2, 3)
    raise PixelFormatError(u"This image is %s. Use 8-bit, 16-bit integer or 32-bit "
                           u"float RGB for Color Lookup / Selective Color." % depth)


def _ptr(buf):
    return ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))


def _farr(values):
    a = array("f", values)
    return a, ctypes.c_void_p(a.buffer_info()[0])


# ------------------------------------------------------------------ LUT
def apply_lut(buf, model, depth, lut, linear=False):
    """buf: bytearray of pixels (modified in place)."""
    size, pos = layout(model, depth)
    npix = len(buf) // (4 * size)
    lib = _load()
    if lib is not None:
        dmin, pmin = _farr(lut.dmin)
        dmax, pmax = _farr(lut.dmax)
        if lut.size:
            data, pdata = _farr(lut.data) if not isinstance(lut.data, array) else \
                (lut.data, ctypes.c_void_p(lut.data.buffer_info()[0]))
            pre_n = lut.size1d if lut.data1d is not None else 0
            pre = ctypes.c_void_p(lut.data1d.buffer_info()[0]) if pre_n else None
            r = lib.ps_lut3d(_ptr(buf), npix, size, pos[0], pos[1], pos[2], pos[3],
                             pdata, lut.size, pmin, pmax, pre, pre_n, 1 if linear else 0)
        else:
            r = lib.ps_lut1d(_ptr(buf), npix, size, pos[0], pos[1], pos[2], pos[3],
                             ctypes.c_void_p(lut.data1d.buffer_info()[0]), lut.size1d, pmin, pmax)
        if r != 0:
            raise RuntimeError("ps_native LUT error %d" % r)
        return
    try:
        import numpy as np
    except Exception:
        np = None
    if np is not None:
        _lut_numpy(np, buf, size, pos, lut, linear)
    else:
        _lut_python(buf, size, pos, lut, linear)


def _np_view(np, buf, size):
    dt = {1: np.uint8, 2: np.uint16, 4: np.float32}[size]
    return np.frombuffer(buf, dt).reshape(-1, 4)


def _np_srgb_to_lin(np, v):
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _np_lin_to_srgb(np, v):
    v = np.maximum(v, 0)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


NUMPY_CHUNK_PIXELS = 65536


def _chunks(buf, size):
    view = memoryview(buf)
    step = NUMPY_CHUNK_PIXELS * 4 * size
    for start in range(0, len(view), step):
        yield view[start:start + step]


def _lut_numpy(np, buf, size, pos, lut, linear):
    for chunk in _chunks(buf, size):
        _lut_numpy_chunk(np, chunk, size, pos, lut, linear)


def _lut_numpy_chunk(np, buf, size, pos, lut, linear):
    px = _np_view(np, buf, size)
    scale = {1: 255.0, 2: 65535.0, 4: 1.0}[size]
    ok = px[:, pos[3]] > 0
    rgb = np.stack([px[:, pos[0]], px[:, pos[1]], px[:, pos[2]]], 1).astype(np.float32) / scale
    if linear:
        rgb = _np_srgb_to_lin(np, rgb)
    dmin = np.array(lut.dmin, np.float32)
    dmax = np.array(lut.dmax, np.float32)
    t = np.clip((rgb - dmin) / (dmax - dmin), 0, 1)
    if lut.size:
        if lut.data1d is not None:
            t = _np_1d(np, t, lut.data1d, lut.size1d)
        n = lut.size
        L = np.array(lut.data, np.float32).reshape(n, n, n, 3)   # [b][g][r]
        x = t * (n - 1)
        i0 = np.minimum(x.astype(np.int32), n - 2)
        f = x - i0
        out = np.zeros_like(t)
        for db in (0, 1):
            wb = f[:, 2] if db else 1 - f[:, 2]
            for dg in (0, 1):
                wg = f[:, 1] if dg else 1 - f[:, 1]
                for dr in (0, 1):
                    wr = f[:, 0] if dr else 1 - f[:, 0]
                    out += (wb * wg * wr)[:, None] * L[i0[:, 2] + db, i0[:, 1] + dg, i0[:, 0] + dr]
    else:
        out = _np_1d(np, t, lut.data1d, lut.size1d)
    if linear:
        out = _np_lin_to_srgb(np, out)
    if size != 4:
        out = np.clip(out, 0, 1) * scale + 0.5
    for k in range(3):
        px[ok, pos[k]] = out[ok, k].astype(px.dtype)


def _np_1d(np, t, table, n):
    T = np.array(table, np.float32).reshape(n, 3)
    x = np.clip(t, 0, 1) * (n - 1)
    i0 = np.minimum(x.astype(np.int32), n - 2)
    f = x - i0
    out = np.empty_like(t)
    for k in range(3):
        out[:, k] = T[i0[:, k], k] * (1 - f[:, k]) + T[i0[:, k] + 1, k] * f[:, k]
    return out


def _lut_python(buf, size, pos, lut, linear):
    """Slow fallback, 8/16-bit only."""
    import struct
    fmt = {1: "B", 2: "H", 4: "f"}[size]
    scale = {1: 255.0, 2: 65535.0, 4: 1.0}[size]
    vals = array(fmt)
    vals.frombytes(bytes(buf))
    n = lut.size
    L = lut.data
    cache = {}

    def s2l(v):
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    def l2s(v):
        if v <= 0:
            return 0.0
        return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055

    def one(r, g, b):
        c = [r / scale, g / scale, b / scale]
        if linear:
            c = [s2l(v) for v in c]
        c = [min(1.0, max(0.0, (c[k] - lut.dmin[k]) / (lut.dmax[k] - lut.dmin[k]))) for k in range(3)]
        if not n:
            out = [_py_1d(lut.data1d, lut.size1d, k, c[k]) for k in range(3)]
        else:
            if lut.data1d is not None:
                c = [_py_1d(lut.data1d, lut.size1d, k, c[k]) for k in range(3)]
            x = [v * (n - 1) for v in c]
            i0 = [min(int(v), n - 2) for v in x]
            f = [x[k] - i0[k] for k in range(3)]
            out = [0.0, 0.0, 0.0]
            for db in (0, 1):
                wb = f[2] if db else 1 - f[2]
                for dg in (0, 1):
                    wg = f[1] if dg else 1 - f[1]
                    for dr in (0, 1):
                        w = wb * wg * (f[0] if dr else 1 - f[0])
                        o = (((i0[2] + db) * n + i0[1] + dg) * n + i0[0] + dr) * 3
                        out[0] += w * L[o]
                        out[1] += w * L[o + 1]
                        out[2] += w * L[o + 2]
        if linear:
            out = [l2s(v) for v in out]
        if size == 4:
            return out
        return [int(min(1.0, max(0.0, v)) * scale + 0.5) for v in out]

    for i in range(0, len(vals), 4):
        if vals[i + pos[3]] <= 0:
            continue
        key = (vals[i + pos[0]], vals[i + pos[1]], vals[i + pos[2]])
        res = cache.get(key)
        if res is None:
            if len(cache) >= 65536:
                cache.clear()
            res = cache[key] = one(*key)
        vals[i + pos[0]], vals[i + pos[1]], vals[i + pos[2]] = res
    buf[:] = vals.tobytes()


def _py_1d(table, n, k, v):
    x = min(1.0, max(0.0, v)) * (n - 1)
    i = min(int(x), n - 2)
    f = x - i
    return table[i * 3 + k] * (1 - f) + table[(i + 1) * 3 + k] * f


# --------------------------------------------------------- Selective Color
def adj_vector(params):
    """params: {"reds": [c, m, y, k] in percent, ...} -> 36 floats in -1..1."""
    out = []
    for name in RANGES:
        v = params.get(name) or [0, 0, 0, 0]
        out.extend(max(-1.0, min(1.0, float(x) / 100.0)) for x in list(v)[:4])
    return out


def selective_color(buf, model, depth, params, relative=True):
    size, pos = layout(model, depth)
    adj = adj_vector(params)
    if not any(adj):
        return
    npix = len(buf) // (4 * size)
    lib = _load()
    if lib is not None:
        a, pa = _farr(adj)
        r = lib.ps_selective_color(_ptr(buf), npix, size, pos[0], pos[1], pos[2], pos[3],
                                   pa, 1 if relative else 0)
        if r != 0:
            raise RuntimeError("ps_native selective color error %d" % r)
        return
    try:
        import numpy as np
    except Exception:
        np = None
    if np is not None:
        _sel_numpy(np, buf, size, pos, adj, relative)
    else:
        _sel_python(buf, size, pos, adj, relative)


def _sel_numpy(np, buf, size, pos, adj, relative):
    for chunk in _chunks(buf, size):
        _sel_numpy_chunk(np, chunk, size, pos, adj, relative)


def _sel_numpy_chunk(np, buf, size, pos, adj, relative):
    px = _np_view(np, buf, size)
    scale = {1: 255.0, 2: 65535.0, 4: 1.0}[size]
    ok = px[:, pos[3]] > 0
    r = np.clip(px[:, pos[0]].astype(np.float32) / scale, 0, 1)
    g = np.clip(px[:, pos[1]].astype(np.float32) / scale, 0, 1)
    b = np.clip(px[:, pos[2]].astype(np.float32) / scale, 0, 1)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    mid = r + g + b - mx - mn
    z = np.zeros_like(r)
    ranges = [
        np.where(r == mx, mx - mid, z), np.where(b == mn, mid - mn, z),
        np.where(g == mx, mx - mid, z), np.where(r == mn, mid - mn, z),
        np.where(b == mx, mx - mid, z), np.where(g == mn, mid - mn, z),
        np.where((r > .5) & (g > .5) & (b > .5), (mn - .5) * 2, z),
        np.where(((r > 0) | (g > 0) | (b > 0)) & ~((r >= 1) & (g >= 1) & (b >= 1)),
                 1 - (np.abs(mx - .5) + np.abs(mn - .5)), z),
        np.where((r < .5) & (g < .5) & (b < .5), (.5 - mx) * 2, z)]
    d = [np.zeros_like(r), np.zeros_like(r), np.zeros_like(r)]
    chans = [r, g, b]
    for k in range(9):
        a = adj[k * 4:k * 4 + 4]
        if not any(a):
            continue
        rng = np.maximum(ranges[k], 0)
        for c in range(3):
            v = chans[c]
            res = (-1 - a[c]) * a[3] - a[c]
            res = res * (1 - v) if relative else res + z
            d[c] += np.clip(res, -v, 1 - v) * rng
    for c in range(3):
        out = chans[c] + d[c]
        if size != 4:
            out = np.clip(out, 0, 1) * scale + 0.5
        px[ok, pos[c]] = out[ok].astype(px.dtype)


def _sel_python(buf, size, pos, adj, relative):
    fmt = {1: "B", 2: "H", 4: "f"}[size]
    scale = {1: 255.0, 2: 65535.0, 4: 1.0}[size]
    vals = array(fmt)
    vals.frombytes(bytes(buf))
    cache = {}

    def one(R, G, B):
        r, g, b = [min(1.0, max(0.0, x / scale)) for x in (R, G, B)]
        mx, mn = max(r, g, b), min(r, g, b)
        mid = r + g + b - mx - mn
        rng = [mx - mid if r == mx else 0, mid - mn if b == mn else 0,
               mx - mid if g == mx else 0, mid - mn if r == mn else 0,
               mx - mid if b == mx else 0, mid - mn if g == mn else 0,
               (mn - .5) * 2 if (r > .5 and g > .5 and b > .5) else 0,
               1 - (abs(mx - .5) + abs(mn - .5)) if ((r or g or b) and not (r >= 1 and g >= 1 and b >= 1)) else 0,
               (.5 - mx) * 2 if (r < .5 and g < .5 and b < .5) else 0]
        ch = [r, g, b]
        d = [0.0, 0.0, 0.0]
        for k in range(9):
            a = adj[k * 4:k * 4 + 4]
            if rng[k] <= 0 or not any(a):
                continue
            for c in range(3):
                v = ch[c]
                res = (-1 - a[c]) * a[3] - a[c]
                if relative:
                    res *= 1 - v
                d[c] += min(1 - v, max(-v, res)) * rng[k]
        out = [ch[c] + d[c] for c in range(3)]
        if size == 4:
            return out
        return [int(min(1.0, max(0.0, v)) * scale + 0.5) for v in out]

    for i in range(0, len(vals), 4):
        if vals[i + pos[3]] <= 0:
            continue
        key = (vals[i + pos[0]], vals[i + pos[1]], vals[i + pos[2]])
        res = cache.get(key)
        if res is None:
            if len(cache) >= 65536:
                cache.clear()
            res = cache[key] = one(*key)
        vals[i + pos[0]], vals[i + pos[1]], vals[i + pos[2]] = res
    buf[:] = vals.tobytes()
