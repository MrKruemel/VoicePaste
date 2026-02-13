"""Whisper model download, caching, and lifecycle management.

Downloads CTranslate2-format Whisper models from Hugging Face Hub and
stores them in %LOCALAPPDATA%\\VoicePaste\\models\\.

This module is independent of faster-whisper and can be used to pre-download
models before the user attempts a transcription.

Thread safety:
    All public functions are safe to call from any thread. Downloads use
    a threading.Lock to prevent concurrent downloads of the same model.
"""

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Model size -> Hugging Face repo mapping
# These are the CTranslate2-converted models maintained by Systran
_MODEL_REPOS: dict[str, str] = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}

# Approximate download sizes in MB (for progress display)
_MODEL_SIZES_MB: dict[str, int] = {
    "tiny": 75,
    "base": 145,
    "small": 480,
    "medium": 1500,
    "large-v2": 3000,
    "large-v3": 3000,
}

# Approximate RAM usage in MB (CPU int8 quantized)
_MODEL_RAM_MB: dict[str, int] = {
    "tiny": 150,
    "base": 200,
    "small": 350,
    "medium": 600,
    "large-v2": 1200,
    "large-v3": 1200,
}

# Lock to prevent concurrent downloads of the same model
_download_lock = threading.Lock()

# Type alias for progress callback
# Arguments: (bytes_downloaded: int, total_bytes: int)
ProgressCallback = Callable[[int, int], None]


def get_cache_dir() -> Path:
    """Get the model cache directory.

    Uses %LOCALAPPDATA%\\VoicePaste\\models\\ on Windows.
    Creates the directory if it does not exist.

    Returns:
        Path to the model cache directory.
    """
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        # Fallback: use the user's home directory
        local_app_data = str(Path.home() / "AppData" / "Local")

    cache_dir = Path(local_app_data) / "VoicePaste" / "models"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_model_path(model_size: str) -> Optional[Path]:
    """Get the path to a downloaded model.

    Args:
        model_size: Model size identifier (e.g., "base").

    Returns:
        Path to the model directory if it exists and is valid, None otherwise.
    """
    if model_size not in _MODEL_REPOS:
        logger.warning("Unknown model size: '%s'.", model_size)
        return None

    model_dir = get_cache_dir() / model_size
    if model_dir.exists() and _is_model_valid(model_dir):
        return model_dir
    return None


def is_model_available(model_size: str) -> bool:
    """Check if a model is downloaded and ready to use.

    Args:
        model_size: Model size identifier.

    Returns:
        True if the model is downloaded and valid.
    """
    return get_model_path(model_size) is not None


def _is_model_valid(model_dir: Path) -> bool:
    """Verify that a model directory contains the required files.

    CTranslate2 models require at minimum:
    - model.bin (the weights)
    - config.json (model configuration)

    Args:
        model_dir: Path to the model directory.

    Returns:
        True if required files exist.
    """
    required_files = ["model.bin", "config.json"]
    for fname in required_files:
        if not (model_dir / fname).exists():
            logger.debug(
                "Model directory '%s' missing required file '%s'.",
                model_dir,
                fname,
            )
            return False
    return True


def get_available_model_sizes() -> list[str]:
    """Return list of model sizes that have been downloaded.

    Returns:
        List of model size strings that are available locally.
    """
    return [size for size in _MODEL_REPOS if is_model_available(size)]


def get_all_model_sizes() -> list[str]:
    """Return all supported model sizes.

    Returns:
        List of all model size strings.
    """
    return list(_MODEL_REPOS.keys())


def get_model_info(model_size: str) -> dict:
    """Get information about a model size.

    Args:
        model_size: Model size identifier.

    Returns:
        Dict with keys: repo, download_mb, ram_mb, available.
    """
    return {
        "repo": _MODEL_REPOS.get(model_size, "unknown"),
        "download_mb": _MODEL_SIZES_MB.get(model_size, 0),
        "ram_mb": _MODEL_RAM_MB.get(model_size, 0),
        "available": is_model_available(model_size),
    }


class _CancelledError(Exception):
    """Raised internally when a model download is cancelled via cancel_event."""

    pass


