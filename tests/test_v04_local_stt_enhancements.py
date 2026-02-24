"""Tests for v0.4 local faster-whisper STT integration enhancements.

Validates:
- _make_progress_tqdm_class() in model_manager.py:
    - Progress tracking via callback
    - Cancellation via cancel_event
    - tqdm-compatible interface (update, close, set_description, context manager, reset)
- _CancelledError exception
- _is_frozen() helper in local_stt.py
- Frozen-exe guard in LocalWhisperSTT.load_model()
- Enhanced error handling in load_model() (DLL, ctranslate2, OSError, MemoryError, RuntimeError)
- Enhanced error handling in is_faster_whisper_available() (DLL, ctranslate2, OSError)
- create_stt_backend() frozen-exe guard and model download checks
- New exception handlers in VoicePasteApp._run_pipeline() (ImportError, RuntimeError, MemoryError)
- New _start_recording() diagnostic checks for local mode
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch, call

import pytest


# =========================================================================
# _make_progress_tqdm_class (model_manager.py)
# =========================================================================


class TestMakeProgressTqdmClass:
    """Test the custom tqdm-compatible progress class factory."""

    def test_class_is_returned(self):
        """Factory should return a class (type), not an instance."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        assert isinstance(cls, type)

    def test_instance_has_tqdm_interface(self):
        """Instances should have update, close, set_description, refresh, reset."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=1000)

        assert hasattr(inst, "update")
        assert hasattr(inst, "close")
        assert hasattr(inst, "set_description")
        assert hasattr(inst, "set_postfix_str")
        assert hasattr(inst, "refresh")
        assert hasattr(inst, "reset")

    def test_init_captures_total(self):
        """Constructor should capture the 'total' kwarg."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=5000)
        assert inst.total == 5000

    def test_init_captures_initial(self):
        """Constructor should capture the 'initial' kwarg."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=5000, initial=100)
        assert inst.n == 100

    def test_init_handles_none_total(self):
        """total=None should be treated as 0."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=None)
        assert inst.total == 0

    def test_update_increments_n(self):
        """update(n) should increment the byte counter."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=1000)
        inst.update(100)
        assert inst.n == 100
        inst.update(250)
        assert inst.n == 350

    def test_update_calls_progress_callback(self):
        """update() should call the on_progress callback with (n, total)."""
        from model_manager import _make_progress_tqdm_class

        progress_calls = []

        def on_progress(downloaded, total):
            progress_calls.append((downloaded, total))

        cls = _make_progress_tqdm_class(on_progress, None)
        inst = cls(total=1000)
        inst.update(200)
        inst.update(300)

        assert len(progress_calls) == 2
        assert progress_calls[0] == (200, 1000)
        assert progress_calls[1] == (500, 1000)

    def test_update_skips_callback_when_total_zero(self):
        """update() should not call callback when total is 0."""
        from model_manager import _make_progress_tqdm_class

        progress_calls = []

        def on_progress(downloaded, total):
            progress_calls.append((downloaded, total))

        cls = _make_progress_tqdm_class(on_progress, None)
        inst = cls(total=0)
        inst.update(100)

        assert len(progress_calls) == 0

    def test_update_skips_callback_when_none(self):
        """update() should not crash when on_progress is None."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=1000)
        inst.update(100)  # Should not raise
        assert inst.n == 100

    def test_cancel_event_raises_cancelled_error(self):
        """_CancelledError raised when cancel_event is set (at init or update)."""
        from model_manager import _make_progress_tqdm_class, _CancelledError

        cancel = threading.Event()
        cancel.set()

        cls = _make_progress_tqdm_class(None, cancel)
        # With cancel already set, __init__ raises immediately
        with pytest.raises(_CancelledError, match="cancelled"):
            cls(total=1000)

    def test_cancel_event_checked_before_progress(self):
        """Cancellation should be checked before calling on_progress."""
        from model_manager import _make_progress_tqdm_class, _CancelledError

        cancel = threading.Event()
        progress_calls = []

        def on_progress(downloaded, total):
            progress_calls.append((downloaded, total))

        cls = _make_progress_tqdm_class(on_progress, cancel)
        # Create instance with cancel NOT set, then set it before update
        inst = cls(total=1000)
        cancel.set()

        with pytest.raises(_CancelledError):
            inst.update(100)

        # Cancel is checked before self.n += n, so callback should NOT be called.
        assert len(progress_calls) == 0

    def test_cancel_event_not_set_allows_progress(self):
        """update() should work normally when cancel_event is not set."""
        from model_manager import _make_progress_tqdm_class

        cancel = threading.Event()  # Not set

        cls = _make_progress_tqdm_class(None, cancel)
        inst = cls(total=1000)
        inst.update(500)  # Should not raise
        assert inst.n == 500

    def test_context_manager_protocol(self):
        """Class should support with-statement (__enter__/__exit__)."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)

        with cls(total=1000) as tracker:
            tracker.update(100)
            assert tracker.n == 100

    def test_set_description(self):
        """set_description should update the desc attribute."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=1000, desc="initial")
        assert inst.desc == "initial"

        inst.set_description("downloading model.bin")
        assert inst.desc == "downloading model.bin"

    def test_reset_resets_n_and_updates_total(self):
        """reset() should set n=0 and optionally update total."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=1000)
        inst.update(500)
        assert inst.n == 500

        inst.reset(total=2000)
        assert inst.n == 0
        assert inst.total == 2000

    def test_reset_without_total_keeps_existing(self):
        """reset() without a total arg should keep the existing total."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=1000)
        inst.update(500)

        inst.reset()
        assert inst.n == 0
        assert inst.total == 1000

    def test_disable_kwarg_accepted(self):
        """Constructor should accept disable=True without error."""
        from model_manager import _make_progress_tqdm_class

        cls = _make_progress_tqdm_class(None, None)
        inst = cls(total=1000, disable=True)
        assert inst.disable is True


