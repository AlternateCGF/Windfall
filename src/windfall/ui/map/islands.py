"""Great Sea island positions for the interactive map.

Positions cross-referenced from ZeldaCentral world map, IGN treasure chart guide,
and Game8 sea chart.

Grid: columns A-G (west→east), rows 1-7 (north→south).
Sector indices run −3…+3 in both axes; world coordinates = sector × SECTOR_SIZE.
"""

from __future__ import annotations

from dataclasses import dataclass

# 7x7 sectors of 100,000 units each — grid corners at ±350,000. (The in-game sailing
# boundary clamps around ±325,000, inside the outermost sectors.)
_HALF_SPAN = 350_000.0
_SECTORS = 7
SECTOR_SIZE = (2 * _HALF_SPAN) / _SECTORS  # = 100 000

_GRID_OFFSET = 3  # grid_index = sector + this


@dataclass(frozen=True)
class Island:
    name: str
    grid_col: int  # 0-6  (column A-G)
    grid_row: int  # 0-6  (row 1-7)
    offset_x: float = 0.0
    offset_z: float = 0.0

    @property
    def world_x(self) -> float:
        return (self.grid_col - _GRID_OFFSET) * SECTOR_SIZE + self.offset_x

    @property
    def world_z(self) -> float:
        return (self.grid_row - _GRID_OFFSET) * SECTOR_SIZE + self.offset_z

    @property
    def sector_label(self) -> str:
        col_letter = chr(ord("A") + self.grid_col)
        row_number = self.grid_row + 1
        return f"{col_letter}{row_number}"


# fmt: off
#  Source: ZeldaCentral Wind Waker World Map.
#  Columns A-G map to grid cols 0-6; rows 1-7 map to grid rows 0-6.
ISLANDS: list[Island] = [
    # ── Row 1 ───────────────────────────────────────────────────────────
    Island("Forsaken Fortress",      0, 0),  # A1
    Island("Star Island",            1, 0),  # B1
    Island("Northern Fairy Island",  2, 0),  # C1
    Island("Gale Isle",              3, 0),  # D1
    Island("Crescent Moon Island",   4, 0),  # E1
    Island("Seven-Star Isles",       5, 0),  # F1
    Island("Overlook Island",        6, 0),  # G1

    # ── Row 2 ───────────────────────────────────────────────────────────
    Island("Four-Eye Reef",          0, 1),  # A2
    Island("Mother & Child Isles",   1, 1),  # B2
    Island("Spectacle Island",       2, 1),  # C2
    Island("Windfall Island",        3, 1),  # D2
    Island("Pawprint Isle",          4, 1),  # E2
    Island("Dragon Roost Island",    5, 1),  # F2
    Island("Flight Control Platform", 6, 1),  # G2

    # ── Row 3 ───────────────────────────────────────────────────────────
    Island("Western Fairy Island",   0, 2),  # A3
    Island("Rock Spire Isle",        1, 2),  # B3
    Island("Tingle Island",          2, 2),  # C3
    Island("Northern Triangle Island", 3, 2),  # D3
    Island("Eastern Fairy Island",   4, 2),  # E3
    Island("Fire Mountain",          5, 2),  # F3
    Island("Star Belt Archipelago",  6, 2),  # G3

    # ── Row 4 ───────────────────────────────────────────────────────────
    Island("Three-Eye Reef",         0, 3),  # A4
    Island("Greatfish Isle",         1, 3),  # B4
    Island("Cyclops Reef",           2, 3),  # C4
    Island("Six-Eye Reef",           3, 3),  # D4
    Island("Tower of the Gods",      4, 3),  # E4
    Island("Eastern Triangle Island", 5, 3),  # F4
    Island("Thorned Fairy Island",   6, 3),  # G4

    # ── Row 5 ───────────────────────────────────────────────────────────
    Island("Needle Rock Isle",       0, 4),  # A5
    Island("Islet of Steel",         1, 4),  # B5
    Island("Stone Watcher Island",   2, 4),  # C5
    Island("Southern Triangle Isle", 3, 4),  # D5
    Island("Private Oasis",          4, 4),  # E5
    Island("Bomb Island",            5, 4),  # F5
    Island("Bird's Peak Rock",       6, 4),  # G5

    # ── Row 6 ───────────────────────────────────────────────────────────
    Island("Diamond Steppe Island",  0, 5),  # A6
    Island("Five-Eye Reef",          1, 5),  # B6
    Island("Shark Island",           2, 5),  # C6
    Island("Southern Fairy Island",  3, 5),  # D6
    Island("Ice Ring Isle",          4, 5),  # E6
    Island("Forest Haven",           5, 5),  # F6
    Island("Cliff Plateau Isles",    6, 5),  # G6

    # ── Row 7 ───────────────────────────────────────────────────────────
    Island("Horseshoe Island",       0, 6),  # A7
    Island("Outset Island",          1, 6),  # B7
    Island("Headstone Island",       2, 6),  # C7
    Island("Two-Eye Reef",           3, 6),  # D7
    Island("Angular Isles",          4, 6),  # E7
    Island("Boating Course",         5, 6),  # F7
    Island("Five-Star Isles",        6, 6),  # G7
]
# fmt: on
