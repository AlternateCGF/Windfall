"""Inventory panel — read / write the player's stats, equipment, and item slots."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyleOption,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.poller import Poller, Snapshot
from ...game.inventory import (
    InventoryReader,
    InventoryState,
    PlayerStats,
    item_name,
)

_EMPTY_ITEM = 0xFF

# Equipment options: (label, value, icon filename or None if no in-game icon exists).
# Equipped sword/shield are item-table IDs (per the tww decomp's d_item_data.h ItemTable),
# the same namespace as inventory slots — 0xFF is the "no item" sentinel used throughout
# that table, not 0x00 (0x00 is dItemNo_HEART_e, which is why writing 0 rendered as the
# default Hero's Shield instead of no shield).
_SWORD_OPTIONS: list[tuple[str, int, Optional[str]]] = [
    ("(none)", 0xFF, None),
    ("Hero's Sword", 0x38, "Hero's Sword.png"),
    ("Master Sword (Uncharged)", 0x39, "Master Sword (Uncharged).png"),
    ("Master Sword (Half Charged)", 0x3A, "Master Sword (Half Charged).png"),
    ("Master Sword (Fully Charged)", 0x3E, "Master Sword (Fully Charged).png"),
]

_SHIELD_OPTIONS: list[tuple[str, int, Optional[str]]] = [
    ("(none)", 0xFF, None),
    ("Hero's Shield", 0x3B, "Hero's Shield.png"),
    ("Mirror Shield", 0x3C, "Mirror Shield.png"),
]

_WALLET_OPTIONS: list[tuple[str, int, Optional[str]]] = [
    ("200", 0, None),
    ("1000", 1, "Wallet (1000).png"),
    ("5000", 2, "Wallet (5000).png"),
]

# Inventory slot names in display order (must match InventoryReader._SLOTS).
_SLOT_ORDER = [
    "Telescope", "Sail", "Wind Waker", "Grappling Hook",
    "Spoils Bag", "Boomerang", "Deku Leaf", "Tingle Tuner",
    "Iron Boots", "Magic Armor", "Bait Bag",
    "Bow", "Bombs",
    "Bottle 1", "Bottle 2", "Bottle 3", "Bottle 4",
    "Delivery Bag", "Hookshot", "Skull Hammer",
]

# Item ID written when left-clicking an empty slot ("give the usual item").
_SLOT_DEFAULT_ITEM: dict[str, int] = {
    "Telescope": 0x20,
    "Sail": 0x78,
    "Wind Waker": 0x22,
    "Grappling Hook": 0x25,
    "Spoils Bag": 0x24,
    "Boomerang": 0x2D,
    "Deku Leaf": 0x34,
    "Tingle Tuner": 0x21,
    "Iron Boots": 0x29,
    "Magic Armor": 0x2A,
    "Bait Bag": 0x2C,
    "Bow": 0x36,
    "Bombs": 0x31,
    "Bottle 1": 0x50,
    "Bottle 2": 0x50,
    "Bottle 3": 0x50,
    "Bottle 4": 0x50,
    "Delivery Bag": 0x30,
    "Hookshot": 0x2F,
    "Skull Hammer": 0x33,
}

_ITEMS_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets" / "items"

# Slot names whose icon filename doesn't match the slot name exactly.
_SLOT_ICON_FILE: dict[str, str] = {
    "Bow": "Hero's Bow.png",
    "Bombs": "Bomb.png",
    "Bottle 1": "Empty Bottle.png",
    "Bottle 2": "Empty Bottle.png",
    "Bottle 3": "Empty Bottle.png",
    "Bottle 4": "Empty Bottle.png",
}

_icon_cache: dict[str, tuple[QPixmap, QPixmap]] = {}  # filename -> (active, inactive)


def _load_icon_pair(filename: str) -> Optional[tuple[QPixmap, QPixmap]]:
    """Return (full-color, grayed-out) pixmaps for an assets/items file, if present."""
    cached = _icon_cache.get(filename)
    if cached is not None:
        return cached
    pm = QPixmap(str(_ITEMS_ASSETS_DIR / filename))
    if pm.isNull():
        return None
    inactive = QApplication.style().generatedIconPixmap(QIcon.Mode.Disabled, pm, QStyleOption())
    pair = (pm, inactive)
    _icon_cache[filename] = pair
    return pair


def _load_slot_icons(slot_name: str) -> Optional[tuple[QPixmap, QPixmap]]:
    """Return (full-color, grayed-out) pixmaps for an item grid slot's icon, if present."""
    filename = _SLOT_ICON_FILE.get(slot_name, f"{slot_name}.png")
    return _load_icon_pair(filename)


