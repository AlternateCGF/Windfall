"""In-game camera — read eye/center/up/fovy/bank, write to control the viewpoint.

The camera system is:  camera_ptr -> camera_class -> (offset 0x244) -> dCamera_c.
All fields live inside dCamera_c at known offsets from the embedded instance.
"""

from __future__ import annotations

from ..addresses.version import Addresses
from ..memory.hook import DolphinHook, Vec3

_ANGLE_SCALE = 360.0 / 65536.0


class Camera:
    def __init__(self, hook: DolphinHook, addr: Addresses) -> None:
        self._hook = hook
        self._addr = addr

    # ---- pointer resolution -------------------------------------------------
    def _dcamera_addr(self) -> int | None:
        """Absolute address of the embedded dCamera_c, or None."""
        cam_off = self._addr.camera
        if cam_off is None or self._addr.camera_ptr is None:
            return None
        try:
            cam_class = self._hook.read_u32(self._addr.camera_ptr)
        except Exception:
            return None
        if not self._hook.is_valid_address(cam_class):
            return None
        return cam_class + cam_off.cam_class_off

    # ---- reads --------------------------------------------------------------
    def get_eye(self) -> Vec3 | None:
        base = self._dcamera_addr()
        if base is None:
            return None
        try:
            return self._hook.read_vec3(base + self._addr.camera.eye)
        except Exception:
            return None

    def get_center(self) -> Vec3 | None:
        base = self._dcamera_addr()
        if base is None:
            return None
        try:
            return self._hook.read_vec3(base + self._addr.camera.center)
        except Exception:
            return None

    def get_up(self) -> Vec3 | None:
        base = self._dcamera_addr()
        if base is None:
            return None
        try:
            return self._hook.read_vec3(base + self._addr.camera.up)
        except Exception:
            return None

    def get_fovy(self) -> float | None:
        base = self._dcamera_addr()
        if base is None:
            return None
        try:
            return self._hook.read_f32(base + self._addr.camera.fovy)
        except Exception:
            return None

    def get_bank_degrees(self) -> float | None:
        base = self._dcamera_addr()
        if base is None:
            return None
        try:
            raw = self._hook.read_s16(base + self._addr.camera.roll)
        except Exception:
            return None
        return raw * _ANGLE_SCALE

    # ---- writes -------------------------------------------------------------
    def write_eye(self, vec: Vec3) -> bool:
        base = self._dcamera_addr()
        if base is None:
            return False
        try:
            self._hook.write_vec3(base + self._addr.camera.eye, vec)
            return True
        except Exception:
            return False

    def write_center(self, vec: Vec3) -> bool:
        base = self._dcamera_addr()
        if base is None:
            return False
        try:
            self._hook.write_vec3(base + self._addr.camera.center, vec)
            return True
        except Exception:
            return False

    def write_up(self, vec: Vec3) -> bool:
        base = self._dcamera_addr()
        if base is None:
            return False
        try:
            self._hook.write_vec3(base + self._addr.camera.up, vec)
            return True
        except Exception:
            return False

    def write_fovy(self, fovy: float) -> bool:
        base = self._dcamera_addr()
        if base is None:
            return False
        try:
            self._hook.write_f32(base + self._addr.camera.fovy, fovy)
            return True
        except Exception:
            return False

    def write_bank(self, degrees: float) -> bool:
        base = self._dcamera_addr()
        if base is None:
            return False
        try:
            raw = int(degrees / _ANGLE_SCALE) & 0xFFFF
            self._hook.write_u16(base + self._addr.camera.roll, raw)
            return True
        except Exception:
            return False

    # ---- camera pause (prevents game from overwriting our values) -----------
    # dCamera_c.mPause is at offset 0x005 (u8). Setting it to 1 stops the
    # camera's Run() update so our held values aren't overwritten each frame.
    # mActive at 0x004 is a harder stop — the camera checks it before any update.
    _PAUSE_OFF = 0x005
    _ACTIVE_OFF = 0x004

    def set_paused(self, paused: bool) -> bool:
        """Pause the camera's Run() update loop."""
        base = self._dcamera_addr()
        if base is None:
            return False
        try:
            self._hook.write_u8(base + self._PAUSE_OFF, 1 if paused else 0)
            return True
        except Exception:
            return False

    def freeze(self, frozen: bool) -> bool:
        """Hard-freeze: set mActive=0 so no camera update runs at all."""
        base = self._dcamera_addr()
        if base is None:
            return False
        try:
            self._hook.write_u8(base + self._ACTIVE_OFF, 0 if frozen else 1)
            return True
        except Exception:
            return False

    def unfreeze(self) -> bool:
        """Restore mActive=1 and mPause=0 to resume normal camera."""
        base = self._dcamera_addr()
        if base is None:
            return False
        try:
            self._hook.write_u8(base + self._ACTIVE_OFF, 1)
            self._hook.write_u8(base + self._PAUSE_OFF, 0)
            return True
        except Exception:
            return False
