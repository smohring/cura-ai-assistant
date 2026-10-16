"""Small closed test meshes as (n, 3, 3) triangle arrays, outward-facing, printer coordinates."""

from __future__ import annotations

import numpy as np


def _ear_clip(poly: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangulate a simple counter-clockwise polygon."""
    idx = list(range(len(poly)))
    tris = []

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= 0 and cross(b, c, p) >= 0 and cross(c, a, p) >= 0

    while len(idx) > 3:
        for k in range(len(idx)):
            i, j, m = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[i], poly[j], poly[m]
            if cross(a, b, c) <= 0:
                continue
            if any(inside(poly[o], a, b, c) for o in idx if o not in (i, j, m)):
                continue
            tris.append((i, j, m))
            idx.remove(j)
            break
        else:
            raise ValueError("polygon is not simple or not counter-clockwise")
    tris.append(tuple(idx))
    return tris


def prism(poly: list[tuple[float, float]], z0: float, z1: float) -> np.ndarray:
    """Extrude a counter-clockwise polygon in XY from z0 to z1."""
    tris = []
    for i, j, k in _ear_clip(poly):
        tris.append([(*poly[i], z1), (*poly[j], z1), (*poly[k], z1)])
        tris.append([(*poly[k], z0), (*poly[j], z0), (*poly[i], z0)])
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        tris.append([(*a, z0), (*b, z0), (*b, z1)])
        tris.append([(*a, z0), (*b, z1), (*a, z1)])
    return np.array(tris, dtype=float)


def cube(size: float = 20.0) -> np.ndarray:
    return prism([(0, 0), (size, 0), (size, size), (0, size)], 0, size)


def profile_part(profile_xz: list[tuple[float, float]], depth: float) -> np.ndarray:
    """A part whose counter-clockwise profile lies in the XZ plane, extruded along +Y."""
    m = prism(profile_xz, 0, depth)  # profile in (x, y_old), extruded along z_old
    # (x, y_old, z_old) -> (x, z_old, y_old); the axis swap flips handedness, so reverse windings
    out = m[:, :, [0, 2, 1]]
    return out[:, ::-1, :].copy()


def l_bracket() -> np.ndarray:
    """40 high, 40 long, 10 thick arms, 20 deep. Profile in XZ, extruded along Y."""
    return profile_part([(0, 0), (40, 0), (40, 10), (10, 10), (10, 40), (0, 40)], 20)


def t_shape() -> np.ndarray:
    """Upright T: 40 wide bar (z 30..40) on a 10 wide stem (z 0..30), 20 deep."""
    pts = [(15, 0), (25, 0), (25, 30), (40, 30), (40, 40), (0, 40), (0, 30), (15, 30)]
    return profile_part(pts, 20)


def write_ascii_stl(path, tris: np.ndarray) -> None:
    lines = ["solid t"]
    for t in tris:
        lines.append("facet normal 0 0 0\n outer loop")
        lines += [f"  vertex {v[0]} {v[1]} {v[2]}" for v in t]
        lines.append(" endloop\nendfacet")
    lines.append("endsolid t")
    with open(path, "w") as f:
        f.write("\n".join(lines))
