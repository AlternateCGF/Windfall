"""DolphinHook — the single choke-point for reading/writing Dolphin's emulated GameCube RAM.

GameCube memory is big-endian; the host is little-endian. To keep endianness explicit and
unambiguous we drive *all* integer access through raw ``read_bytes``/``write_bytes`` and
struct-(un)pack with a leading ``>`` (big-endian). Floats go through the library's dedicated
float helpers which already handle the swap.

No UI or game-model code should ever import ``dolphin_memory_engine`` directly — it goes through
this class so the backend (and later a raw ctypes fallback, or USA/PAL support) stays swappable.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable

import dolphin_memory_engine as dme

# GameCube MEM1 (24 MB) and Wii MEM2 windows in the emulated address space.
_MEM1_START, _MEM1_END = 0x80000000, 0x81800000
_MEM2_START, _MEM2_END = 0x90000000, 0x94000000


@dataclass(frozen=True)
class Vec3:
    """A GameCube 3-float vector (x, y, z)."""

    x: float
    y: float
    z: float

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


class NotHookedError(RuntimeError):
    """Raised when a read/write is attempted before Dolphin is hooked."""


class DolphinHook:
    """Thin, endianness-explicit wrapper over dolphin-memory-engine."""

    # ---- connection ---------------------------------------------------------
    def connect(self) -> bool:
        """Attempt to hook a running Dolphin with active emulation. Returns True on success."""
        if not dme.is_hooked():
            dme.hook()
        return dme.is_hooked()

    def disconnect(self) -> None:
        if dme.is_hooked():
            dme.un_hook()

    def is_connected(self) -> bool:
        # get_status() also re-validates the hook if emulation stopped.
        return dme.is_hooked()

    @staticmethod
    def is_valid_address(addr: int) -> bool:
        return (_MEM1_START <= addr < _MEM1_END) or (_MEM2_START <= addr < _MEM2_END)

    def _require(self, addr: int) -> None:
        if not dme.is_hooked():
            raise NotHookedError("Dolphin is not hooked")
        if not self.is_valid_address(addr):
            raise ValueError(f"address 0x{addr:08X} outside emulated RAM")

    # ---- reads --------------------------------------------------------------
    def read_bytes(self, addr: int, size: int) -> bytes:
        self._require(addr)
        return dme.read_bytes(addr, size)

    def read_u8(self, addr: int) -> int:
        return self.read_bytes(addr, 1)[0]

    def read_s8(self, addr: int) -> int:
        return struct.unpack(">b", self.read_bytes(addr, 1))[0]

    def read_u16(self, addr: int) -> int:
        return struct.unpack(">H", self.read_bytes(addr, 2))[0]

    def read_s16(self, addr: int) -> int:
        return struct.unpack(">h", self.read_bytes(addr, 2))[0]

    def read_u32(self, addr: int) -> int:
        return struct.unpack(">I", self.read_bytes(addr, 4))[0]

    def read_s32(self, addr: int) -> int:
        return struct.unpack(">i", self.read_bytes(addr, 4))[0]

    def read_f32(self, addr: int) -> float:
        return struct.unpack(">f", self.read_bytes(addr, 4))[0]

    def read_vec3(self, addr: int) -> Vec3:
        x, y, z = struct.unpack(">fff", self.read_bytes(addr, 12))
        return Vec3(x, y, z)

    def read_string(self, addr: int, max_len: int = 64, encoding: str = "shift_jis") -> str:
        raw = self.read_bytes(addr, max_len)
        raw = raw.split(b"\x00", 1)[0]
        return raw.decode(encoding, errors="replace")

    # ---- writes -------------------------------------------------------------
    def write_bytes(self, addr: int, data: bytes) -> None:
        self._require(addr)
        dme.write_bytes(addr, data)

    def write_u8(self, addr: int, value: int) -> None:
        self.write_bytes(addr, struct.pack(">B", value & 0xFF))

    def write_u16(self, addr: int, value: int) -> None:
        self.write_bytes(addr, struct.pack(">H", value & 0xFFFF))

    def write_s16(self, addr: int, value: int) -> None:
        self.write_bytes(addr, struct.pack(">h", value))

    def write_u32(self, addr: int, value: int) -> None:
        self.write_bytes(addr, struct.pack(">I", value & 0xFFFFFFFF))

    def write_f32(self, addr: int, value: float) -> None:
        self.write_bytes(addr, struct.pack(">f", value))

    def write_vec3(self, addr: int, vec: Vec3) -> None:
        self.write_bytes(addr, struct.pack(">fff", vec.x, vec.y, vec.z))

    # ---- pointer following --------------------------------------------------
    def follow(self, base: int, offsets: Iterable[int]) -> int | None:
        """Read the pointer at ``base``, then walk each offset. Returns the final address.

        Returns None if any link is a null / out-of-range pointer, so callers can bail cleanly
        when the game hasn't populated the structure yet (e.g. on a loading screen).
        """
        addr = base
        offs = list(offsets)
        for i, off in enumerate(offs):
            ptr = self.read_u32(addr)
            if not self.is_valid_address(ptr):
                return None
            addr = ptr + off
            # After dereferencing the last offset we return the address itself (not a deref).
            if i == len(offs) - 1:
                return addr
        return addr