# Placeholder glyphs, used only if an icon file is missing for a slot.
_SLOT_EMOJI: dict[str, str] = {
    "Telescope": "\U0001F52D",       # 🔭
    "Sail": "⛵",                # ⛵
    "Wind Waker": "\U0001FA84",      # 🪄
    "Grappling Hook": "\U0001FA9D",  # 🪝
    "Spoils Bag": "\U0001F392",      # 🎒
    "Boomerang": "\U0001FA83",       # 🪃
    "Deku Leaf": "\U0001F343",       # 🍃
    "Tingle Tuner": "\U0001F4DF",    # 📟
    "Iron Boots": "\U0001F97E",      # 🥾
    "Magic Armor": "\U0001F4A0",     # 💠
    "Bait Bag": "\U0001FAB1",        # 🪱
    "Bow": "\U0001F3F9",             # 🏹
    "Bombs": "\U0001F4A3",           # 💣
    "Bottle 1": "\U0001F376",        # 🍶
    "Bottle 2": "\U0001F376",        # 🍶
    "Bottle 3": "\U0001F376",        # 🍶
    "Bottle 4": "\U0001F376",        # 🍶
    "Delivery Bag": "\U0001F4E6",    # 📦
    "Hookshot": "⛓️",      # ⛓️
    "Skull Hammer": "\U0001F528",    # 🔨
}

# Right-click menu contents for bottle slots (item ID -> shown via item_name).
_BOTTLE_CONTENTS: list[int] = [
    0x50, 0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
]

_GRID_COLUMNS = 4


class _ItemSlotButton(QToolButton):
    """One cell of the item grid: emoji tile + caption, colored when owned.

    Left-click toggles the slot between empty and its default item; right-click
    opens a menu of every valid item for the slot (bottle contents for bottles).
    """

    item_selected = Signal(str, int)  # (slot name, item ID to write)

    def __init__(self, slot_name: str) -> None:
        super().__init__()
        self._slot_name = slot_name
        self._item_id = _EMPTY_ITEM
        self._icons = _load_slot_icons(slot_name)
        if self._icons is None:
            self.setText(_SLOT_EMOJI.get(slot_name, "?"))
            font = QFont()
            font.setPointSize(18)
            self.setFont(font)
        else:
            self.setIconSize(QSize(40, 40))
        self.setFixedSize(64, 64)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.clicked.connect(self._toggle)
        self._apply_style()

    # ---- state -----------------------------------------------------------------
    def set_item(self, item_id: int) -> None:
        if item_id == self._item_id:
            return
        self._item_id = item_id
        self._apply_style()

    def _owned(self) -> bool:
        return self._item_id != _EMPTY_ITEM

    def _apply_style(self) -> None:
        if self._icons is not None:
            active, inactive = self._icons
            self.setIcon(QIcon(active if self._owned() else inactive))
        if self._owned():
            self.setStyleSheet(
                "QToolButton { background: #2d5a37; border: 1px solid #4caf50;"
                " border-radius: 6px; }"
                "QToolButton:hover { background: #387044; }"
            )
            self.setToolTip(f"{self._slot_name}: {item_name(self._item_id)}\n"
                            "Click to remove • right-click for options")
        else:
            self.setStyleSheet(
                "QToolButton { background: #2a2a2a; border: 1px solid #444;"
                " border-radius: 6px; color: #666; }"
                "QToolButton:hover { background: #333; }"
            )
            self.setToolTip(f"{self._slot_name}: empty\n"
                            "Click to give • right-click for options")

    # ---- interaction -----------------------------------------------------------
    def _toggle(self) -> None:
        if self._owned():
            self.item_selected.emit(self._slot_name, _EMPTY_ITEM)
        else:
            default = _SLOT_DEFAULT_ITEM.get(self._slot_name)
            if default is not None:
                self.item_selected.emit(self._slot_name, default)

    def _menu_options(self) -> list[tuple[str, int]]:
        if self._slot_name.startswith("Bottle"):
            options = [(item_name(iid), iid) for iid in _BOTTLE_CONTENTS]
        else:
            default = _SLOT_DEFAULT_ITEM.get(self._slot_name)
            options = [] if default is None else [(item_name(default), default)]
        return [("Empty", _EMPTY_ITEM)] + options

    def _show_menu(self, pos) -> None:
        menu = QMenu(self)
        for label, iid in self._menu_options():
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(iid == self._item_id)
            action.setData(iid)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen is not None:
            self.item_selected.emit(self._slot_name, chosen.data())


