"""The polling loop.

Lives on a worker QThread so memory I/O never blocks the UI (keeps the map drag smooth). Every
tick it: (1) ensures the Dolphin hook is alive, (2) detects the game version, (3) drains any
queued one-shot writes, (4) re-applies "held" writes (freecam / drag-teleport hold), and
(5) reads a snapshot of watched values and emits it. UI widgets connect to ``snapshot``.

Cross-thread interaction:
  - The UI pushes work in via ``queue_write`` / ``set_hold`` / ``clear_hold``. These are guarded
    by a mutex because they're called from the UI thread while the worker thread reads them.
  - Results flow out only via Qt signals (auto-queued across threads).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QMutex, QMutexLocker, QObject, QTimer, Signal, Slot

from ..addresses.version import GameVersion, detect_version
from ..game.actors import ActorList
from ..game.camera import Camera
from ..game.collision import CollisionMesh, CollisionReader
from ..game.player import Player
from ..memory.hook import DolphinHook

# A held/queued write is just a function that receives the hook and does its thing.
WriteFn = Callable[[DolphinHook], None]


@dataclass
class Snapshot:
    connected: bool = False
    game_id: Optional[str] = None
    label: Optional[str] = None
    supported: bool = False
    link_pos: Optional[tuple[float, float, float]] = None
    link_angle_deg: Optional[float] = None
    # Camera state (None when camera addresses are unavailable or camera isn't loaded).
    cam_eye: Optional[tuple[float, float, float]] = None
    cam_center: Optional[tuple[float, float, float]] = None
    cam_up: Optional[tuple[float, float, float]] = None
    cam_fovy: Optional[float] = None
    cam_bank_deg: Optional[float] = None
    # Current in-game stage name (e.g. "sea", "Tower", "M_Dai").
    stage_name: Optional[str] = None
    # Live actors (refreshed at a lower rate than the tick; empty when unavailable).
    actors: list = field(default_factory=list)
    # Collision meshes (list[CollisionMesh]) — set only on ticks where they were
    # re-read (stage change / periodic refresh); None means "no change".
    collision: Optional[list] = None


class _PositionHammer(threading.Thread):
    """Dedicated ~1 kHz writer for Link's position.

    The game restores current.pos from its own state every frame, so position writes
    must outpace the frame rate to stick. Doing that on the poller thread blocked the
    tick; this daemon thread hammers continuously while a target is set and idles
    otherwise. A target may carry an expiry for burst teleports. Components left None
    are not written (e.g. pin X/Z from a map drag and let Y fall naturally).
    """

    _INTERVAL = 0.001
    _IDLE_WAIT = 0.05

    def __init__(self, get_player: Callable[[], Optional[Player]]) -> None:
        super().__init__(name="position-hammer", daemon=True)
        self._get_player = get_player
        self._lock = threading.Lock()
        self._target: Optional[tuple] = None  # (x, y, z, expiry|None)
        self._wake = threading.Event()
        self._shutdown = False
        self.start()

    def set_target(
        self,
        x: Optional[float],
        y: Optional[float],
        z: Optional[float],
        duration_s: Optional[float] = None,
    ) -> None:
        expiry = time.perf_counter() + duration_s if duration_s is not None else None
        with self._lock:
            self._target = (x, y, z, expiry)
        self._wake.set()

    def clear(self) -> None:
        with self._lock:
            self._target = None

    def is_active(self) -> bool:
        with self._lock:
            return self._target is not None

    def shutdown(self) -> None:
        self._shutdown = True
        self._wake.set()

    def run(self) -> None:
        while not self._shutdown:
            with self._lock:
                tgt = self._target
            if tgt is None:
                self._wake.wait(self._IDLE_WAIT)
                self._wake.clear()
                continue
            x, y, z, expiry = tgt
            if expiry is not None and time.perf_counter() > expiry:
                with self._lock:
                    if self._target is tgt:  # don't clobber a newer target
                        self._target = None
                continue
            player = self._get_player()
            if player is not None:
                try:
                    addr = player.position_address()
                    if addr is not None:
                        if x is not None:
                            player._hook.write_f32(addr, x)
                        if y is not None:
                            player._hook.write_f32(addr + 4, y)
                        if z is not None:
                            player._hook.write_f32(addr + 8, z)
                except Exception:
                    pass  # transient (load screen / disconnect); keep trying
            time.sleep(self._INTERVAL)


class _HoldHammer(threading.Thread):
    """Dedicated ~500 Hz writer for generic holds that must outpace the frame rate.

    Same rationale as _PositionHammer but for arbitrary WriteFns: the game rewrites
    some fields (actor status/condition flags, etc.) every frame, so re-applying them
    at the 30 Hz poller tick loses the race on roughly half the rendered frames,
    which shows up as flicker. Each registered fn runs every interval while present.
    """

    _INTERVAL = 0.002
    _IDLE_WAIT = 0.05

    def __init__(self, hook: DolphinHook) -> None:
        super().__init__(name="hold-hammer", daemon=True)
        self._hook = hook
        self._lock = threading.Lock()
        self._holds: dict[str, WriteFn] = {}
        self._wake = threading.Event()
        self._shutdown = False
        self.start()

    def set(self, key: str, fn: WriteFn) -> None:
        with self._lock:
            self._holds[key] = fn
        self._wake.set()

    def clear(self, key: str) -> None:
        with self._lock:
            self._holds.pop(key, None)

    def shutdown(self) -> None:
        self._shutdown = True
        self._wake.set()

    def run(self) -> None:
        while not self._shutdown:
            with self._lock:
                holds = list(self._holds.values())
            if not holds:
                self._wake.wait(self._IDLE_WAIT)
                self._wake.clear()
                continue
            for fn in holds:
                try:
                    fn(self._hook)
                except Exception:
                    pass  # transient (load screen / disconnect); keep trying
            time.sleep(self._INTERVAL)


class _CameraTracker(threading.Thread):
    """~300 Hz camera follower for actor lock-on.

    Writing the tracked camera at the poller rate (30 Hz) beats against the game's
    frame rate — some rendered frames see a fresh value and some don't, which reads
    as judder. This thread updates much faster than the frame rate using time-based
    exponential smoothing, so every rendered frame samples a freshly glided value.

    Targets: "center" (look-at) and "eye", each following an actor with an optional
    offset, optionally rotated into the actor's facing frame. The smoothing factor
    keeps the UI's meaning: fraction of remaining distance per 1/30 s.
    """

    _INTERVAL = 0.003
    _IDLE_WAIT = 0.05
    _TICK = 1.0 / 30.0

    def __init__(self, get_env: Callable, on_target_lost: Callable[[str], None]) -> None:
        super().__init__(name="camera-tracker", daemon=True)
        self._get_env = get_env
        self._on_lost = on_target_lost
        self._lock = threading.Lock()
        self._targets: dict[str, tuple] = {}  # which -> (addr, pid, smooth, offset, relative)
        self._cur: dict[str, list] = {}  # which -> smoothed position (persists across re-apply)
        self._last_t: dict[str, float] = {}
        self._wake = threading.Event()
        self._shutdown = False
        self.start()

    def set_target(
        self,
        which: str,
        actor_addr: int,
        pid: int,
        smooth: float,
        offset: tuple[float, float, float],
        actor_relative: bool,
    ) -> None:
        with self._lock:
            self._targets[which] = (actor_addr, pid, smooth, offset, actor_relative)
        self._wake.set()

    def clear(self, which: str) -> None:
        with self._lock:
            self._targets.pop(which, None)
            self._cur.pop(which, None)
            self._last_t.pop(which, None)

    def any_active(self) -> bool:
        with self._lock:
            return bool(self._targets)

    def shutdown(self) -> None:
        self._shutdown = True
        self._wake.set()

    def run(self) -> None:
        import math

        from ..memory.hook import Vec3

        while not self._shutdown:
            with self._lock:
                targets = dict(self._targets)
            if not targets:
                self._wake.wait(self._IDLE_WAIT)
                self._wake.clear()
                continue
            camera, addr_table, hook = self._get_env()
            if camera is None or addr_table is None or addr_table.actor_pos_off is None:
                time.sleep(self._IDLE_WAIT)
                continue
            now = time.perf_counter()
            for which, (aaddr, pid, smooth, offset, relative) in targets.items():
                try:
                    if hook.read_u32(aaddr + 4) != pid:
                        self.clear(which)
                        self._on_lost(which)
                        continue
                    tp = hook.read_vec3(aaddr + addr_table.actor_pos_off)
                except Exception:
                    continue  # transient (load screen); keep the target
                ox, oy, oz = offset
                if relative and addr_table.actor_angle_off is not None:
                    try:
                        raw = hook.read_s16(aaddr + addr_table.actor_angle_off)
                    except Exception:
                        raw = 0
                    yaw = raw * (2.0 * math.pi / 65536.0)
                    s, c = math.sin(yaw), math.cos(yaw)
                    ox, oz = ox * c + oz * s, oz * c - ox * s
                goal = (tp.x + ox, tp.y + oy, tp.z + oz)

                cur = self._cur.get(which)
                if cur is None:
                    live = camera.get_center() if which == "center" else camera.get_eye()
                    if live is None:
                        continue
                    cur = [live.x, live.y, live.z]
                last = self._last_t.get(which)
                dt = min(0.1, now - last) if last is not None else self._INTERVAL
                self._last_t[which] = now
                # Convert per-tick smoothing into this dt so the feel matches the slider.
                alpha = 1.0 - (1.0 - min(smooth, 1.0)) ** (dt / self._TICK)
                cur = [c + (g - c) * alpha for c, g in zip(cur, goal)]
                self._cur[which] = cur
                try:
                    write = camera.write_center if which == "center" else camera.write_eye
                    write(Vec3(*cur))
                except Exception:
                    pass
            time.sleep(self._INTERVAL)


class Poller(QObject):
    snapshot = Signal(object)  # emits Snapshot

    def __init__(self, hz: int = 30):
        super().__init__()
        self._interval_ms = max(1, int(1000 / hz))
        self._hook = DolphinHook()
        self._version: Optional[GameVersion] = None
        self._player: Optional[Player] = None
        self._camera: Optional[Camera] = None
        self._actor_list: Optional[ActorList] = None
        self._cached_actors: list = []
        self._collision: Optional[CollisionReader] = None
        self._collision_stage: Optional[str] = None
        self._collision_last_tick = 0
        self._collision_cache: dict[str, list] = {}  # stage_name -> cached collision meshes
        self._tick_count = 0
        self._timer: Optional[QTimer] = None

        self._mutex = QMutex()
        self._one_shots: list[WriteFn] = []
        self._holds: dict[str, WriteFn] = {}
        self._hammer = _PositionHammer(lambda: self._player)
        self._hold_hammer = _HoldHammer(self._hook)
        self._cam_tracker = _CameraTracker(
            lambda: (self._camera, self._version.addr if self._version else None, self._hook),
            on_target_lost=self._on_track_lost,
        )

    # ---- lifecycle (run on the worker thread) -------------------------------
    @Slot()
    def start(self) -> None:
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._interval_ms)

    @Slot()
    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._hammer.shutdown()
        self._hold_hammer.shutdown()
        self._cam_tracker.shutdown()
        self._hook.disconnect()

    # ---- write plumbing (called from the UI thread) -------------------------
    def queue_write(self, fn: WriteFn) -> None:
        """Run ``fn`` once on the next tick (e.g. 'apply' from an editable field)."""
        with QMutexLocker(self._mutex):
            self._one_shots.append(fn)

    def set_hold(self, key: str, fn: WriteFn) -> None:
        """Re-apply ``fn`` every tick until cleared (freecam / drag-teleport hold)."""
        with QMutexLocker(self._mutex):
            self._holds[key] = fn

    def clear_hold(self, key: str) -> None:
        with QMutexLocker(self._mutex):
            self._holds.pop(key, None)

    def set_fast_hold(self, key: str, fn: WriteFn) -> None:
        """Re-apply ``fn`` at ~500 Hz on the hold-hammer thread.

        Use instead of set_hold for fields the game rewrites every frame — at the
        30 Hz tick rate those writes lose the race on some frames and flicker."""
        self._hold_hammer.set(key, fn)

    def clear_fast_hold(self, key: str) -> None:
        self._hold_hammer.clear(key)

    # ---- teleport hold (the drag-to-map / position edit primitive) ----------
    def set_position_hold(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        z: Optional[float] = None,
    ) -> None:
        """Pin Link's position until cleared. Components left None keep following the live
        value (e.g. pin X/Z from a top-down drag and let Y fall naturally).

        Runs on the dedicated hammer thread — the game restores position every frame, so
        the writes must outpace the frame rate, which would stall the poller tick."""
        self._hammer.set_target(x, y, z)

    def clear_position_hold(self) -> None:
        self._hammer.clear()

    def freeze_position(self, x: float, z: float) -> None:
        """Pin X/Z at the given spot and Y at its current live value — a true freeze
        (Link hangs where he was dropped instead of falling; Y left unpinned would
        keep following gravity)."""
        y = None
        if self._player is not None:
            pos = self._player.get_actor_position() or self._player.get_position()
            if pos is not None:
                y = pos.y
        self._hammer.set_target(x, y, z)

    # ---- the tick -----------------------------------------------------------
    def _tick(self) -> None:
        snap = Snapshot()

        self._tick_count += 1
        if not self._hook.is_connected():
            self._version = None
            self._player = None
            self._camera = None
            self._actor_list = None
            self._cached_actors = []
            self._hook.connect()

        snap.connected = self._hook.is_connected()
        if snap.connected:
            self._ensure_version()
            snap.game_id = self._version.game_id if self._version else self._last_game_id
            if self._version:
                snap.label = self._version.label
                snap.supported = True
            self._drain_writes()
            self._read_player(snap)
            self._read_camera(snap)
            self._read_stage(snap)
            self._read_actors(snap)
            self._read_collision(snap)

        self.snapshot.emit(snap)

    _last_game_id: Optional[str] = None

    def _ensure_version(self) -> None:
        if self._version is not None:
            return
        game_id, version = detect_version(self._hook)
        self._last_game_id = game_id
        if version is not None:
            self._version = version
            self._player = Player(self._hook, version.addr)
            self._camera = Camera(self._hook, version.addr)
            self._actor_list = ActorList(self._hook, version.addr)
            self._collision = CollisionReader(self._hook, version.addr)
            self._collision_stage = None
            self._load_collision_cache()

    def _drain_writes(self) -> None:
        with QMutexLocker(self._mutex):
            one_shots = self._one_shots
            self._one_shots = []
            holds = list(self._holds.values())
        for fn in one_shots + holds:
            try:
                fn(self._hook)
            except Exception:
                pass  # a stale write during a load shouldn't kill the loop

    def _read_player(self, snap: Snapshot) -> None:
        if self._player is None:
            return
        pos = self._player.get_position()
        if pos is not None:
            snap.link_pos = pos.as_tuple()
        snap.link_angle_deg = self._player.get_angle_y_degrees()

    def _read_camera(self, snap: Snapshot) -> None:
        if self._camera is None:
            return
        eye = self._camera.get_eye()
        if eye is not None:
            snap.cam_eye = eye.as_tuple()
        center = self._camera.get_center()
        if center is not None:
            snap.cam_center = center.as_tuple()
        up = self._camera.get_up()
        if up is not None:
            snap.cam_up = up.as_tuple()
        snap.cam_fovy = self._camera.get_fovy()
        snap.cam_bank_deg = self._camera.get_bank_degrees()

    def _read_actors(self, snap: Snapshot) -> None:
        """Refresh the actor list every 5th tick (~6 Hz); reuse the cache between refreshes."""
        if self._actor_list is None or not self._actor_list.available():
            snap.actors = []
            return
        if self._tick_count % 5 == 0 or not self._cached_actors:
            self._cached_actors = self._actor_list.enumerate()
        snap.actors = self._cached_actors

    def _read_stage(self, snap: Snapshot) -> None:
        """Read the current stage name from g_dComIfG_gameInfo + stage_name_off."""
        if self._version is None:
            return
        off = self._version.addr.stage_name_off
        if off is None:
            return
        try:
            raw = self._hook.read_bytes(self._version.addr.game_info + off, 8)
            # Null-terminated string — strip at the first \x00.
            name = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
            if name:
                snap.stage_name = name
        except Exception:
            pass

    # Re-read collision this many ticks after the last refresh (~10 s at 30 Hz) to
    # pick up rooms that stream in without a stage-name change.
    _COLLISION_REFRESH_TICKS = 300
    _COLLISION_CACHE_PATH = Path.cwd() / "windfall_cache" / "collision_cache.json"

    def _save_collision_cache(self) -> None:
        """Persist the collision cache to disk."""
        data = {
            "version": 1,
            "stages": {
                name: [m.to_dict() for m in meshes]
                for name, meshes in self._collision_cache.items()
            },
        }
        try:
            self._COLLISION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._COLLISION_CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _load_collision_cache(self) -> None:
        """Load the collision cache from disk into _collision_cache."""
        try:
            if not self._COLLISION_CACHE_PATH.exists():
                return
            data = json.loads(self._COLLISION_CACHE_PATH.read_text(encoding="utf-8"))
            if data.get("version") != 1:
                return
            self._collision_cache = {
                name: [CollisionMesh.from_dict(m) for m in meshes]
                for name, meshes in data.get("stages", {}).items()
            }
        except Exception:
            pass

    def _read_collision(self, snap: Snapshot) -> None:
        """Re-read the collision registry on stage change or periodically.

        Collision meshes are cached per stage name so that rooms which have
        streamed out (Link moved away) stay visible on the map — but only for
        the *current* stage; switching stages (e.g. into Beedle's ship and back
        to the sea) must not keep showing a stage you've since left."""
        if self._collision is None:
            return
        stage_changed = snap.stage_name != self._collision_stage
        stale = self._tick_count - self._collision_last_tick >= self._COLLISION_REFRESH_TICKS
        if not (stage_changed or stale):
            return
        self._collision_stage = snap.stage_name
        self._collision_last_tick = self._tick_count
        # Read collision for the current stage (skip during loading screens).
        if snap.stage_name is not None:
            meshes = self._collision.read_meshes()
            if meshes:
                # Merge into the existing cache so previously-streamed rooms
                # (which may have streamed out) are kept on the map.
                existing = self._collision_cache.get(snap.stage_name, [])
                seen_bgw = {m.bgw_addr for m in existing}
                for m in meshes:
                    if m.bgw_addr not in seen_bgw:
                        existing.append(m)
                        seen_bgw.add(m.bgw_addr)
                self._collision_cache[snap.stage_name] = existing
        # Only the current stage's cache, not every stage ever visited.
        snap.collision = self._collision_cache.get(snap.stage_name, [])
        self._save_collision_cache()

    def teleport_link_once(
        self, x: float, y: Optional[float], z: float, duration_s: float = 0.4
    ) -> None:
        """Teleport Link and let go: hammer the position for ``duration_s`` (enough to
        beat the game's per-frame restore), then the target expires on its own.
        ``y=None`` leaves height following the live value (falls/settles to ground)."""
        self._hammer.set_target(x, y, z, duration_s=duration_s)

    def write_link_angle(self, degrees: float) -> None:
        """One-shot write of Link's facing angle (degrees 0-360)."""
        player = self._player
        if player is not None:
            self.queue_write(lambda _h, _p=player, _a=degrees: _p.write_angle(_a))

    # ---- camera holds -------------------------------------------------------
    _EYE_HOLD_KEY = "cam_eye"
    _CENTER_HOLD_KEY = "cam_center"
    _FOVY_HOLD_KEY = "cam_fovy"
    _BANK_HOLD_KEY = "cam_bank"
    _CAMERA_PAUSED_KEY = "cam_paused"
    _HUD_HOLD_KEY = "hud_disabled"

    def set_eye_hold(
        self,
        x: float,
        y: float,
        z: float,
    ) -> None:
        self._ensure_camera_paused()
        self._cam_tracker.clear("eye")  # a static hold replaces any actor tracking
        def fn(_hook: DolphinHook) -> None:
            if self._camera is None:
                return
            from ..memory.hook import Vec3
            self._camera.write_eye(Vec3(x, y, z))
        self.set_hold(self._EYE_HOLD_KEY, fn)

    def set_center_hold(
        self,
        x: float,
        y: float,
        z: float,
    ) -> None:
        self._ensure_camera_paused()
        self._cam_tracker.clear("center")  # a static hold replaces any actor tracking
        def fn(_hook: DolphinHook) -> None:
            if self._camera is None:
                return
            from ..memory.hook import Vec3
            self._camera.write_center(Vec3(x, y, z))
        self.set_hold(self._CENTER_HOLD_KEY, fn)

    def set_fovy_hold(self, fovy: float) -> None:
        self._ensure_camera_paused()
        def fn(_hook: DolphinHook) -> None:
            if self._camera is not None:
                self._camera.write_fovy(fovy)
        self.set_hold(self._FOVY_HOLD_KEY, fn)

    def set_bank_hold(self, degrees: float) -> None:
        self._ensure_camera_paused()
        def fn(_hook: DolphinHook) -> None:
            if self._camera is not None:
                self._camera.write_bank(degrees)
        self.set_hold(self._BANK_HOLD_KEY, fn)

    # ---- HUD hold -----------------------------------------------------------
    def set_hud_hold(self) -> None:
        """Write HUD-disable values every tick so the game can't restore them.

        Based on Ralf's gecko codes: patch HUD draw code and clear the visibility
        flag to hide hearts, magic meter, rupees, minimap, etc.
        """

        def fn(hook: DolphinHook) -> None:
            if self._version is None:
                return
            for addr, val in self._version.addr.hud_disable_writes.items():
                try:
                    if val <= 0xFF:
                        hook.write_u8(addr, val)
                    else:
                        hook.write_u32(addr, val)
                except Exception:
                    pass

        self.set_hold(self._HUD_HOLD_KEY, fn)

    def set_center_track_actor(
        self,
        actor_addr: int,
        pid: int,
        smooth: float = 0.15,
        offset: tuple[float, float, float] = (0.0, 0.0, 0.0),
        actor_relative: bool = False,
    ) -> None:
        """Lock the look-at center onto an actor (smooth follow, see _track_actor_hold).

        With an ``actor_relative`` forward offset the camera looks *where the actor
        looks* (yaw locked to its facing) instead of at the actor itself."""
        self._ensure_camera_paused()
        self.clear_hold(self._CENTER_HOLD_KEY)  # a static hold would fight the tracker
        self._cam_tracker.set_target("center", actor_addr, pid, smooth, offset, actor_relative)

    def set_eye_track_actor(
        self,
        actor_addr: int,
        pid: int,
        smooth: float = 0.15,
        offset: tuple[float, float, float] = (0.0, 200.0, 0.0),
        actor_relative: bool = False,
    ) -> None:
        """Lock the camera eye onto an actor — the viewpoint rides along at ``offset``
        from the actor. With ``actor_relative`` the offset rotates with the actor's live
        facing angle (+Z = in front), so the camera stays e.g. behind a turning boat."""
        self._ensure_camera_paused()
        self.clear_hold(self._EYE_HOLD_KEY)  # a static hold would fight the tracker
        self._cam_tracker.set_target("eye", actor_addr, pid, smooth, offset, actor_relative)

    def capture_center_offset(self, actor_addr: int) -> Optional[tuple[float, float, float]]:
        """Current look-at offset from the actor, expressed in the actor's facing frame.

        Used by "yaw lock" to freeze whatever view angle is currently set up: track this
        offset actor-relative and the camera direction turns with the actor."""
        if self._camera is None or self._version is None:
            return None
        addr = self._version.addr
        if addr.actor_pos_off is None or addr.actor_angle_off is None:
            return None
        center = self._camera.get_center()
        if center is None:
            return None
        try:
            pos = self._hook.read_vec3(actor_addr + addr.actor_pos_off)
            raw = self._hook.read_s16(actor_addr + addr.actor_angle_off)
        except Exception:
            return None
        import math
        wx, wy, wz = center.x - pos.x, center.y - pos.y, center.z - pos.z
        yaw = raw * (2.0 * math.pi / 65536.0)
        s, c = math.sin(yaw), math.cos(yaw)
        # Inverse of the tracker's rotation (world -> actor-local).
        return (wx * c - wz * s, wy, wz * c + wx * s)

    def _on_track_lost(self, _which: str) -> None:
        """Tracker callback: the followed actor despawned. Release the freeze if idle."""
        self._release_freeze_if_idle()

    def _release_freeze_if_idle(self) -> None:
        with QMutexLocker(self._mutex):
            others = any(
                k in self._holds
                for k in (self._EYE_HOLD_KEY, self._CENTER_HOLD_KEY,
                          self._FOVY_HOLD_KEY, self._BANK_HOLD_KEY)
            )
        if not others and not self._cam_tracker.any_active():
            self.clear_hold(self._CAMERA_PAUSED_KEY)
            if self._camera is not None:
                self._camera.unfreeze()

    def clear_center_track(self) -> None:
        self._cam_tracker.clear("center")
        self._release_freeze_if_idle()

    def clear_eye_track(self) -> None:
        self._cam_tracker.clear("eye")
        self._release_freeze_if_idle()

    def _ensure_camera_paused(self) -> None:
        """Stop the game's camera update so it can't overwrite held values.

        mPause alone doesn't stop dCamera_c from recomputing eye/center each frame,
        so also hard-freeze via mActive=0. Re-applied every tick in case the game
        resets the flags (e.g. on a room transition).
        """
        def fn(_hook: DolphinHook) -> None:
            if self._camera is not None:
                self._camera.set_paused(True)
                self._camera.freeze(True)
        self.set_hold(self._CAMERA_PAUSED_KEY, fn)

    def clear_camera_holds(self) -> None:
        self._cam_tracker.clear("eye")
        self._cam_tracker.clear("center")
        for key in (self._EYE_HOLD_KEY, self._CENTER_HOLD_KEY,
                    self._FOVY_HOLD_KEY, self._BANK_HOLD_KEY,
                    self._CAMERA_PAUSED_KEY):
            self.clear_hold(key)
        # Restore mActive=1 / mPause=0 when all holds are cleared.
        if self._camera is not None:
            self._camera.unfreeze()
