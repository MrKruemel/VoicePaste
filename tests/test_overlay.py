"""Tests for the floating overlay window module (src/overlay.py).

Covers:
    - OverlayWindow lifecycle: init, start, stop, is_running
    - State transitions: IDLE hides, RECORDING/PROCESSING/SPEAKING show
    - Recording timer logic: tick, elapsed formatting
    - Processing animation: dot cycling
    - Speaking animation: color pulsing
    - Pasting auto-hide: 800ms timer
    - Timer cancellation on state transitions
    - Thread safety: update_state from external thread
    - Win32 style constants correctness
    - Graceful behavior when not running

All tkinter and ctypes Win32 APIs are mocked to allow headless testing.
"""

import threading
import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock, call

from constants import AppState

# Win32 constant values from overlay.py (verified against MSDN)
import overlay as overlay_module


# ---------------------------------------------------------------------------
# Win32 Constants Correctness
# ---------------------------------------------------------------------------

class TestWin32Constants:
    """Verify Win32 constant values match MSDN documentation."""

    def test_gwl_exstyle(self):
        assert overlay_module.GWL_EXSTYLE == -20

    def test_ws_ex_noactivate(self):
        assert overlay_module.WS_EX_NOACTIVATE == 0x08000000

    def test_ws_ex_topmost(self):
        assert overlay_module.WS_EX_TOPMOST == 0x00000008

    def test_ws_ex_transparent(self):
        assert overlay_module.WS_EX_TRANSPARENT == 0x00000020

    def test_ws_ex_toolwindow(self):
        assert overlay_module.WS_EX_TOOLWINDOW == 0x00000080

    def test_ws_ex_layered(self):
        assert overlay_module.WS_EX_LAYERED == 0x00080000

    def test_lwa_alpha(self):
        assert overlay_module.LWA_ALPHA == 0x02

    def test_spi_getworkarea(self):
        assert overlay_module.SPI_GETWORKAREA == 0x0030

    def test_swp_noactivate(self):
        assert overlay_module.SWP_NOACTIVATE == 0x0010

    def test_hwnd_topmost(self):
        assert overlay_module.HWND_TOPMOST == -1


# ---------------------------------------------------------------------------
# State-specific configuration (colors, labels)
# ---------------------------------------------------------------------------

class TestStateConfiguration:
    """Verify that state-specific colors and labels are defined."""

    def test_recording_has_color(self):
        assert AppState.RECORDING in overlay_module.STATE_COLORS

    def test_processing_has_color(self):
        assert AppState.PROCESSING in overlay_module.STATE_COLORS

    def test_speaking_has_color(self):
        assert AppState.SPEAKING in overlay_module.STATE_COLORS

    def test_recording_color_is_red(self):
        assert overlay_module.STATE_COLORS[AppState.RECORDING] == "#e63232"

    def test_processing_color_is_amber(self):
        assert overlay_module.STATE_COLORS[AppState.PROCESSING] == "#f0c828"

    def test_speaking_color_is_blue(self):
        assert overlay_module.STATE_COLORS[AppState.SPEAKING] == "#4682e6"

    def test_recording_has_label(self):
        assert AppState.RECORDING in overlay_module.STATE_LABELS

    def test_processing_has_label(self):
        assert AppState.PROCESSING in overlay_module.STATE_LABELS

    def test_speaking_has_label(self):
        assert AppState.SPEAKING in overlay_module.STATE_LABELS

    def test_idle_not_in_labels(self):
        """IDLE has no label since the overlay is hidden in IDLE state."""
        assert AppState.IDLE not in overlay_module.STATE_LABELS


# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

class TestLayoutConstants:
    """Verify layout constants are reasonable."""

    def test_overlay_width_positive(self):
        assert overlay_module.OVERLAY_WIDTH > 0

    def test_overlay_height_positive(self):
        assert overlay_module.OVERLAY_HEIGHT > 0

    def test_overlay_bg_alpha_in_range(self):
        assert 0 <= overlay_module.OVERLAY_BG_ALPHA <= 255

    def test_overlay_corner_radius_positive(self):
        assert overlay_module.OVERLAY_CORNER_RADIUS > 0


# ---------------------------------------------------------------------------
# OverlayWindow.__init__
# ---------------------------------------------------------------------------

