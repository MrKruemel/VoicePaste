"""Piper TTS voice model download, caching, and lifecycle management.

Downloads Piper voice ONNX models from Hugging Face Hub and stores them
in %LOCALAPPDATA%\\VoicePaste\\models\\tts\\.

This module is independent of the local_tts inference engine and can be
used to pre-download models before the user attempts TTS synthesis.

Thread safety:
    All public functions are safe to call from any thread. Downloads use
    a threading.Lock to prevent concurrent downloads.

v0.7: Initial implementation.
"""

import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Type alias for progress callback: (bytes_downloaded, total_bytes)
ProgressCallback = Callable[[int, int], None]

# Lock to prevent concurrent downloads
_download_lock = threading.Lock()


def get_tts_cache_dir() -> Path:
    """Get the TTS model cache directory.

    Uses %LOCALAPPDATA%\\VoicePaste\\models\\tts\\ on Windows.
    Creates the directory if it does not exist.

    Returns:
        Path to the TTS model cache directory.
    """
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        local_app_data = str(Path.home() / "AppData" / "Local")

    cache_dir = Path(local_app_data) / "VoicePaste" / "models" / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_tts_model_path(voice_name: str) -> Optional[Path]:
    """Get the path to a downloaded TTS voice model.

    Args:
        voice_name: Voice name identifier (e.g., "de_DE-thorsten-medium").

    Returns:
        Path to the model directory if it exists and is valid, None otherwise.
    """
    model_dir = get_tts_cache_dir() / voice_name
    if model_dir.exists() and is_tts_model_valid(model_dir):
        return model_dir
    return None


def is_tts_model_available(voice_name: str) -> bool:
    """Check if a TTS voice model is downloaded and ready to use.

    Args:
        voice_name: Voice name identifier.

    Returns:
        True if the model is downloaded and valid.
    """
    return get_tts_model_path(voice_name) is not None


def is_tts_model_valid(model_dir: Path) -> bool:
    """Verify that a model directory contains the required files.

    Piper voice models require:
    - One .onnx file (the VITS model)
    - One .onnx.json file (model configuration with phoneme_id_map)

    Args:
        model_dir: Path to the model directory.

    Returns:
        True if required files exist.
    """
    onnx_files = list(model_dir.glob("*.onnx"))
    json_files = list(model_dir.glob("*.onnx.json"))

    if not onnx_files:
        logger.debug(
            "TTS model directory '%s' has no .onnx file.", model_dir
        )
        return False

    if not json_files:
        logger.debug(
            "TTS model directory '%s' has no .onnx.json file.", model_dir
        )
        return False

    # Basic size check: ONNX model should be at least 1 MB
    for onnx_file in onnx_files:
        if onnx_file.stat().st_size < 1024 * 1024:
            logger.debug(
                "TTS model file '%s' is suspiciously small (%d bytes).",
                onnx_file,
                onnx_file.stat().st_size,
            )
            return False

    return True


def get_tts_model_size_mb(voice_name: str) -> float:
    """Get the total size of a downloaded TTS model in MB.

    Args:
        voice_name: Voice name identifier.

    Returns:
        Size in megabytes, or 0.0 if the model is not downloaded.
    """
    model_dir = get_tts_cache_dir() / voice_name
    if not model_dir.exists():
        return 0.0

    total = 0
    for path in model_dir.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total / (1024 * 1024)


