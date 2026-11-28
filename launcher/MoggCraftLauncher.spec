# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir-сборка. Формат совместим с апдейтером 3.1/4.0."""

from PyInstaller.utils.hooks import collect_all

datas = [
    ("assets/app_icon.png", "assets"),
    ("assets/app_icon.ico", "assets"),
]

# mll >= 2.x поддерживает NeoForge напрямую, но hidden import страхует
# от расхождений версий на машинах игроков.
hiddenimports = [
    "minecraft_launcher_lib.mod_loader._neoforge",
    "minecraft_launcher_lib.runtime",
    "minecraft_launcher_lib.command",
]

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MoggCraftLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    version="version_info.txt",
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/app_icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MoggCraftLauncher",
)
