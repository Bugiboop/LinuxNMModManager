# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Universal Mod Manager GUI
# Build with: .venv/bin/pyinstaller ModManager.spec

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Bundle customtkinter themes/images
datas = collect_data_files("customtkinter")

a = Analysis(
    ["sbmm_gui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=(
        collect_submodules("customtkinter")
        + ["PIL._tkinter_finder", "tkinter", "_tkinter", "tkinter.filedialog"]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ModManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
