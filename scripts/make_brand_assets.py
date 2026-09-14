"""Generate the Home Assistant brand images for this integration.

Home Assistant wants local brand images at
``custom_components/<domain>/brand/`` (see the "Brand images" section of the
integration file structure documentation). Committing a generator keeps those
binary assets reproducible instead of mysterious, and avoids pulling in an image
library just to draw three rectangles and a triangle.

Usage::

    python scripts/make_brand_assets.py

Requires nothing but the standard library (PNG is written by hand with zlib).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

# Home Assistant blue, top to bottom.
TOP_COLOR = (41, 182, 246)
BOTTOM_COLOR = (2, 119, 189)

#: Supersampling factor: the shape is drawn this much larger and then box
#: filtered down, which gives clean anti-aliased edges.
SUPERSAMPLE = 4

BRAND_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "generic_video_proxy" / "brand"
)


def write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    """Write an RGBA buffer as a PNG file."""
    raw = bytearray()
    stride = width * 4
    for row in range(height):
        raw.append(0)  # filter type: none
        raw.extend(pixels[row * stride : (row + 1) * stride])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def _inside_rounded_rect(x: float, y: float, size: float, radius: float) -> bool:
    if x < 0 or y < 0 or x > size or y > size:
        return False
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def _inside_triangle(
    x: float, y: float, a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> bool:
    def sign(p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]) -> float:
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])

    d1 = sign((x, y), a, b)
    d2 = sign((x, y), b, c)
    d3 = sign((x, y), c, a)
    has_negative = d1 < 0 or d2 < 0 or d3 < 0
    has_positive = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_negative and has_positive)


def _blend(dst: list[int], src: tuple[int, int, int, int]) -> None:
    """Alpha-composite ``src`` over the pixel in ``dst``."""
    alpha = src[3] / 255
    if alpha <= 0:
        return
    for index in range(3):
        dst[index] = round(src[index] * alpha + dst[index] * (1 - alpha))
    dst[3] = max(dst[3], src[3])


def render_mark(size: int, *, transparent: bool) -> list[list[int]]:
    """Render the square logo mark at ``size`` pixels, RGBA per pixel."""
    big = size * SUPERSAMPLE
    radius = big * 0.22
    # A play triangle, optically centred in the square.
    tri = (
        (big * 0.38, big * 0.29),
        (big * 0.38, big * 0.71),
        (big * 0.74, big * 0.50),
    )

    high = [[0, 0, 0, 0] for _ in range(big * big)]
    for y in range(big):
        ratio = y / (big - 1)
        background = (
            round(TOP_COLOR[0] + (BOTTOM_COLOR[0] - TOP_COLOR[0]) * ratio),
            round(TOP_COLOR[1] + (BOTTOM_COLOR[1] - TOP_COLOR[1]) * ratio),
            round(TOP_COLOR[2] + (BOTTOM_COLOR[2] - TOP_COLOR[2]) * ratio),
        )
        for x in range(big):
            pixel = high[y * big + x]
            if not transparent and _inside_rounded_rect(x + 0.5, y + 0.5, big, radius):
                _blend(pixel, (*background, 255))
            if _inside_triangle(x + 0.5, y + 0.5, *tri):
                # Keep the glyph inside the rounded square on transparent assets.
                if transparent and not _inside_rounded_rect(x + 0.5, y + 0.5, big, radius):
                    continue
                _blend(pixel, (255, 255, 255, 255))

    # Box filter down to the target size.
    pixels = [[0, 0, 0, 0] for _ in range(size * size)]
    samples = SUPERSAMPLE * SUPERSAMPLE
    for y in range(size):
        for x in range(size):
            total = [0, 0, 0, 0]
            for dy in range(SUPERSAMPLE):
                row = (y * SUPERSAMPLE + dy) * big
                for dx in range(SUPERSAMPLE):
                    sample = high[row + x * SUPERSAMPLE + dx]
                    for index in range(4):
                        total[index] += sample[index]
            pixels[y * size + x] = [round(value / samples) for value in total]
    return pixels


def flatten(pixels: list[list[int]]) -> bytearray:
    """Flatten an RGBA pixel list into a byte buffer."""
    buffer = bytearray()
    for pixel in pixels:
        buffer.extend(pixel)
    return buffer


def write_square(path: Path, size: int, *, transparent: bool) -> None:
    """Write a square mark."""
    write_png(path, size, size, flatten(render_mark(size, transparent=transparent)))


def write_logo(path: Path, width: int, height: int) -> None:
    """Write a landscape logo: the mark centred on a transparent canvas."""
    mark_size = min(width, height)
    mark = render_mark(mark_size, transparent=True)
    pixels = [[0, 0, 0, 0] for _ in range(width * height)]
    offset_x = (width - mark_size) // 2
    offset_y = (height - mark_size) // 2
    for y in range(mark_size):
        for x in range(mark_size):
            source = mark[y * mark_size + x]
            if source[3] == 0:
                continue
            target = pixels[(y + offset_y) * width + x + offset_x]
            _blend(target, tuple(source))
    write_png(path, width, height, flatten(pixels))


def main() -> None:
    """Generate every brand image.

    No ``dark_*`` variants are produced: the mark carries its own coloured
    background, so it reads the same on light and dark themes.
    """
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    write_square(BRAND_DIR / "icon.png", 256, transparent=False)
    write_square(BRAND_DIR / "icon@2x.png", 512, transparent=False)
    write_logo(BRAND_DIR / "logo.png", 512, 256)
    write_logo(BRAND_DIR / "logo@2x.png", 1024, 512)
    for file in sorted(BRAND_DIR.iterdir()):
        print(f"{file.name}: {file.stat().st_size} bytes")


if __name__ == "__main__":
    main()
