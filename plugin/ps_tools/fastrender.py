# -*- coding: utf-8 -*-
"""Krita PS (engine level 6): the Photoshop-like rendering speed-ups of the
engine (krita/patches/0011, kis_ps_perf.h) - composite caches below and
above the layer being painted / changed, the area visible on the canvas
rendered first, reused clipping buffers.

The engine reads the switches from the application property
"ps_tools_perf" (passed on by the psfilters plugin). The menu option
Tools > Scripts > "Fast rendering (experimental)" turns them all on or
off, so a speed-up that misbehaves can be switched off without a new
build."""
from krita import Krita

GROUP = "ps_tools"
KEY_ON = "fastRendering"
ALL = 0xF          # psPerf::AllFlags


def available():
    from . import core
    return core.engine_level() >= 6


def enabled():
    return Krita.instance().readSetting(GROUP, KEY_ON, "true") == "true"


def flags():
    return ALL if enabled() else 0


def apply():
    """Tell the engine (cheap: only a changed value reaches it)."""
    if not available():
        return
    try:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app.property("ps_tools_perf") != flags():
            app.setProperty("ps_tools_perf", flags())
    except Exception:
        pass


def set_enabled(on):
    Krita.instance().writeSetting(GROUP, KEY_ON, "true" if on else "false")
    apply()
