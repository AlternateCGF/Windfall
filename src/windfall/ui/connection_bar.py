"""Top-of-window strip showing hook status and the detected game."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..core.poller import Snapshot


class ConnectionBar(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._dot = QLabel("●")  # ●
        self._status = QLabel("Searching for Dolphin…")
        self._stage = QLabel("")
        self._stage.setStyleSheet("color:#f1c40f;font-weight:bold;")
        self._game = QLabel("")
        self._game.setStyleSheet("color:#9aa;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(self._dot)
        layout.addWidget(self._status)
        layout.addStretch(1)
        layout.addWidget(self._stage)
        layout.addWidget(self._game)
        self._set_color("#c0392b")  # red until connected

    def _set_color(self, color: str) -> None:
        self._dot.setStyleSheet(f"color:{color};")

    def update_from(self, snap: Snapshot) -> None:
        if not snap.connected:
            self._set_color("#c0392b")
            self._status.setText("Not hooked — start Dolphin and load the game")
            self._stage.setText("")
            self._game.setText("")
            return

        if snap.supported:
            self._set_color("#27ae60")  # green
            self._status.setText("Hooked")
            self._game.setText(f"{snap.label}  ({snap.game_id})")
        else:
            self._set_color("#f39c12")  # amber: hooked but unknown game
            self._status.setText("Hooked — unsupported game")
            self._game.setText(snap.game_id or "unknown")

        self._stage.setText(snap.stage_name or "")
