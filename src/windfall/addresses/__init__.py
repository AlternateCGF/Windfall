"""Per-region address tables and version detection."""

from .version import Addresses, GameVersion, VERSIONS, detect_version

__all__ = ["Addresses", "GameVersion", "VERSIONS", "detect_version"]