class TestCancelledError:
    """Test the _CancelledError exception class."""

    def test_is_exception(self):
        """_CancelledError should be an Exception subclass."""
        from model_manager import _CancelledError

        assert issubclass(_CancelledError, Exception)

    def test_can_be_raised_and_caught(self):
        """Should be raisable and catchable."""
        from model_manager import _CancelledError

        with pytest.raises(_CancelledError):
            raise _CancelledError("test cancellation")

    def test_message_preserved(self):
        """The error message should be preserved."""
        from model_manager import _CancelledError

        err = _CancelledError("Download cancelled by user.")
        assert str(err) == "Download cancelled by user."


# =========================================================================
# download_model with progress and cancellation (model_manager.py)
# =========================================================================


class TestDownloadModelProgressIntegration:
    """Test that download_model() wires progress and cancellation correctly."""

    @patch("model_manager._verify_stt_integrity", return_value=True)
    def test_progress_callback_receives_updates(self, mock_verify, tmp_path, monkeypatch):
        """Progress callback should be called during download."""
        monkeypatch.setattr("platform_impl.get_cache_dir", lambda: tmp_path / "VoicePaste")
        progress_calls = []

        def on_progress(downloaded, total):
            progress_calls.append((downloaded, total))

        def fake_download(repo_id, local_dir, tqdm_class=None, **kwargs):
            Path(local_dir).mkdir(parents=True, exist_ok=True)
            (Path(local_dir) / "model.bin").write_bytes(b"fake")
            (Path(local_dir) / "config.json").write_text("{}")
            # Simulate HF Hub using the tqdm_class
            if tqdm_class:
                tracker = tqdm_class(total=1000)
                tracker.update(500)
                tracker.update(500)
                tracker.close()
            return local_dir

        mock_hf = MagicMock()
        mock_hf.snapshot_download = fake_download

        import model_manager

        with patch.dict("sys.modules", {"huggingface_hub": mock_hf}):
            result = model_manager.download_model("base", on_progress=on_progress)

        assert result is True
        assert len(progress_calls) == 2
        assert progress_calls[0] == (500, 1000)
        assert progress_calls[1] == (1000, 1000)

    def test_cancellation_aborts_download(self, tmp_path, monkeypatch):
        """Setting cancel_event during download should cause failure."""
        monkeypatch.setattr("platform_impl.get_cache_dir", lambda: tmp_path / "VoicePaste")

        cancel = threading.Event()

        def fake_download(repo_id, local_dir, tqdm_class=None, **kwargs):
            Path(local_dir).mkdir(parents=True, exist_ok=True)
            (Path(local_dir) / "model.bin").write_bytes(b"fake")
            (Path(local_dir) / "config.json").write_text("{}")
            # Simulate HF Hub using the tqdm_class -- cancel mid-way
            if tqdm_class:
                tracker = tqdm_class(total=1000)
                tracker.update(200)
                cancel.set()  # User clicks cancel
                tracker.update(200)  # This should raise _CancelledError
            return local_dir

        mock_hf = MagicMock()
        mock_hf.snapshot_download = fake_download

        import model_manager

        with patch.dict("sys.modules", {"huggingface_hub": mock_hf}):
            result = model_manager.download_model(
                "tiny", cancel_event=cancel
            )

        assert result is False
        # Partial download should be cleaned up
        partial = tmp_path / "VoicePaste" / "models" / "tiny"
        assert not partial.exists()


