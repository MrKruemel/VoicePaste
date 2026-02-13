# =============================================================================
# PyInstaller Runtime Hook: onnxruntime DLL search path setup
# =============================================================================
#
# This hook runs BEFORE any user code at application startup.  It adds the
# onnxruntime/capi subdirectory inside the PyInstaller temp directory to the
# Windows DLL search path.  Without this, onnxruntime_pybind11_state.pyd
# may fail to locate onnxruntime.dll when running inside a --onefile bundle.
#
# Background:
#   Starting with Python 3.8 on Windows, DLL search paths were restricted.
#   The only directories automatically searched are:
#     1. Directories added via os.add_dll_directory()
#     2. The application directory (for PyInstaller: the _MEI* root)
#     3. System directories (System32, etc.)
#
#   PyInstaller's bootloader adds the _MEI root to the DLL search path,
#   but it does NOT add subdirectories like _MEI/onnxruntime/capi/.
#   Since onnxruntime.dll lives in onnxruntime/capi/, it may not be found
#   when Windows tries to load dependencies of the .pyd extension.
#
#   Additionally, ctranslate2 bundles libiomp5md.dll (Intel OpenMP) which
#   may conflict with onnxruntime's thread pool.  Setting OMP_NUM_THREADS=1
#   and OMP_WAIT_POLICY=PASSIVE prevents OpenMP from aggressively spinning
#   up threads that could interfere with ORT's own thread management.
#
# References:
#   - https://github.com/microsoft/onnxruntime/issues/25193
#   - https://github.com/pyinstaller/pyinstaller/issues/8083
# =============================================================================

import os
import sys

if getattr(sys, "frozen", False):
    _meipass = getattr(sys, "_MEIPASS", None)
    if _meipass and os.path.isdir(_meipass):
        # --- Add onnxruntime/capi to DLL search path ---
        _ort_capi_dir = os.path.join(_meipass, "onnxruntime", "capi")
        if os.path.isdir(_ort_capi_dir):
            try:
                os.add_dll_directory(_ort_capi_dir)
            except (OSError, AttributeError):
                # os.add_dll_directory requires Python 3.8+ and may fail
                # if the directory is already added or inaccessible.
                pass

        # --- Add ctranslate2 to DLL search path ---
        _ct2_dir = os.path.join(_meipass, "ctranslate2")
        if os.path.isdir(_ct2_dir):
            try:
                os.add_dll_directory(_ct2_dir)
            except (OSError, AttributeError):
                pass

        # --- Prevent OpenMP thread-pool conflicts ---
        # ctranslate2 ships libiomp5md.dll (Intel OpenMP).  When both
        # ctranslate2 and onnxruntime are loaded in the same process,
        # the OpenMP runtime may spawn background threads that interfere
        # with onnxruntime's internal thread pool, causing segfaults
        # during inference.
        #
        # Setting OMP_NUM_THREADS=1 limits OpenMP to a single thread,
        # and OMP_WAIT_POLICY=PASSIVE prevents busy-waiting spinloops.
        # These have negligible performance impact because:
        #   - onnxruntime uses its own MLAS thread pool, not OpenMP.
        #   - ctranslate2 manages its own threading via config.
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
