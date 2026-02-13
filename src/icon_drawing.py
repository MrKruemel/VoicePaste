"""Shared microphone icon drawing for tray, .ico generation, and UI.

Provides reusable Pillow drawing functions for the VoicePaste microphone
icon. Used by:
- src/tray.py (system tray icon at 32x32 RGB)
- assets/generate_icons.py (multi-resolution .ico at 16-256px RGBA)
"""

from PIL import Image, ImageDraw


# Tray icon background (dark neutral grey -- matches dark taskbar area)
ICON_BG_COLOR = (45, 45, 45)

# Application icon badge background (high-contrast blue for Explorer/taskbar).
# Dark grey (#2D2D2D) is nearly invisible against the Windows 11 dark taskbar
# (#1F1F1F to #2D2D2D). This blue provides strong contrast in both light and
# dark Windows themes, and reads as "voice/audio" at a glance.
ICON_BADGE_COLOR = (30, 100, 180)


def draw_microphone(
    draw: ImageDraw.ImageDraw,
    color: tuple[int, int, int],
    size: int,
) -> None:
    """Draw a microphone silhouette on the given ImageDraw canvas.

    The microphone consists of:
    - A rounded-rectangle body (capsule shape) in the upper portion.
    - A U-shaped cradle/arc below the body.
    - A vertical stand line from the cradle bottom.
    - A horizontal base line at the very bottom.

    All coordinates are computed relative to the icon size so the drawing
    scales to any resolution.

    Args:
        draw: Pillow ImageDraw instance to draw on.
        color: RGB tuple for the microphone color.
        size: The icon canvas size (width and height are equal).
    """
    lw = max(3, size // 16)

    # Microphone body (filled capsule)
    body_left = size * 0.30
    body_right = size * 0.70
    body_top = size * 0.08
    body_bottom = size * 0.55
    body_radius = (body_right - body_left) / 2

    draw.rounded_rectangle(
        [body_left, body_top, body_right, body_bottom],
        radius=body_radius,
        fill=color,
    )

    # U-shaped cradle arc
    arc_left = size * 0.20
    arc_right = size * 0.80
    arc_top = size * 0.30
    arc_bottom = size * 0.72

    draw.arc(
        [arc_left, arc_top, arc_right, arc_bottom],
        start=0,
        end=180,
        fill=color,
        width=lw,
    )

    # Vertical stand
    stand_x = size * 0.50
    stand_top = arc_bottom * 0.5 + size * 0.36
    stand_bottom = size * 0.85

    draw.line(
        [(stand_x, stand_top), (stand_x, stand_bottom)],
        fill=color,
        width=lw,
    )

    # Horizontal base
    base_left = size * 0.30
    base_right = size * 0.70
    base_y = stand_bottom

    draw.line(
        [(base_left, base_y), (base_right, base_y)],
        fill=color,
        width=lw,
    )


def create_icon_image(
    size: int = 32,
    color: tuple[int, int, int] = (220, 220, 230),
    bg_color: tuple[int, int, int] = ICON_BG_COLOR,
    mode: str = "RGB",
) -> Image.Image:
    """Create an icon image with a microphone silhouette.

    Args:
        size: Icon width and height in pixels.
        color: RGB tuple for the microphone foreground color.
        bg_color: RGB tuple for the background. Ignored if mode="RGBA"
            (transparent background with rounded-rect badge).
        mode: "RGB" for opaque (tray icon), "RGBA" for transparent (.ico).

    Returns:
        Pillow Image with the microphone icon.
    """
    if mode == "RGBA":
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        # Draw rounded-rect badge background
        border_radius = max(2, size // 6)
        draw.rounded_rectangle(
            [0, 0, size - 1, size - 1],
            radius=border_radius,
            fill=bg_color + (255,),
        )
    else:
        image = Image.new("RGB", (size, size), bg_color)
        draw = ImageDraw.Draw(image)
        # Subtle border for RGB mode
        border_color = (80, 80, 80)
        border_radius = max(2, size // 8)
        draw.rounded_rectangle(
            [1, 1, size - 2, size - 2],
            radius=border_radius,
            outline=border_color,
            width=2,
        )

    draw_microphone(draw, color, size)
    return image