# =========================================================================
# _is_frozen() in local_stt.py
# =========================================================================


class TestIsFrozen:
    """Test the _is_frozen() helper function."""

    def test_returns_false_in_script_mode(self):
        """Should return False when running as a Python script."""
        from local_stt import _is_frozen

        # In test environment, sys.frozen should not be set
        result = _is_frozen()
        assert result is False

    def test_returns_true_when_frozen_attribute_set(self):
        """Should return True when sys.frozen is True."""
        from local_stt import _is_frozen

        with patch.object(sys, "frozen", True, create=True):
            result = _is_frozen()
        assert result is True

    def test_returns_false_when_frozen_attribute_false(self):
        """Should return False when sys.frozen is explicitly False."""
        from local_stt import _is_frozen

        with patch.object(sys, "frozen", False, create=True):
            result = _is_frozen()
        assert result is False

    def test_returns_false_when_no_frozen_attribute(self):
        """Should return False when sys.frozen does not exist."""
        from local_stt import _is_frozen

        # Ensure no frozen attribute
        if hasattr(sys, "frozen"):
            with patch.object(sys, "frozen", False):
                delattr(sys, "frozen")
                result = _is_frozen()
        else:
            result = _is_frozen()
        assert result is False


# =========================================================================
# Frozen-exe guard in LocalWhisperSTT.load_model()
# =========================================================================


class TestLoadModelFrozenGuard:
    """Test that load_model() refuses auto-download in frozen executables."""

    def setup_method(self):
        """Reset availability cache."""
        import local_stt
        local_stt._faster_whisper_available = True

    def test_frozen_no_model_path_raises_stt_error(self):
        """In frozen exe with no model_path, load_model should raise STTError."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        stt = LocalWhisperSTT(model_size="base", model_path=None)

        with patch("local_stt._is_frozen", return_value=True):
            with pytest.raises(STTError, match="not downloaded"):
                stt.load_model()

    def test_frozen_with_model_path_proceeds(self):
        """In frozen exe with model_path set, load_model should proceed."""
        from local_stt import LocalWhisperSTT

        mock_model = MagicMock()
        mock_cls = MagicMock(return_value=mock_model)

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(
                model_size="base",
                model_path=Path("/fake/models/base"),
            )
            with patch("local_stt._is_frozen", return_value=True):
                stt.load_model()

        assert stt.is_model_loaded is True

    def test_not_frozen_no_model_path_proceeds_with_warning(self):
        """In script mode with no model_path, load_model should proceed."""
        from local_stt import LocalWhisperSTT

        mock_model = MagicMock()
        mock_cls = MagicMock(return_value=mock_model)

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=None)
            with patch("local_stt._is_frozen", return_value=False):
                stt.load_model()

        assert stt.is_model_loaded is True

    def test_frozen_error_message_mentions_settings(self):
        """Error message should guide the user to Settings > Download Model."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        stt = LocalWhisperSTT(model_size="small", model_path=None)

        with patch("local_stt._is_frozen", return_value=True):
            with pytest.raises(STTError) as exc_info:
                stt.load_model()

        error_msg = str(exc_info.value)
        assert "Settings" in error_msg
        assert "Download Model" in error_msg
        assert "small" in error_msg


