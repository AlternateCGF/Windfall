"""Camera control panel — read/write the in-game camera with lock and smooth-transition support.

Layout:
  - Top row: current camera readout (eye, center, fovy, bank) — read-only, updated each tick
  - Middle: lock checkboxes + target spinboxes for eye/center, fovy/bank sliders
  - Bottom: smooth-transition controls (duration in frames, "Go" button to start lerp)
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.poller import Poller, Snapshot
from ..format import fmt_float


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


# Keyboard fly controls: key -> (forward, right, up) direction, scaled by Step.
_KEY_DIRECTIONS = {
    Qt.Key.Key_Up: (1.0, 0.0, 0.0),
    Qt.Key.Key_Down: (-1.0, 0.0, 0.0),
    Qt.Key.Key_Left: (0.0, -1.0, 0.0),
    Qt.Key.Key_Right: (0.0, 1.0, 0.0),
    Qt.Key.Key_PageUp: (0.0, 0.0, 1.0),
    Qt.Key.Key_PageDown: (0.0, 0.0, -1.0),
}

_KEY_TICK_MS = 16  # ~60 Hz movement while a key is held (or while gliding to a stop)
_KEY_TICK_SCALE = 0.1  # fraction of Step applied per tick at full speed (≈6× Step per second)
_KEY_ACCEL = 0.12  # per-tick blend toward the target velocity (ease-in)
_KEY_DECEL = 0.18  # per-tick blend toward zero after release (ease-out)
_KEY_STOP_EPS = 0.02  # velocity magnitude below which the glide stops


class CameraPanel(QWidget):
    """Camera control panel wired to the shared Poller."""

    def __init__(self, poller: Poller) -> None:
        super().__init__()
        self._poller = poller

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # ---- live readout ---------------------------------------------------
        live_box = QGroupBox("Current Camera")
        live_form = QFormLayout(live_box)
        self._live_eye = QLabel("—")
        self._live_center = QLabel("—")
        self._live_fovy = QLabel("—")
        self._live_bank = QLabel("—")
        for lbl in (self._live_eye, self._live_center, self._live_fovy, self._live_bank):
            lbl.setFont(mono)
        live_form.addRow("Eye", self._live_eye)
        live_form.addRow("Center", self._live_center)
        live_form.addRow("FoV Y", self._live_fovy)
        live_form.addRow("Bank", self._live_bank)
        root.addWidget(live_box)

        # ---- target controls ------------------------------------------------
        tgt_box = QGroupBox("Target (lock to override)")
        tgt_root = QVBoxLayout(tgt_box)

        # Eye lock + XYZ
        eye_row = QHBoxLayout()
        self._eye_lock = QCheckBox("Eye lock")
        self._eye_lock.toggled.connect(self._on_eye_lock_toggled)
        eye_row.addWidget(self._eye_lock)
        self._eye_x = self._make_spin(eye_row)
        self._eye_y = self._make_spin(eye_row)
        self._eye_z = self._make_spin(eye_row)
        tgt_root.addLayout(eye_row)

        # Center lock + XYZ
        ctr_row = QHBoxLayout()
        self._ctr_lock = QCheckBox("Ctr lock")
        self._ctr_lock.toggled.connect(self._on_ctr_lock_toggled)
        ctr_row.addWidget(self._ctr_lock)
        self._ctr_x = self._make_spin(ctr_row)
        self._ctr_y = self._make_spin(ctr_row)
        self._ctr_z = self._make_spin(ctr_row)
        tgt_root.addLayout(ctr_row)

        # FoV slider
        fovy_row = QHBoxLayout()
        self._fovy_lock = QCheckBox("FoV lock")
        self._fovy_lock.toggled.connect(self._on_fovy_lock_toggled)
        fovy_row.addWidget(self._fovy_lock)
        self._fovy_slider = QSlider(Qt.Orientation.Horizontal)
        self._fovy_slider.setRange(10, 170)
        self._fovy_slider.setValue(60)
        self._fovy_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._fovy_slider.setTickInterval(10)
        self._fovy_slider.valueChanged.connect(self._on_fovy_slider)
        fovy_row.addWidget(self._fovy_slider, stretch=1)
        self._fovy_val = QLabel("60")
        self._fovy_val.setMinimumWidth(36)
        fovy_row.addWidget(self._fovy_val)
        tgt_root.addLayout(fovy_row)

        # Bank slider
        bank_row = QHBoxLayout()
        self._bank_lock = QCheckBox("Bank")
        self._bank_lock.toggled.connect(self._on_bank_lock_toggled)
        bank_row.addWidget(self._bank_lock)
        self._bank_slider = QSlider(Qt.Orientation.Horizontal)
        self._bank_slider.setRange(-180, 180)
        self._bank_slider.setValue(0)
        self._bank_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._bank_slider.setTickInterval(30)
        self._bank_slider.valueChanged.connect(self._on_bank_slider)
        bank_row.addWidget(self._bank_slider, stretch=1)
        self._bank_val = QLabel("0")
        self._bank_val.setMinimumWidth(36)
        bank_row.addWidget(self._bank_val)
        tgt_root.addLayout(bank_row)

        root.addWidget(tgt_box)

        # ---- move controls ----------------------------------------------------
        move_box = QGroupBox("Move Camera  (arrow keys · PgUp/PgDn = up/down)")
        move_root = QHBoxLayout(move_box)

        # Arrow pad: forward/back/left/right relative to where the camera faces.
        pad = QGridLayout()
        pad.setSpacing(2)

        def _arrow(text: str, tip: str, fwd: float, right: float, up: float) -> QPushButton:
            b = QPushButton(text)
            b.setFixedSize(36, 28)
            b.setToolTip(f"{tip} (hold to repeat)")
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(250)
            b.setAutoRepeatInterval(50)
            b.clicked.connect(lambda: self._nudge(fwd, right, up))
            return b

        pad.addWidget(_arrow("↑", "Move forward", 1, 0, 0), 0, 1)
        pad.addWidget(_arrow("←", "Move left", 0, -1, 0), 1, 0)
        pad.addWidget(_arrow("↓", "Move backward", -1, 0, 0), 1, 1)
        pad.addWidget(_arrow("→", "Move right", 0, 1, 0), 1, 2)
        move_root.addLayout(pad)

        lift = QGridLayout()
        lift.setSpacing(2)
        lift.addWidget(_arrow("▲", "Move up", 0, 0, 1), 0, 0)
        lift.addWidget(_arrow("▼", "Move down", 0, 0, -1), 1, 0)
        move_root.addLayout(lift)

        move_root.addSpacing(8)
        move_root.addWidget(QLabel("Step:"))
        self._move_step = QDoubleSpinBox()
        self._move_step.setRange(1.0, 10000.0)
        self._move_step.setValue(100.0)
        self._move_step.setDecimals(0)
        self._move_step.setSingleStep(50.0)
        self._move_step.setToolTip("World units moved per click.")
        move_root.addWidget(self._move_step)
        move_root.addStretch(1)
        root.addWidget(move_box)

        # ---- smooth transition ----------------------------------------------
        trans_box = QGroupBox("Smooth Transition")
        trans_row = QHBoxLayout(trans_box)
        trans_row.addWidget(QLabel("Duration (frames):"))
        self._trans_frames = QSpinBox()
        self._trans_frames.setRange(1, 300)
        self._trans_frames.setValue(30)
        trans_row.addWidget(self._trans_frames)
        self._go_btn = QPushButton("Go")
        self._go_btn.clicked.connect(self._on_go)
        trans_row.addWidget(self._go_btn)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        trans_row.addWidget(self._stop_btn)
        trans_row.addStretch(1)
        root.addWidget(trans_box)

        root.addStretch(1)

        # lerp state
        self._lerp_active = False
        self._lerp_frame = 0
        self._lerp_total = 1
        self._lerp_eye0 = (0.0, 0.0, 0.0)
        self._lerp_eye1 = (0.0, 0.0, 0.0)
        self._lerp_ctr0 = (0.0, 0.0, 0.0)
        self._lerp_ctr1 = (0.0, 0.0, 0.0)
        self._last_eye: tuple[float, float, float] | None = None
        self._last_ctr: tuple[float, float, float] | None = None
        # When set, the finishing lerp checks the matching lock so the target keeps
        # being held instead of handing the camera back to the game.
        self._lock_center_on_finish = False
        self._lock_eye_on_finish = False

        # Keyboard fly state: keys currently held, the smoothed velocity (in
        # forward/right/up space), and the tick timer driving movement.
        self._held_keys: set[int] = set()
        self._key_vel = [0.0, 0.0, 0.0]
        self._key_timer = QTimer(self)
        self._key_timer.setInterval(_KEY_TICK_MS)
        self._key_timer.timeout.connect(self._on_key_tick)
        # Optional delegate: handler(fwd, right, up, scale) -> bool. When it
        # returns True (e.g. camera is orbit-locked on an actor), the tick is
        # routed there instead of the free-fly nudge.
        self._orbit_handler = None

    # ---- helpers ------------------------------------------------------------
    def _make_spin(self, layout: QHBoxLayout) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setRange(-999999, 999999)
        sb.setDecimals(1)
        sb.setSingleStep(100.0)
        sb.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        layout.addWidget(sb)
        return sb

    def _sync_spin(self, sb: QDoubleSpinBox, val: float) -> None:
        if abs(sb.value() - val) > 0.05:
            sb.setValue(val)

    # ---- called by MainWindow each tick -------------------------------------
    def update_from(self, snap: Snapshot) -> None:
        # Dolphin/the game closed: every hold is now pinning a dead pointer. Release
        # them (restore() is a no-op past the first tick, since unchecking an already-
        # unchecked box doesn't re-fire toggled).
        if not snap.connected:
            self.restore()

        if snap.cam_eye is not None:
            self._last_eye = snap.cam_eye
            x, y, z = snap.cam_eye
            self._live_eye.setText(f"{fmt_float(x)}, {fmt_float(y)}, {fmt_float(z)}")
        else:
            self._last_eye = None
            self._live_eye.setText("—")

        if snap.cam_center is not None:
            self._last_ctr = snap.cam_center
            x, y, z = snap.cam_center
            self._live_center.setText(f"{fmt_float(x)}, {fmt_float(y)}, {fmt_float(z)}")
        else:
            self._last_ctr = None
            self._live_center.setText("—")

        self._live_fovy.setText(fmt_float(snap.cam_fovy, 1))
        self._live_bank.setText(fmt_float(snap.cam_bank_deg, 1))

        # populate spinboxes from live values on first connect (when unlocked)
        if snap.cam_eye is not None and not self._eye_lock.isChecked():
            x, y, z = snap.cam_eye
            self._sync_spin(self._eye_x, x)
            self._sync_spin(self._eye_y, y)
            self._sync_spin(self._eye_z, z)
        if snap.cam_center is not None and not self._ctr_lock.isChecked():
            x, y, z = snap.cam_center
            self._sync_spin(self._ctr_x, x)
            self._sync_spin(self._ctr_y, y)
            self._sync_spin(self._ctr_z, z)
        if snap.cam_fovy is not None and not self._fovy_lock.isChecked():
            self._fovy_slider.setValue(int(snap.cam_fovy))
        if snap.cam_bank_deg is not None and not self._bank_lock.isChecked():
            self._bank_slider.setValue(int(snap.cam_bank_deg))

        # advance lerp if active
        if self._lerp_active:
            self._lerp_frame += 1
            t = min(1.0, self._lerp_frame / self._lerp_total)
            ease = _smoothstep(t)
            ex = _lerp(self._lerp_eye0[0], self._lerp_eye1[0], ease)
            ey = _lerp(self._lerp_eye0[1], self._lerp_eye1[1], ease)
            ez = _lerp(self._lerp_eye0[2], self._lerp_eye1[2], ease)
            cx = _lerp(self._lerp_ctr0[0], self._lerp_ctr1[0], ease)
            cy = _lerp(self._lerp_ctr0[1], self._lerp_ctr1[1], ease)
            cz = _lerp(self._lerp_ctr0[2], self._lerp_ctr1[2], ease)
            self._poller.set_eye_hold(ex, ey, ez)
            self._poller.set_center_hold(cx, cy, cz)
            if self._lerp_frame >= self._lerp_total:
                self._on_stop()

    # ---- lock callbacks -----------------------------------------------------
    def _any_lock_active(self) -> bool:
        return (
            self._eye_lock.isChecked()
            or self._ctr_lock.isChecked()
            or self._fovy_lock.isChecked()
            or self._bank_lock.isChecked()
        )

    def _on_eye_lock_toggled(self, on: bool) -> None:
        if on:
            self._poller.set_eye_hold(
                self._eye_x.value(), self._eye_y.value(), self._eye_z.value()
            )
        else:
            self._poller.clear_hold(self._poller._EYE_HOLD_KEY)
            self._sync_pause()

    def _on_ctr_lock_toggled(self, on: bool) -> None:
        if on:
            self._poller.set_center_hold(
                self._ctr_x.value(), self._ctr_y.value(), self._ctr_z.value()
            )
        else:
            self._poller.clear_hold(self._poller._CENTER_HOLD_KEY)
            self._sync_pause()

    def _on_fovy_lock_toggled(self, on: bool) -> None:
        if on:
            self._poller.set_fovy_hold(float(self._fovy_slider.value()))
        else:
            self._poller.clear_hold(self._poller._FOVY_HOLD_KEY)
            self._sync_pause()

    def _on_bank_lock_toggled(self, on: bool) -> None:
        if on:
            self._poller.set_bank_hold(float(self._bank_slider.value()))
        else:
            self._poller.clear_hold(self._poller._BANK_HOLD_KEY)
            self._sync_pause()

    def _sync_pause(self) -> None:
        """Pause the camera while any lock is active, unpause when all are off
        (deferring to the poller, which also accounts for active actor tracking)."""
        if self._any_lock_active():
            self._poller._ensure_camera_paused()
        else:
            self._poller._release_freeze_if_idle()

    def _on_fovy_slider(self, val: int) -> None:
        self._fovy_val.setText(str(val))
        if self._fovy_lock.isChecked():
            self._poller.set_fovy_hold(float(val))

    def _on_bank_slider(self, val: int) -> None:
        self._bank_val.setText(str(val))
        if self._bank_lock.isChecked():
            self._poller.set_bank_hold(float(val))

    # ---- keyboard fly -------------------------------------------------------
    def set_orbit_handler(self, handler) -> None:
        """Install a delegate for key ticks while the camera is actor-locked."""
        self._orbit_handler = handler

    def is_camera_key(self, key: int) -> bool:
        return key in _KEY_DIRECTIONS

    def handle_key(self, key: int, pressed: bool) -> bool:
        """Track arrow/PageUp/PageDown holds; the tick timer does the moving.

        Returns True when the key belongs to the camera (caller should consume it).
        """
        if key not in _KEY_DIRECTIONS:
            return False
        if pressed:
            self._held_keys.add(key)
            if not self._key_timer.isActive():
                self._key_timer.start()
        else:
            # Keep the timer running: the tick eases the velocity down to a stop.
            self._held_keys.discard(key)
        return True

    def release_keys(self) -> None:
        """Drop all held keys and halt (window lost focus, releases won't arrive)."""
        self._held_keys.clear()
        self._key_vel = [0.0, 0.0, 0.0]
        self._key_timer.stop()

    def _on_key_tick(self) -> None:
        # Target velocity from the keys held right now (normalized so diagonals
        # aren't faster than a single axis).
        tf = tr = tu = 0.0
        for key in self._held_keys:
            f, r, u = _KEY_DIRECTIONS[key]
            tf += f
            tr += r
            tu += u
        mag = math.sqrt(tf * tf + tr * tr + tu * tu)
        if mag > 1.0:
            tf, tr, tu = tf / mag, tr / mag, tu / mag

        # Ease the actual velocity toward the target: ramp up on press, glide
        # out on release.
        blend = _KEY_ACCEL if self._held_keys else _KEY_DECEL
        v = self._key_vel
        v[0] += (tf - v[0]) * blend
        v[1] += (tr - v[1]) * blend
        v[2] += (tu - v[2]) * blend

        speed = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
        if not self._held_keys and speed < _KEY_STOP_EPS:
            self._key_vel = [0.0, 0.0, 0.0]
            self._key_timer.stop()
            return
        if speed > 0.0:
            if self._orbit_handler is not None and self._orbit_handler(
                v[0], v[1], v[2], _KEY_TICK_SCALE
            ):
                return
            self._nudge(v[0], v[1], v[2], scale=_KEY_TICK_SCALE)

    # ---- move controls ------------------------------------------------------
    def _nudge(self, fwd: float, right: float, up: float, scale: float = 1.0) -> None:
        """Shift eye and center together by one step, relative to camera facing.

        Forward/right stay in the horizontal (XZ) plane so up/down remains a
        separate axis. Locks eye+center so the game doesn't pull the camera back.
        """
        ex, ey, ez = self._eye_x.value(), self._eye_y.value(), self._eye_z.value()
        cx, cy, cz = self._ctr_x.value(), self._ctr_y.value(), self._ctr_z.value()

        # Facing direction projected onto XZ; fall back to +Z if looking straight down.
        fx, fz = cx - ex, cz - ez
        length = math.hypot(fx, fz)
        if length < 1e-6:
            fx, fz = 0.0, 1.0
        else:
            fx, fz = fx / length, fz / length
        # Right-hand perpendicular in XZ.
        rx, rz = -fz, fx

        step = self._move_step.value() * scale
        dx = (fx * fwd + rx * right) * step
        dy = up * step
        dz = (fz * fwd + rz * right) * step

        ex, ey, ez = ex + dx, ey + dy, ez + dz
        cx, cy, cz = cx + dx, cy + dy, cz + dz
        self._eye_x.setValue(ex)
        self._eye_y.setValue(ey)
        self._eye_z.setValue(ez)
        self._ctr_x.setValue(cx)
        self._ctr_y.setValue(cy)
        self._ctr_z.setValue(cz)

        # Checking a lock sets its hold; if already locked, refresh the hold directly.
        if self._eye_lock.isChecked():
            self._poller.set_eye_hold(ex, ey, ez)
        else:
            self._eye_lock.setChecked(True)
        if self._ctr_lock.isChecked():
            self._poller.set_center_hold(cx, cy, cz)
        else:
            self._ctr_lock.setChecked(True)

    # ---- smooth transition --------------------------------------------------
    def _on_go(self) -> None:
        """Start a smooth lerp from the current live camera to the target values."""
        if self._last_eye is None or self._last_ctr is None:
            return
        self._lerp_eye0 = self._last_eye
        self._lerp_ctr0 = self._last_ctr
        self._lerp_eye1 = (self._eye_x.value(), self._eye_y.value(), self._eye_z.value())
        self._lerp_ctr1 = (self._ctr_x.value(), self._ctr_y.value(), self._ctr_z.value())
        self._lerp_frame = 0
        self._lerp_total = max(1, self._trans_frames.value())
        self._lerp_active = True
        self._go_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    def _on_stop_clicked(self) -> None:
        # A user-initiated Stop cancels the whole move, including pending locks.
        self._lock_center_on_finish = False
        self._lock_eye_on_finish = False
        self._on_stop()

    def _on_stop(self) -> None:
        self._lerp_active = False
        lock_eye = self._lock_eye_on_finish
        lock_center = self._lock_center_on_finish
        self._lock_eye_on_finish = False
        self._lock_center_on_finish = False

        if lock_eye:
            ex, ey, ez = self._lerp_eye1
            self._eye_x.setValue(ex)
            self._eye_y.setValue(ey)
            self._eye_z.setValue(ez)
            if self._eye_lock.isChecked():
                self._poller.set_eye_hold(ex, ey, ez)
            else:
                self._eye_lock.setChecked(True)  # sets the hold and keeps the camera paused
        elif self._eye_lock.isChecked():
            # Restore the user's own eye hold (the lerp was overwriting it).
            self._poller.set_eye_hold(
                self._eye_x.value(), self._eye_y.value(), self._eye_z.value()
            )
        else:
            self._poller.clear_hold(self._poller._EYE_HOLD_KEY)

        if lock_center:
            cx, cy, cz = self._lerp_ctr1
            self._ctr_x.setValue(cx)
            self._ctr_y.setValue(cy)
            self._ctr_z.setValue(cz)
            if self._ctr_lock.isChecked():
                self._poller.set_center_hold(cx, cy, cz)
            else:
                self._ctr_lock.setChecked(True)
        elif self._ctr_lock.isChecked():
            self._poller.set_center_hold(
                self._ctr_x.value(), self._ctr_y.value(), self._ctr_z.value()
            )
        else:
            self._poller.clear_hold(self._poller._CENTER_HOLD_KEY)

        self._sync_pause()
        self._go_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    # ---- fly-to (map focus / actor focus) -------------------------------------
    def fly_to(
        self,
        eye: tuple[float, float, float] | None = None,
        center: tuple[float, float, float] | None = None,
    ) -> None:
        """Smoothly move the camera eye and/or look-at center to world points.

        Components not given stay where they are. On arrival each moved component
        is locked (Eye/Ctr lock) so the game can't pull it back; uncheck to release.
        """
        if self._last_eye is None or self._last_ctr is None:
            return
        self._lerp_eye0 = self._last_eye
        self._lerp_eye1 = eye if eye is not None else self._last_eye
        self._lerp_ctr0 = self._last_ctr
        self._lerp_ctr1 = center if center is not None else self._last_ctr
        self._lerp_frame = 0
        self._lerp_total = max(1, self._trans_frames.value())
        self._lerp_active = True
        self._lock_eye_on_finish = eye is not None
        self._lock_center_on_finish = center is not None
        self._go_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    def focus_world(self, x: float, z: float, y: float = 0.0) -> None:
        """Swing the camera to look at a world point (eye stays put)."""
        self.fly_to(center=(x, y, z))

    def restore(self) -> None:
        """Uncheck all lock buttons — hands full control back to the game."""
        for btn in (self._eye_lock, self._ctr_lock, self._fovy_lock, self._bank_lock):
            btn.setChecked(False)
