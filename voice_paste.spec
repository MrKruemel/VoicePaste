# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# PyInstaller spec file for Voice-to-Summary Paste Tool
# =============================================================================
#
# Produces a single portable .exe (--onefile) with all dependencies bundled,
# including faster-whisper and CTranslate2 for offline speech-to-text,
# and ElevenLabs TTS with miniaudio for audio playback (v0.6).
# Transcription works offline; summarization and TTS require internet.
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
# Expected output:  dist/VoicePaste.exe  (~115-165 MB, +~15 MB from TTS deps)
#
# Prerequisites:
#   pip install -r requirements.txt
#
# =============================================================================
# NATIVE LIBRARY BUNDLING NOTES (updated 2026-02-18)
# =============================================================================
# This build bundles several packages with native C++/Rust/C DLLs that
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
# _miniaudio       _miniaudio.pyd (CFFI, contains miniaudio  ~0.6 MB  (v0.6)
#                  C library statically linked)
# websockets       speedups.cp3xx-win_amd64.pyd              ~12 KB   (v0.6)
#
# ctranslate2.__init__.py (lines 3-18) uses pkg_resources to find its
# package directory, then calls os.add_dll_directory() and explicitly
# loads every *.dll via ctypes.CDLL().  Therefore:
#   1. The DLLs MUST land in a `ctranslate2/` subdirectory.
#   2. pkg_resources (setuptools) must be a hidden import.
#
# _miniaudio.pyd is a CFFI compiled extension.  It is imported by
# miniaudio.py via `from _miniaudio import ffi, lib`.  CFFI modules
# require the cffi and _cffi_backend hidden imports to load correctly
# in frozen mode.
#
# All native DLLs and .pyd extensions are excluded from UPX to prevent
# corruption.
#
# =============================================================================
# ONNXRUNTIME SEGFAULT FIX (2026-02-13)
# =============================================================================
# The Silero VAD component in faster-whisper uses onnxruntime for inference.
# In a PyInstaller --onefile bundle, onnxruntime can crash with a native
# segfault during InferenceSession.run() due to two issues:
#
# 1. MISSING ONNX MODEL FILES: faster_whisper.vad loads Silero encoder/
#    decoder ONNX models from faster_whisper/assets/. These are data files
#    that PyInstaller cannot detect via import analysis. They must be
#    bundled with collect_data_files('faster_whisper').
#
# 2. DLL SEARCH PATH: On Python 3.8+ (Windows), DLL search was restricted.
#    PyInstaller's bootloader adds _MEI root to the DLL search path but
#    NOT subdirectories like _MEI/onnxruntime/capi/. The runtime hook
#    rthook_onnxruntime.py calls os.add_dll_directory() for the capi/
#    subdirectory before any onnxruntime imports happen.
#
# 3. OPENMP THREAD-POOL CONFLICT: ctranslate2 bundles libiomp5md.dll
#    (Intel OpenMP). When both ctranslate2 and onnxruntime run in the
#    same process, the OpenMP thread pool can interfere with ORT's own
#    thread management. The runtime hook sets OMP_NUM_THREADS=1 to
#    prevent contention.
#
# References:
#   https://github.com/microsoft/onnxruntime/issues/25193
#   https://github.com/pyinstaller/pyinstaller/issues/8083
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

print(f'[voice_paste.spec] Building VoicePaste v{_version}')

# ---------------------------------------------------------------------------
# Debug mode: pass `-- --debug` on the pyinstaller command line
# ---------------------------------------------------------------------------
_debug_mode = '--debug' in sys.argv

if _debug_mode:
    print('[voice_paste.spec] DEBUG BUILD -- console enabled, no UPX')

# ---------------------------------------------------------------------------
# Data files to bundle inside the .exe
# ---------------------------------------------------------------------------
# sounddevice: PortAudio DLLs (libportaudio64bit.dll etc.)
_datas = collect_data_files('_sounddevice_data')

# faster_whisper: ONNX models for Silero VAD (silero_encoder_v5.onnx, etc.)
# These are loaded at runtime from faster_whisper/assets/ and PyInstaller
# cannot detect them via static analysis.
try:
    _fw_data = collect_data_files('faster_whisper')
    _datas += _fw_data
    print(f'[voice_paste.spec] Collected {len(_fw_data)} faster_whisper data files:')
    for _src, _dst in _fw_data:
        print(f'  {os.path.basename(_src)} -> {_dst}/')
except Exception as e:
    print(f'[voice_paste.spec] WARNING: Failed to collect faster_whisper data: {e}')
    print(f'  Silero VAD will fail at runtime (missing .onnx files)!')