# =========================================================================
# Enhanced error handling in load_model()
# =========================================================================


class TestLoadModelErrorHandling:
    """Test enhanced error handlers in LocalWhisperSTT.load_model()."""

    def setup_method(self):
        """Reset availability cache."""
        import local_stt
        local_stt._faster_whisper_available = True

    def test_import_error_dll_gives_vc_redist_hint(self):
        """ImportError mentioning DLL should suggest VC++ Redistributable."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(side_effect=ImportError(
            "DLL load failed while importing _ext"
        ))

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=Path("/fake"))
            with pytest.raises(STTError, match="(?s)DLL.*Visual C"):
                stt.load_model()

    def test_import_error_ctranslate2_gives_install_hint(self):
        """ImportError mentioning ctranslate2 should suggest reinstall."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(side_effect=ImportError(
            "cannot import name 'ctranslate2'"
        ))

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=Path("/fake"))
            with pytest.raises(STTError, match="CTranslate2.*missing"):
                stt.load_model()

    def test_os_error_model_bin_missing(self):
        """OSError mentioning model.bin should suggest re-download."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(side_effect=OSError(
            "model.bin not found in /fake/models/base"
        ))

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=Path("/fake"))
            with pytest.raises(STTError, match="missing or corrupted"):
                stt.load_model()

    def test_os_error_generic_gives_dll_hint(self):
        """Generic OSError should mention DLL and VC++ Redistributable."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(side_effect=OSError(
            "Could not find shared library"
        ))

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=Path("/fake"))
            with pytest.raises(STTError, match="Operating system error"):
                stt.load_model()

    def test_runtime_error_compute_type(self):
        """RuntimeError about compute type should suggest changing settings."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(side_effect=RuntimeError(
            "Unsupported compute type 'float16' for device 'cpu'"
        ))

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=Path("/fake"))
            with pytest.raises(STTError, match="Compute type.*not supported"):
                stt.load_model()

    def test_runtime_error_cuda(self):
        """RuntimeError about CUDA should suggest setting device to cpu."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(side_effect=RuntimeError(
            "CUDA initialization: no CUDA-capable device is detected"
        ))

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=Path("/fake"))
            with pytest.raises(STTError, match="(?s)CUDA/GPU.*cpu"):
                stt.load_model()

    def test_runtime_error_generic(self):
        """Generic RuntimeError should suggest re-downloading the model."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(side_effect=RuntimeError(
            "Corrupted weight matrix"
        ))

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=Path("/fake"))
            with pytest.raises(STTError, match="(?s)Failed to load.*re-downloading"):
                stt.load_model()

    def test_generic_exception_with_not_found(self):
        """Exception with 'not found' should mention downloading."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_cls = MagicMock(side_effect=Exception(
            "Model directory not found"
        ))

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="base", model_path=Path("/fake"))
            with pytest.raises(STTError, match="(?s)not found.*download"):
                stt.load_model()


# =========================================================================
# Enhanced is_faster_whisper_available() error handling
# =========================================================================


class TestIsFasterWhisperAvailableEnhanced:
    """Test enhanced error handling in is_faster_whisper_available()."""

    def setup_method(self):
        """Reset the availability cache."""
        import local_stt
        local_stt._faster_whisper_available = None

    def test_dll_import_error_returns_false(self):
        """DLL-related ImportError should return False with guidance."""
        import local_stt
        local_stt._faster_whisper_available = None

        with patch.dict("sys.modules", {"faster_whisper": None}):
            with patch("builtins.__import__", side_effect=ImportError(
                "DLL load failed while importing _ext"
            )):
                result = local_stt.is_faster_whisper_available()

        assert result is False

    def test_os_error_returns_false(self):
        """OSError (native lib load failure) should return False."""
        import local_stt
        local_stt._faster_whisper_available = None

        with patch.dict("sys.modules", {"faster_whisper": None}):
            with patch("builtins.__import__", side_effect=OSError(
                "libctranslate2.dll: wrong architecture"
            )):
                result = local_stt.is_faster_whisper_available()

        assert result is False


