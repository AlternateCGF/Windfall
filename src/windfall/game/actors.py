"""Live actor enumeration — walk the fopAc actor queue and name each entry.

Every loaded actor (fopAc_ac_c) carries an intrusive {next, prev} queue node at
``actor_node_off``; the queue's head is a static global. Walking next-pointers from the
head visits every live actor. Names come from the game's own l_objectName table
(char[8] name + u16 procName per 12-byte entry), read once per connection and inverted
into procName -> object names.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..addresses.version import Addresses
from ..memory.hook import DolphinHook

_ENTRY_SIZE = 12
_MAX_ACTORS = 512  # safety cap for a corrupt/looping list

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
        span = pos_off + 12  # one read covers pid/proc through current.pos

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
            out.append(ActorInfo(owner, pid, proc, self.label_for(proc), (x, y, z)))
            (node,) = struct.unpack_from(">I", blob, node_off)
        return out