# CTranslate2: Python source files, converter scripts, spec modules.
# NOTE: collect_data_files also picks up .dll files for ctranslate2, but
# we handle those properly in _binaries below via collect_dynamic_libs.
try:
    _ct2_data = collect_data_files('ctranslate2')
    # Filter out .dll files to avoid double-bundling (they go in _binaries)
    _ct2_data = [(src, dst) for src, dst in _ct2_data if not src.endswith('.dll')]
    _datas += _ct2_data
    print(f'[voice_paste.spec] Collected {len(_ct2_data)} ctranslate2 data files.')
except Exception as e:
    print(f'[voice_paste.spec] Note: ctranslate2 data files not found: {e}')

# onnxruntime: validation scripts, build_and_package_info, version_info.
# These are needed by onnxruntime's import-time validation code
# (onnxruntime_validation.py reads build_and_package_info at import).
# Filter out .dll files (already in _binaries) and heavy subpackages
# (quantization, transformers, tools) we don't need for inference.
try:
    _ort_data = collect_data_files('onnxruntime')
    # Filter out DLLs (handled by collect_dynamic_libs) and heavy subpackages
    _ort_data = [
        (src, dst) for src, dst in _ort_data
        if not src.endswith(('.dll', '.pyd'))
        and 'quantization' not in dst
        and 'transformers' not in dst
        and 'tools' not in dst
    ]
    _datas += _ort_data
    print(f'[voice_paste.spec] Collected {len(_ort_data)} onnxruntime data files.')
except Exception as e:
    print(f'[voice_paste.spec] Note: onnxruntime data files not found: {e}')

# sv_ttk: Sun Valley theme Tcl/Tk files (required for modern UI)
try:
    _svttk_data = collect_data_files('sv_ttk')
    _datas += _svttk_data
    print(f'[voice_paste.spec] Collected {len(_svttk_data)} sv_ttk theme data files.')
except Exception as e:
    print(f'[voice_paste.spec] Note: sv_ttk data files not found: {e}')

print(f'[voice_paste.spec] Collected {len(_datas)} total data files.')

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
    print(f'[voice_paste.spec] Collected {len(_ct2_bins)} ctranslate2 binaries:')
    for _src, _dst in _ct2_bins:
        print(f'  {os.path.basename(_src)} -> {_dst}/')
except Exception as e:
    print(f'[voice_paste.spec] WARNING: Failed to collect ctranslate2 binaries: {e}')
    print(f'  Local STT will likely fail at runtime!')

# --- onnxruntime: ONNX inference (used by Silero VAD in faster-whisper) ---
# Ships onnxruntime.dll + onnxruntime_providers_shared.dll in the capi/ subdir.
# The pybind11_state.pyd links to these at load time.
try:
    _ort_bins = collect_dynamic_libs('onnxruntime')
    _binaries += _ort_bins
    print(f'[voice_paste.spec] Collected {len(_ort_bins)} onnxruntime binaries:')
    for _src, _dst in _ort_bins:
        print(f'  {os.path.basename(_src)} -> {_dst}/')
except Exception as e:
    print(f'[voice_paste.spec] WARNING: Failed to collect onnxruntime binaries: {e}')
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
        print(f'[voice_paste.spec] Collected {len(_tok_bins)} tokenizers binaries.')
    else:
        print(f'[voice_paste.spec] tokenizers: no dynamic libs found (OK, .pyd '
              f'handled via hidden import).')
except Exception as e:
    print(f'[voice_paste.spec] Note: tokenizers dynamic libs not found: {e}')

print(f'[voice_paste.spec] Total native binaries to bundle: {len(_binaries)}')

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

    # --- faster-whisper and CTranslate2 (local STT) ---
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

    # --- huggingface_hub transitive deps ---
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

    # --- sv_ttk (Sun Valley theme for modern tkinter UI) ---
    'sv_ttk',

    # --- TTS: ElevenLabs SDK + miniaudio + websockets (v0.6) ---
    # elevenlabs: Uses __getattr__ lazy imports with importlib.import_module.
    # PyInstaller's static analysis CANNOT resolve these.
    # We only need the text_to_speech and core subpackages (used by tts.py).
    'elevenlabs',
    'elevenlabs.client',
    'elevenlabs.base_client',
    'elevenlabs.environment',
    'elevenlabs.realtime_tts',       # imports websockets.sync.client at module level
    'elevenlabs.music_custom',
    'elevenlabs.speech_to_text_custom',
    'elevenlabs.webhooks_custom',
    'elevenlabs.core',
    'elevenlabs.core.api_error',
    'elevenlabs.core.client_wrapper',
    'elevenlabs.core.jsonable_encoder',
    'elevenlabs.core.remove_none_from_dict',
    'elevenlabs.core.request_options',
    'elevenlabs.text_to_speech',
    'elevenlabs.text_to_speech.client',
    'elevenlabs.types',
    'elevenlabs.types.voice_settings',
    'elevenlabs.models',

    # miniaudio: CFFI-based audio decoder.
    # `from _miniaudio import ffi, lib` requires the CFFI runtime.
    'miniaudio',
    '_miniaudio',                    # CFFI compiled extension (.pyd)
    'cffi',                          # CFFI runtime (required by _miniaudio)
    '_cffi_backend',                 # CFFI C backend (.pyd)

    # websockets: used by elevenlabs SDK for streaming support.
    # Has a small C speedups extension (.pyd).
    'websockets',
    'websockets.asyncio',
    'websockets.sync',
    'websockets.sync.client',
    'websockets.sync.connection',
    'websockets.extensions',
    'websockets.legacy',
]

