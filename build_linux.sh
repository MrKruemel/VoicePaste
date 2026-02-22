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
#       sudo apt install wl-clipboard  # Wayland clipboard (wl-copy/wl-paste)
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

# Check wl-clipboard (for Wayland clipboard support)
if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]] || true; then
    if ! command -v wl-copy &>/dev/null; then
        echo "[INFO] wl-clipboard not found. Install for native Wayland clipboard support:"
        echo "       sudo apt install wl-clipboard"
        echo "       (xclip will be used as XWayland fallback if available)"
        echo ""
    else
        echo "[OK] wl-clipboard found (wl-copy / wl-paste)."
    fi
fi

# Check /dev/uinput access (for Wayland paste simulation via evdev UInput)
if [[ -e /dev/uinput ]]; then
    if [[ -w /dev/uinput ]]; then
        echo "[OK] /dev/uinput writable (evdev UInput paste available)."
    else
        echo "[INFO] /dev/uinput not writable. Wayland paste will fall back to ydotool/wtype."
        echo "       For native paste support (no external tools needed), run:"
        echo "       echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | sudo tee /etc/udev/rules.d/99-voicepaste-uinput.rules"
        echo "       sudo udevadm control --reload-rules && sudo udevadm trigger"
        echo ""
    fi
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

# -- Copy config example and desktop integration files --
echo "[4/4] Copying dist assets..."
if [[ -f config.example.toml ]]; then
    cp config.example.toml dist/config.example.toml
    echo "       dist/config.example.toml copied."
else
    echo "[WARN] config.example.toml not found. Skipping."
fi

# Copy app icon (PNG) for desktop integration
if [[ -f assets/app.png ]]; then
    cp assets/app.png dist/VoicePaste.png
    echo "       dist/VoicePaste.png copied."
else
    echo "[WARN] assets/app.png not found. Generating..."
    python3 -c "
import sys; sys.path.insert(0, 'src')
from icon_drawing import ICON_BADGE_COLOR, create_icon_image
img = create_icon_image(size=256, color=(255,255,255), bg_color=ICON_BADGE_COLOR, mode='RGBA')
img.save('dist/VoicePaste.png', format='PNG')
print('       dist/VoicePaste.png generated.')
" || echo "[WARN] Could not generate icon."
fi

# Create .desktop file with correct path
DIST_DIR="$(cd dist && pwd)"
cat > dist/VoicePaste.desktop <<DESKTOP_EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=VoicePaste
GenericName=Voice to Text
Comment=Record speech, transcribe, summarize, and paste at cursor
Exec=${DIST_DIR}/VoicePaste
Icon=${DIST_DIR}/VoicePaste.png
Terminal=false
Categories=Audio;Utility;
Keywords=voice;speech;transcription;whisper;paste;
StartupNotify=false
DESKTOP_EOF
echo "       dist/VoicePaste.desktop created."

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
echo "    1. Copy dist/VoicePaste + dist/VoicePaste.png to your desired location."
echo "    2. Copy dist/config.example.toml to config.toml next to the binary."
echo "    3. Edit config.toml and add your API keys."
echo "    4. chmod +x VoicePaste && ./VoicePaste"
echo ""
echo "  Desktop integration (optional):"
echo "    cp dist/VoicePaste.desktop ~/.local/share/applications/"
echo "    # Edit Exec= and Icon= paths in the .desktop file if you moved the binary"
echo ""
echo "  System dependencies:"
echo "    sudo apt install espeak-ng libportaudio2 xclip xdotool python3-tk"
echo "    sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1"
echo "    sudo apt install gnome-shell-extension-appindicator  # GNOME only"
echo "    sudo apt install wl-clipboard  # Wayland clipboard (wl-copy/wl-paste)"
echo ""
echo "  Wayland paste support (choose one):"
echo "    Option 1 (recommended): evdev UInput (no extra packages)"
echo "      sudo usermod -aG input \$USER  # then logout/login"
echo "      echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | sudo tee /etc/udev/rules.d/99-voicepaste-uinput.rules"
echo "      sudo udevadm control --reload-rules && sudo udevadm trigger"
echo "    Option 2: ydotool"
echo "      sudo apt install ydotool"
echo "======================================================================"
echo ""
