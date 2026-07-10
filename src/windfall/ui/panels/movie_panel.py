"""Movie panel — cinematic recording helpers (HUD visibility, etc.)."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QGroupBox, QVBoxLayout, QWidget

from ...core.poller import Poller, Snapshot

# HUD disable is only supported for USA (GZLE01).
_HUD_SUPPORTED_GAME_IDS = {"GZLE01"}


class MoviePanel(QWidget):
    """Movie/recording tools panel wired to the shared Poller."""

    def __init__(self, poller: Poller) -> None:
        super().__init__()
        self._poller = poller

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        # ---- HUD visibility --------------------------------------------------
        hud_box = QGroupBox("HUD")
        hud_layout = QVBoxLayout(hud_box)

        self._disable_hud = QCheckBox("Disable HUD")
        self._disable_hud.toggled.connect(self._on_disable_hud_toggled)
        hud_layout.addWidget(self._disable_hud)

        root.addWidget(hud_box)
        root.addStretch(1)

    def _on_disable_hud_toggled(self, on: bool) -> None:
        if on:
            self._poller.set_hud_hold()
        else:
            self._poller.clear_hold(self._poller._HUD_HOLD_KEY)

    def update_from(self, snap: Snapshot) -> None:
        """Called each tick — enable/disable the HUD checkbox based on game version."""
        supported = snap.game_id in _HUD_SUPPORTED_GAME_IDS if snap.game_id else False
        self._disable_hud.setEnabled(supported)
        if not supported and self._disable_hud.isChecked():
            self._disable_hud.setChecked(False)
