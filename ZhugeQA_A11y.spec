# -*- mode: python ; coding: utf-8 -*-


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
    ],
    hiddenimports=[
        'aiohttp',
        'aiosignal',
        'frozenlist',
        'imageio_ffmpeg',
        'multidict',
        'pyaudio',
        'pydub',
        'propcache',
        'realtime_dialog',
        'realtime_dialog.config',
        'realtime_dialog.dialog_worker',
        'realtime_dialog.protocol',
        'realtime_dialog.realtime_dialog_client',
        'websockets',
        'yarl',
    ],
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