def _make_progress_tqdm_class(
    on_progress: Optional[ProgressCallback],
    cancel_event: Optional[threading.Event],
) -> type:
    """Create a tqdm-compatible class that routes progress to our callback.

    huggingface_hub's ``snapshot_download`` (and the underlying
    ``hf_hub_download``) accept a ``tqdm_class`` parameter. By providing
    a custom class we can intercept the progress updates that HF Hub
    normally renders to stderr and forward them to our UI callback.

    The class also checks ``cancel_event`` on every ``update()`` call and
    raises ``_CancelledError`` to abort the download promptly.

    Args:
        on_progress: Callback receiving (bytes_downloaded, total_bytes).
        cancel_event: Event that signals cancellation.

    Returns:
        A class compatible with the tqdm interface expected by HF Hub.
    """

    _class_lock = threading.Lock()

    class _ProgressTracker:
        """Full tqdm-compatible progress tracker for HF Hub downloads.

        Implements all methods that huggingface_hub (and its vendored tqdm
        usage in thread_map / hf_hub_download) may call. Missing methods
        cause AttributeError crashes during model downloads.
        """

        def __init__(self, *args: object, **kwargs: object) -> None:
            # Check cancellation when each new tqdm instance is created
            # (HF Hub creates one per file download)
            if cancel_event and cancel_event.is_set():
                raise _CancelledError("Download cancelled by user.")
            self.total: int = int(kwargs.get("total", 0) or 0)
            self.n: int = int(kwargs.get("initial", 0) or 0)
            self.desc: str = str(kwargs.get("desc", "") or "")
            self.disable: bool = bool(kwargs.get("disable", False))
            self.unit: str = str(kwargs.get("unit", "it") or "it")
            self.unit_scale: bool = bool(kwargs.get("unit_scale", False))
            self.pos: int = 0
            self.last_print_n: int = self.n

        @classmethod
        def get_lock(cls) -> threading.Lock:
            return _class_lock

        @classmethod
        def set_lock(cls, lock: object) -> None:
            pass

        def _check_cancel(self) -> None:
            """Raise _CancelledError if cancel_event is set."""
            if cancel_event and cancel_event.is_set():
                raise _CancelledError("Download cancelled by user.")

        def update(self, n: int = 1) -> None:
            self._check_cancel()
            self.n += n
            if on_progress and self.total > 0:
                on_progress(self.n, self.total)

        def close(self) -> None:
            pass

        def clear(self, nolock: bool = False) -> None:
            pass

        def display(self, msg: str = "", pos: int = 0) -> None:
            self._check_cancel()

        def moveto(self, n: int = 0) -> None:
            self.pos = n

        def set_description(self, desc: str = "", refresh: bool = True) -> None:
            self._check_cancel()
            self.desc = desc

        def set_description_str(self, desc: str = "", refresh: bool = True) -> None:
            self._check_cancel()
            self.desc = desc

        def set_postfix(self, ordered_dict: object = None, refresh: bool = True, **kwargs: object) -> None:
            pass

        def set_postfix_str(self, s: str = "", refresh: bool = True) -> None:
            pass

        def unpause(self) -> None:
            pass

        @classmethod
        def write(cls, s: str, file: object = None, end: str = "\n", nolock: bool = False) -> None:
            pass

        def __enter__(self) -> "_ProgressTracker":
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

        def refresh(self, nolock: bool = False, lock_args: object = None) -> None:
            self._check_cancel()

        def reset(self, total: Optional[int] = None) -> None:
            if total is not None:
                self.total = total
            self.n = 0

        @property
        def format_dict(self) -> dict:
            return {"n": self.n, "total": self.total, "elapsed": 0, "rate": None}

    return _ProgressTracker


def _clean_hf_lock_files(target_dir: Path) -> None:
    """Remove stale HF Hub lock and incomplete files from a previous download.

    huggingface_hub uses .lock and .incomplete files in a hidden
    .cache/huggingface/download/ directory. If a previous download was
    interrupted, these files block subsequent download attempts and cause
    the "Connecting..." phase to hang indefinitely.

    Args:
        target_dir: The model target directory.
    """
    cache_dir = target_dir / ".cache" / "huggingface" / "download"
    if not cache_dir.exists():
        return

    cleaned = 0
    for pattern in ("*.lock", "*.incomplete"):
        for f in cache_dir.glob(pattern):
            try:
                f.unlink()
                cleaned += 1
            except OSError:
                pass
    if cleaned:
        logger.info(
            "Cleaned %d stale lock/incomplete files from '%s'.",
            cleaned, cache_dir,
        )


# Files required for CTranslate2 Whisper models.
# Only these are downloaded (skips README.md, .gitattributes, etc.)
_MODEL_ALLOW_PATTERNS: list[str] = [
    "model.bin",
    "config.json",
    "tokenizer.json",
    "vocabulary.*",
    "preprocessor_config.json",
]


