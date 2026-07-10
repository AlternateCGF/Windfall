"""Map panel: the interactive top-down map plus a small toolbar and coordinate readout."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtCore import Signal

from ...core.poller import Poller, Snapshot
from ..format import fmt_float
from .map_view import MapView


class MapPanel(QWidget):
    focus_requested = Signal(str, float, float)  # island name, world x, z
    actor_focus_requested = Signal(object)  # ActorInfo
    orbit_drag_delta = Signal(float, float)  # yaw_delta, pitch_delta (degrees)

    def __init__(self, poller: Poller):
        super().__init__()
        self._poller = poller
        self._view = MapView(poller)

        self._freeze = QCheckBox("Freeze on release")
        self._freeze.setToolTip("Keep holding Link at the dropped spot after you let go.")
        self._freeze.toggled.connect(self._on_freeze)

        self._follow = QCheckBox("Follow")
        self._follow.setChecked(True)
        self._follow.setToolTip("Keep the camera centered on Link as he moves.")
        self._follow.toggled.connect(self._on_follow)

        recenter = QPushButton("Recenter")
        recenter.clicked.connect(self._view.recenter)

        release = QPushButton("Release")
        release.setToolTip("Stop holding Link (hand control back to the game).")
        release.clicked.connect(poller.clear_position_hold)

        self._grid = QCheckBox("Grid")
        self._grid.setChecked(True)
        self._grid.toggled.connect(self._view.set_grid_visible)

        self._islands = QCheckBox("Islands")
        self._islands.setChecked(True)
        self._islands.setToolTip("Show/hide island name markers on the map.")
        self._islands.toggled.connect(self._view.set_islands_visible)

        self._actors = QCheckBox("Actors")
        self._actors.setChecked(True)
        self._actors.setToolTip("Show/hide live actor markers on the map.")
        self._actors.toggled.connect(self._view.set_actors_visible)

        self._collision = QCheckBox("Collision")
        self._collision.setChecked(True)
        self._collision.setToolTip("Show/hide the loaded collision geometry (island/terrain outlines).")
        self._collision.toggled.connect(self._view.set_collision_visible)

        self._last_link: Optional[tuple[float, float]] = None
        self._link_lbl = QLabel("Link: —")
        self._cursor_lbl = QLabel("Cursor: —")
        for lbl in (self._link_lbl, self._cursor_lbl):
            lbl.setStyleSheet("color:#9aa;")

        bar = QHBoxLayout()
        bar.addWidget(self._freeze)
        bar.addWidget(self._follow)
        bar.addWidget(recenter)
        bar.addWidget(release)
        bar.addWidget(self._grid)
        bar.addWidget(self._islands)
        bar.addWidget(self._actors)
        bar.addWidget(self._collision)
        bar.addStretch(1)
        bar.addWidget(self._link_lbl)
        bar.addWidget(QLabel("  "))
        bar.addWidget(self._cursor_lbl)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(bar)
        layout.addWidget(self._view, stretch=1)

        self._view.cursor_moved.connect(self._on_cursor)
        self._view.link_moved.connect(self._on_link)
        self._view.auto_follow_changed.connect(self._on_follow_changed)
        self._view.focus_requested.connect(self._on_focus_requested)
        self._view.actor_focus_requested.connect(self._on_actor_focus_requested)
        self._view.orbit_drag_delta.connect(self.orbit_drag_delta)

        self._hint = QLabel(
            "Left-drag Link to teleport · drag to pan · wheel to zoom"
            " · double-click an island or actor to aim the camera at it"
        )
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet("color:#667; padding:2px;")
        layout.addWidget(self._hint)

    def apply_snapshot(self, snap: Snapshot) -> None:
        self._view.apply_snapshot(snap)

    def set_orbit_drag_mode(self, enabled: bool) -> None:
        """Enable/disable orbit drag on the map view."""
        self._view.set_orbit_drag_mode(enabled)

    def _on_freeze(self, on: bool) -> None:
        self._view.freeze = on
        if not on:
            # Turning freeze off should let go immediately if we're not actively dragging.
            self._view.clear_hold_if_not_frozen()

    def _on_follow(self, on: bool) -> None:
        self._view.set_auto_follow(on)

    def _on_follow_changed(self, on: bool) -> None:
        self._follow.blockSignals(True)
        self._follow.setChecked(on)
        self._follow.blockSignals(False)

    def _on_focus_requested(self, name: str, x: float, z: float) -> None:
        self._hint.setText(f"Aiming camera at {name}…  (uncheck Ctr lock to release)")
        self.focus_requested.emit(name, x, z)

    def _on_actor_focus_requested(self, actor) -> None:
        self._hint.setText(f"Aiming camera at {actor.name}…  (uncheck Ctr lock to release)")
        self.actor_focus_requested.emit(actor)

    def _on_cursor(self, x: float, z: float) -> None:
        self._cursor_lbl.setText(f"Cursor: X={fmt_float(x, 0)}  Z={fmt_float(z, 0)}")

    def _on_link(self, x: float, z: float) -> None:
        self._last_link = (x, z)
        self._link_lbl.setText(f"Link: X={fmt_float(x, 0)}  Z={fmt_float(z, 0)}")