# ---------------------------------------------------------------------------
# Collect submodules for packages with lazy/dynamic imports
# ---------------------------------------------------------------------------
# These ensure that lazily-imported submodules are included in the bundle.
# For onnxruntime, we filter out the heavy subpackages (quantization,
# transformers, tools, backend) since we only need the capi/ inference core.
# For elevenlabs, we filter out subpackages not used by our TTS integration
# to avoid pulling in the entire SDK (~5000 types in __init__.py).
for _pkg in ('ctranslate2', 'faster_whisper', 'onnxruntime', 'elevenlabs',
             'websockets'):
    try:
        _subs = collect_submodules(_pkg)
        if _pkg == 'onnxruntime':
            _excluded_prefixes = (
                'onnxruntime.quantization',
                'onnxruntime.transformers',
                'onnxruntime.tools',
                'onnxruntime.backend',
            )
            _subs_before = len(_subs)
            _subs = [
                s for s in _subs
                if not any(s.startswith(pfx) for pfx in _excluded_prefixes)
            ]
            print(f'[voice_paste.spec] Collected {len(_subs)} onnxruntime '
                  f'submodules (excluded {_subs_before - len(_subs)} from '
                  f'quantization/transformers/tools/backend).')
        elif _pkg == 'elevenlabs':
            # The elevenlabs SDK is huge. We include all submodules because
            # the __getattr__ lazy-import mechanism makes it impossible to
            # predict which types get imported at runtime (e.g. via pydantic
            # model validation). The size impact is pure Python (~11 MB source)
            # which compresses well in the onefile archive.
            print(f'[voice_paste.spec] Collected {len(_subs)} submodules for {_pkg}.')
        else:
            print(f'[voice_paste.spec] Collected {len(_subs)} submodules for {_pkg}.')
        _hidden_imports += _subs
    except Exception as e:
        print(f'[voice_paste.spec] Note: could not collect submodules for {_pkg}: {e}')

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

    # onnxruntime subpackages not needed for Silero VAD inference.
    # These add hundreds of .py files and can pull in heavy dependencies.
    'onnxruntime.quantization',
    'onnxruntime.transformers',
    'onnxruntime.tools',
    'onnxruntime.backend',
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
    # Runtime hook to configure DLL search paths and OpenMP before any
    # user code runs.  This prevents native segfaults when onnxruntime
    # tries to load from the _MEI* temp directory.
    runtime_hooks=[os.path.join(SPECPATH, 'rthook_onnxruntime.py')],
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
    # These are compiled C++/Rust/C extensions.  UPX may corrupt them.
    'onnxruntime_pybind11_state.pyd',
    'tokenizers.pyd',

    # --- miniaudio CFFI extension (v0.6: TTS audio decoding) ---
    # _miniaudio.pyd is a CFFI-compiled C extension (~587 KB) containing
    # the entire miniaudio.h library statically linked.  UPX compression
    # can corrupt CFFI modules due to their embedded function pointer tables.
    '_miniaudio.pyd',
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
    name='VoicePaste',
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
    icon=os.path.join(SPECPATH, 'assets', 'app.ico'),
    version=None,           # TODO: Add version_info.txt for Windows properties
)

print(f'[voice_paste.spec] Build configuration complete.')
print(f'  Mode:    {"DEBUG" if _debug_mode else "RELEASE"}')
print(f'  Console: {"Yes" if _debug_mode else "No (windowed)"}')
print(f'  UPX:     {"Disabled" if _debug_mode else "Enabled (if available)"}')
print(f'  Output:  dist/VoicePaste.exe')