class TestOverlayWindowInit:
    """Test OverlayWindow initial state after construction."""

    def test_initial_state_is_idle(self):
        ow = overlay_module.OverlayWindow()
        assert ow._current_state == AppState.IDLE

    def test_not_running_after_init(self):
        ow = overlay_module.OverlayWindow()
        assert ow.is_running is False

    def test_no_thread_after_init(self):
        ow = overlay_module.OverlayWindow()
        assert ow._thread is None

    def test_no_root_after_init(self):
        ow = overlay_module.OverlayWindow()
        assert ow._root is None

    def test_no_window_after_init(self):
        ow = overlay_module.OverlayWindow()
        assert ow._window is None

    def test_timer_id_none_after_init(self):
        ow = overlay_module.OverlayWindow()
        assert ow._timer_id is None

    def test_animation_id_none_after_init(self):
        ow = overlay_module.OverlayWindow()
        assert ow._animation_id is None


# ---------------------------------------------------------------------------
# OverlayWindow.is_running property
# ---------------------------------------------------------------------------

class TestOverlayWindowIsRunning:
    """Test the is_running property logic."""

    def test_false_when_not_running_flag(self):
        ow = overlay_module.OverlayWindow()
        ow._running = False
        ow._thread = MagicMock(is_alive=MagicMock(return_value=True))
        assert ow.is_running is False

    def test_false_when_no_thread(self):
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._thread = None
        assert ow.is_running is False

    def test_false_when_thread_not_alive(self):
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._thread = MagicMock(is_alive=MagicMock(return_value=False))
        assert ow.is_running is False

    def test_true_when_running_and_thread_alive(self):
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._thread = MagicMock(is_alive=MagicMock(return_value=True))
        assert ow.is_running is True


# ---------------------------------------------------------------------------
# OverlayWindow.start
# ---------------------------------------------------------------------------

class TestOverlayWindowStart:
    """Test start method behavior."""

    def test_start_spawns_thread(self):
        """start() creates and starts a daemon thread."""
        ow = overlay_module.OverlayWindow()

        # Mock _run_overlay to immediately set ready and running
        def fake_run():
            ow._running = True
            ow._ready_event.set()

        with patch.object(ow, '_run_overlay', side_effect=fake_run):
            # Patch Thread to capture the target
            with patch('overlay.threading.Thread') as MockThread:
                mock_thread_instance = MagicMock()
                mock_thread_instance.is_alive.return_value = True
                MockThread.return_value = mock_thread_instance

                ow.start()

                MockThread.assert_called_once()
                call_kwargs = MockThread.call_args[1]
                assert call_kwargs['daemon'] is True
                assert call_kwargs['name'] == 'overlay-T4'
                mock_thread_instance.start.assert_called_once()

    def test_start_idempotent_when_running(self):
        """start() is a no-op when already running."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._thread = MagicMock(is_alive=MagicMock(return_value=True))

        # Should not spawn a new thread
        with patch('overlay.threading.Thread') as MockThread:
            ow.start()
            MockThread.assert_not_called()

    def test_start_waits_for_ready_event(self):
        """start() blocks until _ready_event is set (up to 2s timeout)."""
        ow = overlay_module.OverlayWindow()

        def fake_run():
            ow._running = True
            ow._ready_event.set()

        with patch.object(ow, '_run_overlay', side_effect=fake_run):
            with patch('overlay.threading.Thread') as MockThread:
                mock_thread = MagicMock()
                mock_thread.is_alive.return_value = True
                MockThread.return_value = mock_thread

                # Mock the ready_event to verify wait is called
                original_event = ow._ready_event
                ow._ready_event = MagicMock()
                ow._ready_event.wait.return_value = True

                ow.start()

                ow._ready_event.clear.assert_called_once()
                ow._ready_event.wait.assert_called_once_with(timeout=2.0)


# ---------------------------------------------------------------------------
# OverlayWindow.stop
# ---------------------------------------------------------------------------

class TestOverlayWindowStop:
    """Test stop method behavior."""

    def test_stop_when_not_running_is_noop(self):
        """stop() does nothing when not running."""
        ow = overlay_module.OverlayWindow()
        ow._running = False

        # Should not raise
        ow.stop()

    def test_stop_sets_running_false(self):
        """stop() sets _running to False."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = MagicMock()
        ow._thread = MagicMock()
        ow._thread.is_alive.return_value = False

        ow.stop()

        assert ow._running is False

    def test_stop_calls_root_quit(self):
        """stop() calls root.quit() to exit mainloop."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = MagicMock()
        ow._thread = MagicMock()
        ow._thread.is_alive.return_value = False

        ow.stop()

        ow._root.quit.assert_called_once()

    def test_stop_joins_thread(self):
        """stop() joins the thread with a timeout."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = MagicMock()
        mock_thread = MagicMock()
        # is_alive returns True initially (thread running), then False after join
        mock_thread.is_alive.side_effect = [True, False]
        ow._thread = mock_thread

        ow.stop()

        # stop() sets _thread = None after joining, so check the original mock
        mock_thread.join.assert_called_once_with(timeout=3.0)

    def test_stop_clears_thread_reference(self):
        """stop() sets _thread to None after joining."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = MagicMock()
        ow._thread = MagicMock()
        ow._thread.is_alive.return_value = False

        ow.stop()

        assert ow._thread is None

    def test_stop_handles_root_quit_exception(self):
        """stop() catches exceptions from root.quit() gracefully."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = MagicMock()
        ow._root.quit.side_effect = RuntimeError("Tcl error")
        ow._thread = MagicMock()
        ow._thread.is_alive.return_value = False

        # Should not raise
        ow.stop()
        assert ow._running is False