# =========================================================================
# create_stt_backend() frozen-exe guard and model checks (stt.py)
# =========================================================================


class TestCreateSTTBackendFrozenGuard:
    """Test create_stt_backend() behavior for frozen exe and model checks."""

    def _make_config(self, **overrides):
        """Create a mock AppConfig with sensible defaults."""
        from config import AppConfig
        defaults = {
            "stt_backend": "local",
            "openai_api_key": "",
            "local_model_size": "base",
            "local_device": "cpu",
            "local_compute_type": "int8",
        }
        defaults.update(overrides)
        return AppConfig(**defaults)

    def test_local_frozen_no_model_returns_none(self):
        """In frozen exe with model not downloaded, should return None."""
        config = self._make_config(stt_backend="local")

        mock_local_stt = MagicMock()
        mock_local_stt.is_faster_whisper_available.return_value = True

        mock_model_manager = MagicMock()
        mock_model_manager.get_model_path.return_value = None  # Not downloaded

        with patch.dict("sys.modules", {
            "local_stt": mock_local_stt,
            "model_manager": mock_model_manager,
        }):
            with patch.object(sys, "frozen", True, create=True):
                from stt import create_stt_backend
                backend = create_stt_backend(config)

        assert backend is None

    def test_local_not_frozen_no_model_still_creates_backend(self):
        """In script mode with model not downloaded, should still create backend."""
        config = self._make_config(stt_backend="local")

        mock_local_stt = MagicMock()
        mock_local_stt.is_faster_whisper_available.return_value = True
        mock_local_instance = MagicMock()
        mock_local_stt.LocalWhisperSTT.return_value = mock_local_instance

        mock_model_manager = MagicMock()
        mock_model_manager.get_model_path.return_value = None  # Not downloaded

        # Ensure sys.frozen is not set
        frozen_val = getattr(sys, "frozen", "SENTINEL")
        if frozen_val != "SENTINEL":
            with patch.object(sys, "frozen", False, create=True):
                with patch.dict("sys.modules", {
                    "local_stt": mock_local_stt,
                    "model_manager": mock_model_manager,
                }):
                    from stt import create_stt_backend
                    backend = create_stt_backend(config)
        else:
            with patch.dict("sys.modules", {
                "local_stt": mock_local_stt,
                "model_manager": mock_model_manager,
            }):
                from stt import create_stt_backend
                backend = create_stt_backend(config)

        # Should still create (model_path=None passed to LocalWhisperSTT)
        assert backend is mock_local_instance

    def test_local_stt_error_returns_none(self):
        """STTError from LocalWhisperSTT.__init__ should return None."""
        from stt import STTError
        config = self._make_config(stt_backend="local")

        mock_local_stt = MagicMock()
        mock_local_stt.is_faster_whisper_available.return_value = True
        mock_local_stt.LocalWhisperSTT.side_effect = STTError("not installed")

        mock_model_manager = MagicMock()
        mock_model_manager.get_model_path.return_value = Path("/fake")

        with patch.dict("sys.modules", {
            "local_stt": mock_local_stt,
            "model_manager": mock_model_manager,
        }):
            from stt import create_stt_backend
            backend = create_stt_backend(config)

        assert backend is None

    def test_import_error_returns_none(self):
        """ImportError importing local_stt should return None."""
        config = self._make_config(stt_backend="local")

        # Make local_stt import fail
        with patch.dict("sys.modules", {
            "local_stt": None,
            "model_manager": None,
        }):
            from stt import create_stt_backend
            backend = create_stt_backend(config)

        assert backend is None


# =========================================================================
# _run_pipeline() new exception handlers (main.py)
# =========================================================================


