"""Piper TTS voice model download, caching, and lifecycle management.

Downloads Piper voice ONNX models from Hugging Face and stores them
in %LOCALAPPDATA%\\VoicePaste\\models\\tts\\.

This module is independent of the local_tts inference engine and can be
used to pre-download models before the user attempts TTS synthesis.

Downloads use direct HTTPS requests to the Hugging Face CDN instead of
the huggingface_hub SDK.  This avoids known issues with hf_hub_download:
  - Xet Storage repos cause ``AttributeError: 'NoneType' ... 'write'``
  - Stale .lock files left by interrupted downloads block all retries

Thread safety:
    All public functions are safe to call from any thread. Downloads use
    a threading.Lock to prevent concurrent downloads.

v0.7: Initial implementation (hf_hub_download).
v0.7.1: Replaced hf_hub_download with direct HTTP streaming.
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
    """Download a Piper TTS voice model from Hugging Face.

    Downloads the .onnx and .onnx.json files for the specified voice
    directly via HTTPS streaming (no hf_hub SDK needed).

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
        import requests

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

        # Pre-flight connectivity check
        logger.info("Testing connectivity to Hugging Face...")
        try:
            resp = requests.head("https://huggingface.co", timeout=10)
            logger.info("Hugging Face reachable (HTTP %d).", resp.status_code)
        except Exception as conn_err:
            logger.error(
                "Cannot reach huggingface.co: %s: %s",
                type(conn_err).__name__,
                conn_err,
            )
            return False

        # Clean up any partial/stale download from a previous attempt
        if target_dir.exists() and not is_tts_model_valid(target_dir):
            logger.info("Cleaning up stale partial download at '%s'.", target_dir)
            _cleanup_partial_download(target_dir)

        # Ensure target directory exists
        target_dir.mkdir(parents=True, exist_ok=True)

        # Calculate total download size for progress reporting
        total_bytes_all = 0
        bytes_downloaded_all = 0

        # Download each file via direct HTTPS streaming
        total_files = len(file_paths)
        for i, file_path in enumerate(file_paths):
            if cancel_event and cancel_event.is_set():
                logger.info("TTS model download cancelled.")
                _cleanup_partial_download(target_dir)
                return False

            file_name = file_path.split("/")[-1]
            dest_path = target_dir / file_name

            # Construct direct download URL
            url = (
                f"https://huggingface.co/{repo_id}"
                f"/resolve/main/{file_path}"
            )
            logger.info(
                "Downloading file %d/%d: %s", i + 1, total_files, file_name
            )

            try:
                resp = requests.get(
                    url,
                    stream=True,
                    timeout=(10, 30),
                    allow_redirects=True,
                    headers={"User-Agent": "VoicePaste/0.7"},
                )
                resp.raise_for_status()

                file_total = int(resp.headers.get("content-length", 0))
                total_bytes_all += file_total
                file_downloaded = 0

                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        if cancel_event and cancel_event.is_set():
                            logger.info("TTS model download cancelled.")
                            f.close()
                            _cleanup_partial_download(target_dir)
                            return False

                        f.write(chunk)
                        file_downloaded += len(chunk)
                        bytes_downloaded_all += len(chunk)

                        if on_progress and total_bytes_all > 0:
                            on_progress(bytes_downloaded_all, total_bytes_all)

                logger.info(
                    "Downloaded: %s (%d bytes)", dest_path, file_downloaded
                )

            except Exception as e:
                logger.error(
                    "Failed to download '%s': %s: %s",
                    file_name,
                    type(e).__name__,
                    e,
                )
                _cleanup_partial_download(target_dir)
                return False

        # Report completion
        if on_progress and total_bytes_all > 0:
            on_progress(total_bytes_all, total_bytes_all)

        # Post-download cancel check (consistent with model_manager.py)
        if cancel_event and cancel_event.is_set():
            logger.info("TTS model download cancelled after download.")
            _cleanup_partial_download(target_dir)
            return False

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