# ---------------------------------------------------------------------------
# OverlayWindow.update_state
# ---------------------------------------------------------------------------

class TestOverlayWindowUpdateState:
    """Test update_state method behavior."""

    def test_noop_when_not_running(self):
        """update_state() is a no-op when overlay is not running."""
        ow = overlay_module.OverlayWindow()
        ow._running = False
        ow._root = None

        # Should not raise
        ow.update_state(AppState.RECORDING)

    def test_noop_when_root_is_none(self):
        """update_state() is a no-op when root is None."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = None

        # Should not raise
        ow.update_state(AppState.RECORDING)

    def test_sets_current_state(self):
        """update_state() updates _current_state."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = MagicMock()

        ow.update_state(AppState.RECORDING)

        assert ow._current_state == AppState.RECORDING

    def test_generates_state_changed_event(self):
        """update_state() calls event_generate with <<StateChanged>>."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = MagicMock()

        ow.update_state(AppState.PROCESSING)

        ow._root.event_generate.assert_called_once_with(
            "<<StateChanged>>", when="tail"
        )

    def test_handles_event_generate_exception(self):
        """update_state() catches exceptions from event_generate."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = MagicMock()
        ow._root.event_generate.side_effect = RuntimeError("Widget destroyed")

        # Should not raise
        ow.update_state(AppState.RECORDING)
        assert ow._current_state == AppState.RECORDING

    def test_updates_to_all_states(self):
        """update_state() accepts all AppState values."""
        ow = overlay_module.OverlayWindow()
        ow._running = True
        ow._root = MagicMock()

        for state in AppState:
            ow.update_state(state)
            assert ow._current_state == state


# ---------------------------------------------------------------------------
# _on_state_changed event handler (routes to display methods)
# ---------------------------------------------------------------------------

class TestOnStateChanged:
    """Test _on_state_changed routing logic."""

    def _make_overlay(self):
        """Create an OverlayWindow with mocked display methods."""
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._window = MagicMock()
        ow._dot_label = MagicMock()
        ow._state_label = MagicMock()
        ow._time_label = MagicMock()
        return ow

    def test_idle_calls_show_idle(self):
        ow = self._make_overlay()
        ow._current_state = AppState.IDLE

        with patch.object(ow, '_show_idle') as mock_show:
            ow._on_state_changed()
            mock_show.assert_called_once()

    def test_recording_calls_show_recording(self):
        ow = self._make_overlay()
        ow._current_state = AppState.RECORDING

        with patch.object(ow, '_show_recording') as mock_show:
            ow._on_state_changed()
            mock_show.assert_called_once()

    def test_processing_calls_show_processing(self):
        ow = self._make_overlay()
        ow._current_state = AppState.PROCESSING

        with patch.object(ow, '_show_processing') as mock_show:
            ow._on_state_changed()
            mock_show.assert_called_once()

    def test_speaking_calls_show_speaking(self):
        ow = self._make_overlay()
        ow._current_state = AppState.SPEAKING

        with patch.object(ow, '_show_speaking') as mock_show:
            ow._on_state_changed()
            mock_show.assert_called_once()

    def test_pasting_calls_show_pasting(self):
        ow = self._make_overlay()
        ow._current_state = AppState.PASTING

        with patch.object(ow, '_show_pasting') as mock_show:
            ow._on_state_changed()
            mock_show.assert_called_once()

    def test_cancels_timers_before_dispatch(self):
        """_cancel_timers is called before dispatching to display method."""
        ow = self._make_overlay()
        ow._current_state = AppState.IDLE
        call_order = []

        def track_cancel():
            call_order.append('cancel')

        def track_show():
            call_order.append('show')

        with patch.object(ow, '_cancel_timers', side_effect=track_cancel):
            with patch.object(ow, '_show_idle', side_effect=track_show):
                ow._on_state_changed()

        assert call_order == ['cancel', 'show']


