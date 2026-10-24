# -*- mode: python ; coding: utf-8 -*-

from package_inputs import filter_package_toc
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules


nats_server = Path("tools/nats-server/nats-server.exe")
if not nats_server.is_file() or nats_server.stat().st_size == 0:
    raise FileNotFoundError(f"Required NATS server not found: {nats_server.resolve()}")
nats_datas = [(str(nats_server), "nats-server")]
nats_hiddenimports = collect_submodules("nats")
chat_title_rules = Path("assets/chat_title_rules.json")
if not chat_title_rules.is_file():
    raise FileNotFoundError(f"Required chat title rules not found: {chat_title_rules.resolve()}")


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
        ('assets/chat_title_rules.json', 'assets'),
        ('assets/codex-skills', 'assets/codex-skills'),
    ] + nats_datas,
    hiddenimports=[
        'aiohttp',
        'aiosignal',
        'frozenlist',
        'imageio_ffmpeg',
        'multidict',
        'pyaudio',
        'propcache',
        'websocket',
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
a.datas = filter_package_toc(a.datas)
a.binaries = filter_package_toc(a.binaries)
pyz = PYZ(a.pure)

b = Analysis(
    ['worker_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['websocket'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
b.datas = filter_package_toc(b.datas)
b.binaries = filter_package_toc(b.binaries)
worker_pyz = PYZ(b.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='mc',
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
)
worker_exe = EXE(
    worker_pyz,
    b.scripts,
    [],
    exclude_binaries=True,
    name='mc_worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    worker_exe,
    a.binaries,
    a.datas,
    b.binaries,
    b.datas,
    strip=False,
    upx=True,
    upx_exclude=['ZDSRAPI.dll', 'ZDSRAPI_x64.dll'],
    name='mc',
)
