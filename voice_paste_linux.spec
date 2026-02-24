# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# PyInstaller spec file for VoicePaste -- Linux (Ubuntu 22.04 / 24.04)
# =============================================================================
#
# Produces a single portable binary (--onefile) for Ubuntu Linux.
#
# Key differences from the Windows spec (voice_paste.spec):
#   - No Windows-specific hidden imports (keyboard, winsound, ctypes.wintypes)
#   - Linux-specific hidden imports (pynput for X11, evdev for Wayland, pystray._appindicator)
#   - No espeakng-loader bundling (uses system espeak-ng package)
#   - No .ico icon (no Windows icon resource)
#   - No UPX (rarely beneficial on Linux ELF binaries)
#   - Runtime hook rthook_av_stub.py blocks `av` import early (before any
#     code can transitively pull in PyAV, which is excluded from the bundle)
#   - Runtime hook rthook_onnxruntime.py sets OMP_NUM_THREADS for onnxruntime stability
#
# System dependencies (must be installed separately):
#   sudo apt install espeak-ng libportaudio2 xclip xdotool
#   sudo apt install gnome-shell-extension-appindicator  # for tray icon
#
# Build environment dependencies (for PyInstaller):
#   pip install pynput evdev  # both required: X11 and Wayland hotkey support
#   sudo usermod -aG input $USER  # for Wayland evdev device access (logout/login)
#
# Entry point:  src/main.py
# App name:     VoicePaste
#
# Build:
#   ./build_linux.sh              # release build
#   ./build_linux.sh debug        # debug build (console enabled)
#
# Expected output:  dist/VoicePaste  (~80-140 MB)
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

print(f'[voice_paste_linux.spec] Building VoicePaste v{_version} for Linux')

# ---------------------------------------------------------------------------
# Debug mode: pass `-- --debug` on the pyinstaller command line
# ---------------------------------------------------------------------------
_debug_mode = '--debug' in sys.argv

if _debug_mode:
    print('[voice_paste_linux.spec] DEBUG BUILD -- console enabled')

# ---------------------------------------------------------------------------
# Data files to bundle
# ---------------------------------------------------------------------------
# sounddevice: PortAudio shared libraries
_datas = collect_data_files('_sounddevice_data')

# faster_whisper: ONNX models for Silero VAD
try:
    _fw_data = collect_data_files('faster_whisper')
    _datas += _fw_data
    print(f'[voice_paste_linux.spec] Collected {len(_fw_data)} faster_whisper data files.')
except Exception as e:
    print(f'[voice_paste_linux.spec] WARNING: Failed to collect faster_whisper data: {e}')

# CTranslate2: Python source files (filter out .so/.dll)
try:
    _ct2_data = collect_data_files('ctranslate2')
    _ct2_data = [(src, dst) for src, dst in _ct2_data
                 if not src.endswith(('.dll', '.so', '.so.1'))]
    _datas += _ct2_data
    print(f'[voice_paste_linux.spec] Collected {len(_ct2_data)} ctranslate2 data files.')
except Exception as e:
    print(f'[voice_paste_linux.spec] Note: ctranslate2 data files not found: {e}')

# onnxruntime: validation scripts and build info
try:
    _ort_data = collect_data_files('onnxruntime')
    _ort_data = [
        (src, dst) for src, dst in _ort_data
        if not src.endswith(('.dll', '.so', '.pyd'))
        and 'quantization' not in dst
        and 'transformers' not in dst
        and 'tools' not in dst
    ]
    _datas += _ort_data
    print(f'[voice_paste_linux.spec] Collected {len(_ort_data)} onnxruntime data files.')
except Exception as e:
    print(f'[voice_paste_linux.spec] Note: onnxruntime data files not found: {e}')

# sv_ttk: Sun Valley theme for tkinter
try:
    _svttk_data = collect_data_files('sv_ttk')
    _datas += _svttk_data
    print(f'[voice_paste_linux.spec] Collected {len(_svttk_data)} sv_ttk theme data files.')
except Exception as e:
    print(f'[voice_paste_linux.spec] Note: sv_ttk data files not found: {e}')

# NOTE: espeakng-loader is NOT bundled on Linux.
# espeak-ng is a system package: sudo apt install espeak-ng
# The application loads libespeak-ng.so.1 from the system at runtime.

print(f'[voice_paste_linux.spec] Collected {len(_datas)} total data files.')

# ---------------------------------------------------------------------------
# Native binaries (.so files)
# ---------------------------------------------------------------------------
_binaries = []

# ctranslate2: C++ inference engine
try:
    _ct2_bins = collect_dynamic_libs('ctranslate2')
    _binaries += _ct2_bins
    print(f'[voice_paste_linux.spec] Collected {len(_ct2_bins)} ctranslate2 binaries.')