# ---------------------------------------------------------------------------
# _show_idle (hides overlay)
# ---------------------------------------------------------------------------

class TestShowIdle:
    """Test _show_idle hides the overlay window."""

    def test_withdraws_window(self):
        ow = overlay_module.OverlayWindow()
        ow._window = MagicMock()

        ow._show_idle()

        ow._window.withdraw.assert_called_once()

    def test_noop_when_window_is_none(self):
        ow = overlay_module.OverlayWindow()
        ow._window = None

        # Should not raise
        ow._show_idle()


# ---------------------------------------------------------------------------
# _show_recording (timer display)
# ---------------------------------------------------------------------------

class TestShowRecording:
    """Test _show_recording state display."""

    def _make_overlay(self):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._window = MagicMock()
        ow._dot_label = MagicMock()
        ow._state_label = MagicMock()
        ow._time_label = MagicMock()
        return ow

    def test_sets_recording_start_time(self):
        ow = self._make_overlay()

        before = time.monotonic()
        ow._show_recording()
        after = time.monotonic()

        assert before <= ow._recording_start_time <= after

    def test_configures_dot_color_red(self):
        ow = self._make_overlay()
        ow._show_recording()

        ow._dot_label.configure.assert_called_with(
            fg=overlay_module.STATE_COLORS[AppState.RECORDING]
        )

    def test_configures_state_label(self):
        ow = self._make_overlay()
        ow._show_recording()

        ow._state_label.configure.assert_called_with(
            text=overlay_module.STATE_LABELS[AppState.RECORDING]
        )

    def test_initializes_timer_to_zero(self):
        ow = self._make_overlay()
        ow._show_recording()

        ow._time_label.configure.assert_any_call(text="00:00")

    def test_deiconifies_window(self):
        ow = self._make_overlay()
        ow._show_recording()

        ow._window.deiconify.assert_called_once()

    def test_starts_timer_tick(self):
        """_tick_recording_timer is called to start the timer."""
        ow = self._make_overlay()

        with patch.object(ow, '_tick_recording_timer') as mock_tick:
            ow._show_recording()
            mock_tick.assert_called_once()


# ---------------------------------------------------------------------------
# _tick_recording_timer
# ---------------------------------------------------------------------------

class TestTickRecordingTimer:
    """Test recording timer tick logic."""

    def _make_overlay(self, state=AppState.RECORDING):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._window = MagicMock()
        ow._dot_label = MagicMock()
        ow._state_label = MagicMock()
        ow._time_label = MagicMock()
        ow._current_state = state
        ow._recording_start_time = time.monotonic() - 65  # 1m 5s ago
        return ow

    def test_formats_elapsed_time(self):
        """Timer label shows MM:SS format."""
        ow = self._make_overlay()
        ow._recording_start_time = time.monotonic() - 65  # 1 min 5 sec

        ow._tick_recording_timer()

        # Check the configure call contains a time string like "01:05"
        configure_calls = ow._time_label.configure.call_args_list
        assert len(configure_calls) == 1
        text = configure_calls[0][1]['text']
        assert text == "01:05"

    def test_schedules_next_tick(self):
        """Schedules itself again via root.after(1000)."""
        ow = self._make_overlay()
        ow._root.after.return_value = "timer_id_123"

        ow._tick_recording_timer()

        ow._root.after.assert_called_once_with(1000, ow._tick_recording_timer)
        assert ow._timer_id == "timer_id_123"

    def test_stops_when_not_recording(self):
        """Does nothing when state is no longer RECORDING."""
        ow = self._make_overlay(state=AppState.PROCESSING)

        ow._tick_recording_timer()

        ow._root.after.assert_not_called()
        ow._time_label.configure.assert_not_called()

    def test_zero_seconds_elapsed(self):
        """Timer shows 00:00 when recording just started."""
        ow = self._make_overlay()
        ow._recording_start_time = time.monotonic()  # Just now

        ow._tick_recording_timer()

        text = ow._time_label.configure.call_args[1]['text']
        assert text == "00:00"


