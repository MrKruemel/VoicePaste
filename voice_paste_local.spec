# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# PyInstaller spec file for Voice-to-Summary Paste Tool -- LOCAL VARIANT
# =============================================================================
#
# This spec produces VoicePaste-Local.exe which includes faster-whisper
# and CTranslate2 for offline speech-to-text. It does NOT require an
# internet connection for transcription (only for summarization).
#
# Entry point:  src/main.py
# App name:     VoicePaste-Local
# Version:      Read from src/constants.py (APP_VERSION)
#
# Build commands:
#   Release:  pyinstaller voice_paste_local.spec
#   Debug:    pyinstaller voice_paste_local.spec -- --debug
#   (or use build.bat local for convenience)
#
# Expected output:  dist/VoicePaste-Local.exe  (~150-250 MB)
#
# Prerequisites (in addition to requirements.txt):
#   pip install -r requirements-local.txt
#
# =============================================================================
# NATIVE LIBRARY BUNDLING NOTES (2026-02-13)
# =============================================================================
# The local build bundles several packages with native C++/Rust DLLs that
# PyInstaller's static import analysis CANNOT detect.  These must be
# collected explicitly with collect_dynamic_libs() and added to `binaries`.
#
# Package          Native files                              Total size
# -------          ------------                              ----------
# ctranslate2      ctranslate2.dll, cudnn64_9.dll,           ~58 MB
#                  libiomp5md.dll, _ext.*.pyd
# onnxruntime      onnxruntime.dll,                          ~31 MB
#                  onnxruntime_providers_shared.dll,
#                  onnxruntime_pybind11_state.pyd
# tokenizers       tokenizers.pyd                            ~6.5 MB
#
# ctranslate2.__init__.py (lines 3-18) uses pkg_resources to find its
# package directory, then calls os.add_dll_directory() and explicitly
# loads every *.dll via ctypes.CDLL().  Therefore:
#   1. The DLLs MUST land in a `ctranslate2/` subdirectory.
#   2. pkg_resources (setuptools) must be a hidden import.
#
# All native DLLs are excluded from UPX to prevent corruption.
# =============================================================================

import os
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

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

print(f'[voice_paste_local.spec] Building VoicePaste-Local v{_version}')

# ---------------------------------------------------------------------------
# Debug mode: pass `-- --debug` on the pyinstaller command line
# ---------------------------------------------------------------------------
_debug_mode = '--debug' in sys.argv

if _debug_mode:
    print('[voice_paste_local.spec] DEBUG BUILD -- console enabled, no UPX')

# ---------------------------------------------------------------------------
# Data files to bundle inside the .exe
# ---------------------------------------------------------------------------
# sounddevice: PortAudio DLLs (libportaudio64bit.dll etc.)
_datas = collect_data_files('_sounddevice_data')

# CTranslate2: Python source files, converter scripts, spec modules.
# NOTE: collect_data_files also picks up .dll files for ctranslate2, but
# we handle those properly in _binaries below via collect_dynamic_libs.
try:
    _ct2_data = collect_data_files('ctranslate2')
    # Filter out .dll files to avoid double-bundling (they go in _binaries)
    _ct2_data = [(src, dst) for src, dst in _ct2_data if not src.endswith('.dll')]
    _datas += _ct2_data
    print(f'[voice_paste_local.spec] Collected {len(_ct2_data)} ctranslate2 data files.')
except Exception as e:
    print(f'[voice_paste_local.spec] Note: ctranslate2 data files not found: {e}')

print(f'[voice_paste_local.spec] Collected {len(_datas)} total data files.')

# ---------------------------------------------------------------------------
# Native binaries (DLLs / .pyd files) -- CRITICAL for local STT
# ---------------------------------------------------------------------------
# PyInstaller's static analysis cannot detect DLLs loaded via ctypes.CDLL()
# or implicit DLL dependencies of compiled extension modules.  We must
# collect them explicitly.
_binaries = []

