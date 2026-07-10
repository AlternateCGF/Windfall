"""Memory access layer: everything that touches Dolphin's emulated RAM goes through here."""

from .hook import DolphinHook, Vec3

__all__ = ["DolphinHook", "Vec3"]