# ---------------------------------------------------------------------------
# _show_processing (animated dots)
# ---------------------------------------------------------------------------

class TestShowProcessing:
    """Test _show_processing state display."""

    def _make_overlay(self):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._window = MagicMock()
        ow._dot_label = MagicMock()
        ow._state_label = MagicMock()
        ow._time_label = MagicMock()
        return ow

    def test_configures_dot_color_amber(self):
        ow = self._make_overlay()
        ow._show_processing()

        ow._dot_label.configure.assert_called_with(
            fg=overlay_module.STATE_COLORS[AppState.PROCESSING]
        )

    def test_hides_time_label(self):
        ow = self._make_overlay()
        ow._show_processing()

        ow._time_label.pack_forget.assert_called_once()

    def test_deiconifies_window(self):
        ow = self._make_overlay()
        ow._show_processing()

        ow._window.deiconify.assert_called_once()

    def test_starts_dot_animation(self):
        ow = self._make_overlay()

        with patch.object(ow, '_animate_processing') as mock_animate:
            ow._show_processing()
            mock_animate.assert_called_once()

    def test_resets_animation_frame(self):
        ow = self._make_overlay()
        ow._animation_frame = 99

        with patch.object(ow, '_animate_processing'):
            ow._show_processing()

        assert ow._animation_frame == 0


# ---------------------------------------------------------------------------
# _animate_processing
# ---------------------------------------------------------------------------

class TestAnimateProcessing:
    """Test processing dot animation logic."""

    def _make_overlay(self, state=AppState.PROCESSING):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._window = MagicMock()
        ow._dot_label = MagicMock()
        ow._state_label = MagicMock()
        ow._time_label = MagicMock()
        ow._current_state = state
        ow._animation_frame = 0
        return ow

    def test_cycles_dots_1(self):
        """Frame 0 -> 'Processing.' (1 dot)."""
        ow = self._make_overlay()
        ow._animation_frame = 0

        ow._animate_processing()

        text = ow._state_label.configure.call_args[1]['text']
        assert text.startswith("Processing.")
        assert not text.startswith("Processing..")

    def test_cycles_dots_2(self):
        """Frame 1 -> 'Processing..' (2 dots)."""
        ow = self._make_overlay()
        ow._animation_frame = 1

        ow._animate_processing()

        text = ow._state_label.configure.call_args[1]['text']
        assert "Processing.." in text

    def test_cycles_dots_3(self):
        """Frame 2 -> 'Processing...' (3 dots)."""
        ow = self._make_overlay()
        ow._animation_frame = 2

        ow._animate_processing()

        text = ow._state_label.configure.call_args[1]['text']
        assert "Processing..." in text

    def test_wraps_around(self):
        """Frame 3 -> back to 'Processing.' (wraps via modulo)."""
        ow = self._make_overlay()
        ow._animation_frame = 3

        ow._animate_processing()

        text = ow._state_label.configure.call_args[1]['text']
        assert text.startswith("Processing.")
        assert not text.startswith("Processing..")

    def test_schedules_next_frame(self):
        """Schedules itself again via root.after(500)."""
        ow = self._make_overlay()
        ow._root.after.return_value = "anim_id_456"

        ow._animate_processing()

        ow._root.after.assert_called_once_with(500, ow._animate_processing)
        assert ow._animation_id == "anim_id_456"

    def test_increments_frame_counter(self):
        ow = self._make_overlay()
        ow._animation_frame = 0

        ow._animate_processing()

        assert ow._animation_frame == 1

    def test_stops_when_not_processing(self):
        ow = self._make_overlay(state=AppState.IDLE)

        ow._animate_processing()

        ow._root.after.assert_not_called()


# ---------------------------------------------------------------------------
# _show_speaking (pulse animation)
# ---------------------------------------------------------------------------

class TestShowSpeaking:
    """Test _show_speaking state display."""

    def _make_overlay(self):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._window = MagicMock()
        ow._dot_label = MagicMock()
        ow._state_label = MagicMock()
        ow._time_label = MagicMock()
        return ow

    def test_configures_dot_color_blue(self):
        ow = self._make_overlay()
        ow._show_speaking()

        ow._dot_label.configure.assert_called_with(
            fg=overlay_module.STATE_COLORS[AppState.SPEAKING]
        )

    def test_hides_time_label(self):
        ow = self._make_overlay()
        ow._show_speaking()

        ow._time_label.pack_forget.assert_called_once()

    def test_deiconifies_window(self):
        ow = self._make_overlay()
        ow._show_speaking()

        ow._window.deiconify.assert_called_once()

    def test_starts_pulsing_animation(self):
        ow = self._make_overlay()

        with patch.object(ow, '_animate_speaking') as mock_animate:
            ow._show_speaking()
            mock_animate.assert_called_once()


