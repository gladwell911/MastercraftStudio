# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules


nats_server = Path("tools/nats-server/nats-server.exe")
nats_datas = [(str(nats_server), "nats-server")] if nats_server.exists() else []
nats_hiddenimports = collect_submodules("nats")


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        ('ZDSRAPI.dll', '.'),
        ('ZDSRAPI_x64.dll', '.'),
    ],
    datas=[
        ('sound', 'sound'),
        ('ZDSRAPI.ini', '.'),
    ] + nats_datas,
    hiddenimports=[
        'aiohttp',
        'aiosignal',
        'frozenlist',
        'imageio_ffmpeg',
        'multidict',
        'pyaudio',
        'pydub',
        'propcache',
        'websockets',
        'yarl',
    ] + nats_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ZhugeQA_A11y',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
