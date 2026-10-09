"""Draw assets/studio.ico: three white bars on a blue rounded square. Standard library only.

Run from the project root: python dev/make_icon.py
"""
import struct
import zlib
from pathlib import Path

BLUE = (51, 85, 224)
WHITE = (255, 255, 255)
SIZES = (16, 24, 32, 48, 64, 256)
SAMPLES = 4  # per pixel side, for smooth edges
OUT = Path(__file__).resolve().parent.parent / "assets" / "studio.ico"


def in_rounded(x, y, left, top, right, bottom, radius):
    if not (left <= x <= right and top <= y <= bottom):
        return False
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def colour_at(x, y):
    """Colour and coverage of one point in a 64 x 64 drawing."""
    for left, top in ((14, 34), (27.5, 24), (41, 14)):
        if in_rounded(x, y, left, top, left + 9, 50, 2):
            return WHITE, 1
    if in_rounded(x, y, 0, 0, 64, 64, 14):
        return BLUE, 1
    return BLUE, 0


def png(size):
    rows = bytearray()
    scale = 64 / size
    for py in range(size):
        rows.append(0)
        for px in range(size):
            red = green = blue = alpha = 0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    colour, inside = colour_at((px + (sx + 0.5) / SAMPLES) * scale, (py + (sy + 0.5) / SAMPLES) * scale)
                    red += colour[0] * inside
                    green += colour[1] * inside
                    blue += colour[2] * inside
                    alpha += inside
            total = SAMPLES * SAMPLES
            if alpha:
                rows += bytes((round(red / alpha), round(green / alpha), round(blue / alpha), round(255 * alpha / total)))
            else:
                rows += bytes(4)

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


def main():
    images = [png(size) for size in SIZES]
    offset = 6 + 16 * len(images)
    directory = b""
    for size, image in zip(SIZES, images):
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + directory + b"".join(images))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
