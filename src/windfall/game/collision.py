"""Live collision geometry — read the loaded stage/room DZB meshes from the dBgS registry.

Structure chain (offsets from the zeldaret/tww decomp; array offset verified live):
  g_dComIfG_gameInfo + 0x12A0  = dBgS.m_chk_element[256] (cBgS check-element array;
    located by scanning for the 256x repeated slot vtable at +0x10, stride 0x14)
    each 0x14-byte slot: cBgW* at +0x00, flags at +0x04 (bit 0 = in use)
      cBgW + 0x90              : pm_vtx_tbl — vertex table (transformed copy; preferred)
      cBgW + 0x94              : pm_bgd     — cBgD_t, the parsed DZB header (c_bg_w.h):
        +0x00 s32 v_num; +0x04 Vtx* v_tbl; +0x08 s32 t_num; +0x0C Tri* t_tbl;
        +0x28 s32 ti_num; +0x2C Ti* ti_tbl
          Vtx = 3 x f32 (12 bytes)
          Tri (cBgD_Tri_t) = 5 x u16 (10 bytes: vtx0, vtx1, vtx2, id, grp)
          Ti  (cBgD_Ti_t)  = 4 x u32 (16 bytes: polyInf0, polyInf1, polyInf2, polyInf3)

Each triangle's terrain type (grass/sand/stone/water/...) comes from a second,
indirect table: Tri.id indexes into ti_tbl, whose polyInf1 field's bits 16-20
(mask 0x001F0000) give a raw attribute index that maps through ``_ATTR_CONV``
to a dBgS_AttributeCode (see dBgS::GetAttributeCode in the decomp's d_bg_s.cpp).

X/Z drive the top-down map; Y is kept alongside (not projected away) so a teleport
target's ground height can be looked up via ``ground_height_below``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from ..addresses.version import Addresses
from ..memory.hook import DolphinHook

_CHK_ELM_ARRAY_OFF = 0x12A0  # dBgS.m_chk_element within g_dComIfG_gameInfo (verified live, JP)
_CHK_ELM_COUNT = 256
_CHK_ELM_SIZE = 0x14
_BGW_VTX_TBL_OFF = 0x90
_BGW_BGD_OFF = 0x94
_BGD_TI_HEADER_OFF = 0x28  # cBgD_t.m_ti_num / m_ti_tbl, right after the group table fields

# Sanity caps so a mid-load garbage pointer can't make us read megabytes of noise.
_MAX_VERTS = 200_000
_MAX_TRIS = 200_000
_MAX_TI = 200_000

# dBgS_AttributeCode (d_bg_s.h) — terrain/material type per triangle.
ATTR_NORMAL, ATTR_DIRT, ATTR_WOOD, ATTR_STONE, ATTR_GRASS = 0x00, 0x01, 0x02, 0x03, 0x04
ATTR_GIANT_FLOWER, ATTR_LAVA, ATTR_VOID, ATTR_DAMAGE = 0x05, 0x06, 0x08, 0x09
ATTR_CARPET, ATTR_SAND, ATTR_ICE, ATTR_WATER, ATTR_METAL = 0x0A, 0x0B, 0x0F, 0x13, 0x14
ATTR_FREEZE, ATTR_ELECTRICITY, ATTR_WATERFALL = 0x15, 0x16, 0x17

# Raw attribute index (bits 16-20 of polyInf1, 0-31) -> dBgS_AttributeCode, straight from
# the decomp's static atr_conv[] table in d_bg_s.cpp.
_ATTR_CONV = [
    ATTR_NORMAL, ATTR_DIRT, ATTR_WOOD, ATTR_STONE, ATTR_GRASS, ATTR_GIANT_FLOWER, ATTR_LAVA,
    ATTR_DIRT, ATTR_VOID, ATTR_DAMAGE, ATTR_CARPET, ATTR_SAND, ATTR_WOOD, ATTR_WOOD, ATTR_WOOD,
    ATTR_ICE, ATTR_WOOD, ATTR_METAL, ATTR_DIRT, ATTR_WATER, ATTR_METAL, ATTR_FREEZE,
    ATTR_ELECTRICITY, ATTR_WATERFALL, ATTR_METAL, ATTR_CARPET, ATTR_WOOD, ATTR_NORMAL,
    ATTR_NORMAL, ATTR_NORMAL, ATTR_NORMAL, ATTR_NORMAL,
]


@dataclass
class CollisionMesh:
    """One registered cBgW's triangles (world XZ for the map, plus each vertex's Y)."""

    bgw_addr: int
    # Flat triangle list: (x0, z0, x1, z1, x2, z2) per triangle.
    tris: list[tuple[float, float, float, float, float, float]] = field(default_factory=list)
    # Parallel to tris: (y0, y1, y2) per triangle, for ground-height lookups.
    tris_y: list[tuple[float, float, float]] = field(default_factory=list)
    # Parallel to tris: dBgS_AttributeCode (terrain type) per triangle.
    tris_attr: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "bgw_addr": self.bgw_addr,
            "tris": [list(t) for t in self.tris],
            "tris_y": [list(t) for t in self.tris_y],
            "tris_attr": self.tris_attr,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CollisionMesh:
        return cls(
            bgw_addr=d["bgw_addr"],
            tris=[tuple(t) for t in d.get("tris", [])],
            # Absent in caches saved before these existed — those features just get no
            # data for the still-cached meshes until the stage is re-read.
            tris_y=[tuple(t) for t in d.get("tris_y", [])],
            tris_attr=list(d.get("tris_attr", [])),
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
        ti_num, ti_tbl = 0, 0
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
            try:
                ti_num, ti_tbl = struct.unpack(
                    ">iI", self._hook.read_bytes(bgd + _BGD_TI_HEADER_OFF, 8)
                )
                if not (0 < ti_num <= _MAX_TI and self._hook.is_valid_address(ti_tbl)):
                    ti_num, ti_tbl = 0, 0
            except Exception:
                ti_num, ti_tbl = 0, 0
        except Exception:
            return None

        # polyInf1 (the 2nd of 4 u32s per ti_tbl entry) bits 16-20 = raw attribute index.
        ti_inf1: list[int] = []
        if ti_num:
            try:
                ti_raw = self._hook.read_bytes(ti_tbl, ti_num * 16)
                ti_inf1 = list(struct.unpack(f">{ti_num * 4}I", ti_raw))[1::4]
            except Exception:
                ti_inf1 = []

        verts = struct.unpack(f">{v_num * 3}f", v_raw)
        mesh = CollisionMesh(bgw_addr=bgw)
        tris = mesh.tris
        tris_y = mesh.tris_y
        tris_attr = mesh.tris_attr
        for t in range(t_num):
            i0, i1, i2, ti_id, _grp = struct.unpack_from(">HHHHH", t_raw, t * 10)
            if i0 >= v_num or i1 >= v_num or i2 >= v_num:
                continue
            tris.append(
                (
                    verts[i0 * 3], verts[i0 * 3 + 2],
                    verts[i1 * 3], verts[i1 * 3 + 2],
                    verts[i2 * 3], verts[i2 * 3 + 2],
                )
            )
            tris_y.append((verts[i0 * 3 + 1], verts[i1 * 3 + 1], verts[i2 * 3 + 1]))
            attr = ATTR_NORMAL
            if ti_id < len(ti_inf1):
                raw_idx = (ti_inf1[ti_id] & 0x001F0000) >> 16
                if raw_idx < len(_ATTR_CONV):
                    attr = _ATTR_CONV[raw_idx]
            tris_attr.append(attr)
        return mesh


def _barycentric_xz(
    px: float, pz: float,
    x0: float, z0: float, x1: float, z1: float, x2: float, z2: float,
) -> Optional[tuple[float, float, float]]:
    """Barycentric weights of (px, pz) in triangle (x0,z0)-(x1,z1)-(x2,z2), or None if outside."""
    denom = (z1 - z2) * (x0 - x2) + (x2 - x1) * (z0 - z2)
    if abs(denom) < 1e-9:
        return None  # degenerate (zero-area when projected to XZ)
    a = ((z1 - z2) * (px - x2) + (x2 - x1) * (pz - z2)) / denom
    b = ((z2 - z0) * (px - x2) + (x0 - x2) * (pz - z2)) / denom
    c = 1.0 - a - b
    epsilon = -1e-6
    if a < epsilon or b < epsilon or c < epsilon:
        return None
    return a, b, c


def ground_height_below(
    meshes: list[CollisionMesh],
    x: float,
    z: float,
    max_y: Optional[float] = None,
    margin: float = 50.0,
) -> Optional[float]:
    """Highest collision surface at world (x, z), or None if uncovered.

    ``max_y`` (e.g. Link's current height) + ``margin`` excludes surfaces too far
    above the reference, so a roof doesn't get picked over the floor beneath it.
    Y is barycentric-interpolated per triangle since meshes aren't flat."""
    best: Optional[float] = None
    limit = None if max_y is None else max_y + margin
    for mesh in meshes:
        for (x0, z0, x1, z1, x2, z2), (y0, y1, y2) in zip(mesh.tris, mesh.tris_y):
            bc = _barycentric_xz(x, z, x0, z0, x1, z1, x2, z2)
            if bc is None:
                continue
            a, b, c = bc
            y = a * y0 + b * y1 + c * y2
            if limit is not None and y > limit:
                continue
            if best is None or y > best:
                best = y
    return best
