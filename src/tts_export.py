"""TTS Audio Export for Voice Paste.

Saves TTS-generated audio files to a user-chosen directory with human-readable
filenames. Unlike the internal TTS cache (tts_cache.py), exported files are
permanent and managed by the user.

Use cases: audiobook creation, training material, presentations, archival.

Filenames follow the pattern: YYYYMMDD_HHMMSS_sanitized-text.mp3
- Timestamp ensures uniqueness
- Sanitized text prefix gives human-readable context
- Extension auto-detected from audio data (MP3 or WAV)

Thread-safe: export() can be called from any worker thread.

v1.0: Initial implementation.
"""

import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum number of characters from the text to include in the filename.
_MAX_TEXT_CHARS_IN_FILENAME = 40

# Maximum total filename length (without extension) to stay safe on Windows.
# Windows MAX_PATH is 260; we leave room for the directory path and extension.
_MAX_FILENAME_LENGTH = 120


# ---------------------------------------------------------------------------
# Format detection (shared logic with tts_cache.py)
# ---------------------------------------------------------------------------


def _detect_audio_format(data: bytes) -> str:
    """Detect audio format from file header bytes.

    Args:
        data: Raw audio bytes (at least a few bytes needed for detection).

    Returns:
        File extension string: "wav", "mp3", or "bin" (fallback for unknown).
    """
    if len(data) < 4:
        return "bin"
    if data[:4] == b"RIFF":
        return "wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return "bin"


# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------


def _sanitize_for_filename(text: str) -> str:
    """Sanitize text for use in a Windows-safe filename.

    Applies the following transformations:
    1. Strips leading/trailing whitespace.
    2. Lowercases the text.
    3. Removes all characters except alphanumeric, spaces, and hyphens.
    4. Collapses multiple spaces/hyphens into single hyphens.
    5. Strips leading/trailing hyphens.
    6. Truncates to _MAX_TEXT_CHARS_IN_FILENAME characters.

    Args:
        text: Raw text to sanitize.

    Returns:
        A filesystem-safe string suitable for use in filenames.
        Returns "untitled" if the sanitization produces an empty string.
    """
    # Lowercase and strip
    sanitized = text.strip().lower()

    # Remove everything except alphanumeric, spaces, and hyphens
    sanitized = re.sub(r"[^a-z0-9\s\-]", "", sanitized)

    # Collapse whitespace and hyphens into single hyphens
    sanitized = re.sub(r"[\s\-]+", "-", sanitized)

    # Strip leading/trailing hyphens
    sanitized = sanitized.strip("-")

    # Truncate to max length
    if len(sanitized) > _MAX_TEXT_CHARS_IN_FILENAME:
        sanitized = sanitized[:_MAX_TEXT_CHARS_IN_FILENAME].rstrip("-")

    # Fallback for empty result (e.g., text was all special characters)
    if not sanitized:
        sanitized = "untitled"

    return sanitized


