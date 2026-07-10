"""Tiny PowerPC decoder — just enough to recover the static address a small accessor loads.

Wind Waker accessors like ``daPy_getPlayerLinkActorClass`` are a handful of instructions that build
a global address and (usually) dereference it. Rather than hardcode per-region globals, we read the
function's bytes straight out of live Dolphin RAM and simulate the classic idioms:

    lis  rX, HI ; addi rX, rX, LO      -> address (HI<<16)+signext(LO)      [returns the object]
    lis  rX, HI ; ori  rX, rX, LO      -> address (HI<<16)|LO
    lis  rX, HI ; lwz  rY, LO(rX)      -> pointer stored at (HI<<16)+signext(LO)

This covers the common cases; anything exotic (e.g. loads relative to the r13/r2 small-data base)
returns ``None`` so the caller can fall back to manual inspection.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional


@dataclass
class ResolvedLoad:
    address: int  # the global address the function computed
    is_pointer: bool  # True if the function dereferences it (address holds a pointer), else it *is* the object


def _sx16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def resolve_accessor(code: bytes) -> Optional[ResolvedLoad]:
    """Decode up to ~8 big-endian instructions, returning the address the accessor loads."""
    regs: dict[int, int] = {}
    n = len(code) // 4
    for i in range(n):
        (w,) = struct.unpack_from(">I", code, i * 4)
        op = w >> 26
        rd = (w >> 21) & 31
        ra = (w >> 16) & 31
        imm = w & 0xFFFF

        if op == 15:  # addis / lis
            base = regs.get(ra, 0) if ra else 0
            regs[rd] = (base + (imm << 16)) & 0xFFFFFFFF
        elif op == 14:  # addi
            base = regs.get(ra, 0) if ra else 0
            regs[rd] = (base + _sx16(imm)) & 0xFFFFFFFF
        elif op == 24:  # ori  (rS>>21, rA=dest)
            rs = (w >> 21) & 31
            regs[ra] = (regs.get(rs, 0) | imm) & 0xFFFFFFFF
        elif op == 32:  # lwz  -> dereference
            base = regs.get(ra, 0) if ra else 0
            ea = (base + _sx16(imm)) & 0xFFFFFFFF
            return ResolvedLoad(address=ea, is_pointer=True)
        elif op == 19 and ((w >> 1) & 0x3FF) == 16:  # blr
            break
        # other instructions (mr, etc.) are ignored for this narrow purpose

    # No load hit; if the function built an address in r3, treat it as a direct object address.
    if 3 in regs:
        return ResolvedLoad(address=regs[3], is_pointer=False)
    return None
