# -*- coding: utf-8 -*-
"""QSlider that jumps to where you click (like Photoshop), and you can keep
dragging from there without releasing the button."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class JumpSlider(QSlider):
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            st = self.style()
            handle = st.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
            if not handle.contains(e.pos()):
                groove = st.subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
                if self.orientation() == Qt.Horizontal:
                    span = groove.width() - handle.width()
                    pos = e.pos().x() - groove.x() - handle.width() // 2
                else:
                    span = groove.height() - handle.height()
                    pos = e.pos().y() - groove.y() - handle.height() // 2
                v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                                   max(0, min(span, pos)), max(1, span),
                                                   opt.upsideDown)
                self.setValue(v)
                # the handle is under the mouse now: the normal press below
                # starts a drag from here
        super(JumpSlider, self).mousePressEvent(e)
