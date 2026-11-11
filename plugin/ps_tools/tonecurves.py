# -*- coding: utf-8 -*-
"""Photoshop tone functions for Brightness/Contrast and Exposure, turned into
Krita curve points (natural cubic spline, which is what Krita's curves use),
so they run as a native Krita filter layer and update live."""
import math

def _enc(v):
    if v <= 0: return 0.0
    return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055

def _dec(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

def exposure_fn(e, off, g):
    a = 2.0 ** e; g = max(0.01, g)
    def f(x):
        v = _dec(x) * a + off
        v = max(0.0, v)
        v = v ** (1.0 / g)
        return min(1.0, max(0.0, _enc(v)))
    return f

def _raised(b):
    """PS modern brightness, b in 0..150 (linear from black, knee, hermite roll-off)."""
    s = 1.375 ** (b / 50.0)
    yk = 126.0 / 255.0
    xk = yk / s
    dx, dy = 1.0 - xk, 1.0 - yk
    m0, m1 = s, 1.0 / (1.0 + 0.084 * b)
    a, bb = m0 * dx / dy, m1 * dx / dy
    r = math.hypot(a, bb)
    if r > 3.0:                 # keep the cubic monotone (Fritsch-Carlson)
        m0 *= 3.0 / r; m1 *= 3.0 / r
    def f(x):
        if x <= xk: return s * x
        t = (x - xk) / dx
        h00 = 2*t**3 - 3*t**2 + 1; h10 = t**3 - 2*t**2 + t; h01 = -2*t**3 + 3*t**2; h11 = t**3 - t**2
        return min(1.0, h00 * yk + h10 * dx * m0 + h01 * 1.0 + h11 * dx * m1)
    return f

def _inverse(f):
    xs = [i / 4096.0 for i in range(4097)]
    ys = [f(x) for x in xs]
    def g(y):
        lo, hi = 0, 4096
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ys[mid] < y: lo = mid
            else: hi = mid
        y0, y1 = ys[lo], ys[hi]
        t = 0 if y1 == y0 else (y - y0) / (y1 - y0)
        return xs[lo] + t * (xs[hi] - xs[lo])
    return g

def bc_fn(b, c, legacy=False):
    if legacy:
        def f(x):
            if c >= 0:
                k = 100.0 / max(1e-3, 100.0 - c)
                v = (x + b / 255.0 - 0.5) * k + 0.5
            else:
                v = (x - 0.5) * (100.0 + c) / 100.0 + 0.5 + b / 255.0
            return min(1.0, max(0.0, v))
        return f
    if b > 0: fb = _raised(b)
    elif b < 0: fb = _inverse(_raised(-b))
    else: fb = lambda x: x
    gam = 1.0 + c / 100.0
    def fc(v):
        if gam == 1.0 or v <= 0 or v >= 1: return v
        p, q = v ** gam, (1 - v) ** gam
        return p / (p + q)
    return lambda x: min(1.0, max(0.0, fc(fb(x))))

def _unused_to_points(f, tol=0.0015, maxpts=48):
    """Few control points whose natural spline follows f (Douglas-Peucker on 513 samples)."""
    xs = [i / 512.0 for i in range(513)]
    ys = [f(x) for x in xs]
    keep = {0, 512}
    def dp(i, j):
        if j <= i + 1: return
        x0, y0, x1, y1 = xs[i], ys[i], xs[j], ys[j]
        best, bi = -1, None
        for k in range(i + 1, j):
            yl = y0 + (y1 - y0) * (xs[k] - x0) / (x1 - x0)
            d = abs(ys[k] - yl)
            if d > best: best, bi = d, k
        if best > tol:
            keep.add(bi); dp(i, bi); dp(bi, j)
    dp(0, 512)
    idx = sorted(keep)
    # at least every 1/16 so the spline has support everywhere
    for k in range(0, 513, 32): idx.append(k)
    idx = sorted(set(idx))
    while len(idx) > maxpts:
        idx = idx[::2] + ([idx[-1]] if idx[-1] != idx[::2][-1] else [])
    return [(xs[i], ys[i]) for i in idx]


def natural_spline(pts):
    n = len(pts) - 1
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    h = [xs[i + 1] - xs[i] for i in range(n)]
    # solve for second derivatives M (natural: M0 = Mn = 0)
    M = [0.0] * (n + 1)
    if n >= 2:
        a = [h[i] for i in range(1, n - 1)]
        b = [2 * (h[i] + h[i + 1]) for i in range(n - 1)]
        c = a[:]
        d = [6 * ((ys[i + 2] - ys[i + 1]) / h[i + 1] - (ys[i + 1] - ys[i]) / h[i]) for i in range(n - 1)]
        m = n - 1
        cp = [0.0] * m; dp_ = [0.0] * m
        cp[0] = c[0] / b[0] if m > 1 else 0.0
        dp_[0] = d[0] / b[0]
        for i in range(1, m):
            den = b[i] - a[i - 1] * cp[i - 1]
            cp[i] = c[i] / den if i < m - 1 else 0.0
            dp_[i] = (d[i] - a[i - 1] * dp_[i - 1]) / den
        sol = [0.0] * m
        sol[-1] = dp_[-1]
        for i in range(m - 2, -1, -1):
            sol[i] = dp_[i] - cp[i] * sol[i + 1]
        M[1:n] = sol
    def ev(x):
        lo, hi = 0, n
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x: lo = mid
            else: hi = mid
        i = lo; hi_ = h[i]
        t1 = xs[i + 1] - x; t0 = x - xs[i]
        return (M[i] * t1 ** 3 + M[i + 1] * t0 ** 3) / (6 * hi_) + \
            (ys[i] / hi_ - M[i] * hi_ / 6) * t1 + (ys[i + 1] / hi_ - M[i + 1] * hi_ / 6) * t0
    return ev


def fit_points(f, tol=0.35 / 255, maxpts=96, grid=512):
    xs = [i / float(grid) for i in range(grid + 1)]
    ys = [f(x) for x in xs]
    idx = set(range(0, grid + 1, grid // 16))
    for _ in range(maxpts):
        sel = sorted(idx)
        ev = natural_spline([(xs[i], ys[i]) for i in sel])
        worst, wi = 0.0, None
        for k in range(grid + 1):
            e = abs(min(1.0, max(0.0, ev(xs[k]))) - ys[k])
            if e > worst: worst, wi = e, k
        if worst <= tol or len(idx) >= maxpts or wi in idx:
            break
        idx.add(wi)
    sel = sorted(idx)
    return [(xs[i], ys[i]) for i in sel], worst