# --- ctranslate2: C++ inference engine ---
# Ships 3 DLLs (ctranslate2.dll, cudnn64_9.dll, libiomp5md.dll) that are
# loaded at import time by ctranslate2/__init__.py via ctypes.CDLL().
# collect_dynamic_libs returns them targeted to the 'ctranslate2/' subdir,
# which is exactly where pkg_resources.resource_filename() expects them.
try:
    _ct2_bins = collect_dynamic_libs('ctranslate2')
    _binaries += _ct2_bins
    print(f'[voice_paste_local.spec] Collected {len(_ct2_bins)} ctranslate2 binaries:')
    for _src, _dst in _ct2_bins:
        print(f'  {os.path.basename(_src)} -> {_dst}/')
except Exception as e:
    print(f'[voice_paste_local.spec] WARNING: Failed to collect ctranslate2 binaries: {e}')
    print(f'  The local STT build will likely fail at runtime!')

# --- onnxruntime: ONNX inference (used by Silero VAD in faster-whisper) ---
# Ships onnxruntime.dll + onnxruntime_providers_shared.dll in the capi/ subdir.
# The pybind11_state.pyd links to these at load time.
try:
    _ort_bins = collect_dynamic_libs('onnxruntime')
    _binaries += _ort_bins
    print(f'[voice_paste_local.spec] Collected {len(_ort_bins)} onnxruntime binaries:')
    for _src, _dst in _ort_bins:
        print(f'  {os.path.basename(_src)} -> {_dst}/')
except Exception as e:
    print(f'[voice_paste_local.spec] WARNING: Failed to collect onnxruntime binaries: {e}')
    print(f'  Silero VAD will fail at runtime!')

# --- tokenizers: Rust-compiled tokenizer ---
# The tokenizers.pyd is a Python extension module and should be picked up
# by PyInstaller's import analysis via the hidden import.  However, we add
# it defensively via collect_dynamic_libs in case auto-detection fails.
# (collect_dynamic_libs may return empty for tokenizers since its .pyd is
# treated as a Python extension, not a standalone DLL -- that is OK.)
try:
    _tok_bins = collect_dynamic_libs('tokenizers')
    if _tok_bins:
        _binaries += _tok_bins
        print(f'[voice_paste_local.spec] Collected {len(_tok_bins)} tokenizers binaries.')
    else:
        print(f'[voice_paste_local.spec] tokenizers: no dynamic libs found (OK, .pyd '
              f'handled via hidden import).')
except Exception as e:
    print(f'[voice_paste_local.spec] Note: tokenizers dynamic libs not found: {e}')

print(f'[voice_paste_local.spec] Total native binaries to bundle: {len(_binaries)}')

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
_hidden_imports = [
    # --- pystray (system tray) ---
    'pystray._win32',
    'pystray._util.win32',

    # --- Pillow (PIL) ---
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',

    # --- sounddevice ---
    'sounddevice',
    '_sounddevice_data',

    # --- numpy ---
    'numpy',

    # --- keyboard ---
    'keyboard',
    'keyboard._winkeyboard',

    # --- openai SDK and transitive dependencies ---
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

    # --- keyring (v0.3: Windows Credential Manager) ---
    'keyring',
    'keyring.backends',
    'keyring.backends.Windows',

    # --- v0.4: faster-whisper and CTranslate2 ---
    'faster_whisper',
    'ctranslate2',
    'ctranslate2._ext',          # C++ Python extension (.pyd)
    'huggingface_hub',
    'tokenizers',
    'tokenizers.tokenizers',     # Rust-compiled .pyd -- import name differs from package
    'onnxruntime',
    'onnxruntime.capi',
    'onnxruntime.capi._pybind_state',
    'onnxruntime.capi.onnxruntime_pybind11_state',  # The actual .pyd extension
    # faster-whisper may import these lazily
    'faster_whisper.audio',
    'faster_whisper.transcribe',
    'faster_whisper.vad',
    'faster_whisper.feature_extractor',
    'faster_whisper.tokenizer',
    'faster_whisper.utils',

    # --- pkg_resources / setuptools ---
    # ctranslate2.__init__.py uses pkg_resources.resource_filename() on
    # Windows to locate its DLL directory.  Without this, ctranslate2
    # import fails with ModuleNotFoundError: No module named 'pkg_resources'.
    'pkg_resources',
    'setuptools',

    # --- v0.4: huggingface_hub transitive deps ---
    'yaml',
    'filelock',
    'fsspec',
    'requests',
    'urllib3',
    'charset_normalizer',

    # --- stdlib modules sometimes missed in onefile mode ---
    'ctypes',
    'ctypes.wintypes',
    'winsound',
    'tomllib',
    'email.mime.multipart',
    'email.mime.text',
]