def _generate_export_filename(text: str, audio_format: str) -> str:
    """Generate a human-readable, timestamped filename for an exported audio file.

    Format: YYYYMMDD_HHMMSS_sanitized-text.ext

    Args:
        text: The TTS input text (used for the readable portion).
        audio_format: File extension without dot (e.g., "mp3", "wav").

    Returns:
        Complete filename string (e.g., "20260220_143022_hello-world.mp3").
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized = _sanitize_for_filename(text)
    filename = f"{timestamp}_{sanitized}"

    # Ensure total filename (without extension) does not exceed limit
    if len(filename) > _MAX_FILENAME_LENGTH:
        filename = filename[:_MAX_FILENAME_LENGTH].rstrip("-")

    return f"{filename}.{audio_format}"


# ---------------------------------------------------------------------------
# TTSAudioExporter
# ---------------------------------------------------------------------------


class TTSAudioExporter:
    """Thread-safe TTS audio exporter that saves audio to a user-chosen directory.

    Unlike the TTS cache, exported files are permanent: there is no eviction
    policy. The user manages the export directory manually.

    Typical usage::

        exporter = TTSAudioExporter(
            export_dir=Path("C:/Users/tim/TTS_Exports"),
            enabled=True,
        )
        path = exporter.export("Hello world", audio_bytes)
        # -> C:/Users/tim/TTS_Exports/20260220_143022_hello-world.mp3

    Thread safety is guaranteed via an internal lock around all file operations.
    """

    def __init__(
        self,
        export_dir: Path,
        enabled: bool = False,
    ) -> None:
        """Initialize the exporter.

        Args:
            export_dir: Directory where exported audio files are saved.
                Created lazily on first export (not during __init__).
            enabled: Whether exporting is active. When False, export()
                returns None immediately without writing anything.
        """
        self._export_dir = Path(export_dir) if export_dir else Path("")
        self._enabled = enabled
        self._lock = threading.Lock()

        logger.info(
            "TTS exporter initialized: enabled=%s, dir='%s'",
            self._enabled,
            self._export_dir,
        )

    @property
    def enabled(self) -> bool:
        """Whether export is currently enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable exporting."""
        self._enabled = value

    @property
    def export_dir(self) -> Path:
        """Current export directory."""
        return self._export_dir

    def set_export_dir(self, path: Path) -> None:
        """Change the export directory.

        The new directory is created lazily on the next export() call,
        not immediately.

        Args:
            path: New export directory path.
        """
        with self._lock:
            self._export_dir = Path(path) if path else Path("")
            logger.info("TTS export directory changed to: '%s'", self._export_dir)

    def export(
        self,
        text: str,
        audio_data: bytes,
        filename_hint: str = "",
    ) -> Optional[Path]:
        """Save TTS audio to the export directory with a readable filename.

        This method is thread-safe and can be called from any worker thread.

        The filename is generated from the text content and a timestamp.
        If filename_hint is provided, it is used instead of deriving the
        name from text (useful for API-driven exports with custom names).

        Audio format is auto-detected from the audio data header bytes
        (RIFF = WAV, ID3/0xFF = MP3).

        Args:
            text: The TTS input text. Used for filename generation and
                metadata. Must be non-empty.
            audio_data: Raw audio bytes to write. Must be non-empty.
            filename_hint: Optional custom filename (without extension).
                If empty, the filename is auto-generated from text.

        Returns:
            Path to the exported file on success, or None if:
            - Exporting is disabled
            - Export directory is not configured (empty path)
            - Text or audio data is empty
            - A filesystem error occurred (logged, not raised)
        """
        if not self._enabled:
            return None

        if not text or not text.strip():
            logger.warning("TTS export: empty text, skipping.")
            return None

        if not audio_data:
            logger.warning("TTS export: empty audio data, skipping.")
            return None

        export_dir = self._export_dir
        if not export_dir or str(export_dir) == "" or str(export_dir) == ".":
            logger.warning(
                "TTS export: no export directory configured. "
                "Set tts_export_path in config.toml or Settings."
            )
            return None

        # Detect format from audio bytes
        audio_format = _detect_audio_format(audio_data)

        # Generate filename
        if filename_hint and filename_hint.strip():
            sanitized_hint = _sanitize_for_filename(filename_hint)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}_{sanitized_hint}.{audio_format}"
        else:
            filename = _generate_export_filename(text, audio_format)

        with self._lock:
            # Create directory if needed (lazy creation)
            try:
                export_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.error(
                    "TTS export: cannot create directory '%s': %s",
                    export_dir,
                    e,
                )
                return None

            file_path = export_dir / filename

            # Avoid overwriting existing files (edge case: two exports in the
            # same second with the same text). Append a counter.
            if file_path.exists():
                stem = file_path.stem
                ext = file_path.suffix
                counter = 1
                while file_path.exists():
                    file_path = export_dir / f"{stem}_{counter}{ext}"
                    counter += 1
                    if counter > 999:
                        logger.error(
                            "TTS export: too many filename collisions for '%s'.",
                            stem,
                        )
                        return None

            # Write atomically: tmp file + rename
            tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
            try:
                tmp_path.write_bytes(audio_data)
                tmp_path.replace(file_path)
            except OSError as e:
                logger.error(
                    "TTS export: failed to write '%s': %s",
                    file_path,
                    e,
                )
                # Clean up temp file if it exists
                try:
                    if tmp_path.exists():
                        tmp_path.unlink()
                except OSError:
                    pass
                return None

        logger.info(
            "TTS export: saved '%s' (%d bytes, %s)",
            file_path.name,
            len(audio_data),
            audio_format,
        )
        return file_path

    def list_exports(self) -> list[dict]:
        """List all exported audio files with metadata.

        Scans the export directory for audio files and returns metadata
        sorted by modification time (newest first).

        Returns:
            List of dicts, each containing:
            - "filename": str -- the file name
            - "path": str -- full absolute path
            - "size_bytes": int -- file size in bytes
            - "format": str -- "mp3", "wav", or "bin"
            - "modified_at": str -- ISO 8601 timestamp of last modification
            - "created_at": str -- ISO 8601 timestamp of creation

            Returns an empty list if exporting is disabled, the directory
            does not exist, or a read error occurs.
        """
        if not self._enabled:
            return []

        export_dir = self._export_dir
        if not export_dir or str(export_dir) == "" or str(export_dir) == ".":
            return []

        if not export_dir.exists():
            return []

        results: list[dict] = []
        audio_extensions = {".mp3", ".wav", ".bin"}

        try:
            for entry in export_dir.iterdir():
                if entry.is_file() and entry.suffix.lower() in audio_extensions:
                    try:
                        stat = entry.stat()
                        results.append({
                            "filename": entry.name,
                            "path": str(entry.resolve()),
                            "size_bytes": stat.st_size,
                            "format": entry.suffix.lstrip(".").lower(),
                            "modified_at": datetime.fromtimestamp(
                                stat.st_mtime
                            ).isoformat(),
                            "created_at": datetime.fromtimestamp(
                                stat.st_ctime
                            ).isoformat(),
                        })
                    except OSError as e:
                        logger.debug(
                            "TTS export: could not stat '%s': %s",
                            entry.name,
                            e,
                        )
        except OSError as e:
            logger.warning(
                "TTS export: error listing directory '%s': %s",
                export_dir,
                e,
            )
            return []

        # Sort by modification time, newest first
        results.sort(key=lambda r: r.get("modified_at", ""), reverse=True)

        return results

    def stats(self) -> dict:
        """Return export directory statistics.

        Returns:
            Dict with:
            - "enabled": bool
            - "export_dir": str
            - "total_files": int
            - "total_size_mb": float
            - "total_size_bytes": int
        """
        exports = self.list_exports()
        total_bytes = sum(e.get("size_bytes", 0) for e in exports)

        return {
            "enabled": self._enabled,
            "export_dir": str(self._export_dir),
            "total_files": len(exports),
            "total_size_mb": round(total_bytes / (1024 * 1024), 1),
            "total_size_bytes": total_bytes,
        }
