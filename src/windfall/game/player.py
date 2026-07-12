"""Link (the player) — read the debug mirror for display, write the actor's current.pos to teleport.

Two distinct addresses are in play:
  * ``link_pos`` (l_debug_keep_pos) is a read-only mirror — great for *reading* Link's position.
  * the actor's ``current.pos`` (player_ptr -> actor + player_pos_off) is the *writable* position,
    but it only relocates Link under continuous per-frame writes (physics overwrites a one-shot).
    The actor is reallocated on room/stage load, so we re-follow the pointer on every write.
"""

from __future__ import annotations

from ..addresses.version import Addresses
from ..memory.hook import DolphinHook, Vec3

# WW stores facing as a u16 "s16 angle": the full circle is 0..65535.
_ANGLE_SCALE = 360.0 / 65536.0


class Player:
    def __init__(self, hook: DolphinHook, addr: Addresses):
        self._hook = hook
        self._addr = addr

    # ---- reading (debug mirror) --------------------------------------------
    def get_position(self) -> Vec3 | None:
        try:
            return self._hook.read_vec3(self._addr.link_pos)
        except Exception:
            return None

    # ---- writable actor position (current.pos) -----------------------------
    def position_address(self) -> int | None:
        """Absolute address of the writable current.pos, or None if unavailable.

        Re-followed on demand because the actor moves when a room/stage reloads.
        """
        base, off = self._addr.player_ptr, self._addr.player_pos_off
        if base is None or off is None:
            return None
        try:
            actor = self._hook.read_u32(base)
        except Exception:
            return None
        if not self._hook.is_valid_address(actor):
            return None
        return actor + off

    def get_actor_position(self) -> Vec3 | None:
        addr = self.position_address()
        if addr is None:
            return None
        try:
            return self._hook.read_vec3(addr)
        except Exception:
            return None

    def write_position(self, vec: Vec3) -> bool:
        """Write current.pos once. For teleport this must be called every tick (a poller hold)."""
        addr = self.position_address()
        if addr is None:
            return False
        try:
            self._hook.write_vec3(addr, vec)
            return True
        except Exception:
            return False

    def write_position_xyz(
        self,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
    ) -> bool:
        """Write only the given components, preserving the rest (reads the live actor pos)."""
        current = self.get_actor_position()
        if current is None:
            return False
        return self.write_position(
            Vec3(
                current.x if x is None else x,
                current.y if y is None else y,
                current.z if z is None else z,
            )
        )

    # ---- facing angle -------------------------------------------------------
    def get_angle_y_raw(self) -> int | None:
        try:
            return self._hook.read_u16(self._addr.link_angle_y)
        except Exception:
            return None

    def get_angle_y_degrees(self) -> float | None:
        raw = self.get_angle_y_raw()
        return None if raw is None else raw * _ANGLE_SCALE

    def angle_address(self) -> int | None:
        """Absolute address of the writable actor angle, or None."""
        base, off = self._addr.player_ptr, self._addr.actor_angle_off
        if base is None or off is None:
            return None
        try:
            actor = self._hook.read_u32(base)
        except Exception:
            return None
        if not self._hook.is_valid_address(actor):
            return None
        return actor + off

    def write_angle(self, degrees: float) -> bool:
        """Write facing angle as degrees (0-360, one-shot)."""
        addr = self.angle_address()
        if addr is None:
            return False
        try:
            raw = int(round(degrees / _ANGLE_SCALE)) & 0xFFFF
            self._hook.write_u16(addr, raw)
            return True
        except Exception:
            return False
