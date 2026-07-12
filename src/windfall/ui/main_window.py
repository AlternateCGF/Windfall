"""Main window: owns the poller thread and fans snapshots out to the panels."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QThread
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QDockWidget,
    QLineEdit,
    QMainWindow,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.poller import Poller, Snapshot
from .connection_bar import ConnectionBar
from .map.map_panel import MapPanel
from .panels.actors_panel import ActorsPanel
from .panels.camera_panel import CameraPanel
from .panels.link_panel import LinkPanel
from .panels.inventory_panel import InventoryPanel
from .panels.movie_panel import MoviePanel

# Height offset for "eye to actor" so the camera sits just above the object, not inside it.
_EYE_ABOVE = 200.0

# Keyboard orbit rates while locked on an actor, per full-speed key tick
# (scaled by the camera panel's eased velocity; ~60 ticks/s at 0.1 scale).
_ORBIT_YAW_RATE = 15.0  # degrees -> ~90°/s at full speed
_ORBIT_PITCH_RATE = 12.0  # degrees -> ~72°/s
_ORBIT_DIST_RATE = 170.0  # world units -> ~1000 u/s


class MainWindow(QMainWindow):
    def __init__(self, hz: int = 30) -> None:
        super().__init__()
        self.setWindowTitle("Windfall")
        self.resize(920, 640)

        self._bar = ConnectionBar()

        # Start the poller first so panels can wire holds into it.
        self._start_poller(hz)

        self._map_panel = MapPanel(self._poller)
        self._link_panel = LinkPanel(self._poller)
        self._camera_panel = CameraPanel(self._poller)
        self._map_panel.focus_requested.connect(
            lambda _name, x, z: self._camera_panel.focus_world(x, z)
        )
        self._map_panel.actor_focus_requested.connect(self._on_map_actor_focus)
        self._actors_panel = ActorsPanel(self._poller)
        self._movie_panel = MoviePanel(self._poller)
        self._inventory_panel = InventoryPanel(self._poller)
        self._actors_panel.aim_requested.connect(
            lambda x, y, z: self._camera_panel.fly_to(center=(x, y, z))
        )
        self._actors_panel.eye_requested.connect(
            lambda x, y, z: self._camera_panel.fly_to(eye=(x, y + _EYE_ABOVE, z))
        )
        self._actors_panel.focus_requested.connect(
            lambda x, y, z: self._camera_panel.fly_to(
                eye=(x, y + _EYE_ABOVE, z), center=(x, y, z)
            )
        )
        self._actors_panel.lock_state_changed.connect(self._on_lock_state_changed)
        self._map_panel.orbit_drag_delta.connect(
            self._actors_panel.adjust_orbit_angles
        )

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._bar)
        layout.addWidget(self._map_panel, stretch=1)
        self.setCentralWidget(central)

        # Side panels live in one tabbed dock section, browser-style: tabs along the top.
        self.setDockOptions(
            QMainWindow.DockOption.AllowTabbedDocks | QMainWindow.DockOption.AnimatedDocks
        )
        self.setTabPosition(
            Qt.DockWidgetArea.RightDockWidgetArea, QTabWidget.TabPosition.North
        )
        docks = []
        for title, panel in (
            ("Link", self._link_panel),
            ("Camera", self._camera_panel),
            ("Actors", self._actors_panel),
            ("Movie", self._movie_panel),
            ("Inventory", self._inventory_panel),
        ):
            dock = QDockWidget(title, self)
            dock.setWidget(panel)
            dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            docks.append(dock)
        for prev, nxt in zip(docks, docks[1:]):
            self.tabifyDockWidget(prev, nxt)
        docks[0].raise_()  # show the first tab initially

        # While locked on an actor the poller rewrites eye/center every tick, so
        # free-fly key nudges would be snapped back; steer the orbit instead.
        self._camera_panel.set_orbit_handler(self._orbit_key_handler)
        # Fractional remainders: the orbit sliders/spinbox are integer-backed, so
        # sub-unit per-tick deltas must accumulate or the eased ramp does nothing.
        self._orbit_frac = [0.0, 0.0, 0.0]  # yaw, pitch, dist

        # Intercept scroll wheel globally: when locked on, redirect to orbit distance.
        QApplication.instance().installEventFilter(self)

    # ---- poller thread ------------------------------------------------------
    def _start_poller(self, hz: int) -> None:
        self._thread = QThread(self)
        self._poller = Poller(hz=hz)
        self._poller.moveToThread(self._thread)
        self._thread.started.connect(self._poller.start)
        self._poller.snapshot.connect(self._on_snapshot)
        self._thread.start()

    def _on_map_actor_focus(self, actor) -> None:
        """Map dot double-clicked: Link → restore camera; anything else → lock on."""
        if actor.name == "Link":
            self._actors_panel.select_actor(actor.address)
            self._actors_panel.release_all_locks()
            self._camera_panel.restore()
            self._poller.clear_camera_holds()
        else:
            self._actors_panel.lock_on_actor(actor.address)

    def _on_lock_state_changed(self, locked: bool) -> None:
        """Toggle orbit drag mode on the map when lock state changes."""
        self._map_panel.set_orbit_drag_mode(locked)
        self._orbit_frac = [0.0, 0.0, 0.0]

    def _orbit_key_handler(self, fwd: float, right: float, up: float, scale: float) -> bool:
        """Keyboard fly while actor-locked: ←/→ orbit yaw, PgUp/PgDn pitch, ↑/↓ dolly."""
        if not self._actors_panel.is_locked:
            return False
        frac = self._orbit_frac
        frac[0] += right * _ORBIT_YAW_RATE * scale
        frac[1] += up * _ORBIT_PITCH_RATE * scale
        frac[2] += -fwd * _ORBIT_DIST_RATE * scale  # forward = closer
        yaw, frac[0] = int(frac[0]), frac[0] - int(frac[0])
        pitch, frac[1] = int(frac[1]), frac[1] - int(frac[1])
        dist, frac[2] = int(frac[2]), frac[2] - int(frac[2])
        if yaw or pitch:
            self._actors_panel.adjust_orbit_angles(yaw, pitch)
        if dist:
            self._actors_panel.adjust_orbit_distance_units(dist)
        return True

    def _on_snapshot(self, snap: Snapshot) -> None:
        self._bar.update_from(snap)
        self._link_panel.update_from(snap)
        self._camera_panel.update_from(snap)
        self._actors_panel.update_from(snap)
        self._movie_panel.update_from(snap)
        self._inventory_panel.update_from(snap)
        self._map_panel.apply_snapshot(snap)

    # ---- shutdown -----------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        try:
            self._poller.stop()
            self._thread.quit()
            self._thread.wait(1000)
        finally:
            super().closeEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # Wheel over the map while locked on: adjust orbit distance instead of map zoom.
        # Elsewhere (actor table, panels) the wheel scrolls normally.
        if (
            event.type() == QEvent.Type.Wheel
            and self._actors_panel.is_locked
            and isinstance(obj, QWidget)
            and self._map_panel.isAncestorOf(obj)
        ):
            delta = event.angleDelta().y()
            if delta != 0:
                self._actors_panel.adjust_orbit_distance(1 if delta > 0 else -1)
            return True
        # Arrow / PageUp / PageDown keys fly the camera, unless an input widget
        # that needs them (spinbox, slider, list, text field) has focus.
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            focus = QApplication.focusWidget()
            if not isinstance(focus, (QAbstractSpinBox, QSlider, QAbstractItemView, QLineEdit)):
                if event.isAutoRepeat():
                    # The hold timer does the moving; just swallow repeats of our keys.
                    return self._camera_panel.is_camera_key(event.key())
                pressed = event.type() == QEvent.Type.KeyPress
                if self._camera_panel.handle_key(event.key(), pressed):
                    return True
        elif event.type() == QEvent.Type.WindowDeactivate and obj is self:
            # Key releases won't reach us anymore — stop any keyboard fly.
            self._camera_panel.release_keys()
        return super().eventFilter(obj, event)
