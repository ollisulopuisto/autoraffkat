# -*- mode: python ; coding: utf-8 -*-
from __future__ import annotations

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None

BASE_DIR = Path(SPEC).parent.resolve()
SRC_DIR = BASE_DIR / "src"

datas = [
    (str(SRC_DIR / "autoraffkat" / "server" / "static"), "autoraffkat/server/static"),
    (str(SRC_DIR / "autoraffkat" / "server" / "static"), "server/static"),
]
assets_dir = BASE_DIR / "assets"
if assets_dir.exists():
    datas.append((str(assets_dir), "assets"))
    datas.append((str(assets_dir), "autoraffkat/assets"))

datas += collect_data_files("pedalboard")

binaries = []
bin_dir = BASE_DIR / "bin"
if bin_dir.exists():
    for f in bin_dir.glob("*"):
        if f.is_file() and not f.name.startswith("."):
            binaries.append((str(f), "bin"))

binaries += collect_dynamic_libs("pedalboard")

hiddenimports = [
    "autoraffkat",
    "autoraffkat.paths",
    "autoraffkat.gui",
    "autoraffkat.audio.binaries",
    "pedalboard",
    "pyloudnorm",
    "fastapi",
    "fastapi.staticfiles",
    "fastapi.responses",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespans",
    "uvicorn.lifespans.auto",
    "webview",
]
if sys.platform == "darwin":
    hiddenimports += [
        "webview.platforms.cocoa",
        "objc",
        "AppKit",
        "WebKit",
        "Foundation",
    ]
elif sys.platform.startswith("win"):
    hiddenimports += [
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
    ]

hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("autoraffkat")

a = Analysis(
    [str(SRC_DIR / "autoraffkat" / "__main__.py")],
    pathex=[str(SRC_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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

darwin_icon = None
for _name in ("autoraffkat.icns", "icon.icns"):
    _p = BASE_DIR / "assets" / _name
    if _p.exists():
        darwin_icon = str(_p)
        break

win_icon = None
for _name in ("autoraffkat.ico", "icon.ico"):
    _p = BASE_DIR / "assets" / _name
    if _p.exists():
        win_icon = str(_p)
        break

icon_path = darwin_icon if sys.platform == "darwin" else win_icon

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="autoraffkat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="autoraffkat",
)

if sys.platform == "darwin":
    bundle_icon_file = os.path.basename(darwin_icon) if darwin_icon else "autoraffkat.icns"
    app = BUNDLE(
        coll,
        name="autoraffkat.app",
        icon=darwin_icon,
        bundle_identifier="com.sulopuis.autoraffkat",
        info_plist={
            "CFBundleName": "autoraffkat",
            "CFBundleDisplayName": "autoraffkat",
            "CFBundleVersion": "2026.8.22.1",
            "CFBundleShortVersionString": "2026.8.22.1",
            "CFBundleIconFile": bundle_icon_file,
            "NSHighResolutionCapable": "True",
            "NSRequiresAquaSystemAppearance": "False",
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Final Cut Pro XML",
                    "CFBundleTypeRole": "Editor",
                    "LSHandlerRank": "Alternate",
                    "LSItemContentTypes": [
                        "com.apple.finalcutpro.xml",
                        "public.xml",
                    ],
                    "CFBundleTypeExtensions": ["fcpxml", "fcpxmld", "xml"],
                }
            ],
        },
    )