# ---------------------------------------------------------------------------
# _animate_speaking
# ---------------------------------------------------------------------------

class TestAnimateSpeaking:
    """Test speaking dot pulse animation."""

    def _make_overlay(self, state=AppState.SPEAKING):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._dot_label = MagicMock()
        ow._current_state = state
        ow._animation_frame = 0
        return ow

    def test_alternates_bright_and_dim(self):
        """Frame 0 -> bright blue, Frame 1 -> dim blue."""
        ow = self._make_overlay()
        ow._animation_frame = 0

        ow._animate_speaking()
        color_0 = ow._dot_label.configure.call_args[1]['fg']

        ow._dot_label.reset_mock()
        ow._animate_speaking()
        color_1 = ow._dot_label.configure.call_args[1]['fg']

        assert color_0 != color_1
        assert color_0 == "#4682e6"
        assert color_1 == "#2a5298"

    def test_schedules_next_frame_600ms(self):
        ow = self._make_overlay()
        ow._root.after.return_value = "pulse_id"

        ow._animate_speaking()

        ow._root.after.assert_called_once_with(600, ow._animate_speaking)
        assert ow._animation_id == "pulse_id"

    def test_stops_when_not_speaking(self):
        ow = self._make_overlay(state=AppState.IDLE)

        ow._animate_speaking()

        ow._root.after.assert_not_called()
        ow._dot_label.configure.assert_not_called()


# ---------------------------------------------------------------------------
# _show_pasting (auto-hide after 800ms)
# ---------------------------------------------------------------------------

class TestShowPasting:
    """Test _show_pasting brief indicator with auto-hide."""

    def _make_overlay(self):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._window = MagicMock()
        ow._dot_label = MagicMock()
        ow._state_label = MagicMock()
        ow._time_label = MagicMock()
        return ow

    def test_shows_green_dot(self):
        ow = self._make_overlay()
        ow._show_pasting()

        ow._dot_label.configure.assert_called_with(fg="#32c850")

    def test_shows_pasted_label(self):
        ow = self._make_overlay()
        ow._show_pasting()

        ow._state_label.configure.assert_called_with(text="Pasted")

    def test_deiconifies_window(self):
        ow = self._make_overlay()
        ow._show_pasting()

        ow._window.deiconify.assert_called_once()

    def test_schedules_auto_hide_800ms(self):
        ow = self._make_overlay()
        ow._root.after.return_value = "autohide_id"

        ow._show_pasting()

        ow._root.after.assert_called_once_with(800, ow._show_idle)
        assert ow._timer_id == "autohide_id"

    def test_hides_time_label(self):
        ow = self._make_overlay()
        ow._show_pasting()

        ow._time_label.pack_forget.assert_called_once()


# ---------------------------------------------------------------------------
# _cancel_timers
# ---------------------------------------------------------------------------

class TestCancelTimers:
    """Test timer cancellation logic."""

    def test_cancels_timer_id(self):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._timer_id = "timer_123"

        ow._cancel_timers()

        ow._root.after_cancel.assert_any_call("timer_123")
        assert ow._timer_id is None

    def test_cancels_animation_id(self):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._animation_id = "anim_456"

        ow._cancel_timers()

        ow._root.after_cancel.assert_any_call("anim_456")
        assert ow._animation_id is None

    def test_cancels_both_timers(self):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._timer_id = "t1"
        ow._animation_id = "a1"

        ow._cancel_timers()

        assert ow._root.after_cancel.call_count == 2
        assert ow._timer_id is None
        assert ow._animation_id is None

    def test_noop_when_no_timers(self):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._timer_id = None
        ow._animation_id = None

        # Should not raise
        ow._cancel_timers()

        ow._root.after_cancel.assert_not_called()

    def test_handles_cancel_exception(self):
        """Gracefully handles after_cancel raising an exception."""
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._root.after_cancel.side_effect = RuntimeError("Already cancelled")
        ow._timer_id = "expired"

        # Should not raise
        ow._cancel_timers()
        assert ow._timer_id is None

    def test_noop_when_root_is_none(self):
        ow = overlay_module.OverlayWindow()
        ow._root = None
        ow._timer_id = "orphaned"
        ow._animation_id = "orphaned"

        # Should not raise, and timer IDs remain (can't cancel without root)
        ow._cancel_timers()


