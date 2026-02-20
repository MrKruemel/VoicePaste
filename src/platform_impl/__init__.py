"""Platform abstraction layer for VoicePaste.

Detects the current OS at import time and re-exports the correct
backend implementations.  On Windows the existing modules (paste.py,
winsound, ctypes.windll) are wrapped.  On Linux, equivalent
implementations use xclip/xdotool, fcntl, zenity, and sounddevice.

Usage::

    from platform_impl import clipboard_backup, clipboard_restore, paste_text
    from platform_impl import play_beep, show_fatal_error
    from platform_impl import acquire_single_instance_lock, release_single_instance_lock
"""

import sys

if sys.platform == "win32":
    from platform_impl._windows import (  # noqa: F401
        acquire_single_instance_lock,
        clipboard_backup,
        clipboard_restore,
        enable_debug_console,
        get_app_data_dir,
        get_cache_dir,
        paste_text,
        play_beep,
        register_key_press,
        release_single_instance_lock,
        send_key,
        show_fatal_error,
        unregister_key_hook,
    )
elif sys.platform == "linux":
    from platform_impl._linux import (  # noqa: F401
        acquire_single_instance_lock,
        clipboard_backup,
        clipboard_restore,
        enable_debug_console,
        get_app_data_dir,
        get_cache_dir,
        paste_text,
        play_beep,
        register_key_press,
        release_single_instance_lock,
        send_key,
        show_fatal_error,
        unregister_key_hook,
    )
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")
