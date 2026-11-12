"""Tests of the Camera Raw kernel (ps_cameraraw.h) through cameraraw_cli:

  python3 test_cameraraw.py <cameraraw_cli> [preview_dir]

- no settings: the image comes back unchanged
- tile consistency: tiles with border() pixels of context give exactly the
  whole-image result (Krita renders filters tile by tile)
- every slider does what its name says (direction checks)
- no NaN / inf for extreme settings
With preview_dir, PNG previews of every setting on a sample photo are
written (scikit-image's sample images when available)."""
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

CLI = sys.argv[1]
PREVIEW = sys.argv[2] if len(sys.argv) > 2 else None
TMP = tempfile.mkdtemp()
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("FAIL:", msg)


def write_raw(path, img):
    h, w = img.shape[:2]
    with open(path, "wb") as f:
        f.write(struct.pack("<ii", w, h))
        f.write(np.ascontiguousarray(img, dtype=np.float32).tobytes())


def read_raw(path):
    with open(path, "rb") as f:
        w, h = struct.unpack("<ii", f.read(8))
        return np.frombuffer(f.read(), dtype=np.float32).reshape(h, w, 4).copy()


def run(img, *args, **kw):
    src = os.path.join(TMP, "in.raw")
    dst = os.path.join(TMP, "out.raw")
    write_raw(src, img)
    extra = ["%s=%s" % (k, v) for k, v in kw.items()]
    subprocess.run([CLI, src, dst] + list(args) + extra, check=True, stdout=subprocess.DEVNULL)
    return read_raw(dst)


