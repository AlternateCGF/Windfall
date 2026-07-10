"""Georeferenced map background — placed by a world rectangle you drag/resize on screen.

The image is auto-loaded from ``windfall/assets`` (``map.png`` etc.) and pinned to world space by a
single ``world_rect`` (the world-coordinate box the image fills). You position it visually: drag the
body to move, drag a corner to scale (aspect locked to the image), and flip per axis if the sea reads
mirrored. The placement (rect + flips) is persisted to JSON so it survives restarts.

Keyed storage (default ``"__default__"``) leaves room for per-stage backgrounds later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPixmap

_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
_CONFIG_PATH = _ASSETS_DIR / "map_backgrounds.json"
_DEFAULT_KEY = "__default__"
_MAP_FILES = ("map.png", "great_sea.png", "map.jpg", "map.jpeg")

# Default Great Sea extent (7x7 grid, 100k units/sector, corners at ±350k, centered on 0).
_DEFAULT_SEA_SPAN = 700_000.0


@dataclass
class MapBackground:
    image_path: str
    world_rect: QRectF
    flip_h: bool = False
    flip_v: bool = False
    pixmap: Optional[QPixmap] = None
    oriented: Optional[QPixmap] = None

    def __post_init__(self) -> None:
        if self.pixmap is None:
            self.pixmap = QPixmap(self.image_path)
        self._reorient()

    def _reorient(self) -> None:
        pm = self.pixmap
        if pm is None or pm.isNull():
            self.oriented = pm
            return
        if self.flip_h or self.flip_v:
            self.oriented = QPixmap.fromImage(pm.toImage().mirrored(self.flip_h, self.flip_v))
        else:
            self.oriented = pm

    def set_flip(self, flip_h: Optional[bool] = None, flip_v: Optional[bool] = None) -> None:
        if flip_h is not None:
            self.flip_h = flip_h
        if flip_v is not None:
            self.flip_v = flip_v
        self._reorient()

    def is_valid(self) -> bool:
        return self.pixmap is not None and not self.pixmap.isNull()

    @property
    def aspect(self) -> float:
        """Image width / height (falls back to 1 for a degenerate image)."""
        if self.pixmap is None or self.pixmap.height() == 0:
            return 1.0
        return self.pixmap.width() / self.pixmap.height()

    @classmethod
    def default_placement(cls, image_path: str) -> "MapBackground":
        """Center the image on the world origin at a Great-Sea-sized default, aspect preserved."""
        pm = QPixmap(image_path)
        w = _DEFAULT_SEA_SPAN
        aspect = (pm.width() / pm.height()) if (not pm.isNull() and pm.height()) else 1.0
        h = w / aspect
        rect = QRectF(-w / 2.0, -h / 2.0, w, h)
        return cls(image_path, rect, pixmap=pm)

    # ---- persistence --------------------------------------------------------
    def to_dict(self) -> dict:
        r = self.world_rect
        return {
            "image_path": self.image_path,
            "rect": [r.x(), r.y(), r.width(), r.height()],
            "flip_h": self.flip_h,
            "flip_v": self.flip_v,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MapBackground":
        if "rect" in data:
            x, y, w, h = data["rect"]
        elif "p1" in data and "p2" in data:
            # Legacy format: two corner points → derive rect.
            x1, y1 = data["p1"][0], data["p1"][1]
            x2, y2 = data["p2"][0], data["p2"][1]
            x, y = min(x1, x2), min(y1, y2)
            w, h = abs(x2 - x1), abs(y2 - y1)
        else:
            raise KeyError("no rect or p1/p2 data")
        return cls(
            data["image_path"],
            QRectF(x, y, w, h),
            flip_h=bool(data.get("flip_h", False)),
            flip_v=bool(data.get("flip_v", False)),
        )


def find_assets_map() -> Optional[str]:
    """Path to the bundled map image in assets, if one exists."""
    for name in _MAP_FILES:
        p = _ASSETS_DIR / name
        if p.exists():
            return str(p)
    return None


def save_background(bg: MapBackground, key: str = _DEFAULT_KEY) -> None:
    store = _read_store()
    store[key] = bg.to_dict()
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(store, indent=2), encoding="utf-8")


def load_background(key: str = _DEFAULT_KEY) -> Optional[MapBackground]:
    data = _read_store().get(key)
    if not data:
        return None
    try:
        bg = MapBackground.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None
    if not bg.is_valid():
        return None
    # Migrate legacy p1/p2 format to rect on disk.
    if "rect" not in data:
        save_background(bg, key)
    return bg


def _read_store() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
