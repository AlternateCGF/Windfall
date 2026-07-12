"""Entry point: ``python -m windfall``."""

from __future__ import annotations

import sys
from pathlib import Path

_ASSETS = Path(__file__).resolve().parent / "assets"


def main() -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Windfall")

    icon_path = _ASSETS / "windfall.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
