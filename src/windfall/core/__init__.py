"""Core runtime: the polling loop that reads snapshots and applies held writes off the UI thread."""

from .poller import Poller, Snapshot

__all__ = ["Poller", "Snapshot"]
