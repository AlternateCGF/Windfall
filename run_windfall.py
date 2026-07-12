"""PyInstaller entry point — imports windfall as a package so relative imports resolve."""

from windfall.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
