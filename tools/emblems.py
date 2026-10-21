"""Emblèmes orange possibles pour le logo d'Aria Pilote, sous forme de distances signées.

Chaque emblème est une fonction (x, y) -> distance au bord de la forme (négative à
l'intérieur), en coordonnées normalisées de 0 à 1, centrée sous l'apex du « A ».
"""

import math

CX, CY = 0.5, 0.64


def seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def capsule(px, py, a, b, r):
    return seg(px, py, *a, *b) - r


def circle(px, py, c, r):
    return math.hypot(px - c[0], py - c[1]) - r


def ring(px, py, c, r, w):
    return abs(math.hypot(px - c[0], py - c[1]) - r) - w


def box(px, py, c, half_w, half_h, radius=0.0):
    qx = abs(px - c[0]) - (half_w - radius)
    qy = abs(py - c[1]) - (half_h - radius)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius


def polygon(px, py, pts):
    """Distance signée à un polygone quelconque (méthode d'Inigo Quilez)."""
    d = (px - pts[0][0]) ** 2 + (py - pts[0][1]) ** 2
    sign = 1.0
    j = len(pts) - 1
    for i in range(len(pts)):
        ex, ey = pts[j][0] - pts[i][0], pts[j][1] - pts[i][1]
        wx, wy = px - pts[i][0], py - pts[i][1]
        t = max(0.0, min(1.0, (wx * ex + wy * ey) / (ex * ex + ey * ey)))
        bx, by = wx - ex * t, wy - ey * t
        d = min(d, bx * bx + by * by)
        c1, c2, c3 = py >= pts[i][1], py < pts[j][1], ex * wy > ey * wx
        if (c1 and c2 and c3) or (not c1 and not c2 and not c3):
            sign = -sign
        j = i
    return sign * math.sqrt(d)


def at(dx, dy):
    return (CX + dx, CY + dy)


# --- les emblèmes -------------------------------------------------------------------

def barre(px, py):
    """Barre de navire."""
    d = min(ring(px, py, at(0, 0), 0.085, 0.017), circle(px, py, at(0, 0), 0.028))
    for k in range(8):
        a = k * math.pi / 4
        tip = at(math.cos(a) * 0.135, math.sin(a) * 0.135)
        d = min(d, capsule(px, py, at(0, 0), tip, 0.011), circle(px, py, tip, 0.019))
    return d


def boussole(px, py):
    """Boussole : rose des vents dans un anneau."""
    star = [at(0, -0.135), at(0.03, -0.03), at(0.135, 0), at(0.03, 0.03),
            at(0, 0.135), at(-0.03, 0.03), at(-0.135, 0), at(-0.03, -0.03)]
    return min(polygon(px, py, star), ring(px, py, at(0, 0), 0.095, 0.011))


def manche(px, py):
    """Manche à balai d'avion de chasse."""
    return min(
        box(px, py, at(0, 0.105), 0.1, 0.03, 0.015),
        capsule(px, py, at(0, 0.08), at(0.025, -0.05), 0.022),
        circle(px, py, at(0.03, -0.085), 0.042),
    )


def volant(px, py):
    """Volant d'avion (yoke) et sa colonne."""
    return min(
        capsule(px, py, at(-0.115, -0.07), at(-0.115, 0.02), 0.024),
        capsule(px, py, at(0.115, -0.07), at(0.115, 0.02), 0.024),
        capsule(px, py, at(-0.115, 0.02), at(0.115, 0.02), 0.02),
        capsule(px, py, at(0, 0.02), at(0, 0.13), 0.022),
        circle(px, py, at(0, 0.02), 0.035),
    )


def helice(px, py):
    """Hélice à trois pales."""
    d = circle(px, py, at(0, 0), 0.035)
    for deg in (-90, 30, 150):
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)
        # Pale en goutte : large au milieu, fine au bout
        d = min(d, capsule(px, py, at(ca * 0.03, sa * 0.03), at(ca * 0.09, sa * 0.09), 0.033))
        d = min(d, capsule(px, py, at(ca * 0.09, sa * 0.09), at(ca * 0.135, sa * 0.135), 0.018))
    return d


def avion(px, py):
    """Avion à réaction vu de dessus."""
    right = [at(0.02, -0.09), at(0.02, -0.025), at(0.14, 0.035), at(0.14, 0.06),
             at(0.02, 0.04), at(0.017, 0.095), at(0.058, 0.12), at(0.058, 0.14)]
    left = [(2 * CX - x, y) for x, y in reversed(right)]
    return polygon(px, py, [at(0, -0.14)] + right + [at(0, 0.125)] + left)


def navigation(px, py):
    """Flèche de navigation (GPS)."""
    return polygon(px, py, [at(0, -0.135), at(0.11, 0.12), at(0, 0.065), at(-0.11, 0.12)])


def radar(px, py):
    """Écran radar avec son balayage."""
    sweep = [at(0, 0)] + [at(math.cos(math.radians(a)) * 0.115, math.sin(math.radians(a)) * 0.115)
                          for a in range(-90, -19, 10)]
    return min(
        ring(px, py, at(0, 0), 0.115, 0.012),
        ring(px, py, at(0, 0), 0.065, 0.008),
        polygon(px, py, sweep),
        circle(px, py, at(0, 0), 0.018),
    )


def reticule(px, py):
    """Réticule de visée HUD."""
    d = min(ring(px, py, at(0, 0), 0.085, 0.014), circle(px, py, at(0, 0), 0.016))
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        d = min(d, capsule(px, py, at(dx * 0.055, dy * 0.055), at(dx * 0.14, dy * 0.14), 0.012))
    return d


def ailes(px, py):
    """Insigne de pilote : deux ailes autour d'un écusson."""
    d = circle(px, py, at(0, 0), 0.04)
    for side in (-1, 1):
        for i, (y0, x1, y1, r) in enumerate(((-0.01, 0.15, -0.045, 0.016), (0.015, 0.13, 0.0, 0.014), (0.04, 0.1, 0.04, 0.012))):
            d = min(d, capsule(px, py, at(side * 0.045, y0), at(side * x1, y1), r))
    return d


def terminal(px, py):
    """Invite de commande « >_ » : l'agent aux commandes."""
    return min(
        capsule(px, py, at(-0.1, -0.055), at(-0.025, 0), 0.02),
        capsule(px, py, at(-0.025, 0), at(-0.1, 0.055), 0.02),
        capsule(px, py, at(0.015, 0.065), at(0.11, 0.065), 0.02),
    )


EMBLEMS = {
    "barre": barre,
    "boussole": boussole,
    "manche": manche,
    "volant": volant,
    "helice": helice,
    "avion": avion,
    "navigation": navigation,
    "radar": radar,
    "reticule": reticule,
    "ailes": ailes,
    "terminal": terminal,
}