def download_tts_model(
    voice_name: str,
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> bool:
    """Download a Piper TTS voice model from Hugging Face Hub.

    Downloads the .onnx and .onnx.json files for the specified voice
    to the local cache directory.

    Args:
        voice_name: Voice name (e.g., "de_DE-thorsten-medium").
        on_progress: Optional callback for progress updates.
            Called with (bytes_downloaded, total_bytes).
        cancel_event: Optional threading.Event to cancel the download.

    Returns:
        True if download succeeded, False on error or cancellation.
    """
    try:
        from constants import PIPER_VOICE_MODELS
    except ImportError:
        logger.error("Cannot import PIPER_VOICE_MODELS from constants.")
        return False

    if voice_name not in PIPER_VOICE_MODELS:
        logger.error("Unknown TTS voice: '%s'.", voice_name)
        return False

    voice_info = PIPER_VOICE_MODELS[voice_name]
    repo_id = voice_info.get("repo", "rhasspy/piper-voices")
    file_paths = voice_info.get("files", [])

    if not file_paths:
        logger.error(
            "No file paths configured for voice '%s'.", voice_name
        )
        return False

    target_dir = get_tts_cache_dir() / voice_name

    if not _download_lock.acquire(timeout=1):
        logger.warning("Another TTS model download is already in progress.")
        return False

    try:
        logger.info(
            "Downloading Piper voice '%s' from '%s' to '%s'...",
            voice_name,
            repo_id,
            target_dir,
        )

        # Check for cancellation before starting
        if cancel_event and cancel_event.is_set():
            logger.info("TTS model download cancelled before starting.")
            return False

        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            logger.error(
                "huggingface_hub is not installed. Cannot download models. "
                "Install with: pip install huggingface_hub"
            )
            return False

        # Pre-flight connectivity check
        logger.info("Testing connectivity to Hugging Face...")
        try:
            import requests as _req

            resp = _req.head("https://huggingface.co", timeout=10)
            logger.info("Hugging Face reachable (HTTP %d).", resp.status_code)
        except Exception as conn_err:
            logger.error(
                "Cannot reach huggingface.co: %s: %s",
                type(conn_err).__name__,
                conn_err,
            )
            return False

        # Ensure target directory exists
        target_dir.mkdir(parents=True, exist_ok=True)

        # Download each file
        total_files = len(file_paths)
        for i, file_path in enumerate(file_paths):
            if cancel_event and cancel_event.is_set():
                logger.info("TTS model download cancelled.")
                _cleanup_partial_download(target_dir)
                return False

            file_name = file_path.split("/")[-1]
            logger.info(
                "Downloading file %d/%d: %s",
                i + 1,
                total_files,
                file_name,
            )

            # Report approximate progress based on file index
            if on_progress and total_files > 0:
                on_progress(i, total_files)

            try:
                downloaded_path = hf_hub_download(
                    repo_id=repo_id,
                    filename=file_path,
                    local_dir=str(target_dir),
                    etag_timeout=10,
                )
                logger.info("Downloaded: %s", downloaded_path)

            except Exception as e:
                logger.error(
                    "Failed to download '%s': %s: %s",
                    file_path,
                    type(e).__name__,
                    e,
                )
                _cleanup_partial_download(target_dir)
                return False

        # huggingface_hub downloads to subdirectories matching the repo path.
        # We need to move the files to the root of target_dir.
        _flatten_downloaded_files(target_dir, voice_name)

        # Report completion
        if on_progress and total_files > 0:
            on_progress(total_files, total_files)

        # Verify the download
        if not is_tts_model_valid(target_dir):
            logger.error(
                "Downloaded TTS model '%s' is incomplete or corrupted.",
                voice_name,
            )
            _cleanup_partial_download(target_dir)
            return False

        model_size = get_tts_model_size_mb(voice_name)
        logger.info(
            "TTS voice '%s' downloaded and verified (%.1f MB).",
            voice_name,
            model_size,
        )
        return True

    finally:
        _download_lock.release()


def _flatten_downloaded_files(target_dir: Path, voice_name: str) -> None:
    """Move downloaded files from HF subdirectory structure to model root.

    huggingface_hub's hf_hub_download with local_dir creates the full
    repo path structure (e.g., de/de_DE/thorsten/medium/model.onnx).
    We need the .onnx and .onnx.json files at the root of the voice
    directory for the inference engine to find them.

    Args:
        target_dir: The model target directory.
        voice_name: The voice name (used to find expected filenames).
    """
    # Find all .onnx and .onnx.json files recursively
    onnx_files = list(target_dir.rglob("*.onnx"))
    json_files = list(target_dir.rglob("*.onnx.json"))

    moved = 0
    for f in onnx_files + json_files:
        if f.parent != target_dir:
            dest = target_dir / f.name
            if not dest.exists():
                shutil.move(str(f), str(dest))
                moved += 1
                logger.debug("Moved %s -> %s", f, dest)

    if moved > 0:
        logger.info(
            "Flattened %d files to model root: %s", moved, target_dir
        )

        # Clean up empty subdirectories
        for dirpath in sorted(target_dir.rglob("*"), reverse=True):
            if dirpath.is_dir() and dirpath != target_dir:
                try:
                    dirpath.rmdir()  # Only removes if empty
                except OSError:
                    pass

    # Also clean up the .cache directory left by hf_hub_download
    hf_cache = target_dir / ".cache"
    if hf_cache.exists():
        try:
            shutil.rmtree(hf_cache)
            logger.debug("Cleaned up HF cache directory: %s", hf_cache)
        except OSError as e:
            logger.debug("Could not remove HF cache: %s", e)


def _cleanup_partial_download(target_dir: Path) -> None:
    """Clean up a partial or failed download.

    Args:
        target_dir: The model target directory to remove.
    """
    if target_dir.exists():
        try:
            shutil.rmtree(target_dir)
            logger.info("Cleaned up partial download at '%s'.", target_dir)
        except OSError as e:
            logger.warning(
                "Failed to clean up partial download at '%s': %s",
                target_dir,
                e,
            )


def delete_tts_model(voice_name: str) -> bool:
    """Delete a downloaded TTS voice model from the cache.

    Args:
        voice_name: Voice name to delete.

    Returns:
        True if deleted (or did not exist), False on error.
    """
    model_dir = get_tts_cache_dir() / voice_name
    if not model_dir.exists():
        logger.info(
            "TTS voice '%s' not found in cache (already deleted).",
            voice_name,
        )
        return True

    try:
        shutil.rmtree(model_dir)
        logger.info("TTS voice '%s' deleted from cache.", voice_name)
        return True
    except OSError as e:
        logger.error("Failed to delete TTS voice '%s': %s", voice_name, e)
        return False


def get_tts_cache_size_mb() -> float:
    """Get the total size of all cached TTS models in MB.

    Returns:
        Total cache size in megabytes.
    """
    cache_dir = get_tts_cache_dir()
    total = 0
    for path in cache_dir.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total / (1024 * 1024)
