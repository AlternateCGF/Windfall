"""Actors panel — live table of loaded actors with camera and teleport actions.

Rows refresh from each snapshot (the poller re-enumerates ~6 Hz). Selection is kept
across refreshes by actor address. Actions on the selected actor:
  - Aim camera   : fly the look-at center to the actor (eye stays put)
  - Eye to actor : fly the camera eye to the actor (slightly above it)
  - Link here    : one-shot teleport Link to the actor
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtWidgets import QSpinBox

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.poller import Poller, Snapshot
from ...game.actors import ActorInfo, is_ambient

_COLS = ("Name", "Proc", "X", "Y", "Z", "Dist")


class ActorsPanel(QWidget):
    aim_requested = Signal(float, float, float)  # world x, y, z
    eye_requested = Signal(float, float, float)
    focus_requested = Signal(float, float, float)  # double-click: fly eye + center
    lock_state_changed = Signal(bool)  # True when any lock is active

    def __init__(self, poller: Poller) -> None:
        super().__init__()
        self._poller = poller
        self._actors: list[ActorInfo] = []
        self._row_addrs: list[int] = []
        self._link: Optional[tuple[float, float, float]] = None
        self._sort_column: int = 0
        self._sort_reverse: bool = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        top = QHBoxLayout()
        top.addWidget(QLabel("Filter:"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("name substring…")
        self._filter.setClearButtonEnabled(True)
        top.addWidget(self._filter, stretch=1)
        self._count_lbl = QLabel("0")
        top.addWidget(self._count_lbl)
        root.addLayout(top)

        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        mono = QFont("Consolas", 9)
        self._table.setFont(mono)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 110)
        for c in range(1, len(_COLS)):
            self._table.setColumnWidth(c, 62)
        self._table.horizontalHeader().sectionClicked.connect(self._on_column_clicked)
        root.addWidget(self._table, stretch=1)

        btns = QHBoxLayout()
        aim = QPushButton("Aim camera")
        aim.setToolTip("Swing the camera's look-at point to the selected actor.")
        aim.clicked.connect(self._on_aim)
        eye = QPushButton("Eye to actor")
        eye.setToolTip("Fly the camera eye to the selected actor (slightly above it).")
        eye.clicked.connect(self._on_eye)
        self._lock_btn = QPushButton("Lock on")
        self._lock_btn.setCheckable(True)
        self._lock_btn.setToolTip(
            "Continuously aim at the selected actor (the look-at point follows it)."
        )
        self._lock_btn.toggled.connect(self._on_lock_toggled)
        self._eye_lock_btn = QPushButton("Eye follow")
        self._eye_lock_btn.setCheckable(True)
        self._eye_lock_btn.setToolTip(
            "Continuously ride the camera position along with the selected actor."
        )
        self._eye_lock_btn.toggled.connect(self._on_eye_lock_toggled)
        tp = QPushButton("Link here")
        tp.setToolTip("Teleport Link to the selected actor.")
        tp.clicked.connect(self._on_teleport)
        for b in (aim, eye, self._lock_btn, self._eye_lock_btn, tp):
            btns.addWidget(b)
        self._locked_addr: Optional[int] = None
        self._locked_pid: Optional[int] = None
        self._eye_locked_addr: Optional[int] = None
        self._eye_locked_pid: Optional[int] = None
        btns.addStretch(1)
        root.addLayout(btns)

        smooth_row = QHBoxLayout()
        smooth_row.addWidget(QLabel("Follow smoothing:"))
        self._smooth = QSlider(Qt.Orientation.Horizontal)
        self._smooth.setRange(2, 100)  # % of remaining distance per tick; 100 = hard snap
        self._smooth.setValue(15)
        self._smooth.setToolTip(
            "How fast the lock-on follows: low = loose, cinematic trail; 100% = hard snap."
        )
        self._smooth.valueChanged.connect(self._on_smooth_changed)
        smooth_row.addWidget(self._smooth, stretch=1)
        self._smooth_val = QLabel("15%")
        self._smooth_val.setMinimumWidth(38)
        smooth_row.addWidget(self._smooth_val)
        root.addLayout(smooth_row)

        # Orbit controls for Eye follow: where the camera sits relative to the actor.
        # Yaw orbits around it, pitch tilts up/over, distance dollies in/out. Live while locked.
        orbit_row = QHBoxLayout()
        orbit_row.addWidget(QLabel("Dist:"))
        self._orbit_dist = QSpinBox()
        self._orbit_dist.setRange(50, 50000)
        self._orbit_dist.setValue(600)
        self._orbit_dist.setSingleStep(50)
        self._orbit_dist.valueChanged.connect(self._on_orbit_changed)
        orbit_row.addWidget(self._orbit_dist)
        orbit_row.addWidget(QLabel("Yaw:"))
        self._orbit_yaw = QSlider(Qt.Orientation.Horizontal)
        self._orbit_yaw.setRange(-180, 180)
        self._orbit_yaw.setValue(0)
        self._orbit_yaw.valueChanged.connect(self._on_orbit_changed)
        orbit_row.addWidget(self._orbit_yaw, stretch=2)
        self._orbit_yaw_val = QLabel("0°")
        self._orbit_yaw_val.setMinimumWidth(34)
        orbit_row.addWidget(self._orbit_yaw_val)
        orbit_row.addWidget(QLabel("Pitch:"))
        self._orbit_pitch = QSlider(Qt.Orientation.Horizontal)
        self._orbit_pitch.setRange(-85, 85)
        self._orbit_pitch.setValue(20)
        self._orbit_pitch.valueChanged.connect(self._on_orbit_changed)
        orbit_row.addWidget(self._orbit_pitch, stretch=1)
        self._orbit_pitch_val = QLabel("20°")
        self._orbit_pitch_val.setMinimumWidth(34)
        orbit_row.addWidget(self._orbit_pitch_val)
        for w in (self._orbit_dist, self._orbit_yaw, self._orbit_pitch):
            w.setToolTip(
                "Eye follow placement: distance from the actor, yaw to orbit around it,"
                " pitch to tilt above/below. Drag while locked to rotate the shot live."
            )
        self._orbit_rel = QCheckBox("Relative")
        self._orbit_rel.setToolTip(
            "Rotate the orbit with the actor's facing: yaw 0° stays in front,"
            " ±180° stays behind — a true chase cam that turns with the actor."
        )
        self._orbit_rel.toggled.connect(lambda _on: self._reapply_eye_track())
        orbit_row.addWidget(self._orbit_rel)
        root.addLayout(orbit_row)

        auto_row = QHBoxLayout()
        self._yaw_lock = QCheckBox("Yaw lock")
        self._yaw_lock.setToolTip(
            "Freeze the current view angle relative to the locked actor's facing —"
            " the camera direction then turns with the actor. Set up your shot first,"
            " then check this to hold it."
        )
        self._yaw_lock.toggled.connect(self._on_yaw_lock_toggled)
        auto_row.addWidget(self._yaw_lock)
        self._auto_lock = QCheckBox("Auto lock near Link")
        self._auto_lock.setToolTip(
            "Z-targeting: automatically lock the camera onto the nearest actor when Link"
            " comes within the radius; releases when he leaves. Manual Lock on wins."
        )
        self._auto_lock.toggled.connect(self._on_auto_lock_toggled)
        auto_row.addWidget(self._auto_lock)
        self._auto_radius = QSpinBox()
        self._auto_radius.setRange(100, 20000)
        self._auto_radius.setValue(1000)
        self._auto_radius.setSingleStep(100)
        self._auto_radius.setToolTip("Auto-lock trigger radius (world units) around Link.")
        auto_row.addWidget(self._auto_radius)
        self._auto_target_lbl = QLabel("")
        self._auto_target_lbl.setStyleSheet("color:#9aa;")
        auto_row.addWidget(self._auto_target_lbl, stretch=1)
        root.addLayout(auto_row)
        self._auto_addr: Optional[int] = None
        self._auto_pid: Optional[int] = None
        self._pending_select: Optional[int] = None
        self._yaw_lock_offset: Optional[tuple[float, float, float]] = None

        # Double-clicking a row locks on and eye-follows it — the most common action.
        self._table.itemDoubleClicked.connect(self._on_row_double_clicked)

    # ---- refresh --------------------------------------------------------------
    def update_from(self, snap: Snapshot) -> None:
        self._link = snap.link_pos
        text = self._filter.text().strip().lower()
        actors = [a for a in snap.actors if not text or text in a.name.lower()]
        self._sort_actors(actors)
        self._actors = actors

        addrs = [a.address for a in actors]
        if addrs != self._row_addrs:
            self._rebuild(actors)
            self._row_addrs = addrs
        else:
            self._update_cells(actors)

        if self._pending_select is not None and self._pending_select in addrs:
            pending = self._pending_select
            self._pending_select = None
            self.select_actor(pending)
        self._count_lbl.setText(f"{len(actors)}/{len(snap.actors)}")

        # If Dolphin/the game closes, every actor address is now invalid — release
        # every lock. This can't be folded into the "despawned" check below: while
        # disconnected, snap.actors is always [], so that check never sees a game
        # to compare against and would otherwise leave locks stuck on forever.
        if not snap.connected:
            if self.is_locked:
                self.release_all_locks()
            if self._auto_addr is not None:
                self._auto_addr = None
                self._auto_pid = None
                self._auto_target_lbl.setText("")
                self._poller.clear_center_track()
        # If a locked actor despawned, release its lock (the poller hold also
        # self-cancels via its pid check; this just syncs the buttons).
        elif snap.actors:
            live = {a.address for a in snap.actors}
            if self._locked_addr is not None and self._locked_addr not in live:
                self._lock_btn.setChecked(False)
            if self._eye_locked_addr is not None and self._eye_locked_addr not in live:
                self._eye_lock_btn.setChecked(False)
            if self._auto_addr is not None and self._auto_addr not in live:
                self._auto_addr = None
                self._auto_pid = None
                self._auto_target_lbl.setText("")

        self._update_auto_lock(snap)

    @staticmethod
    def _auto_eligible(a: ActorInfo) -> bool:
        """Skip Link himself and ambient/invisible actors that make poor lock targets."""
        return a.name != "Link" and not is_ambient(a.name)

    def _update_auto_lock(self, snap: Snapshot) -> None:
        """Z-targeting: lock the nearest eligible actor within the radius of Link;
        sticky until it leaves radius x1.25. Manual Lock on always wins."""
        if (
            not self._auto_lock.isChecked()
            or self._locked_addr is not None
            or snap.link_pos is None
            or not snap.actors
        ):
            return
        radius = float(self._auto_radius.value())

        if self._auto_addr is not None:
            cur = next((a for a in snap.actors if a.address == self._auto_addr), None)
            if cur is not None and math.dist(cur.pos, snap.link_pos) <= radius * 1.25:
                return  # keep current target (sticky)
            self._auto_addr = None
            self._auto_pid = None
            self._auto_target_lbl.setText("")
            self._poller.clear_center_track()

        best = None
        best_d = radius
        for a in snap.actors:
            if not self._auto_eligible(a):
                continue
            d = math.dist(a.pos, snap.link_pos)
            if d <= best_d:
                best, best_d = a, d
        if best is not None:
            self._auto_addr = best.address
            self._auto_pid = best.pid
            self._auto_target_lbl.setText(f"→ {best.name} ({best_d:.0f})")
            self._yaw_lock_offset = None  # capture relative to the new target
            self._reapply_center_track()

    def _rebuild(self, actors: list[ActorInfo]) -> None:
        selected = self._selected_address()
        self._sort_actors(actors)
        self._table.setRowCount(len(actors))
        for row, a in enumerate(actors):
            items = self._row_items(a)
            for col, item in enumerate(items):
                self._table.setItem(row, col, item)
            if a.address == selected:
                self._table.selectRow(row)

    def _on_column_clicked(self, col: int) -> None:
        if col == self._sort_column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = col
            self._sort_reverse = False
        self._rebuild(self._actors)

    def _sort_actors(self, actors: list[ActorInfo]) -> None:
        col = self._sort_column
        if col == 0:  # Name
            key = lambda a: a.name
        elif col == 1:  # Proc
            key = lambda a: a.proc
        elif col == 2:  # X
            key = lambda a: a.pos[0]
        elif col == 3:  # Y
            key = lambda a: a.pos[1]
        elif col == 4:  # Z
            key = lambda a: a.pos[2]
        elif col == 5:  # Dist
            key = lambda a: (
                math.dist(a.pos, self._link)
                if self._link is not None
                else float("inf")
            )
        else:
            return
        actors.sort(key=key, reverse=self._sort_reverse)

    def _update_cells(self, actors: list[ActorInfo]) -> None:
        for row, a in enumerate(actors):
            x, y, z = a.pos
            self._table.item(row, 2).setText(f"{x:.0f}")
            self._table.item(row, 3).setText(f"{y:.0f}")
            self._table.item(row, 4).setText(f"{z:.0f}")
            self._table.item(row, 5).setText(self._dist_text(a))

    def _row_items(self, a: ActorInfo) -> list[QTableWidgetItem]:
        x, y, z = a.pos
        texts = (a.name, f"0x{a.proc:04X}", f"{x:.0f}", f"{y:.0f}", f"{z:.0f}", self._dist_text(a))
        items = []
        for t in texts:
            item = QTableWidgetItem(t)
            item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            items.append(item)
        items[0].setToolTip(f"actor @ 0x{a.address:08X}  pid 0x{a.pid:X}")
        return items

    def _dist_text(self, a: ActorInfo) -> str:
        if self._link is None:
            return "—"
        lx, ly, lz = self._link
        x, y, z = a.pos
        return f"{math.dist((lx, ly, lz), (x, y, z)):.0f}"

    def select_actor(self, address: int) -> None:
        """Select the row for this actor (e.g. from a map dot double-click).

        If the filter is hiding it, the filter is cleared and the selection is applied
        on the next snapshot rebuild."""
        for row, a in enumerate(self._actors):
            if a.address == address:
                self._table.selectRow(row)
                self._table.scrollToItem(self._table.item(row, 0))
                return
        self._pending_select = address
        if self._filter.text():
            self._filter.clear()

    def lock_on_actor(self, address: int) -> None:
        """Select, lock on, and eye-follow an actor by address (map double-click)."""
        self.select_actor(address)
        a = next((x for x in self._actors if x.address == address), None)
        if a is None:
            return
        if not self._lock_btn.isChecked():
            self._lock_btn.setChecked(True)
        else:
            self._on_lock_toggled(True)
        if not self._eye_lock_btn.isChecked():
            self._eye_lock_btn.setChecked(True)
        else:
            self._on_eye_lock_toggled(True)

    def release_all_locks(self) -> None:
        """Uncheck Lock on and Eye follow — used when double-clicking Link on the map."""
        self._lock_btn.setChecked(False)
        self._eye_lock_btn.setChecked(False)
        self.lock_state_changed.emit(False)

    @property
    def is_locked(self) -> bool:
        """True when Lock on or Eye follow is active."""
        return self._lock_btn.isChecked() or self._eye_lock_btn.isChecked()

    def adjust_orbit_distance(self, steps: int) -> None:
        """Nudge the orbit distance by *steps* increments (scroll wheel)."""
        step = self._orbit_dist.singleStep()
        self._orbit_dist.setValue(self._orbit_dist.value() + steps * step)

    def adjust_orbit_distance_units(self, units: int) -> None:
        """Nudge the orbit distance by raw world units (keyboard dolly)."""
        self._orbit_dist.setValue(self._orbit_dist.value() + units)

    def adjust_orbit_angles(self, yaw_delta: float, pitch_delta: float) -> None:
        """Nudge orbit yaw/pitch by the given deltas (mouse drag)."""
        new_yaw = self._orbit_yaw.value() + yaw_delta
        while new_yaw > 180:
            new_yaw -= 360
        while new_yaw < -180:
            new_yaw += 360
        self._orbit_yaw.setValue(new_yaw)
        self._orbit_pitch.setValue(
            max(-85, min(85, self._orbit_pitch.value() + pitch_delta))
        )

    # ---- actions ---------------------------------------------------------------
    def _selected_address(self) -> Optional[int]:
        row = self._table.currentRow()
        if 0 <= row < len(self._actors):
            return self._actors[row].address
        return None

    def _selected_actor(self) -> Optional[ActorInfo]:
        row = self._table.currentRow()
        if 0 <= row < len(self._actors):
            return self._actors[row]
        return None

    def _on_row_double_clicked(self, item) -> None:
        row = item.row()
        if 0 <= row < len(self._actors):
            self.lock_on_actor(self._actors[row].address)

    def _on_aim(self) -> None:
        a = self._selected_actor()
        if a is not None:
            self.aim_requested.emit(*a.pos)

    def _on_focus(self) -> None:
        a = self._selected_actor()
        if a is not None:
            self.focus_requested.emit(*a.pos)

    def _on_eye(self) -> None:
        a = self._selected_actor()
        if a is not None:
            self.eye_requested.emit(*a.pos)

    def _smooth_factor(self) -> float:
        return self._smooth.value() / 100.0

    def _eye_offset(self) -> tuple[float, float, float]:
        """Spherical orbit offset -> world-space vector from the actor to the eye."""
        dist = float(self._orbit_dist.value())
        yaw = math.radians(self._orbit_yaw.value())
        pitch = math.radians(self._orbit_pitch.value())
        horiz = dist * math.cos(pitch)
        return (horiz * math.sin(yaw), dist * math.sin(pitch), horiz * math.cos(yaw))

    def _center_target(self) -> tuple[Optional[int], Optional[int]]:
        """Which actor the center track should follow: manual lock wins over auto."""
        if self._locked_addr is not None:
            return self._locked_addr, self._locked_pid
        return self._auto_addr, self._auto_pid

    def _on_yaw_lock_toggled(self, on: bool) -> None:
        self._yaw_lock_offset = None  # capture fresh from the current view on enable
        if on or self._center_target()[0] is not None:
            self._reapply_center_track()

    def _reapply_center_track(self) -> None:
        addr, pid = self._center_target()
        if addr is None or pid is None:
            return
        if self._yaw_lock.isChecked():
            if self._yaw_lock_offset is None:
                # Freeze the angle that's currently set up (actor-local frame); fall back
                # to a plain forward look-ahead if the capture isn't possible yet.
                self._yaw_lock_offset = self._poller.capture_center_offset(addr) or (
                    0.0, 100.0, float(self._orbit_dist.value())
                )
            self._poller.set_center_track_actor(
                addr, pid, self._smooth_factor(), self._yaw_lock_offset, actor_relative=True
            )
        else:
            self._poller.set_center_track_actor(addr, pid, self._smooth_factor())

    def _reapply_eye_track(self) -> None:
        if self._eye_locked_addr is not None and self._eye_locked_pid is not None:
            self._poller.set_eye_track_actor(
                self._eye_locked_addr,
                self._eye_locked_pid,
                self._smooth_factor(),
                self._eye_offset(),
                actor_relative=self._orbit_rel.isChecked(),
            )

    def _on_orbit_changed(self, _val: int) -> None:
        self._orbit_yaw_val.setText(f"{self._orbit_yaw.value()}°")
        self._orbit_pitch_val.setText(f"{self._orbit_pitch.value()}°")
        self._reapply_eye_track()

    def _on_smooth_changed(self, val: int) -> None:
        self._smooth_val.setText(f"{val}%")
        # Re-apply active holds with the new factor; the glide continues seamlessly
        # because a new hold starts from the camera's current value.
        self._reapply_center_track()
        self._reapply_eye_track()

    def _on_lock_toggled(self, on: bool) -> None:
        if on:
            a = self._selected_actor()
            if a is None:
                self._lock_btn.setChecked(False)
                return
            self._locked_addr = a.address
            self._locked_pid = a.pid
            self._lock_btn.setText(f"Locked: {a.name}")
            self._yaw_lock_offset = None  # capture relative to the new target
            self._reapply_center_track()
        else:
            self._locked_addr = None
            self._locked_pid = None
            self._lock_btn.setText("Lock on")
            self._poller.clear_center_track()
            self._auto_addr = None  # let auto-lock re-evaluate on the next snapshot
        self.lock_state_changed.emit(self.is_locked)

    def _on_auto_lock_toggled(self, on: bool) -> None:
        if not on and self._auto_addr is not None:
            self._auto_addr = None
            self._auto_pid = None
            self._auto_target_lbl.setText("")
            if self._locked_addr is None:  # don't drop a manual lock
                self._poller.clear_center_track()

    def _on_eye_lock_toggled(self, on: bool) -> None:
        if on:
            a = self._selected_actor()
            if a is None:
                self._eye_lock_btn.setChecked(False)
                return
            self._eye_locked_addr = a.address
            self._eye_locked_pid = a.pid
            self._eye_lock_btn.setText(f"Eye on: {a.name}")
            self._reapply_eye_track()
        else:
            self._eye_locked_addr = None
            self._eye_locked_pid = None
            self._eye_lock_btn.setText("Eye follow")
            self._poller.clear_eye_track()
        self.lock_state_changed.emit(self.is_locked)

    def _on_teleport(self) -> None:
        a = self._selected_actor()
        if a is not None:
            x, y, z = a.pos
            self._poller.teleport_link_once(x, y + 50.0, z)
