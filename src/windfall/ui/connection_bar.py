"""Top-of-window strip showing hook status and the detected game."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..core.poller import Snapshot


class ConnectionBar(QWidget):
    reconnect_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._status = QLabel("Searching for Dolphin…")
        self._stage = QLabel("")
        self._stage.setStyleSheet("color:#f1c40f;font-weight:bold;")
        self._game = QLabel("")
        self._game.setStyleSheet("color:#9aa;")

        self._reconnect_btn = QPushButton("Reconnect")
        self._reconnect_btn.setToolTip(
            "Drop and re-hook Dolphin — use this if the connection seems stuck."
        )
        self._reconnect_btn.clicked.connect(self.reconnect_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(self._status)
        layout.addWidget(self._reconnect_btn)
        layout.addStretch(1)
        layout.addWidget(self._stage)
        layout.addWidget(self._game)

    def update_from(self, snap: Snapshot) -> None:
        if not snap.connected:
            self._status.setText("Not hooked — start Dolphin and load the game")
            self._stage.setText("")
            self._game.setText("")
            return

        if snap.supported:
            # The game label itself is confirmation enough that we're hooked.
            self._status.setText("")
            self._game.setText(f"{snap.label}  ({snap.game_id})")
        else:
            self._status.setText("Unsupported game")
            self._game.setText(snap.game_id or "unknown")

        self._stage.setText(snap.stage_name or "")
