# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# PyInstaller spec file for Voice-to-Summary Paste Tool
# =============================================================================
#
# Produces a single portable .exe (--onefile) with no console window
# (--noconsole / windowed mode) for the system tray application.
#
# Entry point:  src/main.py
# App name:     VoicePaste
# Version:      Read from src/constants.py (APP_VERSION)
#
# Build commands:
#   Release:  pyinstaller voice_paste.spec
#   Debug:    pyinstaller voice_paste.spec -- --debug
#   (or use build.bat for convenience)
#
# Expected output:  dist/VoicePaste.exe  (~40-60 MB)
# =============================================================================

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# ---------------------------------------------------------------------------
# Read version from constants.py (single source of truth)
# ---------------------------------------------------------------------------
_constants_path = os.path.join(SPECPATH, 'src', 'constants.py')
_version = '0.0.0'
with open(_constants_path, 'r', encoding='utf-8') as _f:
    for _line in _f:
        if _line.startswith('APP_VERSION'):
            _version = _line.split('=')[1].strip().strip('"').strip("'")
            break

print(f'[voice_paste.spec] Building VoicePaste v{_version}')

# ---------------------------------------------------------------------------
# Debug mode: pass `-- --debug` on the pyinstaller command line
# In debug mode we keep the console window and disable UPX for easier
# troubleshooting.  Example:
#   pyinstaller voice_paste.spec -- --debug
# ---------------------------------------------------------------------------
_debug_mode = '--debug' in sys.argv

if _debug_mode:
    print('[voice_paste.spec] DEBUG BUILD -- console enabled, no UPX')

# ---------------------------------------------------------------------------
# Data files to bundle inside the .exe
# ---------------------------------------------------------------------------
# sounddevice ships a `_sounddevice_data` package that contains the
# PortAudio shared libraries (libportaudio64bit.dll). These MUST be
# included or audio recording will fail at runtime.
_datas = collect_data_files('_sounddevice_data')

print(f'[voice_paste.spec] Collected {len(_datas)} sounddevice data files:')
for _src, _dst in _datas:
    print(f'  {_src} -> {_dst}')

# ---------------------------------------------------------------------------
# Hidden imports -- modules that PyInstaller's static analysis misses
# ---------------------------------------------------------------------------
# These are organized by the dependency that requires them.
_hidden_imports = [
    # --- pystray (system tray) ---
    # pystray dynamically imports its platform backend.  On Windows it
    # uses _win32 which in turn uses _util.win32.  Without these, the
    # tray icon silently fails to appear.
    'pystray._win32',
    'pystray._util.win32',

    # --- Pillow (PIL) ---
    # pystray depends on Pillow for icon images.  PyInstaller sometimes
    # tree-shakes out Image/ImageDraw when they appear to be unused at
    # the top level.  Listing them here ensures they survive.
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',

    # --- sounddevice ---
    # sounddevice is a single-file module but it requires
    # _sounddevice_data at runtime for the PortAudio DLL.
    'sounddevice',
    '_sounddevice_data',

    # --- numpy ---
    # numpy is used by sounddevice for audio buffers.  Most of it is
    # auto-detected but we list it here defensively.
    # NOTE: numpy 2.x moved internals from numpy.core to numpy._core.
    # Do NOT add numpy.core._methods -- it no longer exists in numpy 2.x
    # and causes a build ERROR (harmless but noisy).
    'numpy',

    # --- keyboard ---
    # keyboard is a pure-Python package.  On Windows it uses ctypes to
    # call user32.dll (no extra DLLs needed).  Listed here defensively
    # in case auto-detection misses the entry point module.
    'keyboard',
    'keyboard._winkeyboard',

    # --- openai SDK and its transitive dependencies ---
    # The openai SDK uses httpx -> httpcore -> h11 for HTTP, anyio for
    # async I/O, pydantic for response models, and jiter for fast JSON
    # parsing.  PyInstaller often misses some of these because they are
    # imported lazily or conditionally.
    'openai',
    'httpx',
    'httpcore',
    'httpcore._async',
    'httpcore._sync',
    'h11',
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',
    'sniffio',
    'certifi',
    'idna',
    'pydantic',
    'pydantic.deprecated',
    'pydantic.deprecated.decorator',
    'pydantic_core',
    'annotated_types',
    'typing_extensions',
    'distro',
    'jiter',
    'tqdm',
    'colorama',

    # --- stdlib modules sometimes missed in onefile mode ---
    'ctypes',
    'ctypes.wintypes',
    'winsound',
    'tomllib',
    'email.mime.multipart',
    'email.mime.text',
]