def download_model(
    model_size: str,
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> bool:
    """Download a Whisper model from Hugging Face Hub.

    Downloads the CTranslate2-format model to the local cache directory.
    Thread-safe: uses a lock to prevent concurrent downloads.

    Progress reporting: If ``on_progress`` is provided, it is called with
    ``(bytes_downloaded, total_bytes)`` as each file chunk is received.
    This is wired through a custom tqdm class injected into
    ``snapshot_download``.

    Args:
        model_size: Model size to download (e.g., "base").
        on_progress: Optional callback for download progress updates.
            Called with (bytes_downloaded, total_bytes).
        cancel_event: Optional threading.Event that, when set, cancels
            the download.

    Returns:
        True if download succeeded, False on error or cancellation.
    """
    if model_size not in _MODEL_REPOS:
        logger.error("Unknown model size: '%s'.", model_size)
        return False

    repo_id = _MODEL_REPOS[model_size]
    target_dir = get_cache_dir() / model_size

    if not _download_lock.acquire(timeout=1):
        logger.warning("Another model download is already in progress.")
        return False

    try:
        logger.info(
            "Downloading Whisper model '%s' from '%s' to '%s'...",
            model_size,
            repo_id,
            target_dir,
        )

        # Use huggingface_hub for the actual download
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            logger.error(
                "huggingface_hub is not installed. Cannot download models. "
                "Install with: pip install huggingface_hub"
            )
            return False

        # Set HTTP timeout for actual file downloads (not just metadata).
        # Without this, GET requests use no timeout and can hang forever.
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")

        # Clean stale lock/incomplete files from previous aborted downloads.
        # These cause the "Connecting..." phase to hang.
        _clean_hf_lock_files(target_dir)

        # Check for cancellation before starting
        if cancel_event and cancel_event.is_set():
            logger.info("Download cancelled before starting.")
            return False

        # Pre-flight connectivity check (fast fail instead of long hang)
        logger.info("Testing connectivity to Hugging Face...")
        try:
            import requests as _req
            _req.head("https://huggingface.co", timeout=10)
            logger.info("Hugging Face reachable.")
        except Exception as conn_err:
            logger.error("Cannot reach huggingface.co: %s", conn_err)
            return False

        # Build a custom tqdm class that routes progress to our callback
        # and checks for cancellation on every chunk.
        progress_cls = _make_progress_tqdm_class(on_progress, cancel_event)

        # Download only model-essential files (skip README, .gitattributes)
        logger.info("Starting download (allow_patterns=%s)...", _MODEL_ALLOW_PATTERNS)
        try:
            downloaded_path = snapshot_download(
                repo_id=repo_id,
                local_dir=str(target_dir),
                tqdm_class=progress_cls,
                etag_timeout=10,
                allow_patterns=_MODEL_ALLOW_PATTERNS,
            )

            logger.info(
                "Model '%s' downloaded successfully to '%s'.",
                model_size,
                downloaded_path,
            )

        except _CancelledError:
            logger.info("Model download cancelled by user.")
            # Clean up partial download
            if target_dir.exists():
                try:
                    shutil.rmtree(target_dir)
                except OSError as cleanup_err:
                    logger.warning(
                        "Failed to clean up cancelled download at '%s': %s",
                        target_dir,
                        cleanup_err,
                    )
            return False

        except Exception as e:
            logger.error(
                "Failed to download model '%s': %s: %s",
                model_size,
                type(e).__name__,
                e,
            )
            # Clean up partial download
            if target_dir.exists():
                try:
                    shutil.rmtree(target_dir)
                except OSError as cleanup_err:
                    logger.warning(
                        "Failed to clean up partial download at '%s': %s",
                        target_dir,
                        cleanup_err,
                    )
            return False

        # Check cancellation after download (in case it completed just
        # as the user clicked cancel)
        if cancel_event and cancel_event.is_set():
            logger.info("Download cancelled after completion. Cleaning up.")
            if target_dir.exists():
                try:
                    shutil.rmtree(target_dir)
                except OSError as cleanup_err:
                    logger.warning(
                        "Failed to clean up post-cancel download at '%s': %s",
                        target_dir,
                        cleanup_err,
                    )
            return False

        # Verify the download
        if not _is_model_valid(target_dir):
            logger.error(
                "Downloaded model '%s' is incomplete or corrupted.",
                model_size,
            )
            return False

        logger.info("Model '%s' verified and ready to use.", model_size)
        return True

    finally:
        _download_lock.release()


def delete_model(model_size: str) -> bool:
    """Delete a downloaded model from the cache.

    Args:
        model_size: Model size to delete.

    Returns:
        True if deleted (or did not exist), False on error.
    """
    model_dir = get_cache_dir() / model_size
    if not model_dir.exists():
        logger.info("Model '%s' not found in cache (already deleted).", model_size)
        return True

    try:
        shutil.rmtree(model_dir)
        logger.info("Model '%s' deleted from cache.", model_size)
        return True
    except OSError as e:
        logger.error("Failed to delete model '%s': %s", model_size, e)
        return False


def get_cache_size_mb() -> float:
    """Get the total size of all cached models in MB.

    Returns:
        Total cache size in megabytes.
    """
    cache_dir = get_cache_dir()
    total = 0
    for path in cache_dir.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total / (1024 * 1024)
