"""Game-version detection and per-region address tables.

Addresses are sourced from the CGF95/tww decompilation symbol maps
(config/<GAMEID>/symbols.txt) and cross-referenced with the community RAM map at
https://github.com/LagoLunatic/WW-Hacking-Docs .

Primary target is the Japanese GameCube release (GZLJ01). USA (GZLE01) values are included
because the same symbol names resolve cleanly in both maps — a nice free bonus and a sanity
check. Fields we haven't yet verified live are left as ``None`` and filled in during M1.

Verification status of the static player globals (as of M0):
  - ``link_pos`` is the decomp symbol ``l_debug_keep_pos`` (3 floats X/Y/Z, size 0xC).
  - ``link_angle_y`` is ``l_debug_current_angle`` + 2 (the Y component of a 3x s16 vector).
These are documented by the community RAM map as mirroring the player entity every frame, but
because they are *debug* globals they MUST be confirmed against a live game (see game/player.py
for the more-robust actor-pointer path we add once the offsets are verified).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..memory.hook import DolphinHook

# The 6-char game id lives at the very start of MEM1 (disc header: 4-byte game code + 2-byte maker).
_GAME_ID_ADDR = 0x80000000


@dataclass(frozen=True)
class CameraOffsets:
    """Field offsets inside the dCamera_c object (relative to the camera instance pointer).

    Derived from the zeldaret/tww decomp d_camera.h layout.
    ``eye`` is the camera position, ``center`` (a.k.a. "at") is the look-at target.
    ``cam_class_off`` is the offset within camera_class where dCamera_c is embedded.
    """

    cam_class_off: int  # offset of dCamera_c within camera_class (0x244)
    eye: int  # 0x01C — Vec3
    center: int  # 0x010 — Vec3
    up: int  # 0x028 — Vec3
    fovy: int  # 0x038 — f32
    roll: int  # 0x034 — s16 (bank angle)


@dataclass(frozen=True)
class Addresses:
    """Static addresses and struct offsets for one game version."""

    game_id: str
    label: str

    game_info: int  # g_dComIfG_gameInfo (root of most live state)

    # Player statics (debug mirror globals — verify live).
    link_pos: int  # l_debug_keep_pos: 3 floats (X, Y, Z)
    link_angle_y: int  # l_debug_current_angle + 2: s16 Y facing angle

    retrace_count: Optional[int] = None  # VI retrace counter (advances while emulation runs)

    # Accessor functions (their code is decoded live to recover the static globals they read).
    player_accessor: Optional[int] = None  # daPy_getPlayerLinkActorClass
    cam_get_body: Optional[int] = None  # dCam_getBody

    # Filled in during M1 (verified live via tools/discover.py).
    player_ptr: Optional[int] = None  # static global holding the player actor pointer
    player_pos_off: Optional[int] = None  # offset of writable Vec3 position inside the actor
    camera_ptr: Optional[int] = None  # static global holding camera_class* pointer
    camera: Optional[CameraOffsets] = None
    # Offsets into g_dComIfG_gameInfo for stage/room/spawn/layer (M1).
    stage_name_off: Optional[int] = None
    room_no_off: Optional[int] = None

    # Live actor enumeration (discovered by walking the player's process links live).
    actor_queue_head: Optional[int] = None  # static head of the fopAc actor queue
    actor_node_off: Optional[int] = None  # offset of the {next, prev} queue node inside each actor
    actor_pos_off: Optional[int] = None  # fopAc_ac_c current.pos (same field as player_pos_off)
    actor_angle_off: Optional[int] = None  # fopAc_ac_c current.angle.y (s16 facing angle)
    objectname_table: Optional[int] = None  # l_objectName: {char[8] name, u16 procName, u8, u8}
    objectname_count: Optional[int] = None

    # HUD disable writes (from community gecko codes by Ralf).
    # Address→value pairs written every tick to suppress HUD rendering.
    # Empty dict means HUD disable is not supported for this version.
    hud_disable_writes: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class GameVersion:
    game_id: str
    label: str
    addr: Addresses


# --- Japanese GameCube (primary target) -------------------------------------
_JP = Addresses(
    game_id="GZLJ01",
    label="Wind Waker (JP, GameCube)",
    game_info=0x803B8108,
    link_pos=0x803D78FC,
    link_angle_y=0x803EA3C8 + 2,
    retrace_count=0x803EAFDC,
    player_accessor=0x800EDED8,
    cam_get_body=0x80178840,
    # player_ptr confirmed via live accessor decode: g_dComIfG_gameInfo + 0x5B40 -> actor.
    player_ptr=0x803BDC48,
    # current.pos inside the actor — confirmed live to drive Link (mirror-follow hammer test).
    player_pos_off=0x01F8,
    # camera_class* stored at g_dComIfG_gameInfo + 0x5B0C.
    # JPN has no mpHyruleTextArchive/field_0x481c/field_0x4820 vs USA/PAL,
    # shifting mCameraInfo (and everything after it) 0x0C bytes earlier.
    camera_ptr=0x803BDC0C,
    camera=CameraOffsets(
        cam_class_off=0x244,  # dCamera_c is embedded in camera_class at this offset
        eye=0x01C,  # dCamera_c.mEye — Vec3
        center=0x010,  # dCamera_c.mCenter — Vec3
        up=0x028,  # dCamera_c.mUp — Vec3
        fovy=0x038,  # dCamera_c.mFovy — f32
        roll=0x034,  # dCamera_c.mBank — s16 (cSAngle)
    ),
    # Actor queue: verified live (player + sea LOD actors enumerate with correct positions).
    # Head found by walking the player's queue-node prev links into static memory.
    actor_queue_head=0x803654C8,
    actor_node_off=0x0C4,  # node = {next @ +0, prev @ +4} embedded in fopAc_ac_c
    actor_pos_off=0x1F8,  # current.pos — matches player_pos_off
    actor_angle_off=0x206,  # current.angle.y — verified live against the debug angle mirror
    # l_objectName table: located live by finding the "Link" entry with procName 0x00A9,
    # then stepping 12-byte entries to the table bounds.
    objectname_table=0x80365CB8,
    objectname_count=862,
    # Stage name (char[8], null-terminated) inside dComIfG_play_c.
    # Same offset from g_dComIfG_gameInfo for all versions (the 3 removed JPN fields
    # are after this field in the struct).
    stage_name_off=0x5134,
    # HUD disable: not supported for JP (Ralf's gecko codes only cover USA/PAL).
)

# --- USA GameCube (bonus / cross-check) -------------------------------------
_USA = Addresses(
    game_id="GZLE01",
    label="Wind Waker (USA, GameCube)",
    game_info=0x803C4C08,
    link_pos=0x803E440C,
    link_angle_y=0x803F6F10 + 2,
    stage_name_off=0x5134,
    # HUD disable: Ralf's gecko codes for NTSC-U.
    # 0x801F60AC NOP skips the HUD draw call, 0x80205A7C/0x80205BA0 make branches
    # unconditional to skip HUD sub-draws, 0x803CA821 visibility flag hides elements.
    hud_disable_writes={
        0x801F60AC: 0x60000000,  # NOP (mr r3,r27 → nop)
        0x80205A7C: 0x48000070,  # b +0x70 (beq +0x70 → unconditional)
        0x80205BA0: 0x48000040,  # b +0x40 (beq +0x40 → unconditional)
        0x803CA821: 0x00000000,  # visibility byte → 0
    },
)

VERSIONS: dict[str, GameVersion] = {
    v.game_id: GameVersion(v.game_id, v.label, v)
    for v in (_JP, _USA)
}


def read_game_id(hook: DolphinHook) -> Optional[str]:
    """Read the 6-character game id from the disc header, or None if unreadable."""
    try:
        raw = hook.read_bytes(_GAME_ID_ADDR, 6)
    except Exception:
        return None
    text = raw.decode("ascii", errors="replace")
    return text if text.isprintable() else None


def detect_version(hook: DolphinHook) -> tuple[Optional[str], Optional[GameVersion]]:
    """Return (raw_game_id, GameVersion|None). The id is returned even when unsupported so the
    UI can tell the user *what* is running (e.g. a Wii/HD version we don't have a table for)."""
    game_id = read_game_id(hook)
    if not game_id:
        return None, None
    return game_id, VERSIONS.get(game_id)
