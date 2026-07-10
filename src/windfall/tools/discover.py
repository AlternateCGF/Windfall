"""Discover Link's *writable* actor position (and the player pointer), to enable teleporting.

    python -m windfall.tools.discover --test-write

The debug mirror globals are read-only, so teleporting Link requires his actual actor position.
This tool, run with the game LOADED AND UNPAUSED and Link standing on open ground:

  1. reads the read-only mirror as a reference position,
  2. decodes the player accessor function live to recover the player-pointer global,
  3. follows it to the actor and scans the whole object for float-triples matching the mirror,
  4. with --test-write, writes each candidate +100 on X and measures how far the change
     PROPAGATES (to the mirror and to the other copies). The authoritative current.pos scores
     highest; pure sink copies (old/home/mirror) don't propagate at all.

It also samples the VI retrace counter to confirm emulation is actually advancing — if it isn't
(paused / frame-advance), the propagation test can't work and the tool says so.

Paste the printed results back and they get baked into the address table.
"""

from __future__ import annotations

import argparse
import struct
import time

from ..addresses.version import detect_version
from ..memory.hook import DolphinHook
from ..memory.ppc import resolve_accessor

_EPS = 0.5


def _scan_for_triples(hook: DolphinHook, base: int, ref, span: int):
    """Return [(offset, (x,y,z))] where three consecutive big-endian floats ≈ ref."""
    rx, ry, rz = ref
    out = []
    try:
        blob = hook.read_bytes(base, span)
    except Exception:
        return out
    for off in range(0, span - 12, 4):
        x, y, z = struct.unpack_from(">fff", blob, off)
        if abs(x - rx) < _EPS and abs(y - ry) < _EPS and abs(z - rz) < _EPS:
            out.append((off, (x, y, z)))
    return out


def _emulation_advancing(hook: DolphinHook, addr, seconds: float = 0.3) -> tuple[bool, int]:
    """Watch the VI retrace counter to confirm Dolphin is actively emulating."""
    if not addr.retrace_count:
        return True, 0  # can't tell; assume yes
    start = hook.read_u32(addr.retrace_count)
    time.sleep(seconds)
    delta = (hook.read_u32(addr.retrace_count) - start) & 0xFFFFFFFF
    return delta > 0, delta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-write", action="store_true",
                    help="probe each candidate to find the one that drives Link (propagation test)")
    ap.add_argument("--span", type=lambda s: int(s, 0), default=0x3800,
                    help="bytes of the actor to scan (default 0x3800)")
    args = ap.parse_args()

    hook = DolphinHook()
    print("Waiting for Dolphin…")
    while not hook.connect():
        time.sleep(0.5)
    game_id, version = detect_version(hook)
    if version is None:
        print(f"Unsupported game id {game_id!r}")
        return 1
    addr = version.addr
    print(f"Hooked {version.label} ({game_id})\n")

    advancing, delta = _emulation_advancing(hook, addr)
    print(f"Emulation advancing: {advancing} (retrace +{delta} in 0.3s)")
    if not advancing:
        print("  ⚠  Retrace counter isn't moving — the game looks paused / frame-advanced.")
        print("     UNPAUSE the game so physics runs, then re-run. Continuing anyway…\n")

    ref = hook.read_vec3(addr.link_pos)
    print(f"Mirror position (read-only): X={ref.x:.3f} Y={ref.y:.3f} Z={ref.z:.3f}")

    if not addr.player_accessor:
        print("No player_accessor address for this version.")
        return 1
    code = hook.read_bytes(addr.player_accessor, 32)
    print(f"\nAccessor @ 0x{addr.player_accessor:08X}: " + " ".join(f"{b:02X}" for b in code[:16]))
    resolved = resolve_accessor(code)
    if resolved is None:
        print("Could not decode the accessor idiom — paste the bytes above for manual analysis.")
        return 1

    if resolved.is_pointer:
        ptr_global = resolved.address
        actor = hook.read_u32(ptr_global)
        print(f"Player-pointer global: 0x{ptr_global:08X}  ->  actor @ 0x{actor:08X}")
    else:
        actor, ptr_global = resolved.address, None
        print(f"Accessor returns object address directly: 0x{actor:08X}")

    if not hook.is_valid_address(actor):
        print("Actor pointer is null/invalid — make sure Link is loaded and controllable.")
        return 1

    print(f"\nScanning actor for the position triple (span 0x{args.span:X})…")
    hits = _scan_for_triples(hook, actor, ref.as_tuple(), args.span)
    if not hits:
        print("No matching triple found. Try again while standing still on solid ground.")
        return 1
    for off, vals in hits:
        print(f"  actor + 0x{off:04X}  =>  abs 0x{actor + off:08X}")

    driving_off = None
    if args.test_write:
        print("\n[--test-write] continuous-hammer test (write X+300 every frame for ~0.4s;")
        print("               the copy that makes the mirror follow is current.pos)…")
        offs = [o for o, _ in hits]
        delta = 300.0
        for c in offs:
            original = hook.read_vec3(actor + c)
            target_x = original.x + delta
            # Hammer the value faster than the game's per-frame overwrite for ~0.4s.
            deadline = time.perf_counter() + 0.4
            while time.perf_counter() < deadline:
                hook.write_f32(actor + c, target_x)
                time.sleep(0.001)
            mirror_x = hook.read_f32(addr.link_pos)
            followed = abs(mirror_x - target_x) < 5.0
            hook.write_vec3(actor + c, original)  # put it back
            time.sleep(0.05)
            marker = "  <-- DRIVES LINK ✓ (current.pos)" if followed else ""
            print(f"  actor + 0x{c:04X}: mirror {'FOLLOWED' if followed else 'unchanged'}{marker}")
            if followed and driving_off is None:
                driving_off = c
        if driving_off is None:
            print("  Still nothing. Link's live position must be stored in a separate field —")
            print("  next step is reading it from the daPy_lk_c layout in the decomp.")

    print("\n--- suggested table values ---")
    if ptr_global is not None:
        print(f"player_ptr     = 0x{ptr_global:08X}")
    if driving_off is not None:
        print(f"player_pos_off = 0x{driving_off:04X}   (confirmed: drives Link)")
    else:
        print(f"player_pos_off = 0x{hits[0][0]:04X}   (UNCONFIRMED)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
