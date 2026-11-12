"""Photoshop Color panel / Color Picker (color_panel.py): colour maths,
the field and strip of every mode give back the colour they show, and the
dialog (real PyQt5 when available) keeps its fields in step."""
import itertools
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verification"))
import fake_krita                                # noqa: E402
fake_krita.install()
_pkg = types.ModuleType("ps_tools")
_pkg.__path__ = [str(ROOT / "ps_tools")]
sys.modules["ps_tools"] = _pkg

try:
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication(["test"])
    HAVE_QT = hasattr(QApplication, "exec_")
except Exception:
    HAVE_QT = False

from ps_tools import color_panel as CP           # noqa: E402

MODES = ["h", "s", "v", "r", "g", "bl", "L", "a", "b"]
SAMPLES = [(0.8, 0.2, 0.1), (0.1, 0.6, 0.3), (0.2, 0.3, 0.9), (0.5, 0.5, 0.5), (0.95, 0.9, 0.2),
           (0.05, 0.05, 0.08), (0.7, 0.3, 0.75)]


class Maths(unittest.TestCase):
    def test_hsv_round_trip(self):
        for r, g, b in SAMPLES:
            h, s, v = CP.rgb_to_hsv(r, g, b)
            for x, y in zip(CP.hsv_to_rgb(h, s, v), (r, g, b)):
                self.assertAlmostEqual(x, y, places=6)

    def test_grey_keeps_hue(self):
        h, s, v = CP.rgb_to_hsv(0.4, 0.4, 0.4, keep_h=210.0)
        self.assertEqual((h, s), (210.0, 0.0))

    def test_lab(self):
        L, a, b = CP.rgb_to_lab(1, 1, 1)
        self.assertAlmostEqual(L, 100, places=2)
        self.assertAlmostEqual(a, 0, places=2)
        L, a, b = CP.rgb_to_lab(1, 0, 0)             # Photoshop: 54, 81, 70 (sRGB, D65 ~ 53, 80, 67)
        self.assertTrue(50 < L < 56 and 75 < a < 85 and 60 < b < 72, (L, a, b))
        for r, g, bb in SAMPLES:
            back = CP.lab_to_rgb(*CP.rgb_to_lab(r, g, bb))
            for x, y in zip(back, (r, g, bb)):
                self.assertAlmostEqual(x, y, places=4)

    def test_cmyk(self):
        self.assertEqual(CP.rgb_to_cmyk(0, 0, 0), (0.0, 0.0, 0.0, 1.0))
        c = CP.rgb_to_cmyk(1, 0, 0)
        self.assertEqual(tuple(round(x, 6) for x in c), (0, 1, 1, 0))

    def test_pick_at_the_marker_gives_the_colour_back(self):
        """For every mode: clicking the field where its marker is and the
        strip where its handle is keeps the colour."""
        for mode, (r, g, b) in itertools.product(MODES, SAMPLES):
            c = CP.Color.from_rgb(r, g, b)
            field = CP.Field.__new__(CP.Field)
            field.mode, field.color = mode, c
            x, y = CP.Field.xy(field)
            strip = CP.Strip.__new__(CP.Strip)
            strip.mode, strip.color = mode, c
            z = CP.Strip.value(strip)
            got = CP.apply_pick(mode, c, x=x, y=y)
            got = CP.apply_pick(mode, got, z=z)
            for u, w in zip(got.rgb(), c.rgb()):
                self.assertAlmostEqual(u, w, delta=0.004, msg="%s %s" % (mode, (r, g, b)))

    def test_web_safe(self):
        self.assertEqual(CP.web_safe(0.55), 0.6)
        self.assertEqual(CP.web_safe(0.0), 0.0)


@unittest.skipUnless(HAVE_QT, "needs PyQt5")
class Dialog(unittest.TestCase):
    def test_fields_follow(self):
        d = CP.ColorPickerDialog(CP.Color.from_rgb(1, 0, 0))
        self.assertEqual(d.hex.text(), "ff0000")
        self.assertEqual(d.spins["h"].value(), 0)
        self.assertEqual(d.spins["s"].value(), 100)
        d.hex.setText("#3366cc")
        d._hex()
        self.assertEqual((d.spins["r"].value(), d.spins["g"].value(), d.spins["bl"].value()), (51, 102, 204))
        d.spins["v"].setValue(50)                      # typed B = 50%
        self.assertEqual(d.color.rgb255()[2], 128)
        d.web.setChecked(True)
        self.assertTrue(all(v % 51 == 0 for v in d.color.rgb255()))

    def test_every_mode_paints(self):
        d = CP.ColorPickerDialog(CP.Color.from_rgb(0.3, 0.5, 0.7))
        for m in MODES:
            d._mode(m)
            img = d.field.grab().toImage()
            self.assertFalse(img.isNull())
            d.strip.grab()
            d.field.picked.emit(0.25, 0.75)
            d.strip.picked.emit(0.4)

    def test_swatches(self):
        added = []
        d = CP.ColorPickerDialog(CP.Color.from_rgb(0, 0, 0), on_swatch=added.append)
        d._add_swatch()
        self.assertEqual(len(added), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
