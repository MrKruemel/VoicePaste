"""SHA256 integrity verification for downloaded model files.

Provides a shared helper used by both STT (model_manager) and TTS
(tts_model_manager) to verify that downloaded files have not been
corrupted or tampered with during transfer.

Security findings addressed:
    SEC-027 (STT model integrity)
    SEC-040 (TTS model integrity)

Thread safety:
    All functions are stateless and safe to call from any thread.
"""

import hashlib
import hmac
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Chunk size for reading files during SHA256 computation.
# 64 KB balances memory usage with read syscall overhead.
_HASH_CHUNK_SIZE = 64 * 1024


def compute_file_sha256(file_path: Path) -> str:
    """Compute the SHA256 hex digest of a file using chunked reading.

    Reads the file in 64 KB chunks to avoid loading large model files
    (potentially several GB) entirely into memory.

    Args:
        file_path: Absolute path to the file to hash.

    Returns:
        Lowercase hexadecimal SHA256 digest string.

    Raises:
        FileNotFoundError: If the file does not exist.
        OSError: If the file cannot be read.
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_file_sha256(file_path: Path, expected_hash: str) -> bool:
    """Verify a file's SHA256 hash against an expected value.

    Computes the SHA256 digest of the file and compares it (case-
    insensitive) against the expected hash. Logs the computed hash at
    INFO level so users can report it for hash database population.

    Args:
        file_path: Absolute path to the file to verify.
        expected_hash: Expected lowercase hex SHA256 digest.

    Returns:
        True if the computed hash matches the expected hash.
        False if there is a mismatch or if the file cannot be read.
    """
    try:
        computed = compute_file_sha256(file_path)
    except (FileNotFoundError, OSError) as exc:
        logger.error(
            "Cannot compute SHA256 for '%s': %s: %s",
            file_path,
            type(exc).__name__,
            exc,
        )
        return False

    file_name = file_path.name
    logger.info(
        "SHA256(%s) = %s",
        file_name,
        computed,
    )

    if not hmac.compare_digest(computed.lower(), expected_hash.lower()):
        logger.error(
            "SHA256 MISMATCH for '%s': expected=%s, computed=%s. "
            "The file may be corrupted or tampered with.",
            file_name,
            expected_hash.lower(),
            computed,
        )
        return False

    logger.info(
        "SHA256 verified OK for '%s'.",
        file_name,
    )
    return True


def verify_directory_files(
    directory: Path,
    expected_hashes: dict[str, str],
) -> bool:
    """Verify SHA256 hashes for multiple files in a directory.

    For each entry in ``expected_hashes``, verifies the corresponding
    file in ``directory``. If ``expected_hashes`` is empty, logs a
    warning that integrity verification was skipped and returns True
    (graceful degradation).

    Args:
        directory: Path to the directory containing the files.
        expected_hashes: Mapping of filename to expected SHA256 hex
            digest. An empty dict means "skip verification".

    Returns:
        True if all files with expected hashes match (or if the dict
        is empty). False if any file fails verification.
    """
    if not expected_hashes:
        logger.warning(
            "No SHA256 hashes configured for '%s'. "
            "Integrity verification skipped. Populate hashes in "
            "constants.py to enable tamper detection.",
            directory.name,
        )
        # Log computed hashes for all files so they can be collected
        _log_directory_hashes(directory)
        return True

    all_ok = True
    for file_name, expected_hash in expected_hashes.items():
        file_path = directory / file_name
        if not file_path.exists():
            logger.error(
                "Expected file '%s' not found in '%s'.",
                file_name,
                directory,
            )
            all_ok = False
            continue

        if not verify_file_sha256(file_path, expected_hash):
            all_ok = False

    return all_ok


def _log_directory_hashes(directory: Path) -> None:
    """Log SHA256 hashes of all files in a directory for collection.

    Used when no expected hashes are configured so that users (or
    developers) can copy the hashes from the log and populate the
    hash constants.

    Args:
        directory: Path to the directory to scan.
    """
    if not directory.exists():
        return

    for file_path in sorted(directory.iterdir()):
        if not file_path.is_file():
            continue
        try:
            digest = compute_file_sha256(file_path)
            logger.info(
                "Computed SHA256 for collection: %s = %s",
                file_path.name,
                digest,
            )
        except (OSError, FileNotFoundError) as exc:
            logger.warning(
                "Could not hash '%s': %s",
                file_path.name,
                exc,
            )
