"""Tests for single-instance lock enforcement.

Validates:
- REQ-S27: Only one instance of the application can run at a time.
- Lock is properly acquired and released.
- Second instance is blocked when lock is already held.
- Lock release cleans up even on errors.

On Windows, tests the Win32 named mutex via platform_impl._windows.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Tests Win32 named mutex (platform_impl._windows)",
)

# Import the platform-specific implementation directly for testing
from platform_impl._windows import (
    acquire_single_instance_lock,
    release_single_instance_lock,
    _MUTEX_NAME,
    _ERROR_ALREADY_EXISTS,
)


class TestAcquireSingleInstanceLock:
    """Test single-instance lock acquisition logic."""

    @patch("platform_impl._windows.ctypes")
    def test_successful_acquisition_returns_handle(self, mock_ctypes):
        """First instance should acquire the mutex and return a valid handle."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        fake_handle = 12345
        mock_kernel32.CreateMutexW.return_value = fake_handle
        mock_kernel32.GetLastError.return_value = 0  # No error

        result = acquire_single_instance_lock()

        assert result == fake_handle
        mock_kernel32.CreateMutexW.assert_called_once_with(
            None, True, _MUTEX_NAME
        )

    @patch("platform_impl._windows.ctypes")
    def test_already_running_returns_none(self, mock_ctypes):
        """Second instance should get ERROR_ALREADY_EXISTS and return None."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        fake_handle = 12345
        mock_kernel32.CreateMutexW.return_value = fake_handle
        mock_kernel32.GetLastError.return_value = _ERROR_ALREADY_EXISTS

        result = acquire_single_instance_lock()

        assert result is None
        # Should close the duplicate handle
        mock_kernel32.CloseHandle.assert_called_once_with(fake_handle)

    @patch("platform_impl._windows.ctypes")
    def test_create_mutex_fails_returns_none(self, mock_ctypes):
        """If CreateMutexW returns 0, acquisition should fail gracefully."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        mock_kernel32.CreateMutexW.return_value = 0
        mock_kernel32.GetLastError.return_value = 5  # ACCESS_DENIED

        result = acquire_single_instance_lock()

        assert result is None
        # Should NOT call CloseHandle since handle is invalid
        mock_kernel32.CloseHandle.assert_not_called()

    @patch("platform_impl._windows.ctypes")
    def test_create_mutex_returns_none_handle(self, mock_ctypes):
        """If CreateMutexW returns None, acquisition should fail gracefully."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        mock_kernel32.CreateMutexW.return_value = None

        result = acquire_single_instance_lock()

        assert result is None

    def test_mutex_name_uses_global_namespace(self):
        """Mutex name should use Global\\ prefix for cross-session visibility."""
        assert _MUTEX_NAME.startswith("Global\\")

    def test_error_already_exists_constant_is_183(self):
        """ERROR_ALREADY_EXISTS should be 183 per Windows API documentation."""
        assert _ERROR_ALREADY_EXISTS == 183


class TestReleaseSingleInstanceLock:
    """Test single-instance lock release logic."""

    @patch("platform_impl._windows.ctypes")
    def test_release_calls_release_and_close(self, mock_ctypes):
        """Release should call both ReleaseMutex and CloseHandle."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32

        fake_handle = 99999
        release_single_instance_lock(fake_handle)

        mock_kernel32.ReleaseMutex.assert_called_once_with(fake_handle)
        mock_kernel32.CloseHandle.assert_called_once_with(fake_handle)

    @patch("platform_impl._windows.ctypes")
    def test_release_handles_exception_gracefully(self, mock_ctypes):
        """Release should not crash if kernel32 calls raise."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32
        mock_kernel32.ReleaseMutex.side_effect = OSError("mock failure")

        # Should not raise
        release_single_instance_lock(42)

    @patch("platform_impl._windows.ctypes")
    def test_release_order_is_release_then_close(self, mock_ctypes):
        """ReleaseMutex must be called before CloseHandle."""
        mock_kernel32 = MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32
        call_order = []

        mock_kernel32.ReleaseMutex.side_effect = lambda h: call_order.append("release")
        mock_kernel32.CloseHandle.side_effect = lambda h: call_order.append("close")

        release_single_instance_lock(42)

        assert call_order == ["release", "close"]

    def test_release_none_handle_is_noop(self):
        """Releasing a None handle should be a no-op."""
        release_single_instance_lock(None)  # Should not raise
