#!/usr/bin/env python3
"""Generate the VoicePaste application icon (.ico) for PyInstaller.

Uses the shared icon_drawing module to render the microphone icon at
multiple resolutions and saves it as assets/app.ico.

Each size is rendered independently at its native resolution rather than
downscaling from the largest size. This produces sharper results at small
sizes (16x16, 32x32) where the microphone detail would otherwise be lost.

The .ico badge uses a high-contrast blue background (not the dark grey used
for the runtime tray icon) because the dark grey is nearly invisible against
the Windows 11 dark taskbar. The blue reads clearly in Explorer, the
taskbar, Alt+Tab, and the Start menu in both light and dark Windows themes.

The ICO file uses BMP-encoded entries for small sizes (16-48) and PNG for
256x256. This is the standard Windows ICO format and provides maximum
compatibility with PyInstaller's PE resource embedding and Windows shell.

Usage:
    python assets/generate_icons.py

Output:
    assets/app.ico  (multi-resolution: 16x16, 32x32, 48x48, 256x256)
"""

import io
import struct
import sys
from pathlib import Path

# Add src/ to path so we can import icon_drawing
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root / "src"))

from icon_drawing import ICON_BADGE_COLOR, create_icon_image  # noqa: E402

# Icon sizes to include in the .ico file
_ICO_SIZES = [16, 32, 48, 256]

# White microphone on blue badge for the static exe/taskbar icon
_ICON_COLOR = (255, 255, 255)


def _rgba_to_bmp_entry(img) -> bytes:
    """Convert an RGBA Pillow image to a BMP-encoded ICO entry.

    ICO BMP entries use:
    - BITMAPINFOHEADER (40 bytes) with doubled height (XOR + AND masks)
    - Raw BGRA pixel data, bottom-up row order (XOR mask)
    - 1bpp AND mask (transparency), bottom-up, padded to 4-byte rows

    Args:
        img: Pillow RGBA Image.

    Returns:
        bytes: Complete BMP data for one ICO directory entry.
    """
    w, h = img.size
    pixels = img.load()

    # BITMAPINFOHEADER (40 bytes)
    # biHeight is doubled: h for XOR mask + h for AND mask
    header = struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        w,           # biWidth
        h * 2,       # biHeight (doubled for XOR+AND)
        1,           # biPlanes
        32,          # biBitCount (BGRA)
        0,           # biCompression (BI_RGB)
        0,           # biSizeImage (can be 0 for BI_RGB)
        0,           # biXPelsPerMeter
        0,           # biYPelsPerMeter
        0,           # biClrUsed
        0,           # biClrImportant
    )

    # XOR mask: BGRA pixel data, bottom-up
    xor_data = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            xor_data.extend([b, g, r, a])

    # AND mask: 1bpp transparency, bottom-up, rows padded to 4 bytes
    and_row_bytes = (w + 31) // 32 * 4  # Pad each row to 4-byte boundary
    and_data = bytearray()
    for y in range(h - 1, -1, -1):
        row = bytearray(and_row_bytes)
        for x in range(w):
            _, _, _, a = pixels[x, y]
            if a < 128:
                # Transparent pixel: set bit to 1
                byte_idx = x // 8
                bit_idx = 7 - (x % 8)
                row[byte_idx] |= (1 << bit_idx)
        and_data.extend(row)

    return header + bytes(xor_data) + bytes(and_data)


def _build_ico(images: list) -> bytes:
    """Build a multi-resolution .ico file from Pillow RGBA images.

    Uses BMP format for sizes <= 48 and PNG for 256x256.
    This is the standard Windows ICO format and provides maximum
    compatibility with PyInstaller and the Windows shell.

    ICO format:
    - 6-byte header: reserved(2), type(2)=1, count(2)
    - N x 16-byte directory entries
    - N x image data blobs (BMP or PNG)
    """
    count = len(images)
    blobs = []
    for img in images:
        if img.width >= 256:
            # PNG for large sizes (much smaller file size)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            blobs.append(buf.getvalue())
        else:
            # BMP for small sizes (maximum compatibility)
            blobs.append(_rgba_to_bmp_entry(img))

    # ICO header
    header = struct.pack("<HHH", 0, 1, count)

    # Directory entries
    dir_size = 16 * count
    data_offset = 6 + dir_size

    directory = b""
    for i, img in enumerate(images):
        w = img.width if img.width < 256 else 0  # 0 means 256
        h = img.height if img.height < 256 else 0
        blob_size = len(blobs[i])
        offset = data_offset + sum(len(blobs[j]) for j in range(i))
        directory += struct.pack(
            "<BBBBHHII", w, h, 0, 0, 1, 32, blob_size, offset
        )

    return header + directory + b"".join(blobs)


def main() -> None:
    output_path = _project_root / "assets" / "app.ico"

    # Render each size independently for maximum sharpness
    images = []
    for size in _ICO_SIZES:
        img = create_icon_image(
            size=size,
            color=_ICON_COLOR,
            bg_color=ICON_BADGE_COLOR,
            mode="RGBA",
        )
        images.append(img)
        fmt = "PNG" if size >= 256 else "BMP"
        print(f"  Rendered {size}x{size} RGBA -> {fmt}")

    # Build ICO with BMP for small sizes, PNG for 256
    ico_data = _build_ico(images)
    output_path.write_bytes(ico_data)

    file_size = output_path.stat().st_size
    print(f"  Saved {output_path} ({file_size:,} bytes, {len(_ICO_SIZES)} sizes)")


if __name__ == "__main__":
    print("[generate_icons] Generating VoicePaste application icon...")
    main()
    print("[generate_icons] Done.")