def synthetic(w=257, h=193, seed=3):
    """A photo-like test image: gradients, coloured patches, edges, noise,
    a dark corner and a bright sky, and a transparent margin."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 4), np.float32)
    img[..., 0] = 0.15 + 0.7 * x / w
    img[..., 1] = 0.1 + 0.6 * y / h
    img[..., 2] = 0.5 + 0.4 * np.sin(x / 13.0) * np.cos(y / 17.0)
    img[: h // 4, :, :3] = [0.82, 0.86, 0.92]                      # sky
    img[h // 2:h // 2 + 30, w // 4:w // 4 + 40, :3] = [0.85, 0.2, 0.15]   # red patch
    img[h // 2:h // 2 + 30, w // 2:w // 2 + 40, :3] = [0.2, 0.35, 0.85]   # blue patch
    img[h - 40:, :50, :3] = 0.04                                   # dark corner
    img[..., :3] += rng.normal(0, 0.02, (h, w, 3)).astype(np.float32)
    img[..., :3] = np.clip(img[..., :3], 0, 1)
    img[..., 3] = 1.0
    img[:, -6:, 3] = 0.0                                           # transparent margin
    return img


def lum(img):
    return 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]


def sample_photo():
    try:
        from skimage import data
        a = data.astronaut()[::2, ::2] / 255.0
    except Exception:
        return synthetic(400, 300, 7)
    h, w = a.shape[:2]
    img = np.ones((h, w, 4), np.float32)
    img[..., :3] = a
    return img


img = synthetic()
opaque = img[..., 3] > 0

# ---- identity
out = run(img)
check(np.abs(out - img).max() < 1e-4, "no settings: image changed (max %g)" % np.abs(out - img).max())

# ---- every setting at a strong value: finite, alpha kept
ALL = dict(exposure=0.7, contrast=40, highlights=-60, shadows=50, whites=20, blacks=-20,
           pc_highlights=20, pc_lights=-10, pc_darks=15, pc_shadows=-10,
           temperature=25, tint=-15, vibrance=40, saturation=-10,
           mix_hue_red=30, mix_sat_blue=-50, mix_lum_green=20,
           cg_shadows_hue=210, cg_shadows_sat=40, cg_highlights_hue=40, cg_highlights_sat=30,
           cg_midtones_lum=10, cg_global_sat=10, cg_global_hue=120,
           texture=40, clarity=50, dehaze=30,
           vig_amount=-40, vig_highlights=30, grain_amount=30,
           sharp_amount=80, sharp_masking=30, nr_lum=40, nr_color=50)
ALL["curve_rgb"] = "0,0;0.3,0.25;0.7,0.8;1,1"
ALL["curve_red"] = "0,0;0.5,0.55;1,1"
out = run(img, **ALL)
check(np.isfinite(out).all(), "NaN / inf with all settings")
check(np.abs(out[..., 3] - img[..., 3]).max() == 0, "alpha changed")

# ---- tiles == whole image (exactly, apart from float noise)
whole = run(img, **ALL)
for tile in (64, 50):
    tiled = run(img, "tile=%d" % tile, **ALL)
    d = np.abs(whole - tiled)[opaque].max()
    check(d < 2e-4, "tile %d differs from whole image by %g" % (tile, d))
for lodv in (1, 2):
    whole = run(img, "lod=%d" % lodv, **ALL)
    tiled = run(img, "lod=%d" % lodv, "tile=40", **ALL)
    d = np.abs(whole - tiled)[opaque].max()
    check(d < 2e-4, "lod %d: tiles differ by %g" % (lodv, d))
for key in ("highlights", "clarity", "texture", "dehaze", "sharp_amount", "nr_lum", "nr_color", "grain_amount"):
    v = {"highlights": -80, "clarity": 70, "texture": 60, "dehaze": 60, "sharp_amount": 120,
         "nr_lum": 70, "nr_color": 80, "grain_amount": 60}[key]
    a = run(img, **{key: v})
    b = run(img, "tile=37", **{key: v})
    d = np.abs(a - b)[opaque].max()
    check(d < 2e-4, "%s: tiles differ by %g" % (key, d))

# ---- directions
L0 = lum(img)[opaque]
m = lambda o: lum(o)[opaque]


def stat(o, region):
    return float(lum(o)[region].mean())


dark = np.zeros(img.shape[:2], bool)
dark[img.shape[0] - 35:, :45] = True
sky = np.zeros(img.shape[:2], bool)
sky[5:img.shape[0] // 4 - 5, 10:-20] = True
red = np.zeros(img.shape[:2], bool)
h0, w0 = img.shape[0] // 2, img.shape[1] // 4
red[h0 + 5:h0 + 25, w0 + 5:w0 + 35] = True
blue = np.zeros(img.shape[:2], bool)
blue[h0 + 5:h0 + 25, img.shape[1] // 2 + 5:img.shape[1] // 2 + 35] = True

check(m(run(img, exposure=1)).mean() > L0.mean() + 0.05, "exposure +1 does not brighten")
check(m(run(img, exposure=-1)).mean() < L0.mean() - 0.05, "exposure -1 does not darken")
check(m(run(img, contrast=80)).std() > L0.std() * 1.05, "contrast + does not add contrast")
check(m(run(img, contrast=-80)).std() < L0.std() * 0.95, "contrast - does not reduce contrast")
check(stat(run(img, highlights=-100), sky) < stat(img, sky) - 0.03, "highlights - does not darken the sky")
check(abs(stat(run(img, highlights=-100), dark) - stat(img, dark)) < 0.01, "highlights - changes the shadows")
check(stat(run(img, shadows=100), dark) > stat(img, dark) + 0.03, "shadows + does not lift the dark corner")
check(abs(stat(run(img, shadows=100), sky) - stat(img, sky)) < 0.02, "shadows + changes the sky")
check(stat(run(img, whites=80), sky) > stat(img, sky) + 0.01, "whites + does not brighten whites")
check(stat(run(img, blacks=-80), dark) < stat(img, dark) - 0.005, "blacks - does not darken blacks")
check(stat(run(img, pc_shadows=80), dark) > stat(img, dark), "parametric shadows + does not lift")
check(stat(run(img, curve_rgb="0,0;0.5,0.7;1,1"), dark) > stat(img, dark), "point curve does not lift")
o = run(img, temperature=60)
check((o[..., 0] / (o[..., 2] + 1e-3))[opaque].mean() > (img[..., 0] / (img[..., 2] + 1e-3))[opaque].mean(),
      "temperature + is not warmer")
o = run(img, tint=60)
check(o[..., 1][opaque].mean() < img[..., 1][opaque].mean(), "tint + is not more magenta")
o = run(img, saturation=-100)
check(np.abs(o[..., 0] - o[..., 1])[opaque].max() < 0.02, "saturation -100 is not grey")
spread = lambda o, r: float((o[..., :3].max(-1) - o[..., :3].min(-1))[r].mean())
check(spread(run(img, vibrance=80), opaque) > spread(img, opaque), "vibrance + does not saturate")
o = run(img, mix_sat_blue=-100)
check(spread(o, blue) < spread(img, blue) * 0.5, "blue saturation -100 keeps blue")
check(abs(spread(o, red) - spread(img, red)) < 0.03, "blue saturation changes red")
o = run(img, mix_lum_red=-80)
check(stat(o, red) < stat(img, red) - 0.01, "red luminance - does not darken red")
o = run(img, cg_shadows_hue=240, cg_shadows_sat=100)
check(o[..., 2][dark].mean() > img[..., 2][dark].mean() + 0.005, "shadow grading blue does not tint shadows")
check(abs(o[..., 2][sky].mean() - img[..., 2][sky].mean()) < 0.02, "shadow grading tints the sky")


def local_std(o, r=4):
    L = lum(o)
    k = np.ones(2 * r + 1) / (2 * r + 1)
    b = np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 0, L)
    b = np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 1, b)
    return float(np.abs(L - b)[20:-20, 20:-20].mean())


check(local_std(run(img, clarity=80)) > local_std(img), "clarity + does not add local contrast")
check(local_std(run(img, clarity=-80)) < local_std(img), "clarity - does not soften")
check(local_std(run(img, texture=80)) > local_std(img), "texture + does not add detail")
check(local_std(run(img, texture=-80)) < local_std(img), "texture - does not smooth")
check(local_std(run(img, sharp_amount=120)) > local_std(img), "sharpening does not sharpen")
check(local_std(run(img, nr_lum=80)) < local_std(img), "noise reduction does not smooth")
o = run(img, nr_color=100)
check(np.isfinite(o).all(), "colour noise reduction gives NaN")
o = run(img, dehaze=80)
check(stat(o, sky) < stat(img, sky) - 0.02 and spread(o, opaque) > spread(img, opaque),
      "dehaze + does not remove haze (sky %g -> %g)" % (stat(img, sky), stat(o, sky)))
check(m(run(img, dehaze=-80)).std() < L0.std(), "dehaze - does not add haze")
o = run(img, vig_amount=-80)
corner = np.zeros(img.shape[:2], bool)
corner[:15, :15] = True
centre = np.zeros(img.shape[:2], bool)
centre[90:100, 120:130] = True
check(stat(o, corner) < stat(img, corner) - 0.05, "vignette - does not darken the corner")
check(abs(stat(o, centre) - stat(img, centre)) < 0.005, "vignette - darkens the centre")
for style in (1, 2):
    o = run(img, vig_amount=-80, vig_style=style)
    check(stat(o, corner) < stat(img, corner) - 0.03, "vignette style %d does nothing" % style)
o = run(img, vig_amount=80)
check(stat(o, corner) > stat(img, corner) + 0.03, "vignette + does not lighten the corner")
o = run(img, grain_amount=80)
d = (lum(o) - lum(img))[opaque]
check(d.std() > 0.005 and abs(d.mean()) < 0.01, "grain: std %g mean %g" % (d.std(), d.mean()))

print("%d failures" % len(failures))

# ---- previews (for a human to look at)
if PREVIEW:
    from PIL import Image
    os.makedirs(PREVIEW, exist_ok=True)
    photo = sample_photo()
    looks = [
        ("original", {}),
        ("exposure+1", dict(exposure=1)), ("contrast+60", dict(contrast=60)),
        ("highlights-100", dict(highlights=-100)), ("shadows+100", dict(shadows=100)),
        ("whites+60_blacks-60", dict(whites=60, blacks=-60)),
        ("temp+50", dict(temperature=50)), ("tint+50", dict(tint=50)),
        ("vibrance+80", dict(vibrance=80)), ("saturation-60", dict(saturation=-60)),
        ("mixer_red_hue+100", dict(mix_hue_red=100)), ("mixer_blue_lum-80", dict(mix_lum_blue=-80)),
        ("grading_teal_orange", dict(cg_shadows_hue=200, cg_shadows_sat=60, cg_highlights_hue=35,
                                     cg_highlights_sat=50)),
        ("texture+100", dict(texture=100)), ("clarity+100", dict(clarity=100)),
        ("clarity-100", dict(clarity=-100)), ("dehaze+80", dict(dehaze=80)), ("dehaze-80", dict(dehaze=-80)),
        ("vignette-60", dict(vig_amount=-60)), ("grain60", dict(grain_amount=60, grain_size=40)),
        ("sharpen150", dict(sharp_amount=150, sharp_radius=1.5)), ("nr80", dict(nr_lum=80, nr_color=60)),
        ("curve_s", dict(curve_rgb="0,0;0.25,0.18;0.75,0.84;1,1")),
        ("parametric", dict(pc_highlights=50, pc_shadows=-50)),
    ]
    tiles = []
    for name, kw in looks:
        o = run(photo, **kw)
        rgb = (np.clip(o[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8)
        Image.fromarray(rgb).save(os.path.join(PREVIEW, name + ".png"))
        tiles.append((name, rgb))
    # contact sheet
    h, w = tiles[0][1].shape[:2]
    cols = 4
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * (h + 14)), (40, 40, 40))
    from PIL import ImageDraw
    dr = ImageDraw.Draw(sheet)
    for i, (name, rgb) in enumerate(tiles):
        x, y = (i % cols) * w, (i // cols) * (h + 14)
        sheet.paste(Image.fromarray(rgb), (x, y + 14))
        dr.text((x + 3, y + 1), name, fill=(230, 230, 230))
    sheet.save(os.path.join(PREVIEW, "_sheet.png"))
    print("previews in", PREVIEW)

sys.exit(1 if failures else 0)
