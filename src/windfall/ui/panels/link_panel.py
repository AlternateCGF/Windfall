"""Link data panel: editable position + facing angle with Apply button."""

from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from ...core.poller import Poller, Snapshot

_POS_MIN, _POS_MAX = -999999.0, 999999.0

# The poller runs on its own thread and delivers snapshots via a queued signal,
# so one or more snapshots read *before* Apply's teleport lands in game memory
# can still be sitting in the event queue when Apply clears the dirty flag —
# the next one processed would snap the fields back to the pre-teleport
# position. Suppress live overwrites for a bit longer than
# Poller.teleport_link_once's forced-write duration (0.4s) to close that race.
_APPLY_SUPPRESS_S = 0.5


class LinkPanel(QWidget):
    """Editable Link position and facing angle.

    Accepts a ``Poller`` so the Apply button can issue one-shot writes.
    """

    def __init__(self, poller: Poller) -> None:
        super().__init__()
        self._poller = poller
        self._dirty = False
        self._suppress_until = 0.0

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

        # Mark dirty on user edits so the live snapshot doesn't overwrite them
        # before Apply is clicked.
        for sb in (self._x, self._y, self._z, self._angle):
            sb.valueChanged.connect(self._mark_dirty)

    def update_from(self, snap: Snapshot) -> None:
        if self._dirty or time.monotonic() < self._suppress_until:
            return
        fields = (self._x, self._y, self._z, self._angle)
        blocked = [sb.blockSignals(True) for sb in fields]
        try:
            if snap.link_pos is not None:
                x, y, z = snap.link_pos
                self._x.setValue(x)
                self._y.setValue(y)
                self._z.setValue(z)
            if snap.link_angle_deg is not None:
                self._angle.setValue(snap.link_angle_deg)
        finally:
            for sb, was_blocked in zip(fields, blocked):
                sb.blockSignals(was_blocked)

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _on_apply(self) -> None:
        self._poller.teleport_link_once(self._x.value(), self._y.value(), self._z.value())
        self._poller.write_link_angle(self._angle.value())
        self._dirty = False
        self._suppress_until = time.monotonic() + _APPLY_SUPPRESS_S
