"""Movie panel — cinematic recording helpers (HUD visibility, camera timeline, actor visibility)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...core.poller import Poller, Snapshot
from ...game.actors import is_ambient
from ...memory.hook import DolphinHook

# HUD disable is only supported for USA (GZLE01).
_HUD_SUPPORTED_GAME_IDS = {"GZLE01"}

# fopAc_ac_c render-suppression flags (zeldaret/tww f_op_actor.h).
# Setting these makes the game skip the actor's draw call while it stays loaded
# and its logic keeps running — the engine's own "don't render" mechanism.
_STATUS_OFF = 0x1C4  # u32 actor_status
_STATUS_NODRAW = 0x01000000  # fopAcStts_NODRAW_e
_CONDITION_OFF = 0x1C8  # u32 actor_condition
_CONDITION_NODRAW = 0x04  # fopAcCnd_NODRAW_e


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


@dataclass
class _Keyframe:
    """One camera pose on the timeline; ``dur`` is seconds to travel *from the
    previous keyframe* (ignored on the first)."""

    eye: tuple[float, float, float]
    center: tuple[float, float, float]
    fovy: Optional[float]
    bank: Optional[float]
    dur: float = 2.0


class MoviePanel(QWidget):
    """Movie/recording tools panel wired to the shared Poller."""

    def __init__(self, poller: Poller) -> None:
        super().__init__()
        self._poller = poller
        self._hidden_actors: set[int] = set()  # actor addresses with NODRAW held
        self._link_hidden_addr: Optional[int] = None  # Link's actor address while hidden
        self._last_snap: Optional[Snapshot] = None

        # Timeline state.
        self._tl_keys: list[_Keyframe] = []
        self._tl_playing = False
        self._tl_t = 0.0  # playhead position in seconds
        self._tl_last_tick: Optional[float] = None  # perf_counter of previous tick
        self._tl_scrubbing = False  # guards against feedback while we move the slider

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # ---- HUD visibility --------------------------------------------------
        hud_box = QGroupBox("HUD")
        hud_layout = QVBoxLayout(hud_box)

        self._disable_hud = QCheckBox("Disable HUD")
        self._disable_hud.toggled.connect(self._on_disable_hud_toggled)
        hud_layout.addWidget(self._disable_hud)

        root.addWidget(hud_box)

        # ---- Camera timeline -------------------------------------------------
        tl_box = QGroupBox("Camera Timeline")
        tl_root = QVBoxLayout(tl_box)

        # Keyframe ops + per-segment duration.
        key_row = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.setToolTip("Capture the current camera as a new keyframe at the end.")
        add_btn.clicked.connect(self._on_tl_add)
        set_btn = QPushButton("Set")
        set_btn.setToolTip("Re-capture the selected keyframe from the current camera.")
        set_btn.clicked.connect(self._on_tl_set)
        del_btn = QPushButton("Del")
        del_btn.setToolTip("Delete the selected keyframe.")
        del_btn.clicked.connect(self._on_tl_del)
        up_btn = QPushButton("↑")
        dn_btn = QPushButton("↓")
        for b, d in ((up_btn, -1), (dn_btn, 1)):
            b.setFixedWidth(26)
            b.setToolTip("Reorder the selected keyframe.")
            b.clicked.connect(lambda _c, dd=d: self._on_tl_move(dd))
        for b in (add_btn, set_btn, del_btn, up_btn, dn_btn):
            key_row.addWidget(b)
        key_row.addStretch(1)
        key_row.addWidget(QLabel("Travel:"))
        self._tl_dur = QDoubleSpinBox()
        self._tl_dur.setRange(0.1, 120.0)
        self._tl_dur.setValue(2.0)
        self._tl_dur.setDecimals(1)
        self._tl_dur.setSingleStep(0.5)
        self._tl_dur.setSuffix(" s")
        self._tl_dur.setToolTip("Seconds to fly from the previous keyframe to the selected one.")
        self._tl_dur.valueChanged.connect(self._on_tl_dur_changed)
        key_row.addWidget(self._tl_dur)
        tl_root.addLayout(key_row)

        self._tl_list = QListWidget()
        self._tl_list.setMaximumHeight(110)
        self._tl_list.setFont(QFont("Consolas", 9))
        self._tl_list.currentRowChanged.connect(self._on_tl_selected)
        self._tl_list.itemDoubleClicked.connect(lambda _i: self._on_tl_jump())
        self._tl_list.setToolTip("Double-click a keyframe to jump the camera to it.")
        tl_root.addWidget(self._tl_list)

        # Scrubber.
        scrub_row = QHBoxLayout()
        self._tl_scrub = QSlider(Qt.Orientation.Horizontal)
        self._tl_scrub.setRange(0, 1000)
        self._tl_scrub.setToolTip("Scrub the camera along the timeline.")
        self._tl_scrub.valueChanged.connect(self._on_tl_scrub)
        scrub_row.addWidget(self._tl_scrub, stretch=1)
        self._tl_time_lbl = QLabel("0.0 / 0.0s")
        self._tl_time_lbl.setMinimumWidth(76)
        self._tl_time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        scrub_row.addWidget(self._tl_time_lbl)
        tl_root.addLayout(scrub_row)

        # Transport.
        play_row = QHBoxLayout()
        self._tl_play_btn = QPushButton("▶ Play")
        self._tl_play_btn.setCheckable(True)
        self._tl_play_btn.toggled.connect(self._on_tl_play_toggled)
        play_row.addWidget(self._tl_play_btn)
        self._tl_loop = QCheckBox("Loop")
        play_row.addWidget(self._tl_loop)
        self._tl_ease = QCheckBox("Ease")
        self._tl_ease.setChecked(True)
        self._tl_ease.setToolTip("Smooth in/out on each segment instead of constant speed.")
        play_row.addWidget(self._tl_ease)
        play_row.addStretch(1)
        release_btn = QPushButton("Release cam")
        release_btn.setToolTip("Stop playback and hand the camera back to the game.")
        release_btn.clicked.connect(self._on_tl_release)
        play_row.addWidget(release_btn)
        tl_root.addLayout(play_row)

        # File ops.
        file_row = QHBoxLayout()
        save_btn = QPushButton("Save timeline…")
        save_btn.setToolTip("Export this timeline to a JSON file.")
        save_btn.clicked.connect(self._on_tl_save)
        load_btn = QPushButton("Load timeline…")
        load_btn.setToolTip("Import a timeline from a JSON file.")
        load_btn.clicked.connect(self._on_tl_load)
        file_row.addWidget(save_btn)
        file_row.addWidget(load_btn)
        file_row.addStretch(1)
        tl_root.addLayout(file_row)

        root.addWidget(tl_box)

        # ---- Visibility controls ----------------------------------------
        vis_box = QGroupBox("Visibility")
        vis_layout = QVBoxLayout(vis_box)

        # Link visibility
        link_row = QHBoxLayout()
        self._hide_link_btn = QPushButton("Hide Link")
        self._hide_link_btn.setCheckable(True)
        self._hide_link_btn.toggled.connect(self._on_hide_link_toggled)
        link_row.addWidget(self._hide_link_btn)
        link_row.addStretch(1)
        vis_layout.addLayout(link_row)

        # Actor visibility list
        vis_layout.addWidget(QLabel("Hide Actors:"))
        self._actor_list = QListWidget()
        self._actor_list.setMaximumHeight(120)
        self._actor_list.itemClicked.connect(self._on_actor_visibility_toggled)
        vis_layout.addWidget(self._actor_list)

        root.addWidget(vis_box)
        root.addStretch(1)

    def _on_disable_hud_toggled(self, on: bool) -> None:
        if on:
            self._poller.set_hud_hold()
        else:
            self._poller.clear_hold(self._poller._HUD_HOLD_KEY)

    # ---- Camera timeline ----
    def _capture_keyframe(self) -> Optional[_Keyframe]:
        snap = self._last_snap
        if snap is None or snap.cam_eye is None or snap.cam_center is None:
            return None
        return _Keyframe(
            eye=snap.cam_eye,
            center=snap.cam_center,
            fovy=snap.cam_fovy,
            bank=snap.cam_bank_deg,
            dur=self._tl_dur.value(),
        )

    def _on_tl_add(self) -> None:
        key = self._capture_keyframe()
        if key is None:
            return
        self._tl_keys.append(key)
        self._tl_refresh_list(select=len(self._tl_keys) - 1)

    def _on_tl_set(self) -> None:
        row = self._tl_list.currentRow()
        if not (0 <= row < len(self._tl_keys)):
            return
        key = self._capture_keyframe()
        if key is None:
            return
        key.dur = self._tl_keys[row].dur  # keep the segment timing
        self._tl_keys[row] = key
        self._tl_refresh_list(select=row)

    def _on_tl_del(self) -> None:
        row = self._tl_list.currentRow()
        if not (0 <= row < len(self._tl_keys)):
            return
        del self._tl_keys[row]
        self._tl_refresh_list(select=min(row, len(self._tl_keys) - 1))

    def _on_tl_move(self, delta: int) -> None:
        row = self._tl_list.currentRow()
        new = row + delta
        if not (0 <= row < len(self._tl_keys)) or not (0 <= new < len(self._tl_keys)):
            return
        keys = self._tl_keys
        keys[row], keys[new] = keys[new], keys[row]
        self._tl_refresh_list(select=new)

    def _on_tl_selected(self, row: int) -> None:
        if 0 <= row < len(self._tl_keys):
            self._tl_dur.blockSignals(True)
            self._tl_dur.setValue(self._tl_keys[row].dur)
            self._tl_dur.blockSignals(False)
            # The first keyframe has no incoming segment to time.
            self._tl_dur.setEnabled(row > 0)

    def _on_tl_dur_changed(self, val: float) -> None:
        row = self._tl_list.currentRow()
        if 0 < row < len(self._tl_keys):
            self._tl_keys[row].dur = val
            self._tl_refresh_list(select=row)

    def _on_tl_jump(self) -> None:
        """Double-click: snap the camera (and playhead) to the keyframe."""
        row = self._tl_list.currentRow()
        if not (0 <= row < len(self._tl_keys)):
            return
        self._tl_play_btn.setChecked(False)
        self._tl_t = self._tl_key_time(row)
        self._tl_apply(self._tl_t)
        self._tl_sync_scrub()

    def _tl_refresh_list(self, select: int = -1) -> None:
        self._tl_list.blockSignals(True)
        self._tl_list.clear()
        for i, k in enumerate(self._tl_keys):
            timing = "start" if i == 0 else f"+{k.dur:.1f}s"
            x, y, z = k.eye
            fov = f" fov {k.fovy:.0f}" if k.fovy is not None else ""
            self._tl_list.addItem(f"{i + 1}: {timing:>6}  eye {x:.0f}, {y:.0f}, {z:.0f}{fov}")
        self._tl_list.blockSignals(False)
        if 0 <= select < self._tl_list.count():
            self._tl_list.setCurrentRow(select)
        self._tl_t = min(self._tl_t, self._tl_total())
        self._tl_sync_scrub()

    def _tl_total(self) -> float:
        return sum(k.dur for k in self._tl_keys[1:])

    def _tl_key_time(self, index: int) -> float:
        return sum(k.dur for k in self._tl_keys[1 : index + 1])

    def _tl_state_at(
        self, t: float
    ) -> Optional[tuple[tuple[float, float, float], tuple[float, float, float],
                        Optional[float], Optional[float]]]:
        """Interpolated (eye, center, fovy, bank) at time ``t`` along the timeline."""
        keys = self._tl_keys
        if not keys:
            return None
        if len(keys) == 1 or t <= 0:
            k = keys[0]
            return k.eye, k.center, k.fovy, k.bank
        seg_start = 0.0
        for a, b in zip(keys, keys[1:]):
            seg_end = seg_start + b.dur
            if t <= seg_end or b is keys[-1]:
                u = min(1.0, max(0.0, (t - seg_start) / b.dur)) if b.dur > 0 else 1.0
                if self._tl_ease.isChecked():
                    u = _smoothstep(u)
                eye = tuple(av + (bv - av) * u for av, bv in zip(a.eye, b.eye))
                center = tuple(av + (bv - av) * u for av, bv in zip(a.center, b.center))
                fovy = bank = None
                if a.fovy is not None and b.fovy is not None:
                    fovy = a.fovy + (b.fovy - a.fovy) * u
                if a.bank is not None and b.bank is not None:
                    bank = a.bank + (b.bank - a.bank) * u
                return eye, center, fovy, bank
            seg_start = seg_end
        return None  # unreachable

    def _tl_apply(self, t: float) -> None:
        state = self._tl_state_at(t)
        if state is None:
            return
        eye, center, fovy, bank = state
        self._poller.set_eye_hold(*eye)
        self._poller.set_center_hold(*center)
        if fovy is not None:
            self._poller.set_fovy_hold(fovy)
        if bank is not None:
            self._poller.set_bank_hold(bank)

    def _tl_sync_scrub(self) -> None:
        total = self._tl_total()
        pos = int(self._tl_t / total * 1000) if total > 0 else 0
        self._tl_scrubbing = True
        self._tl_scrub.setValue(pos)
        self._tl_scrubbing = False
        self._tl_time_lbl.setText(f"{self._tl_t:.1f} / {total:.1f}s")

    def _on_tl_scrub(self, val: int) -> None:
        if self._tl_scrubbing:  # programmatic update during playback
            return
        self._tl_play_btn.setChecked(False)
        total = self._tl_total()
        self._tl_t = val / 1000.0 * total
        self._tl_time_lbl.setText(f"{self._tl_t:.1f} / {total:.1f}s")
        self._tl_apply(self._tl_t)

    def _on_tl_play_toggled(self, on: bool) -> None:
        if on and not self._tl_keys:
            self._tl_play_btn.setChecked(False)
            return
        self._tl_playing = on
        self._tl_last_tick = None
        if on and self._tl_t >= self._tl_total():
            self._tl_t = 0.0  # replay from the top when the playhead is at the end
        self._tl_play_btn.setText("■ Stop" if on else "▶ Play")

    def _on_tl_release(self) -> None:
        self._tl_play_btn.setChecked(False)
        self._poller.clear_camera_holds()

    def _tl_tick(self) -> None:
        """Advance playback — called from update_from on every snapshot."""
        if not self._tl_playing:
            return
        now = time.perf_counter()
        dt = min(0.1, now - self._tl_last_tick) if self._tl_last_tick is not None else 0.0
        self._tl_last_tick = now
        self._tl_t += dt
        total = self._tl_total()
        if self._tl_t >= total:
            if self._tl_loop.isChecked() and total > 0:
                self._tl_t %= total
            else:
                self._tl_t = total
                self._tl_play_btn.setChecked(False)  # holds keep the final pose
        self._tl_apply(self._tl_t)
        self._tl_sync_scrub()

    def _tl_save(self) -> None:
        """Serialize keyframes to JSON."""
        data = {
            "version": 1,
            "keyframes": [asdict(k) for k in self._tl_keys],
        }
        return data

    def _tl_load(self, data: dict) -> bool:
        """Deserialize keyframes from JSON. Return True on success."""
        try:
            if data.get("version") != 1:
                return False
            self._tl_keys = [
                _Keyframe(
                    eye=tuple(k["eye"]),
                    center=tuple(k["center"]),
                    fovy=k.get("fovy"),
                    bank=k.get("bank"),
                    dur=k.get("dur", 2.0),
                )
                for k in data.get("keyframes", [])
            ]
            self._tl_t = 0.0
            self._tl_last_tick = None
            self._tl_play_btn.setChecked(False)
            self._tl_refresh_list(select=0 if self._tl_keys else -1)
            return True
        except (KeyError, TypeError, ValueError):
            return False

    def _on_tl_save(self) -> None:
        """Export timeline to file."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save timeline",
            str(Path.home() / "windfall_timeline.json"),
            "Timeline JSON (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w") as f:
                json.dump(self._tl_save(), f, indent=2)
        except Exception as e:
            print(f"Failed to save timeline: {e}")

    def _on_tl_load(self) -> None:
        """Import timeline from file."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load timeline",
            str(Path.home()),
            "Timeline JSON (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if not self._tl_load(data):
                print("Failed to load timeline: invalid format")
        except Exception as e:
            print(f"Failed to load timeline: {e}")

    # ---- Visibility controls ----
    def _on_hide_link_toggled(self, hide: bool) -> None:
        """Toggle Link's NODRAW flags — he stays in place and controllable, just unrendered."""
        if hide:
            snap = self._last_snap
            link = None
            if snap is not None:
                link = next((a for a in snap.actors if a.name == "Link"), None)
            if link is None:
                self._hide_link_btn.setChecked(False)
                return
            self._link_hidden_addr = link.address
            self._hidden_actors.add(link.address)
            self._hide_link_btn.setText("Show Link")
        else:
            if self._link_hidden_addr is not None:
                self._show_actor(self._link_hidden_addr)
                self._link_hidden_addr = None
            self._hide_link_btn.setText("Hide Link")
        self._update_actor_visibility_hold()

    def _on_actor_visibility_toggled(self, item: QListWidgetItem) -> None:
        """Toggle the clicked actor's NODRAW flags."""
        addr = item.data(Qt.ItemDataRole.UserRole)
        if addr is None or self._last_snap is None:
            return
        actor = next((a for a in self._last_snap.actors if a.address == addr), None)
        if actor is None:
            return
        if addr in self._hidden_actors:
            self._show_actor(addr)
            item.setText(f"{actor.name} (visible)")
        else:
            self._hidden_actors.add(addr)
            item.setText(f"{actor.name} (hidden)")
        self._update_actor_visibility_hold()

    def _show_actor(self, addr: int) -> None:
        """Drop the actor from the hidden set and clear its NODRAW flags.

        The clear is repeated shortly after in case the hold thread had an
        in-flight write when the set was updated."""
        self._hidden_actors.discard(addr)

        def clear_flags() -> None:
            try:
                hook = self._poller._hook
                status = hook.read_u32(addr + _STATUS_OFF)
                hook.write_u32(addr + _STATUS_OFF, status & ~_STATUS_NODRAW)
                cond = hook.read_u32(addr + _CONDITION_OFF)
                hook.write_u32(addr + _CONDITION_OFF, cond & ~_CONDITION_NODRAW)
            except Exception:
                pass  # actor despawned or emulator disconnected

        clear_flags()
        QTimer.singleShot(150, clear_flags)

    def _update_actor_visibility_hold(self) -> None:
        """Fast hold that keeps NODRAW asserted on every hidden actor.

        The game rewrites these flag words every frame, so this must run on the
        hold-hammer thread — at the 30 Hz tick rate the actor flickers."""
        if not self._hidden_actors:
            self._poller.clear_fast_hold("actor_visibility")
            return

        def hide_actors_fn(hook: DolphinHook) -> None:
            for addr in list(self._hidden_actors):
                try:
                    status = hook.read_u32(addr + _STATUS_OFF)
                    hook.write_u32(addr + _STATUS_OFF, status | _STATUS_NODRAW)
                    cond = hook.read_u32(addr + _CONDITION_OFF)
                    hook.write_u32(addr + _CONDITION_OFF, cond | _CONDITION_NODRAW)
                except Exception:
                    pass  # actor may have despawned

        self._poller.set_fast_hold("actor_visibility", hide_actors_fn)

    def update_from(self, snap: Snapshot) -> None:
        """Called each tick — HUD availability, timeline playback, actor list."""
        self._last_snap = snap

        supported = snap.game_id in _HUD_SUPPORTED_GAME_IDS if snap.game_id else False
        self._disable_hud.setEnabled(supported)
        if not supported and self._disable_hud.isChecked():
            self._disable_hud.setChecked(False)

        self._tl_tick()
        self._update_actor_list(snap)

    def _update_actor_list(self, snap: Snapshot) -> None:
        """Refresh the actor visibility list."""
        if not snap.actors:
            self._actor_list.clear()
            return
        # Skip ambient actors and Link
        actors_to_show = [
            a for a in snap.actors
            if a.name != "Link" and not is_ambient(a.name)
        ]
        # Build set of visible addresses in the list
        visible_addrs = {
            self._actor_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._actor_list.count())
        }
        # Add new actors
        for actor in actors_to_show:
            if actor.address not in visible_addrs:
                item = QListWidgetItem()
                status = "hidden" if actor.address in self._hidden_actors else "visible"
                item.setText(f"{actor.name} ({status})")
                item.setData(Qt.ItemDataRole.UserRole, actor.address)
                self._actor_list.addItem(item)
        # Remove despawned actors
        pruned = False
        for i in range(self._actor_list.count() - 1, -1, -1):
            addr = self._actor_list.item(i).data(Qt.ItemDataRole.UserRole)
            if not any(a.address == addr for a in actors_to_show):
                self._actor_list.takeItem(i)
                if addr in self._hidden_actors:
                    self._hidden_actors.discard(addr)
                    pruned = True
        # Link despawned (room/stage reload) — reset the hide button.
        if self._link_hidden_addr is not None and not any(
            a.address == self._link_hidden_addr for a in snap.actors
        ):
            self._hidden_actors.discard(self._link_hidden_addr)
            self._link_hidden_addr = None
            self._hide_link_btn.setChecked(False)
            pruned = True
        if pruned:
            self._update_actor_visibility_hold()