class _EquipOptionButton(QToolButton):
    """One selectable option in an equipment row: icon tile, colored when selected."""

    def __init__(self, label: str, value: int, icon_file: Optional[str]) -> None:
        super().__init__()
        self.value = value
        self._icons = _load_icon_pair(icon_file) if icon_file else None
        if self._icons is None:
            self.setText(label)
            font = QFont()
            font.setPointSize(10)
            self.setFont(font)
        else:
            self.setIconSize(QSize(40, 40))
        self.setFixedSize(64, 64)
        self.setCheckable(True)
        self.setToolTip(label)
        self.toggled.connect(self._apply_style)
        self._apply_style(False)

    def _apply_style(self, checked: bool) -> None:
        if self._icons is not None:
            active, inactive = self._icons
            self.setIcon(QIcon(active if checked else inactive))
        if checked:
            self.setStyleSheet(
                "QToolButton { background: #2d5a37; border: 1px solid #4caf50;"
                " border-radius: 6px; }"
                "QToolButton:hover { background: #387044; }"
            )
        else:
            self.setStyleSheet(
                "QToolButton { background: #2a2a2a; border: 1px solid #444;"
                " border-radius: 6px; color: #666; }"
                "QToolButton:hover { background: #333; }"
            )


class _EquipRow(QWidget):
    """A row of mutually-exclusive icon buttons for one equipment slot.

    Selecting an option writes immediately (mirrors the item grid); ``set_value``
    reflects live memory state without re-triggering a write.
    """

    value_selected = Signal(int)

    def __init__(self, options: list[tuple[str, int, Optional[str]]]) -> None:
        super().__init__()
        hbox = QHBoxLayout(self)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(6)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[int, _EquipOptionButton] = {}
        for label, value, icon_file in options:
            cell = QVBoxLayout()
            cell.setSpacing(2)
            btn = _EquipOptionButton(label, value, icon_file)
            self._group.addButton(btn)
            self._buttons[value] = btn
            cell.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
            caption = QLabel(label)
            caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            caption.setStyleSheet("font-size: 10px; color: #999;")
            caption.setFixedWidth(64)
            caption.setWordWrap(True)
            cell.addWidget(caption)
            hbox.addLayout(cell)
        hbox.addStretch()
        self._group.buttonToggled.connect(self._on_toggled)

    def _on_toggled(self, button: _EquipOptionButton, checked: bool) -> None:
        if checked:
            self.value_selected.emit(button.value)

    def set_value(self, value: int) -> None:
        btn = self._buttons.get(value)
        if btn is None or btn.isChecked():
            return
        self._group.blockSignals(True)
        try:
            btn.setChecked(True)
        finally:
            self._group.blockSignals(False)


