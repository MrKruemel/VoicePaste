"""Tests for log rotation configuration.

Validates:
- REQ-S26: RotatingFileHandler with 5 MB max and 3 backup files.
- setup_logging configures both file and console handlers.
- Fallback to console-only if file handler creation fails.
- Existing handlers are cleared before reconfiguring.
"""

import logging
import logging.handlers
import os
import pytest
from unittest.mock import patch, MagicMock

from config import AppConfig
from main import setup_logging


class TestSetupLogging:
    """Test the setup_logging function."""

    def test_rotating_file_handler_is_configured(self, tmp_path):
        """REQ-S26: A RotatingFileHandler should be attached to the root logger."""
        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="INFO",
            app_directory=tmp_path,
        )

        setup_logging(config)
        root = logging.getLogger()

        try:
            file_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(file_handlers) == 1, (
                f"Expected 1 RotatingFileHandler, found {len(file_handlers)}. "
                f"Handler types: {[type(h).__name__ for h in root.handlers]}"
            )
        finally:
            # Clean up handlers to avoid leaking file handles across tests
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)

    def test_max_bytes_is_5mb(self, tmp_path):
        """REQ-S26: RotatingFileHandler maxBytes should be 5 MB."""
        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="INFO",
            app_directory=tmp_path,
        )

        setup_logging(config)
        root = logging.getLogger()

        try:
            file_handler = next(
                h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            )
            assert file_handler.maxBytes == 5 * 1024 * 1024
        finally:
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)

    def test_backup_count_is_3(self, tmp_path):
        """REQ-S26: RotatingFileHandler should keep 3 backup files."""
        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="INFO",
            app_directory=tmp_path,
        )

        setup_logging(config)
        root = logging.getLogger()

        try:
            file_handler = next(
                h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            )
            assert file_handler.backupCount == 3
        finally:
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)

    def test_file_handler_uses_utf8_encoding(self, tmp_path):
        """Log file should use UTF-8 encoding for German text support."""
        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="INFO",
            app_directory=tmp_path,
        )

        setup_logging(config)
        root = logging.getLogger()

        try:
            file_handler = next(
                h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            )
            assert file_handler.encoding == "utf-8"
        finally:
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)

    def test_console_handler_is_configured(self, tmp_path):
        """A StreamHandler should be attached for console output."""
        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="INFO",
            app_directory=tmp_path,
        )

        setup_logging(config)
        root = logging.getLogger()

        try:
            stream_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(stream_handlers) == 1
        finally:
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)

    def test_existing_handlers_are_cleared(self, tmp_path):
        """setup_logging should remove existing handlers before adding new ones."""
        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="INFO",
            app_directory=tmp_path,
        )

        root = logging.getLogger()
        # Add a dummy handler
        dummy = logging.StreamHandler()
        root.addHandler(dummy)
        initial_count = len(root.handlers)

        setup_logging(config)

        try:
            # Should have exactly 2 handlers (file + console), not initial_count + 2
            assert len(root.handlers) == 2, (
                f"Expected 2 handlers after setup, got {len(root.handlers)}. "
                f"Dummy handler was not cleared."
            )
            assert dummy not in root.handlers, "Old dummy handler should have been removed."
        finally:
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)

    def test_log_level_is_applied(self, tmp_path):
        """Log level from config should be applied to all handlers."""
        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="DEBUG",
            app_directory=tmp_path,
        )

        setup_logging(config)
        root = logging.getLogger()

        try:
            assert root.level == logging.DEBUG
            for handler in root.handlers:
                assert handler.level == logging.DEBUG
        finally:
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)

    def test_fallback_to_console_if_file_fails(self, tmp_path):
        """If log file creation fails, should still have console logging."""
        # Use a non-existent nested path that cannot be created
        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="INFO",
            app_directory=tmp_path / "nonexistent" / "deeply" / "nested",
        )

        setup_logging(config)
        root = logging.getLogger()

        try:
            # Should have at least the console handler
            stream_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(stream_handlers) == 1, "Console handler should still be present."

            # Should NOT have a file handler (because the directory does not exist)
            file_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(file_handlers) == 0, "File handler should not be present when path is invalid."
        finally:
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)

    def test_log_file_is_created_at_config_path(self, tmp_path):
        """Log file should be created in the app directory."""
        config = AppConfig(
            openai_api_key="sk-test1234567890",
            log_level="INFO",
            app_directory=tmp_path,
        )

        setup_logging(config)
        root = logging.getLogger()

        try:
            # Write a log message to force file creation
            test_logger = logging.getLogger("test_log_rotation")
            test_logger.info("Test log message for file creation")

            # Force flush
            for handler in root.handlers:
                handler.flush()

            expected_path = tmp_path / "voice-paste.log"
            assert expected_path.exists(), f"Log file should exist at {expected_path}"
        finally:
            for h in root.handlers[:]:
                h.close()
                root.removeHandler(h)