except Exception as e:
    print(f'[voice_paste_linux.spec] WARNING: Failed to collect ctranslate2 binaries: {e}')

# onnxruntime
try:
    _ort_bins = collect_dynamic_libs('onnxruntime')
    _binaries += _ort_bins
    print(f'[voice_paste_linux.spec] Collected {len(_ort_bins)} onnxruntime binaries.')
except Exception as e:
    print(f'[voice_paste_linux.spec] WARNING: Failed to collect onnxruntime binaries: {e}')

# tokenizers (Rust extension)
try:
    _tok_bins = collect_dynamic_libs('tokenizers')
    if _tok_bins:
        _binaries += _tok_bins
        print(f'[voice_paste_linux.spec] Collected {len(_tok_bins)} tokenizers binaries.')
except Exception as e:
    print(f'[voice_paste_linux.spec] Note: tokenizers dynamic libs not found: {e}')

print(f'[voice_paste_linux.spec] Total native binaries to bundle: {len(_binaries)}')

# ---------------------------------------------------------------------------
# Hidden imports -- Linux-specific
# ---------------------------------------------------------------------------
_hidden_imports = [
    # --- pystray (system tray) -- Linux AppIndicator backend ---
    'pystray._appindicator',
    'pystray._util',

    # --- pynput (hotkeys on Linux/X11) ---
    'pynput',
    'pynput.keyboard',
    'pynput.keyboard._xorg',
    'pynput.mouse',
    'pynput.mouse._xorg',
    'pynput._util.xorg',
    'pynput._util.xorg_keysyms',

    # --- evdev (hotkeys on Linux/Wayland) ---
    'evdev',
    'evdev.ecodes',
    'evdev.device',
    'evdev.events',
    'evdev.eventio',
    'evdev.util',

    # --- Pillow (PIL) ---
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',

    # --- sounddevice ---
    'sounddevice',
    '_sounddevice_data',

    # --- numpy ---
    'numpy',

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

    # --- keyring (Linux: SecretService / GNOME Keyring) ---
    'keyring',
    'keyring.backends',
    'keyring.backends.SecretService',

    # --- faster-whisper and CTranslate2 (local STT) ---
    'faster_whisper',
    'ctranslate2',
    'huggingface_hub',
    'tokenizers',
    'tokenizers.tokenizers',
    'onnxruntime',
    'onnxruntime.capi',
    'onnxruntime.capi._pybind_state',
    'faster_whisper.audio',
    'faster_whisper.transcribe',
    'faster_whisper.vad',
    'faster_whisper.feature_extractor',
    'faster_whisper.tokenizer',
    'faster_whisper.utils',

    # --- pkg_resources / setuptools ---
    'pkg_resources',
    'setuptools',

    # --- huggingface_hub transitive deps ---
    'yaml',
    'filelock',
    'fsspec',
    'requests',
    'urllib3',
    'charset_normalizer',

    # --- stdlib modules ---
    'ctypes',
    'tomllib',

    # --- sv_ttk (Sun Valley theme) ---
    'sv_ttk',

    # --- TTS: ElevenLabs SDK + miniaudio + websockets ---
    'elevenlabs',
    'elevenlabs.client',
    'elevenlabs.base_client',
    'elevenlabs.environment',
    'elevenlabs.realtime_tts',
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

    'miniaudio',
    '_miniaudio',
    'cffi',
    '_cffi_backend',

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
            print(f'[voice_paste_linux.spec] Collected {len(_subs)} onnxruntime '
                  f'submodules (excluded {_subs_before - len(_subs)}).')
        else:
            print(f'[voice_paste_linux.spec] Collected {len(_subs)} submodules for {_pkg}.')
        _hidden_imports += _subs
    except Exception as e:
        print(f'[voice_paste_linux.spec] Note: could not collect submodules for {_pkg}: {e}')

# ---------------------------------------------------------------------------
# Modules to EXCLUDE
# ---------------------------------------------------------------------------
_excludes = [
    # GUI frameworks we do not use
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',

    # Scientific / plotting libraries
    'matplotlib', 'scipy', 'pandas', 'IPython',
    'notebook', 'jupyter', 'jupyter_client', 'jupyter_core',

    # Test frameworks
    'test', 'tests', 'unittest', 'pytest', 'doctest', '_pytest',

    # Documentation and debugging
    'pdb', 'pydoc', 'pydoc_data',

    # Unused async backends
    'trio', 'curio',

    # Other heavy or unused modules
    'xmlrpc', 'lib2to3',

    # onnxruntime subpackages not needed for inference
    'onnx',
    'onnxruntime.quantization',
    'onnxruntime.transformers',
    'onnxruntime.tools',
    'onnxruntime.backend',

    # Windows-only modules (not available on Linux)
    'keyboard',
    'keyboard._winkeyboard',
    'winsound',
    'ctypes.wintypes',
    'pywin32_ctypes',
    'pystray._win32',
    'pystray._util.win32',
    'keyring.backends.Windows',

    # -----------------------------------------------------------------------
    # SIZE OPTIMIZATION: Packages pulled in by dependencies or leaked via
    # --system-site-packages that VoicePaste does NOT use at runtime.
    # Added 2026-02-22 -- saves ~120-150 MB in the final binary.
    # -----------------------------------------------------------------------

    # --- PyAV / FFmpeg (~119 MB uncompressed) ---
    # faster-whisper declares 'av' as a pip dependency, but VoicePaste
    # records raw PCM via sounddevice and never calls faster_whisper.audio's
    # av-based decode_audio(). Excluding this is the single biggest win.
    'av',

    # --- espeakng_loader (~21 MB: .so + espeak-ng-data/) ---
    # Redundant on Linux where espeak-ng is a system package.
    # local_tts.py checks for system libespeak-ng.so.1 first; the loader
    # is only needed on Windows. The system .so is still bundled as a
    # BINARY dependency automatically.
    'espeakng_loader',

    # --- Cython (pulled in transitively by av) ---
    'Cython',

    # --- System packages leaked via --system-site-packages ---
    # The venv uses --system-site-packages so PyGObject (gi) and
    # AppIndicator3 are available. This exposes all system-installed
    # packages to PyInstaller's import analysis. None of the following
    # are imported by VoicePaste source code.

    # Syntax highlighting / rich terminal (not used by VoicePaste)
    'pygments',
    'rich',
    'markdown_it',
    'mdurl',

    # Web frameworks and servers (not used by VoicePaste)
    'werkzeug',
    'flask', 'Flask',
    'gunicorn',
    'click',       # only an optional extra of httpx ('cli'), not runtime

    # SSH / remote access (not used by VoicePaste)
    'paramiko',
    'nacl', 'PyNaCl',
    'bcrypt',

    # Async HTTP (not used by VoicePaste -- httpx uses httpcore)
    'aiohttp', 'aiosignal', 'frozenlist', 'multidict', 'yarl',

    # XML / HTML parsing (not used by VoicePaste)
    'lxml',
    'html5lib',
    'bs4', 'beautifulsoup4', 'soupsieve',

    # Task runner / automation (not used by VoicePaste)
    'invoke',

    # Encoding detection (requests uses charset_normalizer, not chardet)
    'chardet',

    # JSON alternatives (VoicePaste uses stdlib json + jiter)
    'simplejson',

    # Docker (not used by VoicePaste)
    'docker', 'dockerpty',

    # System monitoring (not used by VoicePaste)
    'psutil',

    # ASN.1 parsing (not used by VoicePaste)
    'pyasn1', 'pyasn1_modules',

    # Template engines (not used by VoicePaste)
    'jinja2', 'Jinja2', 'MarkupSafe',

    # Serialization formats (not used by VoicePaste)
    'flatbuffers', 'protobuf', 'google.protobuf',

    # Crash reporting (Ubuntu system package, not used by VoicePaste)
    'apport',

    # Data structure libraries (not used by VoicePaste)
    'attr', 'attrs',
    'jsonschema', 'pyrsistent',

    # Internationalization (not used by VoicePaste)
    'babel', 'Babel',

    # --- Heavy ML frameworks (not used by VoicePaste) ---
    # May leak in via --system-site-packages or transitive deps.
    'torch', 'torch._C', 'torch.cuda',
    'torchvision', 'torchaudio',
    'transformers',
    'safetensors',
    'sympy', 'networkx', 'mpmath',
    'sentencepiece', 'accelerate', 'datasets', 'evaluate',
    'bitsandbytes', 'triton', 'flash_attn',
    'soundfile',
    'dateutil', 'tzdata',
    'jsonschema_specifications', 'referencing', 'rpds',
    'joblib',
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
    runtime_hooks=[
        os.path.join(SPECPATH, 'rthook_av_stub.py'),
        os.path.join(SPECPATH, 'rthook_onnxruntime.py'),
    ],
    excludes=_excludes,
    noarchive=_debug_mode,
)

# ---------------------------------------------------------------------------
# PYZ archive
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# EXE configuration -- Linux binary (no .exe extension)
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
    strip=not _debug_mode,       # Strip symbols in release mode
    upx=False,                   # UPX not used on Linux
    upx_exclude=[],
    runtime_tmpdir=None,
    console=_debug_mode,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

print(f'[voice_paste_linux.spec] Build configuration complete.')
print(f'  Mode:    {"DEBUG" if _debug_mode else "RELEASE"}')
print(f'  Console: {"Yes" if _debug_mode else "No (windowed)"}')
print(f'  Strip:   {"No" if _debug_mode else "Yes"}')
print(f'  Output:  dist/VoicePaste')
