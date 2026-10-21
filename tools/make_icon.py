"""Dessine l'icône d'Aria Pilote (un « A » néon dans un anneau cyan → magenta), sans dépendance.

Produit un .ico multi-tailles (PNG intégrés) et un PNG pour le favicon de l'interface.
Usage : python tools/make_icon.py DOSSIER_ICO [DOSSIER_FAVICON]
"""

import math
import struct
import sys
import zlib
from pathlib import Path

BG = (13, 15, 23)
CYAN = (54, 226, 245)
MAGENTA = (255, 79, 216)

# Coordonnées normalisées (0 à 1) du « A »
SEGMENTS = [((0.33, 0.73), (0.5, 0.27)), ((0.5, 0.27), (0.67, 0.73)), ((0.40, 0.57), (0.60, 0.57))]


def _seg_dist(px, py, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _rounded_square(px, py, half=0.46, radius=0.16):
    qx, qy = abs(px - 0.5) - (half - radius), abs(py - 0.5) - (half - radius)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius


def _blend(dst, color, alpha):
    return tuple(d + (c - d) * alpha for d, c in zip(dst, color))


def render(size):
    px_unit = 1.0 / size
    rows = []
    for y in range(size):
        row = bytearray([0])  # filtre PNG « None »
        for x in range(size):
            u, v = (x + 0.5) / size, (y + 0.5) / size
            # Fond : carré arrondi, bord antialiasé
            box = _rounded_square(u, v)
            cover = max(0.0, min(1.0, 0.5 - box / px_unit))
            color = BG
            # Anneau en dégradé cyan → magenta, avec un halo
            ring = abs(math.hypot(u - 0.5, v - 0.5) - 0.36)
            angle = (math.atan2(v - 0.5, u - 0.5) + math.pi) / (2 * math.pi)
            mix = 0.5 - 0.5 * math.cos(angle * 2 * math.pi)
            ring_color = tuple(c + (m - c) * mix for c, m in zip(CYAN, MAGENTA))
            color = _blend(color, ring_color, 0.45 * math.exp(-(ring / 0.05) ** 2))
            color = _blend(color, ring_color, max(0.0, min(1.0, 0.5 - (ring - 0.022) / px_unit)))
            # Le « A », avec un halo cyan
            letter = min(_seg_dist(u, v, a, b) for a, b in SEGMENTS)
            color = _blend(color, CYAN, 0.5 * math.exp(-(letter / 0.06) ** 2))
            color = _blend(color, (225, 252, 255), max(0.0, min(1.0, 0.5 - (letter - 0.035) / px_unit)))
            row += bytes(int(round(c)) for c in color) + bytes([int(round(255 * cover))])
        rows.append(bytes(row))
    return b"".join(rows)


def png(size):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # RGBA 8 bits
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(render(size), 9)) + chunk(b"IEND", b"")


def ico(sizes=(256, 64, 48, 32, 16)):
    images = [png(s) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


def main():
    ico_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    ico_dir.mkdir(parents=True, exist_ok=True)
    (ico_dir / "aria.ico").write_bytes(ico())
    if len(sys.argv) > 2:
        (Path(sys.argv[2]) / "favicon.png").write_bytes(png(64))
    print(f"Icône écrite dans {ico_dir / 'aria.ico'}")


if __name__ == "__main__":
    main()