# ---------------------------------------------------------------------------
# Integration: State transition sequence
# ---------------------------------------------------------------------------

class TestStateTransitionSequence:
    """Test realistic state transition sequences."""

    def _make_overlay(self):
        ow = overlay_module.OverlayWindow()
        ow._root = MagicMock()
        ow._window = MagicMock()
        ow._dot_label = MagicMock()
        ow._state_label = MagicMock()
        ow._time_label = MagicMock()
        ow._running = True
        return ow

    def test_idle_to_recording_to_processing_to_idle(self):
        """Simulate a complete recording session."""
        ow = self._make_overlay()

        # Start idle (overlay hidden)
        ow._current_state = AppState.IDLE
        ow._on_state_changed()
        ow._window.withdraw.assert_called()

        # Transition to recording (overlay shown)
        ow._current_state = AppState.RECORDING
        ow._on_state_changed()
        ow._window.deiconify.assert_called()

        # Transition to processing (overlay updated)
        ow._current_state = AppState.PROCESSING
        ow._on_state_changed()

        # Back to idle (overlay hidden)
        ow._current_state = AppState.IDLE
        ow._on_state_changed()
        # withdraw called at least twice (once at start, once now)
        assert ow._window.withdraw.call_count >= 2

    def test_recording_to_processing_cancels_timer(self):
        """Transitioning from RECORDING to PROCESSING cancels the recording timer."""
        ow = self._make_overlay()

        # Enter RECORDING (starts timer)
        ow._current_state = AppState.RECORDING
        ow._root.after.return_value = "rec_timer"
        ow._on_state_changed()
        assert ow._timer_id is not None

        # Enter PROCESSING (should cancel the recording timer)
        ow._current_state = AppState.PROCESSING
        ow._root.after.return_value = "proc_anim"
        ow._on_state_changed()

        # The recording timer should have been cancelled
        ow._root.after_cancel.assert_called()

    def test_update_state_from_external_thread(self):
        """update_state can be safely called from a non-T4 thread."""
        ow = self._make_overlay()

        # Simulate calling from external thread
        ow.update_state(AppState.RECORDING)

        assert ow._current_state == AppState.RECORDING
        ow._root.event_generate.assert_called_once_with(
            "<<StateChanged>>", when="tail"
        )


# ---------------------------------------------------------------------------
# Integration with VoicePasteApp (main.py overlay wiring)
# ---------------------------------------------------------------------------

