"""Link data panel. M0: live read-only readout of position + facing angle (the smoke test)."""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFormLayout, QLabel, QWidget

from ...core.poller import Snapshot
from ..format import fmt_float


class LinkPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)

        self._x = QLabel("—")
        self._y = QLabel("—")
        self._z = QLabel("—")
        self._angle = QLabel("—")
        for lbl in (self._x, self._y, self._z, self._angle):
            lbl.setFont(mono)

        form = QFormLayout(self)
        form.addRow("X", self._x)
        form.addRow("Y", self._y)
        form.addRow("Z", self._z)
        form.addRow("Facing (°)", self._angle)

    def update_from(self, snap: Snapshot) -> None:
        if snap.link_pos is None:
            self._x.setText("—")
            self._y.setText("—")
            self._z.setText("—")
        else:
            x, y, z = snap.link_pos
            self._x.setText(fmt_float(x))
            self._y.setText(fmt_float(y))
            self._z.setText(fmt_float(z))
        self._angle.setText(fmt_float(snap.link_angle_deg))