@pytest.fixture
def pipeline_app():
    """Create a fully mocked VoicePasteApp for pipeline error tests."""
    mock_stt_instance = MagicMock()
    with patch("main.AudioRecorder") as MockRecorder, \
         patch("main.create_stt_backend", return_value=mock_stt_instance), \
         patch("main.CloudLLMSummarizer"), \
         patch("main.PassthroughSummarizer"), \
         patch("main.HotkeyManager"), \
         patch("main.TrayManager") as MockTray, \
         patch("main.clipboard_backup") as MockClipBackup, \
         patch("main.clipboard_restore") as MockClipRestore, \
         patch("main.paste_text") as MockPaste, \
         patch("main.play_recording_start_cue"), \
         patch("main.play_recording_stop_cue"), \
         patch("main.play_cancel_cue"), \
         patch("main.play_error_cue") as MockErrorCue:

        from config import AppConfig
        from main import VoicePasteApp

        config = AppConfig(
            openai_api_key="sk-test1234567890",
            summarization_enabled=True,
            audio_cues_enabled=True,
        )

        app = VoicePasteApp(config)

        app._recorder.start.return_value = True
        app._recorder.stop.return_value = b"fake-wav-data"
        type(app._recorder).is_recording = PropertyMock(return_value=False)

        app._stt.transcribe.return_value = "Test transcript"
        app._summarizer.summarize.return_value = "Test summary"
        MockPaste.return_value = True
        MockClipBackup.return_value = "saved clipboard"

        app._mocks = {
            "clip_backup": MockClipBackup,
            "clip_restore": MockClipRestore,
            "paste": MockPaste,
            "error_cue": MockErrorCue,
            "tray": MockTray,
        }

        yield app


class TestRunPipelineImportError:
    """Test ImportError handler in _run_pipeline()."""

    def test_import_error_dll_shows_vc_redist_message(self, pipeline_app):
        """ImportError with DLL should show VC++ Redistributable guidance."""
        from constants import AppState

        pipeline_app._stt.transcribe.side_effect = ImportError(
            "DLL load failed while importing ctranslate2._ext"
        )

        pipeline_app._on_hotkey()  # IDLE -> RECORDING
        pipeline_app._on_hotkey()  # RECORDING -> PROCESSING
        time.sleep(0.8)

        assert pipeline_app.state == AppState.IDLE
        pipeline_app._tray_manager.notify.assert_called()
        # Check for DLL-specific error message
        notify_calls = [
            str(c) for c in pipeline_app._tray_manager.notify.call_args_list
        ]
        assert any("DLL" in c or "Visual C" in c or "vc_redist" in c.lower()
                    for c in notify_calls)

    def test_import_error_generic_shows_module_message(self, pipeline_app):
        """Generic ImportError should show module loading failure."""
        from constants import AppState

        pipeline_app._stt.transcribe.side_effect = ImportError(
            "No module named 'some_module'"
        )

        pipeline_app._on_hotkey()
        pipeline_app._on_hotkey()
        time.sleep(0.8)

        assert pipeline_app.state == AppState.IDLE
        pipeline_app._tray_manager.notify.assert_called()

    def test_import_error_restores_clipboard(self, pipeline_app):
        """Clipboard should be restored after ImportError."""
        pipeline_app._stt.transcribe.side_effect = ImportError("missing module")

        pipeline_app._on_hotkey()
        pipeline_app._on_hotkey()
        time.sleep(0.8)

        pipeline_app._mocks["clip_restore"].assert_called_once_with(
            "saved clipboard"
        )


