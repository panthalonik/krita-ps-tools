"""Camera Raw Filter (camera_raw.py): the plugin's parameters match the C++
filter's settings (ps_cameraraw.h), params <-> filter properties round trip,
and the panels (real PyQt5 when available) load / give back the params."""
import pathlib
import re
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = ROOT.parent
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

from ps_tools import camera_raw as CR             # noqa: E402

KERNEL = REPO / "krita" / "plugins" / "filters" / "psfilters" / "ps_cameraraw.h"


def kernel_names():
    text = KERNEL.read_text()
    block = text[text.index("settingNames(int &count)"):]
    block = block[:block.index("};")]
    return set(re.findall(r'"([a-z0-9_]+)"', block))


class Keys(unittest.TestCase):
    def test_same_settings_as_the_filter(self):
        self.assertEqual(set(CR.DEFAULTS), kernel_names())

    def test_props_round_trip(self):
        p = CR.defaults()
        p.update(exposure=1.25, contrast=-30, mix_hue_blue=40, cg_shadows_hue=210, cg_shadows_sat=55,
                 vig_style=2, sharp_radius=1.7)
        p["curve_red"] = [[0.0, 0.1], [0.5, 0.62], [1.0, 1.0]]
        props = CR.build_props(p)
        self.assertEqual(props["curve_red"], "0,0.1;0.5,0.62;1,1")
        self.assertEqual(props["curve_rgb"], "0,0;1,1")
        back = CR.params_from_props(props)
        for k in ("exposure", "contrast", "mix_hue_blue", "cg_shadows_hue", "cg_shadows_sat",
                  "vig_style", "sharp_radius"):
            self.assertAlmostEqual(back[k], p[k], places=4, msg=k)
        self.assertEqual(back["curve_red"], p["curve_red"])

    def test_bad_curve_text(self):
        self.assertEqual(CR.parse_curve("garbage"), CR.IDENTITY)
        self.assertEqual(CR.parse_curve("0,0;x,1;1,1.5"), [[0.0, 0.0], [1.0, 1.0]])

    def test_white_balance_of_a_grey_is_zero(self):
        self.assertEqual(CR.wb_from_color(0.3, 0.3, 0.3), (0, 0))
        t, ti = CR.wb_from_color(0.4, 0.3, 0.2)          # warm colour: cool it down
        self.assertLess(t, 0)
        t, ti = CR.wb_from_color(0.3, 0.4, 0.3)          # greenish: tint towards magenta
        self.assertGreater(ti, 0)


@unittest.skipUnless(HAVE_QT, "needs PyQt5")
class Panel(unittest.TestCase):
    def test_load_and_read_back(self):
        panel = CR.CameraRawPanel()
        p = CR.defaults()
        p.update(exposure=-0.75, highlights=-60, temperature=12, mix_sat_red=-20,
                 cg_midtones_hue=40, cg_midtones_sat=30, vig_style=1, grain_amount=25)
        p["curve_rgb"] = [[0.0, 0.0], [0.4, 0.3], [1.0, 1.0]]
        panel.set_params(p)
        got = panel.params()
        for k in ("exposure", "highlights", "temperature", "mix_sat_red", "cg_midtones_hue",
                  "cg_midtones_sat", "vig_style", "grain_amount"):
            self.assertEqual(got[k], p[k], k)
        self.assertEqual(got["curve_rgb"], p["curve_rgb"])
        self.assertEqual(panel.sliders["exposure"].value(), -0.75)

    def test_slider_changes_and_reset(self):
        panel = CR.CameraRawPanel()
        seen = []
        panel.changed.connect(lambda: seen.append(1))
        s = panel.sliders["clarity"]
        s.slider.setValue(35)
        self.assertEqual(panel.params()["clarity"], 35)
        self.assertTrue(seen)
        from PyQt5.QtCore import QEvent
        s.eventFilter(s.slider, QEvent(QEvent.MouseButtonDblClick))
        self.assertEqual(panel.params()["clarity"], 0)

    def test_color_grading_zones(self):
        panel = CR.CameraRawPanel()
        panel.zone_buttons.button(0).setChecked(True)      # shadows
        panel._zone_load()
        panel._wheel(200.0, 45.0)
        p = panel.params()
        self.assertEqual((p["cg_shadows_hue"], p["cg_shadows_sat"]), (200, 45))
        self.assertEqual(p["cg_midtones_sat"], 0)

    def test_point_curve_editing(self):
        panel = CR.CameraRawPanel()
        panel.curve_mode.setCurrentIndex(2)                # red
        panel.curve_view.points = [[0.0, 0.0], [0.5, 0.7], [1.0, 1.0]]
        panel._curve_changed()
        self.assertEqual(panel.params()["curve_red"], [[0.0, 0.0], [0.5, 0.7], [1.0, 1.0]])
        panel._reset_curve()
        self.assertEqual(panel.params()["curve_red"], CR.IDENTITY)

    def test_white_balance_custom(self):
        panel = CR.CameraRawPanel()
        panel.sliders["temperature"].slider.setValue(20)
        self.assertEqual(panel.params()["wb_mode"], "custom")
        panel.wb_mode.setCurrentIndex(0)
        panel._wb_mode(0)
        self.assertEqual(panel.params()["temperature"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
