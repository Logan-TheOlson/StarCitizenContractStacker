# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec — one-file, windowed Windows app.

Build:  pyinstaller --noconfirm StarCitizenCargoStack.spec
Output: dist/Star Citizen Cargo Stack.exe
"""

from PyInstaller.utils.hooks import collect_data_files

# CustomTkinter ships its themes/assets as data files that must be bundled.
datas = collect_data_files("customtkinter")
datas += [
    ("app/data", "app/data"),         # locations.json etc.
    ("assets/icon.ico", "assets"),    # window/taskbar icon at runtime
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["playwright"],          # scraper-only, never imported at runtime
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Star Citizen Cargo Stack",
    icon="assets/icon.ico",
    console=False,                    # windowed (no console)
    debug=False,
    strip=False,
    upx=True,
    bootloader_ignore_signals=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