class TestRunPipelineRuntimeError:
    """Test RuntimeError handler in _run_pipeline()."""

    def test_runtime_error_cuda_shows_gpu_message(self, pipeline_app):
        """RuntimeError with CUDA should suggest switching to cpu."""
        from constants import AppState

        pipeline_app._stt.transcribe.side_effect = RuntimeError(
            "CUDA out of memory. Tried to allocate 256 MB"
        )

        pipeline_app._on_hotkey()
        pipeline_app._on_hotkey()
        time.sleep(0.8)

        assert pipeline_app.state == AppState.IDLE
        notify_calls = [
            str(c) for c in pipeline_app._tray_manager.notify.call_args_list
        ]
        assert any("GPU" in c or "gpu" in c or "cpu" in c for c in notify_calls)

    def test_runtime_error_oom_shows_memory_message(self, pipeline_app):
        """RuntimeError with OOM message should show memory guidance."""
        from constants import AppState

        pipeline_app._stt.transcribe.side_effect = RuntimeError(
            "out of memory"
        )

        pipeline_app._on_hotkey()
        pipeline_app._on_hotkey()
        time.sleep(0.8)

        assert pipeline_app.state == AppState.IDLE
        notify_calls = [
            str(c) for c in pipeline_app._tray_manager.notify.call_args_list
        ]
        assert any("memory" in c.lower() for c in notify_calls)

    def test_runtime_error_generic_shows_truncated_message(self, pipeline_app):
        """Generic RuntimeError should show error message truncated to 200 chars."""
        from constants import AppState

        long_msg = "x" * 500
        pipeline_app._stt.transcribe.side_effect = RuntimeError(long_msg)

        pipeline_app._on_hotkey()
        pipeline_app._on_hotkey()
        time.sleep(0.8)

        assert pipeline_app.state == AppState.IDLE
        pipeline_app._tray_manager.notify.assert_called()

    def test_runtime_error_restores_clipboard(self, pipeline_app):
        """Clipboard should be restored after RuntimeError."""
        pipeline_app._stt.transcribe.side_effect = RuntimeError("crash")

        pipeline_app._on_hotkey()
        pipeline_app._on_hotkey()
        time.sleep(0.8)

        pipeline_app._mocks["clip_restore"].assert_called_once_with(
            "saved clipboard"
        )


class TestRunPipelineMemoryError:
    """Test MemoryError handler in _run_pipeline()."""

    def test_memory_error_shows_oom_message(self, pipeline_app):
        """MemoryError should show out-of-memory guidance."""
        from constants import AppState

        pipeline_app._stt.transcribe.side_effect = MemoryError()

        pipeline_app._on_hotkey()
        pipeline_app._on_hotkey()
        time.sleep(0.8)

        assert pipeline_app.state == AppState.IDLE
        notify_calls = [
            str(c) for c in pipeline_app._tray_manager.notify.call_args_list
        ]
        assert any("memory" in c.lower() for c in notify_calls)

    def test_memory_error_restores_clipboard(self, pipeline_app):
        """Clipboard should be restored after MemoryError."""
        pipeline_app._stt.transcribe.side_effect = MemoryError()

        pipeline_app._on_hotkey()
        pipeline_app._on_hotkey()
        time.sleep(0.8)

        pipeline_app._mocks["clip_restore"].assert_called_once_with(
            "saved clipboard"
        )

    def test_memory_error_returns_to_idle(self, pipeline_app):
        """State should return to IDLE after MemoryError."""
        from constants import AppState

        pipeline_app._stt.transcribe.side_effect = MemoryError()

        pipeline_app._on_hotkey()
        pipeline_app._on_hotkey()
        time.sleep(0.8)

        assert pipeline_app.state == AppState.IDLE


# =========================================================================
# _start_recording() local STT diagnostic checks
# =========================================================================