# ---------------------------------------------------------------------------
# Modules to EXCLUDE -- reduce binary size by removing unused stdlib
# and third-party modules that get pulled in transitively.
# ---------------------------------------------------------------------------
_excludes = [
    # GUI frameworks we do not use
    'tkinter',
    '_tkinter',
    'tk',
    'tcl',
    'PyQt5',
    'PyQt6',
    'PySide2',
    'PySide6',
    'wx',

    # Scientific / plotting libraries (numpy is needed, but not these)
    'matplotlib',
    'scipy',
    'pandas',
    'IPython',
    'notebook',
    'jupyter',
    'jupyter_client',
    'jupyter_core',

    # Test frameworks
    'test',
    'tests',
    'unittest',
    'pytest',
    'doctest',
    '_pytest',

    # Documentation and debugging
    'pdb',
    'pydoc',
    'pydoc_data',

    # Unused async backends (we only use asyncio)
    'trio',
    'curio',

    # Other heavy or unused modules
    'xmlrpc',
    # NOTE: Do NOT exclude 'concurrent' or 'multiprocessing'.
    # asyncio.base_events imports concurrent.futures, which is required
    # by the openai SDK.  Excluding these causes:
    #   ModuleNotFoundError: No module named 'concurrent'
    'lib2to3',
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ['src/main.py'],
    pathex=[
        os.path.join(SPECPATH, 'src'),   # Ensure src/ modules are found
    ],
    binaries=[],
    datas=_datas,
    hiddenimports=_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=_debug_mode,  # Keep .pyc files unpacked in debug mode
)

# ---------------------------------------------------------------------------
# PYZ archive (compressed Python modules)
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# EXE configuration
# ---------------------------------------------------------------------------
# In release mode:  windowed (no console), UPX enabled
# In debug mode:    console visible, UPX disabled
#
# NOTE: UPX is only applied if the `upx` binary is found on PATH.
# If UPX causes issues with bundled DLLs (especially PortAudio or
# pydantic_core), add them to upx_exclude below.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VoicePaste',
    debug=_debug_mode,
    bootloader_ignore_signals=False,
    strip=False,           # Do not strip symbols on Windows
    upx=not _debug_mode,   # UPX only in release mode
    upx_exclude=[
        # DLLs that must NOT be compressed by UPX.
        # PortAudio DLLs can be corrupted by UPX compression.
        'libportaudio64bit.dll',
        'libportaudio64bit-asio.dll',
        # pydantic_core is a Rust-compiled extension; UPX may break it.
        'pydantic_core',
    ],
    runtime_tmpdir=None,
    console=_debug_mode,    # Console only in debug mode
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # --- Windows-specific metadata ---
    icon=None,              # TODO: Add app.ico when icon asset is created
    version=None,           # TODO: Add version_info.txt for Windows properties
)

print(f'[voice_paste.spec] Build configuration complete.')
print(f'  Mode:    {"DEBUG" if _debug_mode else "RELEASE"}')
print(f'  Console: {"Yes" if _debug_mode else "No (windowed)"}')
print(f'  UPX:     {"Disabled" if _debug_mode else "Enabled (if available)"}')
print(f'  Output:  dist/VoicePaste.exe')
