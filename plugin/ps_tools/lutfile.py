# -*- coding: utf-8 -*-
"""3D LUT readers: .cube (Adobe/Resolve), .3dl (Lustre/Nuke/Photoshop),
.look (Adobe SpeedGrade/IRIDAS, the baked LUT block).

Every reader returns a Lut: size N, data = N*N*N*3 floats with RED changing
fastest (index = (b*N + g)*N + r), input domain (min, max) per channel.
A .cube with only a 1D LUT is returned with data1d set instead."""
import os
import re
import struct
from array import array

EXTENSIONS = (".cube", ".3dl", ".look")


class LutError(Exception):
    pass


class Lut(object):
    def __init__(self, size, data, dmin=(0.0, 0.0, 0.0), dmax=(1.0, 1.0, 1.0),
                 title="", data1d=None, size1d=0):
        self.size = size
        self.data = data            # array('f'), r fastest
        self.dmin = tuple(dmin)
        self.dmax = tuple(dmax)
        self.title = title
        self.data1d = data1d        # array('f') size1d*3 (r,g,b per entry)
        self.size1d = size1d


# ----------------------------------------------------------------- .cube
def read_cube(text):
    size3 = size1 = 0
    dmin, dmax = [0.0] * 3, [1.0] * 3
    title = ""
    vals = array("f")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        c = line[0]
        if c.isdigit() or c in "-+.":
            parts = line.split()
            if len(parts) >= 3:
                vals.extend(float(x) for x in parts[:3])
            continue
        key, _, rest = line.partition(" ")
        key = key.upper()
        if key == "TITLE":
            title = rest.strip().strip('"')
        elif key == "LUT_3D_SIZE":
            size3 = int(rest.split()[0])
        elif key == "LUT_1D_SIZE":
            size1 = int(rest.split()[0])
        elif key == "DOMAIN_MIN":
            dmin = [float(x) for x in rest.split()[:3]]
        elif key == "DOMAIN_MAX":
            dmax = [float(x) for x in rest.split()[:3]]
        elif key in ("LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"):
            a, b = [float(x) for x in rest.split()[:2]]
            dmin, dmax = [a] * 3, [b] * 3
    if size3:
        n = size3 ** 3 * 3
        if len(vals) < n:
            raise LutError("The .cube file is incomplete (%d of %d values)." % (len(vals), n))
        if size1 and len(vals) >= n + size1 * 3:
            # Resolve style: the 1D shaper comes first, then the 3D table
            shaper = vals[:size1 * 3]
            return Lut(size3, vals[size1 * 3:size1 * 3 + n], dmin, dmax, title,
                       data1d=shaper, size1d=size1)
        return Lut(size3, vals[:n], dmin, dmax, title)
    if size1:
        if len(vals) < size1 * 3:
            raise LutError("The .cube file is incomplete.")
        return Lut(0, None, dmin, dmax, title, data1d=vals[:size1 * 3], size1d=size1)
    raise LutError("No LUT_3D_SIZE / LUT_1D_SIZE in the .cube file.")


# ------------------------------------------------------------------ .3dl
def read_3dl(text):
    out_bits = None
    shaper = None
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("3dmesh"):
            continue
        if low.startswith("mesh"):
            try:
                out_bits = int(line.split()[2])
            except Exception:
                pass
            continue
        if low.startswith("lut") or low.startswith("gamma"):
            continue
        parts = line.split()
        try:
            nums = [float(x) for x in parts]
        except ValueError:
            continue
        if shaper is None and len(nums) > 3:
            shaper = nums
            continue
        if len(nums) >= 3:
            rows.append(nums[:3])
    if not rows:
        raise LutError("No data in the .3dl file.")
    if shaper:
        n = len(shaper)
    else:
        n = int(round(len(rows) ** (1.0 / 3)))
    if n < 2 or len(rows) < n ** 3:
        raise LutError("The .3dl file is incomplete (%d rows, grid %d)." % (len(rows), n))
    if out_bits:
        scale = float(2 ** out_bits - 1)
    else:
        m = max(max(r) for r in rows)
        scale = 1.0
        for bits in (8, 10, 12, 14, 16):
            if m <= 2 ** bits - 1 + 0.5:
                scale = float(2 ** bits - 1)
                break
        if m <= 1.0:
            scale = 1.0
    # .3dl: blue changes fastest -> reorder to red fastest
    data = array("f", [0.0]) * (n ** 3 * 3)
    i = 0
    for r in range(n):
        for g in range(n):
            for b in range(n):
                o = ((b * n + g) * n + r) * 3
                row = rows[i]
                data[o] = row[0] / scale
                data[o + 1] = row[1] / scale
                data[o + 2] = row[2] / scale
                i += 1
    return Lut(n, data)


# ----------------------------------------------------------------- .look
def read_look(text):
    m = re.search(r"<LUT>.*?<size>\s*\"?\s*(\d+)\s*\"?\s*</size>.*?<data>\s*\"?(.*?)\"?\s*</data>",
                  text, re.S | re.I)
    if not m:
        raise LutError("This .look file has no baked LUT block.")
    n = int(m.group(1))
    hexdata = re.sub(r"[^0-9A-Fa-f]", "", m.group(2))
    count = n ** 3 * 3
    if len(hexdata) < count * 8:
        raise LutError("The LUT block of the .look file is incomplete.")
    raw = bytes.fromhex(hexdata[:count * 8])
    data = array("f", struct.unpack("<%df" % count, raw))
    t = re.search(r"<name>\s*\"?([^\"<]*)", text)
    return Lut(n, data, title=t.group(1) if t else "")


# ------------------------------------------------------------------ entry
_cache = {}


def load(path):
    """Reads a LUT file (cached by path + modification time)."""
    try:
        st = os.stat(path)
    except OSError:
        raise LutError("LUT file not found:\n%s" % path)
    key = (os.path.normcase(os.path.abspath(path)), st.st_mtime, st.st_size)
    if key in _cache:
        return _cache[key]
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", "replace")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".cube":
        lut = read_cube(text)
    elif ext == ".3dl":
        lut = read_3dl(text)
    elif ext == ".look":
        lut = read_look(text)
    else:
        raise LutError("Unsupported LUT format: %s" % ext)
    if not lut.title:
        lut.title = os.path.splitext(os.path.basename(path))[0]
    _cache[key] = lut
    return lut


def is_linear(lut, path):
    """SpeedGrade .look tables are usually display referred, but some (e.g.
    Photoshop's "Bleach Bypass.look") hold linear-light values far above 1;
    those are applied in linear light (sRGB decode -> LUT -> encode)."""
    if os.path.splitext(path)[1].lower() != ".look" or not lut.size:
        return False
    if not hasattr(lut, "_linear"):
        lut._linear = max(lut.data) > 2.0
    return lut._linear


def list_luts(folders):
    """[(display name, path)] of the LUT files in the folders (not recursive
    beyond one level), sorted by name, duplicates by name removed."""
    seen = set()
    out = []
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for root, dirs, files in os.walk(folder):
            depth = root[len(folder):].count(os.sep)
            if depth >= 2:
                dirs[:] = []
            for fn in files:
                if os.path.splitext(fn)[1].lower() in EXTENSIONS:
                    name = os.path.splitext(fn)[0]
                    if name.lower() in seen:
                        continue
                    seen.add(name.lower())
                    out.append((name, os.path.join(root, fn)))
    out.sort(key=lambda x: x[0].lower())
    return out