class TestStartRecordingLocalDiagnostics:
    """Test _start_recording() diagnostic error messages for local STT mode."""

    @pytest.fixture
    def local_app_no_stt(self):
        """Create a VoicePasteApp with local config but no STT backend."""
        with patch("main.AudioRecorder"), \
             patch("main.create_stt_backend", return_value=None), \
             patch("main.CloudLLMSummarizer"), \
             patch("main.PassthroughSummarizer"), \
             patch("main.HotkeyManager"), \
             patch("main.TrayManager") as MockTray, \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text"), \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue") as MockErrorCue:

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                stt_backend="local",
                local_model_size="base",
            )

            app = VoicePasteApp(config)
            app._mocks = {
                "error_cue": MockErrorCue,
                "tray": MockTray,
            }

            yield app

    def test_local_no_stt_faster_whisper_missing(self, local_app_no_stt):
        """Shows specific error when faster-whisper is not available."""
        from constants import AppState

        mock_local_stt = MagicMock()
        mock_local_stt.is_faster_whisper_available.return_value = False
        mock_model_manager = MagicMock()

        with patch.dict("sys.modules", {
            "local_stt": mock_local_stt,
            "model_manager": mock_model_manager,
        }):
            local_app_no_stt._start_recording()

        assert local_app_no_stt.state == AppState.IDLE
        local_app_no_stt._tray_manager.notify.assert_called()
        notify_msg = str(local_app_no_stt._tray_manager.notify.call_args)
        assert "faster-whisper" in notify_msg.lower() or "not available" in notify_msg.lower()

    def test_local_no_stt_model_not_downloaded(self, local_app_no_stt):
        """Shows specific error when model is not downloaded."""
        from constants import AppState

        mock_local_stt = MagicMock()
        mock_local_stt.is_faster_whisper_available.return_value = True
        mock_model_manager = MagicMock()
        mock_model_manager.is_model_available.return_value = False

        with patch.dict("sys.modules", {
            "local_stt": mock_local_stt,
            "model_manager": mock_model_manager,
        }):
            local_app_no_stt._start_recording()

        assert local_app_no_stt.state == AppState.IDLE
        notify_msg = str(local_app_no_stt._tray_manager.notify.call_args)
        assert "download" in notify_msg.lower() or "not downloaded" in notify_msg.lower()

    def test_cloud_no_stt_shows_api_key_message(self):
        """Cloud mode with no API key shows API key guidance."""
        with patch("main.AudioRecorder"), \
             patch("main.create_stt_backend", return_value=None), \
             patch("main.CloudLLMSummarizer"), \
             patch("main.PassthroughSummarizer"), \
             patch("main.HotkeyManager"), \
             patch("main.TrayManager") as MockTray, \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text"), \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue"):

            from config import AppConfig
            from main import VoicePasteApp
            from constants import AppState

            config = AppConfig(stt_backend="cloud", openai_api_key="")
            app = VoicePasteApp(config)
            app._start_recording()

            assert app.state == AppState.IDLE
            notify_msg = str(app._tray_manager.notify.call_args)
            assert "api key" in notify_msg.lower() or "API key" in notify_msg


# =========================================================================
# Transcription error handling in local_stt.py transcribe()
# =========================================================================


class TestTranscribeErrorHandling:
    """Test enhanced error handling in LocalWhisperSTT.transcribe()."""

    def setup_method(self):
        import local_stt
        local_stt._faster_whisper_available = True

    def _make_wav(self):
        """Create minimal WAV bytes for test."""
        import io
        import wave
        import numpy as np

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(np.zeros(100, dtype=np.int16).tobytes())
        return buf.getvalue()

    def test_runtime_error_cuda_in_transcribe(self):
        """RuntimeError with CUDA during transcription gives GPU guidance."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError(
            "CUDA error: device-side assert triggered"
        )

        mock_cls = MagicMock(return_value=mock_model)
        wav = self._make_wav()

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="tiny")
            with pytest.raises(STTError, match="GPU error"):
                stt.transcribe(wav)

    def test_runtime_error_oom_in_transcribe(self):
        """RuntimeError with OOM during transcription gives memory guidance."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError(
            "out of memory"
        )

        mock_cls = MagicMock(return_value=mock_model)
        wav = self._make_wav()

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="tiny")
            with pytest.raises(STTError, match="Out of memory"):
                stt.transcribe(wav)

    def test_runtime_error_generic_in_transcribe(self):
        """Generic RuntimeError during transcription wraps in STTError."""
        from local_stt import LocalWhisperSTT
        from stt import STTError

        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError(
            "Model format mismatch"
        )

        mock_cls = MagicMock(return_value=mock_model)
        wav = self._make_wav()

        with patch.dict("sys.modules", {
            "faster_whisper": MagicMock(WhisperModel=mock_cls),
        }):
            stt = LocalWhisperSTT(model_size="tiny")
            with pytest.raises(STTError, match="Local transcription failed"):
                stt.transcribe(wav)