class TestOverlayMainIntegration:
    """Test overlay integration points in main.py."""

    def test_overlay_created_when_show_overlay_true(self):
        """VoicePasteApp creates overlay when config.show_overlay is True."""
        mock_stt_instance = MagicMock()
        with patch("main.AudioRecorder") as MockRecorder, \
             patch("main.create_stt_backend", return_value=mock_stt_instance), \
             patch("main.CloudLLMSummarizer"), \
             patch("main.PassthroughSummarizer"), \
             patch("main.HotkeyManager"), \
             patch("main.TrayManager"), \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text"), \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue"), \
             patch("main.AudioPlayer"), \
             patch("main.create_tts_backend", return_value=None), \
             patch("main.OverlayWindow") as MockOverlay:

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                openai_api_key="sk-test1234567890",
                show_overlay=True,
            )
            app = VoicePasteApp(config)

            MockOverlay.assert_called_once()
            assert app._overlay is not None

    def test_overlay_not_created_when_show_overlay_false(self):
        """VoicePasteApp does not create overlay when config.show_overlay is False."""
        mock_stt_instance = MagicMock()
        with patch("main.AudioRecorder") as MockRecorder, \
             patch("main.create_stt_backend", return_value=mock_stt_instance), \
             patch("main.CloudLLMSummarizer"), \
             patch("main.PassthroughSummarizer"), \
             patch("main.HotkeyManager"), \
             patch("main.TrayManager"), \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text"), \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue"), \
             patch("main.AudioPlayer"), \
             patch("main.create_tts_backend", return_value=None), \
             patch("main.OverlayWindow") as MockOverlay:

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                openai_api_key="sk-test1234567890",
                show_overlay=False,
            )
            app = VoicePasteApp(config)

            MockOverlay.assert_not_called()
            assert app._overlay is None

    def test_set_state_updates_overlay(self):
        """_set_state calls overlay.update_state when overlay exists."""
        mock_stt_instance = MagicMock()
        with patch("main.AudioRecorder"), \
             patch("main.create_stt_backend", return_value=mock_stt_instance), \
             patch("main.CloudLLMSummarizer"), \
             patch("main.PassthroughSummarizer"), \
             patch("main.HotkeyManager"), \
             patch("main.TrayManager"), \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text"), \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue"), \
             patch("main.AudioPlayer"), \
             patch("main.create_tts_backend", return_value=None), \
             patch("main.OverlayWindow") as MockOverlay:

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                openai_api_key="sk-test1234567890",
                show_overlay=True,
            )
            app = VoicePasteApp(config)
            mock_overlay = MockOverlay.return_value

            app._set_state(AppState.RECORDING)

            mock_overlay.update_state.assert_called_with(AppState.RECORDING)

    def test_shutdown_stops_overlay(self):
        """_shutdown calls overlay.stop() when overlay exists."""
        mock_stt_instance = MagicMock()
        with patch("main.AudioRecorder") as MockRecorder, \
             patch("main.create_stt_backend", return_value=mock_stt_instance), \
             patch("main.CloudLLMSummarizer"), \
             patch("main.PassthroughSummarizer"), \
             patch("main.HotkeyManager"), \
             patch("main.TrayManager"), \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text"), \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue"), \
             patch("main.AudioPlayer"), \
             patch("main.create_tts_backend", return_value=None), \
             patch("main.OverlayWindow") as MockOverlay:

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                openai_api_key="sk-test1234567890",
                show_overlay=True,
            )
            app = VoicePasteApp(config)
            mock_overlay = MockOverlay.return_value

            # Mock recorder and audio player for clean shutdown
            app._recorder.is_recording = False
            app._audio_player = MagicMock()
            app._audio_player.is_playing = False

            app._shutdown()

            mock_overlay.stop.assert_called_once()

    def test_settings_save_enables_overlay(self):
        """_on_settings_saved creates and starts overlay when show_overlay becomes True."""
        mock_stt_instance = MagicMock()
        with patch("main.AudioRecorder"), \
             patch("main.create_stt_backend", return_value=mock_stt_instance), \
             patch("main.CloudLLMSummarizer"), \
             patch("main.PassthroughSummarizer"), \
             patch("main.HotkeyManager"), \
             patch("main.TrayManager"), \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text"), \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue"), \
             patch("main.AudioPlayer"), \
             patch("main.create_tts_backend", return_value=None), \
             patch("main.OverlayWindow") as MockOverlay:

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                openai_api_key="sk-test1234567890",
                show_overlay=False,  # Start disabled
            )
            app = VoicePasteApp(config)
            assert app._overlay is None

            # Simulate settings change enabling overlay
            config.show_overlay = True
            mock_new_overlay = MagicMock()
            mock_new_overlay.is_running = False
            MockOverlay.return_value = mock_new_overlay

            app._on_settings_saved({"show_overlay": True})

            MockOverlay.assert_called()
            mock_new_overlay.start.assert_called_once()

    def test_settings_save_disables_overlay(self):
        """_on_settings_saved stops and removes overlay when show_overlay becomes False."""
        mock_stt_instance = MagicMock()
        with patch("main.AudioRecorder"), \
             patch("main.create_stt_backend", return_value=mock_stt_instance), \
             patch("main.CloudLLMSummarizer"), \
             patch("main.PassthroughSummarizer"), \
             patch("main.HotkeyManager"), \
             patch("main.TrayManager"), \
             patch("main.clipboard_backup"), \
             patch("main.clipboard_restore"), \
             patch("main.paste_text"), \
             patch("main.play_recording_start_cue"), \
             patch("main.play_recording_stop_cue"), \
             patch("main.play_cancel_cue"), \
             patch("main.play_error_cue"), \
             patch("main.AudioPlayer"), \
             patch("main.create_tts_backend", return_value=None), \
             patch("main.OverlayWindow") as MockOverlay:

            from config import AppConfig
            from main import VoicePasteApp

            config = AppConfig(
                openai_api_key="sk-test1234567890",
                show_overlay=True,  # Start enabled
            )
            app = VoicePasteApp(config)
            mock_overlay = MockOverlay.return_value

            # Simulate settings change disabling overlay
            config.show_overlay = False
            app._on_settings_saved({"show_overlay": False})

            mock_overlay.stop.assert_called_once()
            assert app._overlay is None
