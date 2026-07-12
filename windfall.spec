# -*- mode: python ; coding: utf-8 -*-
"""Build a single-file Windfall.exe: `pyinstaller windfall.spec`."""

a = Analysis(
    ["run_windfall.py"],
    pathex=["src"],
    binaries=[],
    datas=[("src/windfall/assets", "windfall/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Windfall",
    debug=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon="src/windfall/assets/windfall.ico",
)
