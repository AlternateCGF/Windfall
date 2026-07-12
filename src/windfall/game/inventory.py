"""Read / write the player's inventory and stats from g_dComIfG_gameInfo.

Address layout (offsets from ``game_info + player_status_off``):
  +0x00  u16  Max HP (quarters of a heart)
  +0x02  u16  Current HP
  +0x04  u16  Rupees
  +0x0E  u8   Equipped sword: an item-table ID (see ItemTable in the tww decomp's
              d_item_data.h), same namespace as inventory slots. 0xFF = none,
              0x38 = Hero's Sword, 0x39/0x3A = Master Sword partial charge,
              0x3E = Master Sword (fully charged).
  +0x0F  u8   Equipped shield: an item-table ID. 0xFF = none, 0x3B = Hero's
              Shield, 0x3C = Mirror Shield.
  +0x12  u8   Wallet tier    (0 = 200, 1 = 1000, 2 = 5000) [see note below]
  +0x13  u8   Max magic (0 = no magic meter yet; doubles once Double Magic is obtained)
  +0x14  u8   Current magic
  +0x1A  u8   Wallet tier — the value this module actually reads/writes; kept as-is
              since it predates this note and hasn't been reported broken. Per the tww
              decomp's dSv_player_status_a_c, mWalletSize is really at +0x12 (right after
              mSelectEquip[4]) with mMaxMagic/mMagic at +0x13/+0x14 immediately after —
              +0x1A falls past the end of that struct. Worth live-verifying and fixing
              separately.

Inventory slots (offsets from ``game_info + inventory_off``):
  21 bytes, one item ID each.  0xFF = empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..addresses.version import Addresses
from ..memory.hook import DolphinHook

# Item IDs that can appear in inventory slots (non-FF).
ITEM_NAMES: dict[int, str] = {
    0x20: "Telescope",
    0x21: "Tingle Tuner",
    0x22: "Wind Waker",
    0x23: "Picto Box",
    0x24: "Spoils Bag",
    0x25: "Grappling Hook",
    0x26: "Empty Bottle",
    0x27: "Bait",
    0x28: "Power Bracelets",
    0x29: "Iron Boots",
    0x2A: "Magic Armor",
    0x2C: "Bait Bag",
    0x2D: "Boomerang",
    0x2F: "Hookshot",
    0x30: "Delivery Bag",
    0x31: "Bombs",
    0x33: "Skull Hammer",
    0x34: "Deku Leaf",
    0x36: "Bow",
    0x3C: "Mirror Shield",
    0x3E: "Master Sword",
    0x3F: "Light Fairy",
    0x50: "Empty Bottle",
    0x51: "Red Potion",
    0x52: "Green Potion",
    0x53: "Blue Potion",
    0x54: "Elixir Soup (1/2)",
    0x55: "Elixir Soup",
    0x56: "Water",
    0x57: "Fairy",
    0x58: "Forest Firefly",
    0x59: "Forest Water",
    0x78: "Sail",
}


def item_name(item_id: int) -> str:
    return ITEM_NAMES.get(item_id, f"0x{item_id:02X}")


# Inventory slot definitions: (name, offset within inventory block, is_bottle_content).
# Matches dInventorySlot_e in the tww decomp's d_com_inf_game.h.
_SLOTS: list[tuple[str, int, bool]] = [
    ("Telescope", 0x00, False),
    ("Sail", 0x01, False),
    ("Wind Waker", 0x02, False),
    ("Grappling Hook", 0x03, False),
    ("Spoils Bag", 0x04, False),
    ("Boomerang", 0x05, False),
    ("Deku Leaf", 0x06, False),
    ("Tingle Tuner", 0x07, False),
    # 0x08 is dInvSlot_CAMERA_e; not exposed as an editable slot here.
    ("Iron Boots", 0x09, False),
    ("Magic Armor", 0x0A, False),
    ("Bait Bag", 0x0B, False),
    ("Bow", 0x0C, False),
    ("Bombs", 0x0D, False),
    ("Bottle 1", 0x0E, True),
    ("Bottle 2", 0x0F, True),
    ("Bottle 3", 0x10, True),
    ("Bottle 4", 0x11, True),
    ("Delivery Bag", 0x12, False),
    ("Hookshot", 0x13, False),
    ("Skull Hammer", 0x14, False),
]

# Wallet tier labels.
WALLET_TIER: dict[int, str] = {
    0: "200",
    1: "1000",
    2: "5000",
}

# Equipped-sword/shield item-table IDs (see module docstring).
_SWORD_NAMES: dict[int, str] = {
    0xFF: "(none)",
    0x38: "Hero's Sword",
    0x39: "Master Sword (uncharged)",
    0x3A: "Master Sword (half charged)",
    0x3E: "Master Sword (fully charged)",
}

_SHIELD_NAMES: dict[int, str] = {
    0xFF: "(none)",
    0x3B: "Hero's Shield",
    0x3C: "Mirror Shield",
}


@dataclass
class PlayerStats:
    max_hp: int  # quarters of a heart
    cur_hp: int
    rupees: int
    sword: int  # item ID
    shield: int
    wallet: int  # tier 0/1/2
    max_magic: int  # 0 = magic meter not yet obtained
    magic: int


@dataclass
class InventoryState:
    slots: dict[str, int]  # slot name -> item ID (0xFF = empty)


class InventoryReader:
    """Read and write the player inventory from g_dComIfG_gameInfo."""

    def __init__(self, hook: DolphinHook, addr: Addresses) -> None:
        self._hook = hook
        self._addr = addr

    def _status_base(self) -> int | None:
        off = self._addr.player_status_off
        if off is None:
            return None
        return self._addr.game_info + off

    def _inv_base(self) -> int | None:
        off = self._addr.inventory_off
        if off is None:
            return None
        return self._addr.game_info + off

    # ---- reads ----------------------------------------------------------------
    def read_stats(self) -> Optional[PlayerStats]:
        base = self._status_base()
        if base is None:
            return None
        try:
            h = self._hook
            return PlayerStats(
                max_hp=h.read_u16(base + 0x00),
                cur_hp=h.read_u16(base + 0x02),
                rupees=h.read_u16(base + 0x04),
                sword=h.read_u8(base + 0x0E),
                shield=h.read_u8(base + 0x0F),
                wallet=h.read_u8(base + 0x1A),
                max_magic=h.read_u8(base + 0x13),
                magic=h.read_u8(base + 0x14),
            )
        except Exception:
            return None

    def read_inventory(self) -> Optional[InventoryState]:
        base = self._inv_base()
        if base is None:
            return None
        try:
            slots: dict[str, int] = {}
            for name, offset, _is_bottle in _SLOTS:
                slots[name] = self._hook.read_u8(base + offset)
            return InventoryState(slots=slots)
        except Exception:
            return None

    # ---- writes ---------------------------------------------------------------
    def write_max_hp(self, quarters: int) -> bool:
        base = self._status_base()
        if base is None:
            return False
        try:
            self._hook.write_u16(base + 0x00, max(0, min(0xFFFF, quarters)))
            return True
        except Exception:
            return False

    def write_cur_hp(self, quarters: int) -> bool:
        base = self._status_base()
        if base is None:
            return False
        try:
            self._hook.write_u16(base + 0x02, max(0, min(0xFFFF, quarters)))
            return True
        except Exception:
            return False

    def write_rupees(self, count: int) -> bool:
        base = self._status_base()
        if base is None:
            return False
        try:
            self._hook.write_u16(base + 0x04, max(0, min(0xFFFF, count)))
            return True
        except Exception:
            return False

    def write_sword(self, item_id: int) -> bool:
        base = self._status_base()
        if base is None:
            return False
        try:
            self._hook.write_u8(base + 0x0E, item_id & 0xFF)
            return True
        except Exception:
            return False

    def write_shield(self, item_id: int) -> bool:
        base = self._status_base()
        if base is None:
            return False
        try:
            self._hook.write_u8(base + 0x0F, item_id & 0xFF)
            return True
        except Exception:
            return False

    def write_magic(self, amount: int) -> bool:
        base = self._status_base()
        if base is None:
            return False
        try:
            self._hook.write_u8(base + 0x14, max(0, min(0xFF, amount)))
            return True
        except Exception:
            return False

    def write_wallet(self, tier: int) -> bool:
        base = self._status_base()
        if base is None:
            return False
        try:
            self._hook.write_u8(base + 0x1A, max(0, min(2, tier)))
            return True
        except Exception:
            return False

    def write_slot(self, slot_name: str, item_id: int) -> bool:
        base = self._inv_base()
        if base is None:
            return False
        for name, offset, _ in _SLOTS:
            if name == slot_name:
                try:
                    self._hook.write_u8(base + offset, item_id & 0xFF)
                    return True
                except Exception:
                    return False
        return False
