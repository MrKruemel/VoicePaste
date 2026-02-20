"""Tests for the model manager module.

Validates:
- Cache directory creation and location
- Model availability checks
- Model validation (required files)
- Download (mocked huggingface_hub)
- Delete and cache size
"""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import model_manager


class TestGetCacheDir:
    """Test cache directory resolution."""

    def test_uses_localappdata_env(self, tmp_path, monkeypatch):
        """Cache dir should use %LOCALAPPDATA%."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        result = model_manager.get_cache_dir()
        assert result == tmp_path / "VoicePaste" / "models"
        assert result.exists()

    def test_creates_directory_if_missing(self, tmp_path, monkeypatch):
        """Cache dir should be created if it doesn't exist."""
        target = tmp_path / "custom"
        monkeypatch.setenv("LOCALAPPDATA", str(target))
        result = model_manager.get_cache_dir()
        assert result.exists()

    def test_fallback_when_no_localappdata(self, monkeypatch):
        """Falls back to ~/AppData/Local when env var is missing."""
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        result = model_manager.get_cache_dir()
        assert "VoicePaste" in str(result)
        assert "models" in str(result)


class TestIsModelValid:
    """Test model directory validation."""

    def test_valid_model_with_required_files(self, tmp_path):
        """Model dir with model.bin and config.json is valid."""
        model_dir = tmp_path / "base"
        model_dir.mkdir()
        (model_dir / "model.bin").write_bytes(b"fake-model")
        (model_dir / "config.json").write_text("{}")
        assert model_manager._is_model_valid(model_dir) is True

    def test_missing_model_bin(self, tmp_path):
        """Model dir without model.bin is invalid."""
        model_dir = tmp_path / "base"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        assert model_manager._is_model_valid(model_dir) is False

    def test_missing_config_json(self, tmp_path):
        """Model dir without config.json is invalid."""
        model_dir = tmp_path / "base"
        model_dir.mkdir()
        (model_dir / "model.bin").write_bytes(b"fake-model")
        assert model_manager._is_model_valid(model_dir) is False

    def test_empty_directory(self, tmp_path):
        """Empty model dir is invalid."""
        model_dir = tmp_path / "base"
        model_dir.mkdir()
        assert model_manager._is_model_valid(model_dir) is False


class TestGetModelPath:
    """Test model path retrieval."""

    def test_returns_path_for_valid_model(self, tmp_path, monkeypatch):
        """Returns path when model is downloaded and valid."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        model_dir = tmp_path / "VoicePaste" / "models" / "base"
        model_dir.mkdir(parents=True)
        (model_dir / "model.bin").write_bytes(b"fake")
        (model_dir / "config.json").write_text("{}")

        result = model_manager.get_model_path("base")
        assert result == model_dir

    def test_returns_none_for_missing_model(self, tmp_path, monkeypatch):
        """Returns None when model is not downloaded."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        result = model_manager.get_model_path("base")
        assert result is None

    def test_returns_none_for_unknown_size(self, tmp_path, monkeypatch):
        """Returns None for unrecognized model size."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        result = model_manager.get_model_path("gigantic")
        assert result is None

    def test_returns_none_for_invalid_model(self, tmp_path, monkeypatch):
        """Returns None when model dir exists but is incomplete."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        model_dir = tmp_path / "VoicePaste" / "models" / "base"
        model_dir.mkdir(parents=True)
        # Only config.json, missing model.bin
        (model_dir / "config.json").write_text("{}")

        result = model_manager.get_model_path("base")
        assert result is None


class TestIsModelAvailable:
    """Test model availability check."""

    def test_available_when_valid(self, tmp_path, monkeypatch):
        """Returns True when model is downloaded and valid."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        model_dir = tmp_path / "VoicePaste" / "models" / "tiny"
        model_dir.mkdir(parents=True)
        (model_dir / "model.bin").write_bytes(b"fake")
        (model_dir / "config.json").write_text("{}")

        assert model_manager.is_model_available("tiny") is True

    def test_not_available_when_missing(self, tmp_path, monkeypatch):
        """Returns False when model is not downloaded."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert model_manager.is_model_available("tiny") is False