# ---------------------------------------------------------------------------
# Collect all ctranslate2, faster_whisper, and onnxruntime submodules
# ---------------------------------------------------------------------------
# These ensure that lazily-imported submodules are included in the bundle.
for _pkg in ('ctranslate2', 'faster_whisper', 'onnxruntime'):
    try:
        _subs = collect_submodules(_pkg)
        _hidden_imports += _subs
        print(f'[voice_paste_local.spec] Collected {len(_subs)} submodules for {_pkg}.')
    except Exception as e:
        print(f'[voice_paste_local.spec] Note: could not collect submodules for {_pkg}: {e}')

# ---------------------------------------------------------------------------
# Modules to EXCLUDE
# ---------------------------------------------------------------------------
_excludes = [
    # GUI frameworks we do not use (except tkinter)
    'tk',
    'tcl',
    'PyQt5',
    'PyQt6',
    'PySide2',
    'PySide6',
    'wx',

    # Scientific / plotting libraries
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

    # Unused async backends
    'trio',
    'curio',

    # Other heavy or unused modules
    'xmlrpc',
    'lib2to3',

    # onnxruntime.quantization pulls in 'onnx' which is not installed
    # and not needed for inference-only usage (Silero VAD).
    'onnx',
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ['src/main.py'],
    pathex=[
        os.path.join(SPECPATH, 'src'),
    ],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=_debug_mode,
)

# ---------------------------------------------------------------------------
# PYZ archive
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# UPX exclude list
# ---------------------------------------------------------------------------
# Native DLLs compiled from C++/Rust must NOT be compressed by UPX.
# UPX can corrupt the internal structure of these binaries, causing
# them to fail to load at runtime with cryptic errors like
# "DLL load failed" or "ImportError: ... is not a valid Win32 application".
_upx_exclude = [
    # --- PortAudio (sounddevice) ---
    'libportaudio64bit.dll',
    'libportaudio64bit-asio.dll',

    # --- pydantic_core (Rust-compiled) ---
    'pydantic_core',

    # --- ctranslate2 native libraries ---
    # ctranslate2.dll is 55+ MB -- the main C++ inference engine.
    # cudnn64_9.dll and libiomp5md.dll are runtime dependencies.
    'ctranslate2.dll',
    'cudnn64_9.dll',
    'libiomp5md.dll',

    # --- onnxruntime native libraries ---
    # onnxruntime.dll is 15+ MB -- the ONNX inference runtime.
    # onnxruntime_providers_shared.dll is its provider abstraction layer.
    'onnxruntime.dll',
    'onnxruntime_providers_shared.dll',

    # --- Python extension modules (.pyd) that wrap native code ---
    # These are compiled C++/Rust extensions.  UPX may corrupt them.
    'onnxruntime_pybind11_state.pyd',
    'tokenizers.pyd',
]

# ---------------------------------------------------------------------------
# EXE configuration
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VoicePaste-Local',
    debug=_debug_mode,
    bootloader_ignore_signals=False,
    strip=False,
    upx=not _debug_mode,
    upx_exclude=_upx_exclude,
    runtime_tmpdir=None,
    console=_debug_mode,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    version=None,
)

print(f'[voice_paste_local.spec] Build configuration complete.')
print(f'  Mode:    {"DEBUG" if _debug_mode else "RELEASE"}')
print(f'  Console: {"Yes" if _debug_mode else "No (windowed)"}')
print(f'  UPX:     {"Disabled" if _debug_mode else "Enabled (if available)"}')
print(f'  Output:  dist/VoicePaste-Local.exe')
