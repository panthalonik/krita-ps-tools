"""A 4096x4096 16-bit greyscale PNG like a Blender depth render (smooth
gradients, dark shapes, the same colour chunks: cICP, sRGB, gAMA, cHRM)."""
import struct
import sys
import zlib

N = 4096


def chunk(t, data):
    return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xffffffff)


blobs = [(1800, 2600, 700), (1000, 1200, 450), (2900, 1500, 380), (600, 3300, 900)]
raw = bytearray()
for y in range(N):
    row = bytearray()
    for x in range(0, N, 64):
        v = min(65535, 9000 + x * 9 + y * 4)
        row += struct.pack(">H", v) * 64
    for cx, cy, r in blobs:
        dy = y - cy
        if abs(dy) < r:
            w = int((r * r - dy * dy) ** 0.5)
            x0, x1 = max(0, cx - w), min(N, cx + w)
            row[x0 * 2:x1 * 2] = struct.pack(">H", 1500 + (cx % 7) * 300) * (x1 - x0)
    raw += b"\x00" + row
png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 16, 0, 0, 0, 0))
png += chunk(b"cICP", bytes((1, 13, 0, 1)))
png += chunk(b"sRGB", b"\x03")
png += chunk(b"gAMA", struct.pack(">I", 45455))
png += chunk(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000))
png += chunk(b"pHYs", struct.pack(">IIB", 2834, 2834, 1))
png += chunk(b"tEXt", b"File\x00C:\\probe\\scene.blend")      # Blender: Krita asks nothing
png += chunk(b"IDAT", zlib.compress(bytes(raw), 3))
png += chunk(b"IEND", b"")
open(sys.argv[1], "wb").write(png)
print("written", sys.argv[1], len(png))
