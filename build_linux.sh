#!/usr/bin/env bash
# ==========================================================================
# Build script for VoicePaste -- Linux (Ubuntu 22.04 / 24.04)
# ==========================================================================
#
# Usage:
#   ./build_linux.sh              Build release binary (stripped, no console)
#   ./build_linux.sh release      Build release binary (same as above)
#   ./build_linux.sh debug        Build debug binary (console, symbols)
#   ./build_linux.sh clean        Remove build artifacts
#
# Prerequisites:
#   - Python 3.11+ with pip in a venv (system Python may block pip installs)
#   - The venv MUST use --system-site-packages so PyGObject (gi) and
#     AppIndicator3 are available. These are system packages (cannot pip install).
#     Without them, pystray falls back to _xorg backend and the tray right-click
#     menu does not work.
#
#     Build venv setup:
#       python3 -m venv --system-site-packages .venv
#       source .venv/bin/activate
#       pip install -r requirements.txt pyinstaller pynput evdev
#
#   - pynput is the Linux hotkey backend for X11. It is NOT in requirements.txt
#     (Windows uses the 'keyboard' library instead). If pynput is missing from
#     the build environment, the binary will fail at startup with:
#       "No module named 'pynput'" / "Could not register the hotkey"
#   - evdev is the Linux hotkey backend for Wayland. It is NOT in requirements.txt.
#     If missing, Wayland hotkey support will be unavailable, but X11 still works.
#   - System packages:
#       sudo apt install espeak-ng libportaudio2 xclip xdotool python3-tk
#       sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1  # tray menu
#       sudo apt install gnome-shell-extension-appindicator  # tray icon (GNOME)
#
# Output:
#   dist/VoicePaste         (~80-140 MB portable binary)
#   dist/config.example.toml
# ==========================================================================

set -euo pipefail

# Project root = directory containing this script
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

BUILD_MODE="${1:-release}"

# ==========================================================================
# CLEAN
# ==========================================================================
if [[ "$BUILD_MODE" == "clean" ]]; then
    echo ""
    echo "======================================================================"
    echo "  VoicePaste Clean"
    echo "======================================================================"
    echo ""

    CLEANED=0
    if [[ -d build ]]; then
        echo "  Removing build/..."
        rm -rf build
        ((CLEANED++)) || true
    fi
    if [[ -d dist ]]; then
        echo "  Removing dist/..."
        rm -rf dist
        ((CLEANED++)) || true
    fi

    # Clean __pycache__
    find src tests -type d -name __pycache__ -print -exec rm -rf {} + 2>/dev/null || true
    if [[ $CLEANED -eq 0 ]]; then
        echo "  Nothing to clean."
    else
        echo ""
        echo "  Cleaned $CLEANED directories."
    fi
    echo ""
    exit 0
fi

# ==========================================================================
# BUILD
# ==========================================================================
echo ""
echo "======================================================================"
echo "  VoicePaste Build ($BUILD_MODE) -- Linux"
echo "======================================================================"
echo ""

# -- Verify Python --
if ! python3 --version &>/dev/null; then
    echo "[ERROR] Python 3 not found. Install Python 3.11+ and try again."
    exit 1
fi
echo "[OK] $(python3 --version)"

# -- Verify PyInstaller --
if ! python3 -m PyInstaller --version &>/dev/null; then
    echo "[ERROR] PyInstaller not found. Install with: pip install pyinstaller"
    exit 1
fi
echo "[OK] PyInstaller $(python3 -m PyInstaller --version 2>&1)"

# -- Check pynput (Linux hotkey backend for X11, not in requirements.txt) --
echo ""
if ! python3 -c "import pynput" &>/dev/null; then
    echo "[ERROR] pynput not found in the Python environment."
    echo "        pynput is the Linux hotkey backend (X11) and MUST be installed for the build."
    echo "        Install with: pip install pynput"
    echo ""
    exit 1
fi
echo "[OK] pynput $(python3 -c 'from importlib.metadata import version; print(version("pynput"))' 2>/dev/null || echo '(version unknown)')"

# -- Check evdev (Linux hotkey backend for Wayland, not in requirements.txt) --
if ! python3 -c "import evdev" &>/dev/null; then
    echo "[INFO] evdev not found. Wayland hotkey support will NOT be available in the build."
    echo "       To enable Wayland support, install with: pip install evdev"
    echo ""
else
    echo "[OK] evdev $(python3 -c 'from importlib.metadata import version; print(version("evdev"))' 2>/dev/null || echo '(version unknown)')"
fi

# -- Check system dependencies --
echo ""
echo "[1/4] Checking system dependencies..."
MISSING_DEPS=()
for dep in espeak-ng xclip xdotool; do
    if ! command -v "$dep" &>/dev/null; then
        MISSING_DEPS+=("$dep")
    fi
done

# Check libportaudio
if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    if ! dpkg -s libportaudio2 &>/dev/null 2>&1; then
        MISSING_DEPS+=("libportaudio2")
    fi
fi

if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
    echo "[WARN] Missing system dependencies: ${MISSING_DEPS[*]}"
    echo "       Install with: sudo apt install ${MISSING_DEPS[*]}"
    echo "       Build will continue, but the binary may not work correctly."
    echo ""
else
    echo "       All system dependencies found."
fi

# -- Clean previous build --
echo "[2/4] Cleaning previous build..."
rm -rf build dist

# -- Run PyInstaller --
echo "[3/4] Running PyInstaller ($BUILD_MODE mode)..."
echo ""

SPEC_FILE="$PROJECT_DIR/voice_paste_linux.spec"
if [[ ! -f "$SPEC_FILE" ]]; then
    echo "[ERROR] $SPEC_FILE not found."
    exit 1
fi

if [[ "$BUILD_MODE" == "debug" ]]; then
    python3 -m PyInstaller "$SPEC_FILE" -- --debug
else
    python3 -m PyInstaller "$SPEC_FILE"
fi

# -- Verify output --
if [[ ! -f dist/VoicePaste ]]; then
    echo ""
    echo "[ERROR] dist/VoicePaste was not created. Build may have failed."
    exit 1
fi

# -- Copy config example --
echo "[4/4] Copying config.example.toml to dist/..."
if [[ -f config.example.toml ]]; then
    cp config.example.toml dist/config.example.toml
    echo "       dist/config.example.toml copied."
else
    echo "[WARN] config.example.toml not found. Skipping."
fi

# -- Report result --
echo ""
echo "======================================================================"
echo "  Output:  dist/VoicePaste"
SIZE_BYTES=$(stat --printf="%s" dist/VoicePaste 2>/dev/null || stat -f%z dist/VoicePaste 2>/dev/null || echo "?")
if [[ "$SIZE_BYTES" != "?" ]]; then
    SIZE_MB=$((SIZE_BYTES / 1048576))
    echo "  Size:    $SIZE_BYTES bytes (~${SIZE_MB} MB)"
fi
echo ""
echo "  To use:"
echo "    1. Copy dist/VoicePaste to your desired location."
echo "    2. Copy dist/config.example.toml to config.toml next to the binary."
echo "    3. Edit config.toml and add your API keys."
echo "    4. chmod +x VoicePaste && ./VoicePaste"
echo ""
echo "  System dependencies:"
echo "    sudo apt install espeak-ng libportaudio2 xclip xdotool python3-tk"
echo "    sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1"
echo "    sudo apt install gnome-shell-extension-appindicator  # GNOME only"
echo "======================================================================"
echo ""
