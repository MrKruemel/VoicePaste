#!/usr/bin/env python3
"""Interactive hotkey diagnostic script.

Run this script directly (NOT via Claude Code) to diagnose hotkey issues.
It tests both the evdev backend (Wayland) and pynput backend (X11).

Usage:
    python3 tests/diag_hotkey.py

You must be in the 'input' group for evdev to work on Wayland.
"""

import os
import select
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

MODIFIER_CODES = {29, 97, 56, 100, 42, 54, 125, 126}
MODIFIER_NAMES = {
    29: "L-Ctrl", 97: "R-Ctrl",
    56: "L-Alt", 100: "R-Alt",
    42: "L-Shift", 54: "R-Shift",
    125: "L-Meta", 126: "R-Meta",
}

def test_raw_evdev():
    """Test 1: Can we read raw events from keyboard devices?"""
    print("\n=== TEST 1: Raw evdev event reading ===")
    try:
        import evdev
    except ImportError:
        print("FAIL: evdev not installed. Run: pip install evdev")
        return False

    try:
        devices = [evdev.InputDevice(p) for p in evdev.list_devices()]
    except PermissionError:
        print("FAIL: Permission denied reading /dev/input/.")
        print("  Fix: sudo usermod -aG input $USER  (then log out and back in)")
        return False

    keyboards = [d for d in devices if 30 in d.capabilities(verbose=False).get(1, [])]
    if not keyboards:
        print("FAIL: No keyboard devices found.")
        for d in devices:
            d.close()
        return False

    print(f"Found {len(keyboards)} keyboard(s):")
    for kb in keyboards:
        print(f"  {kb.path}: {kb.name}")

    # Check modifier key capabilities
    for kb in keyboards:
        caps = kb.capabilities(verbose=False).get(1, [])
        missing_mods = []
        for code, name in MODIFIER_NAMES.items():
            if code not in caps:
                missing_mods.append(f"{name}({code})")
        if missing_mods:
            print(f"  WARNING: {kb.name} missing modifier caps: {', '.join(missing_mods)}")

    print("\nPress keys for 10 seconds. Focus THIS terminal and type.")
    print("We are looking for MODIFIER key events (Ctrl, Alt, Shift).\n")

    fd_to_dev = {kb.fd: kb for kb in keyboards}
    start = time.monotonic()
    event_count = 0
    modifier_seen = False

    while time.monotonic() - start < 10:
        r, _, _ = select.select(list(fd_to_dev.keys()), [], [], 1.0)
        if not r:
            elapsed = time.monotonic() - start
            print(f"  ... waiting ({elapsed:.0f}s / 10s)")
            continue
        for fd in r:
            dev = fd_to_dev[fd]
            try:
                for event in dev.read():
                    if event.type == 1:
                        event_count += 1
                        states = {0: "UP", 1: "DOWN", 2: "REPEAT"}
                        is_mod = event.code in MODIFIER_CODES
                        label = MODIFIER_NAMES.get(event.code, f"key-{event.code}")
                        tag = " *** MODIFIER ***" if is_mod else ""
                        if is_mod:
                            modifier_seen = True
                        print(
                            f"  [{dev.name}] {label} {states.get(event.value, '?')}{tag}"
                        )
            except Exception as e:
                print(f"  [{dev.name}] error: {e}")

    for d in devices:
        d.close()

    if event_count == 0:
        print("\nFAIL: No events received. Possible causes:")
        print("  - Your keyboard is not the device we opened")
        print("  - Another process has an exclusive grab")
        return False

    if not modifier_seen:
        print(f"\nWARNING: {event_count} events received but NO modifier keys.")
        print("  Try pressing Ctrl, Alt, or Shift individually.")
    else:
        print(f"\nOK: {event_count} events received, including modifier keys.")

    return modifier_seen


