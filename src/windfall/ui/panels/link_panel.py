"""Link data panel: editable position + facing angle with Apply button."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from ...core.poller import Poller, Snapshot

_POS_MIN, _POS_MAX = -999999.0, 999999.0


class LinkPanel(QWidget):
    """Editable Link position and facing angle.

    Accepts a ``Poller`` so the Apply button can issue one-shot writes.
    """

    def __init__(self, poller: Poller) -> None:
        super().__init__()
        self._poller = poller

        self._x = QDoubleSpinBox()
        self._y = QDoubleSpinBox()
        self._z = QDoubleSpinBox()
        self._angle = QDoubleSpinBox()

        for sb in (self._x, self._y, self._z):
            sb.setRange(_POS_MIN, _POS_MAX)
            sb.setDecimals(2)
            sb.setSingleStep(10.0)

        self._angle.setRange(0.0, 360.0)
        self._angle.setDecimals(1)
        self._angle.setSingleStep(5.0)
        self._angle.setSuffix("\u00b0")

        self._apply_btn = QPushButton("Apply")
        self._apply_btn.clicked.connect(self._on_apply)

        form = QFormLayout(self)
        form.addRow("X", self._x)
        form.addRow("Y", self._y)
        form.addRow("Z", self._z)
        form.addRow("Facing (\u00b0)", self._angle)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._apply_btn)
        form.addRow(row)

    def update_from(self, snap: Snapshot) -> None:
        blocked = self._x.blockSignals(True)
        try:
            if snap.link_pos is not None:
                x, y, z = snap.link_pos
                self._x.setValue(x)
                self._y.setValue(y)
                self._z.setValue(z)
            if snap.link_angle_deg is not None:
                self._angle.setValue(snap.link_angle_deg)
        finally:
            self._x.blockSignals(blocked)

    def _on_apply(self) -> None:
        self._poller.teleport_link_once(self._x.value(), self._y.value(), self._z.value())
        self._poller.write_link_angle(self._angle.value())
