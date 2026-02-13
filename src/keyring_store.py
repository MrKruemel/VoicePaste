"""Secure credential storage via Windows Credential Manager (keyring).

Uses the ``keyring`` library to store and retrieve API keys securely.
Falls back gracefully if keyring is unavailable.

Service name: "VoicePaste"
Key names: "openai_api_key", "openrouter_api_key"

v0.3: Initial implementation.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "VoicePaste"

# Sentinel to track whether keyring is functional (None = not yet tested)
_keyring_available: Optional[bool] = None


def is_available() -> bool:
    """Check if keyring backend is functional.

    Tests by attempting a no-op read. Caches the result after first call.

    Returns:
        True if keyring is usable, False otherwise.
    """
    global _keyring_available

    if _keyring_available is not None:
        return _keyring_available

    try:
        import keyring as _kr

        # Probe with a non-existent key to verify the backend works
        _kr.get_password(KEYRING_SERVICE, "__probe__")
        _keyring_available = True
        logger.info("Keyring backend available: %s", type(_kr.get_keyring()).__name__)
    except Exception as e:
        _keyring_available = False
        logger.warning("Keyring backend not available: %s", e)

    return _keyring_available


def get_credential(key: str) -> Optional[str]:
    """Retrieve a credential from the keyring.

    Args:
        key: Credential identifier (e.g., "openai_api_key").

    Returns:
        The stored credential string, or None if not found or keyring
        is unavailable.
    """
    if not is_available():
        return None

    try:
        import keyring as _kr

        value = _kr.get_password(KEYRING_SERVICE, key)
        if value:
            logger.debug("Credential '%s' retrieved from keyring.", key)
        else:
            logger.debug("Credential '%s' not found in keyring.", key)
        return value
    except Exception as e:
        logger.warning("Failed to read credential '%s' from keyring: %s", key, e)
        return None


def set_credential(key: str, value: str) -> bool:
    """Store a credential in the keyring.

    Args:
        key: Credential identifier.
        value: The secret value to store.

    Returns:
        True if stored successfully, False otherwise.
    """
    if not is_available():
        logger.warning("Cannot store credential '%s': keyring not available.", key)
        return False

    try:
        import keyring as _kr

        _kr.set_password(KEYRING_SERVICE, key, value)
        logger.info("Credential '%s' stored in keyring.", key)
        return True
    except Exception as e:
        logger.warning("Failed to store credential '%s' in keyring: %s", key, e)
        return False


def delete_credential(key: str) -> bool:
    """Delete a credential from the keyring.

    Args:
        key: Credential identifier.

    Returns:
        True if deleted (or did not exist), False on error.
    """
    if not is_available():
        return True  # Nothing to delete if keyring is unavailable

    try:
        import keyring as _kr

        _kr.delete_password(KEYRING_SERVICE, key)
        logger.info("Credential '%s' deleted from keyring.", key)
        return True
    except Exception as e:
        # keyring raises an error if the credential doesn't exist
        # on some backends. Treat that as success.
        if "not found" in str(e).lower() or "does not exist" in str(e).lower():
            logger.debug("Credential '%s' did not exist in keyring.", key)
            return True
        logger.warning("Failed to delete credential '%s' from keyring: %s", key, e)
        return False
