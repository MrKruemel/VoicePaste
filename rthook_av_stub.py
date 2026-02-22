"""PyInstaller runtime hook: stub out PyAV (av) if not bundled.

faster-whisper declares 'av' as a dependency and imports it at module level
in faster_whisper/audio.py.  VoicePaste never uses av (we record raw PCM via
sounddevice), so we exclude it from the build to save ~119 MB.

This hook injects a lightweight stub module into sys.modules so that
faster-whisper's import succeeds without the real library.
"""
import sys
import types

if "av" not in sys.modules:
    _stub = types.ModuleType("av")
    _stub.__version__ = "0.0.0-stub"
    _stub.__path__ = []  # make it a package so sub-imports don't crash
    sys.modules["av"] = _stub
