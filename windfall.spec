# -*- mode: python ; coding: utf-8 -*-
"""Build a single-file Windfall.exe: `pyinstaller windfall.spec`.

The app only uses PySide6's QtCore/QtGui/QtWidgets (plain widgets, QGraphicsView,
QPainter) — no QML, networking, PDF, SVG, multimedia, etc. PySide6's PyInstaller
hook otherwise drags in most of Qt regardless of what's imported, so both the
unused submodules and their plugin DLLs are excluded/stripped below. This cut
the frozen build from ~47 MB to a fraction of that (see README).
"""

# Whole Qt submodules the app never imports.
_EXCLUDED_QT_MODULES = [
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D",
    "PySide6.QtNetwork",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtXml",
    "PySide6.QtDBus",
    "PySide6.QtHttpServer",
    "PySide6.QtRemoteObjects",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtLocation",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
]

a = Analysis(
    ["run_windfall.py"],
    pathex=["src"],
    binaries=[],
    datas=[("src/windfall/assets", "windfall/assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDED_QT_MODULES,
    noarchive=False,
)

# Plugin/support DLLs the excludes above don't catch on their own — PySide6's
# PyInstaller hook bundles most installed Qt6*.dll binaries regardless of which
# PySide6.QtX python submodules are excluded/imported, so the actual compiled Qt
# libraries for unused modules have to be stripped by filename too: QtQml/Quick
# (no QML), QtPdf/QtNetwork/QtSvg/QtVirtualKeyboard (unused), the software OpenGL
# rasterizer (we never use QOpenGLWidget), the alternate Direct2D platform plugin
# (qwindows.dll is what's actually used), TLS backends (no networking), and every
# image-format plugin except the one we actually need (.ico, for the window icon)
# — our assets are otherwise plain .png, which QtGui reads natively.
_STRIP_SUBSTRINGS = [
    "opengl32sw",
    "qdirect2d",
    "qt6qml", "qt6quick",
    "qt6pdf",
    "qt6network",
    "qt6svg",
    "qt6virtualkeyboard",
    "plugins\\tls\\", "plugins/tls/",
    "plugins\\generic\\qtuiotouchplugin", "plugins/generic/qtuiotouchplugin",
    "plugins\\iconengines\\qsvgicon", "plugins/iconengines/qsvgicon",
    "plugins\\platforminputcontexts", "plugins/platforminputcontexts",
    "plugins\\imageformats\\qjpeg", "plugins/imageformats/qjpeg",
    "plugins\\imageformats\\qwebp", "plugins/imageformats/qwebp",
    "plugins\\imageformats\\qtiff", "plugins/imageformats/qtiff",
    "plugins\\imageformats\\qgif", "plugins/imageformats/qgif",
    "plugins\\imageformats\\qicns", "plugins/imageformats/qicns",
    "plugins\\imageformats\\qpdf", "plugins/imageformats/qpdf",
    "plugins\\imageformats\\qtga", "plugins/imageformats/qtga",
    "plugins\\imageformats\\qwbmp", "plugins/imageformats/qwbmp",
    "plugins\\imageformats\\qsvg", "plugins/imageformats/qsvg",
]
a.binaries = [
    entry
    for entry in a.binaries
    if not any(s.lower() in entry[1].lower() for s in _STRIP_SUBSTRINGS)
]

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
