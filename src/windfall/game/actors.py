"""Live actor enumeration — walk the fopAc actor queue and name each entry.

Every loaded actor (fopAc_ac_c) carries an intrusive {next, prev} queue node at
``actor_node_off``; the queue's head is a static global. Walking next-pointers from the
head visits every live actor. Names come from the game's own l_objectName table
(char[8] name + u16 procName per 12-byte entry), read once per connection and inverted
into procName -> object names.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Optional

from ..addresses.version import Addresses
from ..memory.hook import DolphinHook

_ENTRY_SIZE = 12
_MAX_ACTORS = 512  # safety cap for a corrupt/looping list

# fopAc_ac_c cull volume (fopAc_Cull_e): 0x00-0x0D index the default box table,
# 0x0E = custom box, 0x0F-0x16 index the default sphere table, 0x17 = custom sphere.
_CULLBOX_CUSTOM = 0x0E
_CULLSPHERE_FIRST = 0x0F
_CULLSPHERE_CUSTOM = 0x17
_CULL_UNION_SIZE = 0x18  # box{min,max cXyz} | sphere{center cXyz, f32 radius}

# Default cull tables from f_op_actor_mng.cpp (l_cullSizeBox / l_cullSizeSphere),
# indexed by cullType. Boxes are (min, max) in actor-local space.
_DEFAULT_BOXES = (
    ((-40.0, 0.0, -40.0), (40.0, 125.0, 40.0)),
    ((-25.0, 0.0, -25.0), (25.0, 50.0, 25.0)),
    ((-50.0, 0.0, -50.0), (50.0, 100.0, 50.0)),
    ((-75.0, 0.0, -75.0), (75.0, 150.0, 75.0)),
    ((-100.0, 0.0, -100.0), (100.0, 800.0, 100.0)),
    ((-125.0, 0.0, -125.0), (125.0, 250.0, 125.0)),
    ((-150.0, 0.0, -150.0), (150.0, 300.0, 150.0)),
    ((-200.0, 0.0, -200.0), (200.0, 400.0, 200.0)),
    ((-600.0, 0.0, -600.0), (600.0, 900.0, 600.0)),
    ((-250.0, 0.0, -50.0), (250.0, 450.0, 50.0)),
    ((-60.0, 0.0, -20.0), (40.0, 130.0, 150.0)),
    ((-75.0, 0.0, -75.0), (75.0, 210.0, 75.0)),
    ((-70.0, -100.0, -80.0), (70.0, 240.0, 100.0)),
    ((-60.0, -20.0, -60.0), (60.0, 160.0, 60.0)),
)
_DEFAULT_SPHERE_RADII = (80.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 400.0)

# Reject cull data that is garbage (mid-load reads): any coordinate beyond this is discarded.
_MAX_BOUND = 1e7

# Ambient/invisible actors that make poor camera targets and clutter the map:
# island LOD models, invisible Tag triggers, the Tingle-tuner ghost, skybox, the sea.
_AMBIENT_PREFIXES = ("lod", "tag", "agb", "vrbox", "sea")


def is_ambient(name: str) -> bool:
    return name.lower().startswith(_AMBIENT_PREFIXES)


@dataclass(frozen=True)
class ActorInfo:
    address: int  # absolute address of the fopAc_ac_c
    pid: int  # unique process id
    proc: int  # procName (profile id)
    name: str  # first matching object name, or "proc 0xNNNN"
    pos: tuple[float, float, float]
    # World-XZ footprint of the actor's cull volume — at most one is set.
    bounds_rect: Optional[tuple[float, float, float, float]] = None  # x_min, z_min, x_max, z_max
    bounds_circle: Optional[tuple[float, float, float]] = None  # center x, center z, radius


class ActorList:
    def __init__(self, hook: DolphinHook, addr: Addresses) -> None:
        self._hook = hook
        self._addr = addr
        self._names: dict[int, list[str]] | None = None

    def available(self) -> bool:
        a = self._addr
        return None not in (a.actor_queue_head, a.actor_node_off, a.actor_pos_off)

    # ---- object name table ---------------------------------------------------
    def _proc_names(self) -> dict[int, list[str]]:
        if self._names is not None:
            return self._names
        names: dict[int, list[str]] = {}
        a = self._addr
        if a.objectname_table and a.objectname_count:
            try:
                blob = self._hook.read_bytes(a.objectname_table, a.objectname_count * _ENTRY_SIZE)
                for i in range(0, len(blob) - _ENTRY_SIZE + 1, _ENTRY_SIZE):
                    raw = blob[i : i + 8].split(b"\x00")[0]
                    (proc,) = struct.unpack_from(">H", blob, i + 8)
                    text = raw.decode("ascii", errors="replace")
                    if text:
                        names.setdefault(proc, []).append(text)
            except Exception:
                return {}  # retry next call; don't cache a partial table
        self._names = names
        return names

    def label_for(self, proc: int) -> str:
        entries = self._proc_names().get(proc)
        if not entries:
            return f"proc 0x{proc:04X}"
        if len(entries) == 1:
            return entries[0]
        return f"{entries[0]} (+{len(entries) - 1})"

    def invalidate_cache(self) -> None:
        self._names = None

    # ---- enumeration ----------------------------------------------------------
    def enumerate(self) -> list[ActorInfo]:
        """Snapshot of all live actors, in queue order. Empty on any failure."""
        if not self.available():
            return []
        a = self._addr
        head = a.actor_queue_head
        node_off = a.actor_node_off
        pos_off = a.actor_pos_off
        # One read covers pid/proc through current.pos — plus the cull fields when known.
        cull_ok = None not in (a.actor_cull_type_off, a.actor_cull_mtx_off, a.actor_cull_data_off)
        span = pos_off + 12
        if cull_ok:
            span = max(span, a.actor_cull_data_off + _CULL_UNION_SIZE)

        out: list[ActorInfo] = []
        seen: set[int] = set()
        # Head layout: {tail @ +0, first @ +4, count @ +8}. Walk next-pointers from first.
        try:
            node = self._hook.read_u32(head + 4)
        except Exception:
            return []
        while (
            self._hook.is_valid_address(node)
            and node != head
            and node not in seen
            and len(out) < _MAX_ACTORS
        ):
            seen.add(node)
            owner = node - node_off
            try:
                blob = self._hook.read_bytes(owner, span)
            except Exception:
                break
            (pid,) = struct.unpack_from(">I", blob, 4)
            (proc,) = struct.unpack_from(">H", blob, 8)
            x, y, z = struct.unpack_from(">fff", blob, pos_off)
            rect = circle = None
            if cull_ok:
                rect, circle = self._cull_footprint(blob, (x, y, z))
            out.append(
                ActorInfo(owner, pid, proc, self.label_for(proc), (x, y, z), rect, circle)
            )
            (node,) = struct.unpack_from(">I", blob, node_off)
        return out

    # ---- cull volume -> map footprint ------------------------------------------
    def _read_cull_mtx(self, blob: bytes) -> Optional[tuple[float, ...]]:
        """Dereference cullMtx: a 3x4 row-major float matrix (local -> world)."""
        (ptr,) = struct.unpack_from(">I", blob, self._addr.actor_cull_mtx_off)
        if not self._hook.is_valid_address(ptr):
            return None
        try:
            return struct.unpack(">12f", self._hook.read_bytes(ptr, 48))
        except Exception:
            return None

    def _cull_footprint(
        self, blob: bytes, pos: tuple[float, float, float]
    ) -> tuple[Optional[tuple], Optional[tuple]]:
        """Project the actor's cull volume to world XZ: (rect, circle), at most one set.

        Boxes: all 8 corners go through cullMtx and the result is the world-axis-aligned
        XZ extent. Spheres: the center goes through cullMtx, the radius is scaled by the
        matrix's axis scale. Without a valid matrix, fall back to offsetting by pos.
        """
        cull_type = blob[self._addr.actor_cull_type_off]
        data_off = self._addr.actor_cull_data_off

        if cull_type < _CULLSPHERE_FIRST:  # box
            if cull_type == _CULLBOX_CUSTOM:
                v = struct.unpack_from(">6f", blob, data_off)
                mn, mx = v[0:3], v[3:6]
            elif cull_type < len(_DEFAULT_BOXES):
                mn, mx = _DEFAULT_BOXES[cull_type]
            else:
                return None, None
            m = self._read_cull_mtx(blob)
            if m is None:
                rect = (pos[0] + mn[0], pos[2] + mn[2], pos[0] + mx[0], pos[2] + mx[2])
            else:
                xs, zs = [], []
                for cx in (mn[0], mx[0]):
                    for cy in (mn[1], mx[1]):
                        for cz in (mn[2], mx[2]):
                            xs.append(m[0] * cx + m[1] * cy + m[2] * cz + m[3])
                            zs.append(m[8] * cx + m[9] * cy + m[10] * cz + m[11])
                rect = (min(xs), min(zs), max(xs), max(zs))
            if all(math.isfinite(v) and abs(v) < _MAX_BOUND for v in rect):
                return rect, None
            return None, None

        if cull_type <= _CULLSPHERE_CUSTOM:  # sphere
            if cull_type == _CULLSPHERE_CUSTOM:
                cx, cy, cz, r = struct.unpack_from(">4f", blob, data_off)
            else:
                cx = cy = cz = 0.0
                r = _DEFAULT_SPHERE_RADII[cull_type - _CULLSPHERE_FIRST]
            m = self._read_cull_mtx(blob)
            if m is None:
                circle = (pos[0] + cx, pos[2] + cz, r)
            else:
                wx = m[0] * cx + m[1] * cy + m[2] * cz + m[3]
                wz = m[8] * cx + m[9] * cy + m[10] * cz + m[11]
                scale = math.sqrt(m[0] * m[0] + m[4] * m[4] + m[8] * m[8])
                circle = (wx, wz, r * (scale if scale > 1e-6 else 1.0))
            if all(math.isfinite(v) and abs(v) < _MAX_BOUND for v in circle) and circle[2] > 0:
                return None, circle
            return None, None

        return None, None
