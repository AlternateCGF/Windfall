"""Live collision geometry — read the loaded stage/room DZB meshes from the dBgS registry.

Structure chain (offsets from the CGF95/tww decomp; array offset verified live):
  g_dComIfG_gameInfo + 0x12A0  = dBgS.m_chk_element[256] (cBgS check-element array;
    located by scanning for the 256x repeated slot vtable at +0x10, stride 0x14)
    each 0x14-byte slot: cBgW* at +0x00, flags at +0x04 (bit 0 = in use)
      cBgW + 0x90              : pm_vtx_tbl — vertex table (transformed copy; preferred)
      cBgW + 0x94              : pm_bgd     — cBgD_t, the parsed DZB header
        cBgD_t: s32 v_num; Vtx* v_tbl; s32 t_num; Tri* t_tbl; ...
          Vtx = 3 x f32 (12 bytes) ; Tri = 5 x u16 (10 bytes: vtx0, vtx1, vtx2, id, grp)

Only X/Z are kept — the interactive map is top-down.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..addresses.version import Addresses
from ..memory.hook import DolphinHook

_CHK_ELM_ARRAY_OFF = 0x12A0  # dBgS.m_chk_element within g_dComIfG_gameInfo (verified live, JP)
_CHK_ELM_COUNT = 256
_CHK_ELM_SIZE = 0x14
_BGW_VTX_TBL_OFF = 0x90
_BGW_BGD_OFF = 0x94

# Sanity caps so a mid-load garbage pointer can't make us read megabytes of noise.
_MAX_VERTS = 200_000
_MAX_TRIS = 200_000


@dataclass
class CollisionMesh:
    """One registered cBgW's triangles projected to world XZ."""

    bgw_addr: int
    # Flat triangle list: (x0, z0, x1, z1, x2, z2) per triangle.
    tris: list[tuple[float, float, float, float, float, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"bgw_addr": self.bgw_addr, "tris": [list(t) for t in self.tris]}

    @classmethod
    def from_dict(cls, d: dict) -> CollisionMesh:
        return cls(
            bgw_addr=d["bgw_addr"],
            tris=[tuple(t) for t in d.get("tris", [])],
        )


class CollisionReader:
    def __init__(self, hook: DolphinHook, addr: Addresses) -> None:
        self._hook = hook
        self._addr = addr

    def read_meshes(self) -> list[CollisionMesh]:
        """Read every registered collision mesh. Returns [] when unavailable."""
        base = self._addr.game_info + _CHK_ELM_ARRAY_OFF
        try:
            slots = self._hook.read_bytes(base, _CHK_ELM_COUNT * _CHK_ELM_SIZE)
        except Exception:
            return []
        meshes: list[CollisionMesh] = []
        seen: set[int] = set()
        for i in range(_CHK_ELM_COUNT):
            bgw, flags = struct.unpack_from(">II", slots, i * _CHK_ELM_SIZE)
            if not (flags & 1):  # slot not in use
                continue
            if bgw in seen or not self._hook.is_valid_address(bgw):
                continue
            seen.add(bgw)
            mesh = self._read_bgw(bgw)
            if mesh is not None and mesh.tris:
                meshes.append(mesh)
        return meshes

    def _read_bgw(self, bgw: int) -> CollisionMesh | None:
        try:
            vtx_tbl, bgd = struct.unpack(
                ">II", self._hook.read_bytes(bgw + _BGW_VTX_TBL_OFF, 8)
            )
            if not self._hook.is_valid_address(bgd):
                return None
            v_num, v_tbl, t_num, t_tbl = struct.unpack(
                ">iIiI", self._hook.read_bytes(bgd, 16)
            )
            # Prefer the transformed vertex copy (correct for moving/room-placed BGs).
            if self._hook.is_valid_address(vtx_tbl):
                v_tbl = vtx_tbl
            if not (0 < v_num <= _MAX_VERTS and 0 < t_num <= _MAX_TRIS):
                return None
            if not (self._hook.is_valid_address(v_tbl) and self._hook.is_valid_address(t_tbl)):
                return None
            v_raw = self._hook.read_bytes(v_tbl, v_num * 12)
            t_raw = self._hook.read_bytes(t_tbl, t_num * 10)
        except Exception:
            return None

        verts = struct.unpack(f">{v_num * 3}f", v_raw)
        mesh = CollisionMesh(bgw_addr=bgw)
        tris = mesh.tris
        for t in range(t_num):
            i0, i1, i2 = struct.unpack_from(">HHH", t_raw, t * 10)
            if i0 >= v_num or i1 >= v_num or i2 >= v_num:
                continue
            tris.append(
                (
                    verts[i0 * 3], verts[i0 * 3 + 2],
                    verts[i1 * 3], verts[i1 * 3 + 2],
                    verts[i2 * 3], verts[i2 * 3 + 2],
                )
            )
        return mesh
