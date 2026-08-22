# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).parent
icon = root / "desktop" / "brand" / "AppIcon.icns"

a = Analysis(
    [str(root / "packaging" / "launch_desktop.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[(str(root / "desktop"), "desktop")],
    hiddenimports=[
        "mailkit",
        "mailkit.desktop",
        "mailkit.daemon",
        "mailkit.cli.main",
        "webview",
        "cryptography",
    ],
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
    [],
    exclude_binaries=True,
    name="Mailkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(icon) if icon.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Mailkit",
)
app = BUNDLE(
    coll,
    name="Mailkit.app",
    icon=str(icon) if icon.exists() else None,
    bundle_identifier="org.zermo.mailkit",
    info_plist={
        "CFBundleName": "Mailkit",
        "CFBundleDisplayName": "Mailkit",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "12.0",
        "NSHumanReadableCopyright": "Zermo Brands — an entity of zermo.org",
    },
)
