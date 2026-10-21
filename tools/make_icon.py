"""Dessine le logo d'Aria Pilote dans le gabarit de la famille Aria, sans dépendance.

Octogone bleu nuit à lignes de balayage et liseré bleu roi, « A » néon glitché
(haut blanc, jambe gauche cyan, jambe droite bleu roi) et l'emblème orange de
l'appli : une barre de navire.

Usage : python tools/make_icon.py DOSSIER_ICO [DOSSIER_FAVICON] [CHEMIN_LOGO_PNG]
"""

import math
import struct
import sys
import zlib
from pathlib import Path

NAVY = (10, 17, 34)
SCANLINE = (16, 26, 50)
BEZEL = (5, 9, 20)
ROYAL = (61, 107, 255)
WHITE = (242, 246, 255)
CYAN = (94, 232, 255)
ORANGE = (255, 138, 31)

# Contour du « A » (sans barre, comme dans la famille) : jambes et chevron intérieur
A_PATH = [(0.21, 0.83), (0.43, 0.17), (0.57, 0.17), (0.79, 0.83), (0.66, 0.83), (0.50, 0.37), (0.34, 0.83)]
GLITCH_Y = 0.46  # sous cette ligne, les jambes sont décalées
STROKE = 0.028

# Barre de navire : centre, rayon de la roue, rayon des poignées
WHEEL_C = (0.5, 0.64)
WHEEL_R = 0.085
SPOKE_R = 0.135


def _seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _clip(a, b, y0, y1):
    """Partie du segment ab comprise entre les ordonnées y0 et y1 (ou None)."""
    (ax, ay), (bx, by) = a, b
    if ay == by:
        return (a, b) if y0 <= ay <= y1 else None
    t0, t1 = sorted(((y0 - ay) / (by - ay), (y1 - ay) / (by - ay)))
    t0, t1 = max(t0, 0.0), min(t1, 1.0)
    if t0 >= t1:
        return None
    return ((ax + t0 * (bx - ax), ay + t0 * (by - ay)), (ax + t1 * (bx - ax), ay + t1 * (by - ay)))


def _glitch_parts():
    """Trois morceaux du « A » : (segments, décalage x, couleur, y0, y1)."""
    pts = A_PATH + A_PATH[:1]
    top, left, right = [], [], []
    for i in range(len(A_PATH)):
        seg = _clip(pts[i], pts[i + 1], 0.0, GLITCH_Y)
        if seg:
            top.append(seg)
        seg = _clip(pts[i], pts[i + 1], GLITCH_Y, 1.0)
        if seg:
            (left if (seg[0][0] + seg[1][0]) / 2 < 0.5 else right).append(seg)
    return [(top, 0.012, WHITE, 0.0, GLITCH_Y), (left, -0.045, CYAN, GLITCH_Y, 1.0), (right, 0.03, ROYAL, GLITCH_Y, 1.0)]


PARTS = _glitch_parts()


def _octagon(px, py, half=0.47, cut=0.77):
    x, y = abs(px - 0.5), abs(py - 0.5)
    return max(max(x, y) - half, (x + y - cut) / math.sqrt(2))


def _wheel(px, py):
    cx, cy = WHEEL_C
    dx, dy = px - cx, py - cy
    r = math.hypot(dx, dy)
    ring = abs(r - WHEEL_R) - 0.017
    hub = r - 0.028
    d = min(ring, hub)
    for k in range(8):
        a = k * math.pi / 4
        ex, ey = math.cos(a), math.sin(a)
        d = min(d, _seg(px, py, cx, cy, cx + ex * SPOKE_R, cy + ey * SPOKE_R) - 0.011)
        d = min(d, math.hypot(px - (cx + ex * SPOKE_R), py - (cy + ey * SPOKE_R)) - 0.019)
    return d


def _blend(dst, color, alpha):
    alpha = max(0.0, min(1.0, alpha))
    return tuple(d + (c - d) * alpha for d, c in zip(dst, color))


def _cover(dist, px_unit):
    return max(0.0, min(1.0, 0.5 - dist / px_unit))


def render(size):
    unit = 1.0 / size
    rows = []
    for y in range(size):
        row = bytearray([0])  # filtre PNG « None »
        for x in range(size):
            u, v = (x + 0.5) / size, (y + 0.5) / size
            octo = _octagon(u, v)
            alpha = _cover(octo, unit)
            # Fond bleu nuit et lignes de balayage horizontales
            color = SCANLINE if (v * 96) % 1 < 0.42 else NAVY
            # Biseau sombre au bord, puis liseré bleu roi intérieur
            color = _blend(color, BEZEL, _cover(-(octo + 0.03), unit) * 0 + (1 - _cover(octo + 0.03, unit)))
            color = _blend(color, ROYAL, _cover(abs(octo + 0.045) - 0.006, unit) * 0.9)
            # « A » glitché : trois morceaux décalés, coupés net, chacun avec sa lueur douce
            for segments, shift, tint, y0, y1 in PARTS:
                line = min(_seg(u - shift, v, *a, *b) for a, b in segments) - STROKE / 2
                color = _blend(color, tint, 0.5 * math.exp(-(max(line, 0) / 0.035) ** 2))
                inside = min(_cover(y0 - v, unit), _cover(v - y1, unit))  # coupe horizontale franche
                color = _blend(color, tint, _cover(line, unit) * inside)
            # Emblème orange, avec un léger halo
            wheel = _wheel(u, v)
            color = _blend(color, ORANGE, 0.35 * math.exp(-(max(wheel, 0) / 0.03) ** 2))
            color = _blend(color, ORANGE, _cover(wheel, unit))
            row += bytes(int(round(c)) for c in color) + bytes([int(round(255 * alpha))])
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
    if len(sys.argv) > 3:
        Path(sys.argv[3]).parent.mkdir(parents=True, exist_ok=True)
        Path(sys.argv[3]).write_bytes(png(512))
    print(f"Icône écrite dans {ico_dir / 'aria.ico'}")


if __name__ == "__main__":
    main()