class InventoryPanel(QWidget):
    """Read / write the player inventory.

    Accepts a ``Poller``; reads are done via the poller's ``_player`` and a local
    ``InventoryReader`` on each snapshot tick.
    """

    def __init__(self, poller: Poller) -> None:
        super().__init__()
        self._poller = poller
        self._reader: InventoryReader | None = None
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        root.addWidget(scroll)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- Stats section ---
        stats_group = QGroupBox("Stats")
        stats_form = QFormLayout(stats_group)
        self._max_hp = QSpinBox()
        self._max_hp.setRange(0, 0xFFFF)
        self._max_hp.setSuffix(" quarters")
        self._cur_hp = QSpinBox()
        self._cur_hp.setRange(0, 0xFFFF)
        self._cur_hp.setSuffix(" quarters")
        self._rupees = QSpinBox()
        self._rupees.setRange(0, 0xFFFF)
        self._magic = QSpinBox()
        self._magic.setRange(0, 0)
        self._magic.setEnabled(False)
        self._magic.setToolTip("No magic meter yet")
        stats_form.addRow("Max HP", self._max_hp)
        stats_form.addRow("Current HP", self._cur_hp)
        stats_form.addRow("Rupees", self._rupees)
        stats_form.addRow("Magic", self._magic)
        self._stats_apply = QPushButton("Apply Stats")
        self._stats_apply.clicked.connect(self._apply_stats)
        stats_form.addRow(self._stats_apply)
        layout.addWidget(stats_group)

        # --- Equipment section: clickable icon rows, writes take effect immediately ---
        equip_group = QGroupBox("Equipment")
        equip_vbox = QVBoxLayout(equip_group)
        self._sword_row = _EquipRow(_SWORD_OPTIONS)
        self._shield_row = _EquipRow(_SHIELD_OPTIONS)
        self._wallet_row = _EquipRow(_WALLET_OPTIONS)
        for title, row in (
            ("Sword", self._sword_row),
            ("Shield", self._shield_row),
            ("Wallet", self._wallet_row),
        ):
            label = QLabel(title)
            label.setStyleSheet("color: #999;")
            equip_vbox.addWidget(label)
            equip_vbox.addWidget(row)
        self._sword_row.value_selected.connect(self._on_sword_selected)
        self._shield_row.value_selected.connect(self._on_shield_selected)
        self._wallet_row.value_selected.connect(self._on_wallet_selected)
        layout.addWidget(equip_group)

        # --- Items section: clickable icon grid, writes take effect immediately ---
        items_group = QGroupBox("Items")
        items_vbox = QVBoxLayout(items_group)
        grid = QGridLayout()
        grid.setSpacing(6)
        self._slot_buttons: dict[str, _ItemSlotButton] = {}
        for i, slot_name in enumerate(_SLOT_ORDER):
            cell = QVBoxLayout()
            cell.setSpacing(2)
            btn = _ItemSlotButton(slot_name)
            btn.item_selected.connect(self._on_slot_selected)
            self._slot_buttons[slot_name] = btn
            cell.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
            caption = QLabel(slot_name)
            caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            caption.setStyleSheet("font-size: 10px; color: #999;")
            caption.setWordWrap(True)
            caption.setFixedWidth(72)
            cell.addWidget(caption)
            grid.addLayout(cell, i // _GRID_COLUMNS, i % _GRID_COLUMNS)
        items_vbox.addLayout(grid)
        hint = QLabel("Click a tile to give/remove • right-click for options")
        hint.setStyleSheet("font-size: 10px; color: #777;")
        items_vbox.addWidget(hint)
        layout.addWidget(items_group)

        # --- Refresh button ---
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._force_refresh)
        layout.addWidget(self._refresh_btn)

        layout.addStretch()
        scroll.setWidget(container)

        # Connect signals to mark dirty on user edits (stats only; equipment writes
        # immediately like the item grid, so it needs no dirty tracking).
        for sb in (self._max_hp, self._cur_hp, self._rupees, self._magic):
            sb.valueChanged.connect(self._mark_dirty)

    # ---- snapshot update ------------------------------------------------------
    def update_from(self, snap: Snapshot) -> None:
        """Refresh all fields from a live read."""
        # Ensure we have a reader once connected.
        if self._reader is None and self._poller._player is not None:
            self._reader = InventoryReader(
                self._poller._hook,
                self._poller._version.addr,
            )
        if self._reader is None:
            return

        # Item tiles and equipment reflect live memory always; the dirty flag only
        # protects the editable HP/rupee fields from being overwritten mid-edit.
        inv = self._reader.read_inventory()
        if inv is not None:
            self._refresh_items(inv)

        stats = self._reader.read_stats()
        if stats is not None:
            self._refresh_equipment(stats)
            if not self._dirty:
                self._refresh_stats(stats)

    def _force_refresh(self) -> None:
        """Manually re-read from memory, discarding unsaved edits."""
        if self._reader is None:
            return
        self._dirty = False
        stats = self._reader.read_stats()
        if stats is not None:
            self._refresh_stats(stats)
            self._refresh_equipment(stats)
        inv = self._reader.read_inventory()
        if inv is not None:
            self._refresh_items(inv)

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _refresh_stats(self, s: PlayerStats) -> None:
        for sb in (self._max_hp, self._cur_hp, self._rupees, self._magic):
            sb.blockSignals(True)
        try:
            self._max_hp.setValue(s.max_hp)
            self._cur_hp.setValue(s.cur_hp)
            self._rupees.setValue(s.rupees)
            # No magic meter yet (max == 0): grey out. Otherwise track the live max
            # (e.g. it doubles once Double Magic is obtained).
            self._magic.setEnabled(s.max_magic > 0)
            self._magic.setRange(0, s.max_magic)
            self._magic.setValue(min(s.magic, s.max_magic))
            self._magic.setToolTip("" if s.max_magic > 0 else "No magic meter yet")
        finally:
            for sb in (self._max_hp, self._cur_hp, self._rupees, self._magic):
                sb.blockSignals(False)

    def _refresh_equipment(self, s: PlayerStats) -> None:
        self._sword_row.set_value(s.sword)
        self._shield_row.set_value(s.shield)
        self._wallet_row.set_value(s.wallet)

    def _refresh_items(self, inv: InventoryState) -> None:
        for slot_name, btn in self._slot_buttons.items():
            btn.set_item(inv.slots.get(slot_name, _EMPTY_ITEM))

    # ---- apply ----------------------------------------------------------------
    def _apply_stats(self) -> None:
        r = self._reader
        if r is None:
            return
        r.write_max_hp(self._max_hp.value())
        r.write_cur_hp(self._cur_hp.value())
        r.write_rupees(self._rupees.value())
        if self._magic.isEnabled():
            r.write_magic(self._magic.value())
        self._dirty = False

    def _on_sword_selected(self, value: int) -> None:
        if self._reader is not None:
            self._reader.write_sword(value)

    def _on_shield_selected(self, value: int) -> None:
        if self._reader is not None:
            self._reader.write_shield(value)

    def _on_wallet_selected(self, value: int) -> None:
        if self._reader is not None:
            self._reader.write_wallet(value)

    def _on_slot_selected(self, slot_name: str, item_id: int) -> None:
        """Write a grid click straight to memory and reflect the result."""
        r = self._reader
        if r is None:
            return
        if r.write_slot(slot_name, item_id):
            btn = self._slot_buttons.get(slot_name)
            if btn is not None:
                btn.set_item(item_id)