def test_evdev_monitor():
    """Test 2: Does the EvdevKeyboardMonitor detect Ctrl+Alt+R?"""
    print("\n=== TEST 2: EvdevKeyboardMonitor hotkey detection ===")
    try:
        from evdev_hotkey import EvdevKeyboardMonitor
    except ImportError:
        print("SKIP: evdev_hotkey not importable.")
        return False

    monitor = EvdevKeyboardMonitor()
    fired = []

    monitor.add_hotkey("ctrl+alt+r", lambda: fired.append(time.monotonic()))
    monitor.start()

    print(f"Monitor started: {len(monitor._devices)} device(s)")
    print("Press Ctrl+Alt+R within 15 seconds...\n")

    start = time.monotonic()
    while time.monotonic() - start < 15:
        if fired:
            elapsed = fired[0] - start
            print(f"\nOK: Hotkey detected after {elapsed:.1f}s!")
            monitor.stop()
            return True
        time.sleep(0.1)
        # Print held keys periodically for debugging
        if int((time.monotonic() - start) * 10) % 20 == 0:
            held = monitor._held_keys
            if held:
                names = []
                for code in sorted(held):
                    if code in MODIFIER_NAMES:
                        names.append(MODIFIER_NAMES[code])
                    else:
                        names.append(f"key-{code}")
                print(f"  Held keys: {', '.join(names)}")

    monitor.stop()
    print("\nFAIL: Hotkey not detected in 15 seconds.")
    return False


def test_pynput():
    """Test 3: Does pynput detect Ctrl+Alt+R? (X11/XWayland only)"""
    print("\n=== TEST 3: pynput hotkey detection (X11 path) ===")
    if not os.environ.get("DISPLAY"):
        print("SKIP: No DISPLAY set. pynput requires X11.")
        return False

    try:
        from pynput import keyboard as pynput_kb
    except ImportError:
        print("SKIP: pynput not installed.")
        return False

    fired = []

    combo = "<ctrl>+<alt>+r"
    print(f"Registering pynput GlobalHotKeys: {combo}")
    hotkeys = pynput_kb.GlobalHotKeys({combo: lambda: fired.append(time.monotonic())})
    hotkeys.daemon = True
    hotkeys.start()

    print("Press Ctrl+Alt+R within 15 seconds...\n")

    start = time.monotonic()
    while time.monotonic() - start < 15:
        if fired:
            elapsed = fired[0] - start
            print(f"\nOK: pynput detected hotkey after {elapsed:.1f}s!")
            hotkeys.stop()
            return True
        time.sleep(0.1)

    hotkeys.stop()
    print("\nFAIL: pynput did not detect hotkey in 15 seconds.")
    return False


def test_full_stack():
    """Test 4: Does the HotkeyManager work end-to-end?"""
    print("\n=== TEST 4: HotkeyManager end-to-end ===")
    from hotkey import HotkeyManager, _is_wayland

    backend = "evdev (Wayland)" if _is_wayland() else "pynput (X11)"
    print(f"Backend: {backend}")

    manager = HotkeyManager()
    fired = []

    try:
        manager.register(lambda: fired.append(time.monotonic()))
    except Exception as e:
        print(f"FAIL: Registration error: {e}")
        return False

    print(f"Hotkey registered: {manager.hotkey}")
    print("Press the hotkey within 15 seconds...\n")

    start = time.monotonic()
    while time.monotonic() - start < 15:
        if fired:
            elapsed = fired[0] - start
            print(f"\nOK: HotkeyManager detected hotkey after {elapsed:.1f}s!")
            manager.unregister()
            return True
        time.sleep(0.1)

    manager.unregister()
    print("\nFAIL: HotkeyManager did not detect hotkey in 15 seconds.")
    return False


def main():
    print("VoicePaste Hotkey Diagnostic Tool")
    print("=" * 50)
    print(f"Platform: {sys.platform}")
    print(f"Python: {sys.version}")
    print(f"XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE', 'not set')}")
    print(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY', 'not set')}")
    print(f"DISPLAY: {os.environ.get('DISPLAY', 'not set')}")

    from hotkey import _is_wayland
    print(f"_is_wayland(): {_is_wayland()}")

    results = {}

    if _is_wayland():
        results["raw_evdev"] = test_raw_evdev()
        results["evdev_monitor"] = test_evdev_monitor()

    results["pynput"] = test_pynput()
    results["full_stack"] = test_full_stack()

    print("\n" + "=" * 50)
    print("RESULTS:")
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {name}: {status}")

    if all(results.values()):
        print("\nAll tests passed! Hotkeys should work.")
    else:
        print("\nSome tests failed. See output above for details.")


if __name__ == "__main__":
    main()
