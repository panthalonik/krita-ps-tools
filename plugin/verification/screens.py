"""Screenshots of the Camera Raw panels and the Color panel / picker, printed
as base64 PNG into the CI log (for a look without Krita). Not a test."""
import base64
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "verification"))
import fake_krita  # noqa: E402
fake_krita.install()
_pkg = types.ModuleType("ps_tools")
_pkg.__path__ = [str(ROOT / "ps_tools")]
sys.modules["ps_tools"] = _pkg
from PyQt5.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PyQt5.QtGui import QColor, QPalette  # noqa: E402
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget  # noqa: E402

app = QApplication.instance() or QApplication(["x"])
app.setStyle("Fusion")
pal = QPalette()
for role, c in ((QPalette.Window, "#535353"), (QPalette.WindowText, "#e0e0e0"), (QPalette.Base, "#3c3c3c"),
                (QPalette.Text, "#e0e0e0"), (QPalette.Button, "#5d5d5d"), (QPalette.ButtonText, "#e0e0e0"),
                (QPalette.Mid, "#9a9a9a")):
    pal.setColor(role, QColor(c))
app.setPalette(pal)
from ps_tools import camera_raw as CR, color_panel as CP  # noqa: E402


def dump(name, widget, w, h):
    widget.resize(w, h)
    widget.show()
    app.processEvents()
    img = widget.grab().toImage()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    print("@@SCREEN %s %s @@END" % (name, base64.b64encode(bytes(ba)).decode()))


panel = CR.CameraRawPanel()
p = CR.defaults()
p.update(exposure=0.35, highlights=-40, shadows=25, temperature=8, vibrance=15, cg_shadows_hue=210,
         cg_shadows_sat=35, clarity=20, sharp_amount=40)
panel.set_params(p)
for i, name in enumerate(("light", "color", "effects", "detail")):
    panel.tabs.setCurrentIndex(i)
    dump("cr_" + name, panel, 330, 900)
panel.tabs.setCurrentIndex(0)
panel.curve_mode.setCurrentIndex(1)
dump("cr_curve", panel, 330, 900)

dock = QWidget()
row = QHBoxLayout(dock)
fgbg = CP.FgBg()
field = CP.Field(None, 0)
strip = CP.Strip(None, 0, 14, arrows=False)
c = CP.Color(230, 0.75, 0.85)
field.set_state("h", c)
strip.set_state("h", c)
row.addWidget(fgbg)
row.addWidget(field, 1)
row.addWidget(strip)
dump("color_docker", dock, 320, 230)
dlg = CP.ColorPickerDialog(CP.Color(230, 0.6, 0.7))
dump("color_picker", dlg, 640, 380)
dlg._mode("L")
dump("color_picker_L", dlg, 640, 380)