class TestGetModelInfo:
    """Test model info retrieval."""

    def test_info_for_base_model(self, tmp_path, monkeypatch):
        """Returns correct info for base model."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        info = model_manager.get_model_info("base")
        assert info["repo"] == "Systran/faster-whisper-base"
        assert info["download_mb"] == 145
        assert info["ram_mb"] == 200
        assert info["available"] is False

    def test_info_for_unknown_model(self):
        """Returns defaults for unknown model."""
        info = model_manager.get_model_info("nonexistent")
        assert info["repo"] == "unknown"
        assert info["download_mb"] == 0


class TestGetAllModelSizes:
    """Test model size listing."""

    def test_returns_all_sizes(self):
        """Returns all known model sizes."""
        sizes = model_manager.get_all_model_sizes()
        assert "tiny" in sizes
        assert "base" in sizes
        assert "small" in sizes
        assert "medium" in sizes
        assert "large-v2" in sizes
        assert "large-v3" in sizes

    def test_returns_list(self):
        """Returns a list type."""
        assert isinstance(model_manager.get_all_model_sizes(), list)


class TestGetAvailableModelSizes:
    """Test available model listing."""

    def test_empty_when_none_downloaded(self, tmp_path, monkeypatch):
        """Returns empty list when no models are downloaded."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert model_manager.get_available_model_sizes() == []

    def test_lists_downloaded_models(self, tmp_path, monkeypatch):
        """Returns only downloaded model sizes."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        # Create a valid "tiny" model
        model_dir = tmp_path / "VoicePaste" / "models" / "tiny"
        model_dir.mkdir(parents=True)
        (model_dir / "model.bin").write_bytes(b"fake")
        (model_dir / "config.json").write_text("{}")

        available = model_manager.get_available_model_sizes()
        assert "tiny" in available
        assert "base" not in available


class TestDownloadModel:
    """Test model download (mocked)."""

    @patch("model_manager.snapshot_download", create=True)
    @patch("model_manager._verify_stt_integrity", return_value=True)
    def test_successful_download(self, mock_verify, mock_download, tmp_path, monkeypatch):
        """Successful download returns True."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        target_dir = tmp_path / "VoicePaste" / "models" / "base"

        def fake_download(repo_id, local_dir, **kwargs):
            """Accept any keyword arguments (including tqdm_class added in v0.4)."""
            Path(local_dir).mkdir(parents=True, exist_ok=True)
            (Path(local_dir) / "model.bin").write_bytes(b"fake-model-data")
            (Path(local_dir) / "config.json").write_text("{}")
            return local_dir

        # Mock huggingface_hub import inside download_model
        mock_hf_module = MagicMock()
        mock_hf_module.snapshot_download = fake_download
        with patch.dict("sys.modules", {"huggingface_hub": mock_hf_module}):
            result = model_manager.download_model("base")

        assert result is True
        assert target_dir.exists()

    def test_unknown_model_size_returns_false(self):
        """Unknown model size returns False immediately."""
        result = model_manager.download_model("nonexistent")
        assert result is False

    def test_cancelled_before_start(self, tmp_path, monkeypatch):
        """Cancelled download returns False."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        cancel = threading.Event()
        cancel.set()  # Pre-cancelled

        mock_hf = MagicMock()
        with patch.dict("sys.modules", {"huggingface_hub": mock_hf}):
            result = model_manager.download_model("base", cancel_event=cancel)

        assert result is False

    def test_download_failure_cleans_up(self, tmp_path, monkeypatch):
        """Failed download cleans up partial directory."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        mock_hf = MagicMock()
        mock_hf.snapshot_download.side_effect = ConnectionError("Network error")

        with patch.dict("sys.modules", {"huggingface_hub": mock_hf}):
            result = model_manager.download_model("tiny")

        assert result is False
        # Partial dir should be cleaned up
        partial = tmp_path / "VoicePaste" / "models" / "tiny"
        assert not partial.exists()

    def test_missing_huggingface_hub(self, tmp_path, monkeypatch):
        """Returns False when huggingface_hub is not installed."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        # Remove huggingface_hub from sys.modules if present
        import sys

        with patch.dict("sys.modules", {"huggingface_hub": None}):
            result = model_manager.download_model("base")

        assert result is False


class TestDeleteModel:
    """Test model deletion."""

    def test_delete_existing_model(self, tmp_path, monkeypatch):
        """Deleting an existing model returns True."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        model_dir = tmp_path / "VoicePaste" / "models" / "tiny"
        model_dir.mkdir(parents=True)
        (model_dir / "model.bin").write_bytes(b"data")

        result = model_manager.delete_model("tiny")
        assert result is True
        assert not model_dir.exists()

    def test_delete_nonexistent_model(self, tmp_path, monkeypatch):
        """Deleting a model that doesn't exist returns True."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        # Ensure cache dir exists
        (tmp_path / "VoicePaste" / "models").mkdir(parents=True)

        result = model_manager.delete_model("tiny")
        assert result is True


class TestGetCacheSizeMb:
    """Test cache size calculation."""

    def test_empty_cache(self, tmp_path, monkeypatch):
        """Empty cache returns 0."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        size = model_manager.get_cache_size_mb()
        assert size == 0.0

    def test_cache_with_files(self, tmp_path, monkeypatch):
        """Cache with files returns correct size."""
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        model_dir = tmp_path / "VoicePaste" / "models" / "tiny"
        model_dir.mkdir(parents=True)
        # Write 1 MB of data
        (model_dir / "model.bin").write_bytes(b"x" * (1024 * 1024))

        size = model_manager.get_cache_size_mb()
        assert abs(size - 1.0) < 0.01
