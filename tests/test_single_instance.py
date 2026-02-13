"""Tests for single-instance mutex enforcement.

Validates:
- REQ-S27: Only one instance of the application can run at a time.
- Mutex is properly acquired and released.
- Second instance is blocked when mutex is already held.
- Mutex release cleans up even on errors.
"""

import ctypes
import ctypes.wintypes
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from main import (
    _acquire_single_instance_mutex,
    _release_single_instance_mutex,
    _MUTEX_NAME,
    _ERROR_ALREADY_EXISTS,
)


class TestAcquireSingleInstanceMutex:
    """Test single-instance mutex acquisition logic."""

    @patch("main.ctypes")
    def test_successful_acquisition_returns_handle(self, mock_ctypes):
        """First instance should acquire the mutex and return a valid handle."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        fake_handle = 12345
        mock_kernel32.CreateMutexW.return_value = fake_handle
        mock_kernel32.GetLastError.return_value = 0  # No error

        result = _acquire_single_instance_mutex()

        assert result == fake_handle
        mock_kernel32.CreateMutexW.assert_called_once_with(
            None, True, _MUTEX_NAME
        )

    @patch("main.ctypes")
    def test_already_running_returns_none(self, mock_ctypes):
        """Second instance should get ERROR_ALREADY_EXISTS and return None."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        fake_handle = 12345
        mock_kernel32.CreateMutexW.return_value = fake_handle
        mock_kernel32.GetLastError.return_value = _ERROR_ALREADY_EXISTS

        result = _acquire_single_instance_mutex()

        assert result is None
        # Should close the duplicate handle
        mock_kernel32.CloseHandle.assert_called_once_with(fake_handle)

    @patch("main.ctypes")
    def test_create_mutex_fails_returns_none(self, mock_ctypes):
        """If CreateMutexW returns 0, acquisition should fail gracefully."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        mock_kernel32.CreateMutexW.return_value = 0
        mock_kernel32.GetLastError.return_value = 5  # ACCESS_DENIED

        result = _acquire_single_instance_mutex()

        assert result is None
        # Should NOT call CloseHandle since handle is invalid
        mock_kernel32.CloseHandle.assert_not_called()

    @patch("main.ctypes")
    def test_create_mutex_returns_none_handle(self, mock_ctypes):
        """If CreateMutexW returns None, acquisition should fail gracefully."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        mock_kernel32.CreateMutexW.return_value = None

        result = _acquire_single_instance_mutex()

        assert result is None

    @patch("main.ctypes")
    def test_mutex_name_uses_global_namespace(self, mock_ctypes):
        """Mutex name should use Global\\ prefix for cross-session visibility."""
        assert _MUTEX_NAME.startswith("Global\\")

    @patch("main.ctypes")
    def test_error_already_exists_constant_is_183(self, mock_ctypes):
        """ERROR_ALREADY_EXISTS should be 183 per Windows API documentation."""
        assert _ERROR_ALREADY_EXISTS == 183


class TestReleaseSingleInstanceMutex:
    """Test single-instance mutex release logic."""

    @patch("main.ctypes")
    def test_release_calls_release_and_close(self, mock_ctypes):
        """Release should call both ReleaseMutex and CloseHandle."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        fake_handle = 99999
        _release_single_instance_mutex(fake_handle)

        mock_kernel32.ReleaseMutex.assert_called_once_with(fake_handle)
        mock_kernel32.CloseHandle.assert_called_once_with(fake_handle)

    @patch("main.ctypes")
    def test_release_handles_exception_gracefully(self, mock_ctypes):
        """Release should not crash if kernel32 calls raise."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32
        mock_kernel32.ReleaseMutex.side_effect = OSError("mock failure")

        # Should not raise
        _release_single_instance_mutex(42)

    @patch("main.ctypes")
    def test_release_order_is_release_then_close(self, mock_ctypes):
        """ReleaseMutex must be called before CloseHandle."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32
        call_order = []

        mock_kernel32.ReleaseMutex.side_effect = lambda h: call_order.append("release")
        mock_kernel32.CloseHandle.side_effect = lambda h: call_order.append("close")

        _release_single_instance_mutex(42)

        assert call_order == ["release", "close"]
