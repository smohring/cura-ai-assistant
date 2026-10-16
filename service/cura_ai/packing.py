"""Dense placement of copies of a part on the build plate, on a raster of the real silhouettes.

Cura's own arranger works with convex hulls, so concave parts waste room. Here the footprint is
the actual top-down silhouette (so an L-bracket can nest into its neighbour), the plate is a mask
(its allowed rectangle minus Cura's disallowed areas and the parts already there), and a copy is
placed where the footprint fits entirely on free cells. Everything is in printer coordinates,
millimetres, centred on the plate.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel
from scipy.ndimage import binary_closing, binary_dilation, binary_fill_holes
from scipy.signal import fftconvolve

from cura_ai.mesh import normals_and_areas

MAX_COPIES = 500
STRATEGIES = ("bottom_left", "bottom_right", "top_left", "top_right", "centre")


class Plate(BaseModel):
    """The free area: an axis-aligned rectangle minus polygons (all [[x, y], ...])."""

    rect: list[float]  # xmin, ymin, xmax, ymax
    disallowed: list[list[list[float]]] = []


class Placement(BaseModel):
    rotation_deg: float  # about the vertical axis through the part's own bounding-box centre
    x: float  # where that centre ends up
    y: float


class PackResult(BaseModel):
    placements: list[Placement]
    requested: int | None
    strategy: str
    resolution_mm: float


def _disk(radius_px: int) -> np.ndarray:
    r = np.arange(-radius_px, radius_px + 1)
    return np.hypot(*np.meshgrid(r, r)) <= radius_px + 0.25


def _dilate(mask: np.ndarray, radius_mm: float, res: float) -> np.ndarray:
    px = int(math.ceil(radius_mm / res))
    return binary_dilation(mask, structure=_disk(px)) if px > 0 else mask


def _grow(mask: np.ndarray, radius_mm: float, res: float) -> np.ndarray:
    """Dilate and enlarge the array so the grown outline is not clipped."""
    px = int(math.ceil(radius_mm / res))
    return _dilate(np.pad(mask, px), radius_mm, res) if px > 0 else mask


def surface_points(tris: np.ndarray, res: float, seed: int = 0) -> np.ndarray:
    """Area-weighted points on the surface, XY only, dense enough to fill a raster of `res`."""
    _, areas = normals_and_areas(tris)
    total = float(areas.sum())
    count = int(min(600_000, max(20_000, 8 * total / (res * res))))
    rng = np.random.default_rng(seed)
    tri = rng.choice(len(tris), size=count, p=areas / total)
    u, v = rng.random(count), rng.random(count)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    a, b, c = tris[tri, 0], tris[tri, 1], tris[tri, 2]
    pts = a + u[:, None] * (b - a) + v[:, None] * (c - a)
    return np.vstack([pts[:, :2], tris.reshape(-1, 3)[:, :2]])


def silhouette(points: np.ndarray, rotation_deg: float, res: float) -> np.ndarray:
    """Boolean mask of the top-down silhouette after turning by `rotation_deg`, bbox centred."""
    t = math.radians(rotation_deg)
    rot = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    p = points @ rot.T
    lo, hi = p.min(axis=0), p.max(axis=0)
    size = np.ceil((hi - lo) / res).astype(int) + 5
    idx = np.floor((p - lo) / res).astype(int) + 2
    mask = np.zeros((size[1], size[0]), dtype=bool)
    mask[idx[:, 1], idx[:, 0]] = True
    # Every cell holding a sample is taken, so the mask contains the part; closing only fills the
    # gaps between samples, it never grows past the outline.
    mask = binary_fill_holes(binary_closing(mask, structure=_disk(2), iterations=1))
    ys, xs = np.nonzero(mask)
    return mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]  # tight: no empty border


def _polygon_mask(poly: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Even-odd point-in-polygon for every cell centre (xs along columns, ys along rows)."""
    gx, gy = np.meshgrid(xs, ys)
    inside = np.zeros(gx.shape, dtype=bool)
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if y1 == y2:
            continue
        crosses = (y1 > gy) != (y2 > gy)
        x_at = x1 + (gy - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (gx < x_at)
    return inside


@dataclass
class _Grid:
    x0: float
    y0: float
    res: float
    free: np.ndarray  # True where a footprint cell may sit

    @property
    def shape(self) -> tuple[int, int]:
        return self.free.shape


def make_grid(plate: Plate, obstacles: np.ndarray | None, spacing: float, res: float) -> _Grid:
    xmin, ymin, xmax, ymax = plate.rect
    w, h = int(math.floor((xmax - xmin) / res)), int(math.floor((ymax - ymin) / res))
    xs, ys = xmin + (np.arange(w) + 0.5) * res, ymin + (np.arange(h) + 0.5) * res
    free = np.ones((h, w), dtype=bool)
    for poly in plate.disallowed:
        free &= ~_polygon_mask(np.asarray(poly, dtype=float), xs, ys)
    grid = _Grid(xmin, ymin, res, free)
    if obstacles is not None and len(obstacles):
        mask = np.zeros_like(free)
        ij = np.floor((obstacles - [xmin, ymin]) / res).astype(int)
        ok = (ij[:, 0] >= 0) & (ij[:, 0] < w) & (ij[:, 1] >= 0) & (ij[:, 1] < h)
        mask[ij[ok, 1], ij[ok, 0]] = True
        mask = binary_fill_holes(binary_closing(mask, structure=_disk(2)))
        grid.free &= ~_dilate(mask, spacing, res)
    return grid


def _best_spot(free: np.ndarray, foot: np.ndarray, strategy: str):
    """Best valid top-left cell for `foot` as (key, iy, ix), or None."""
    if foot.shape[0] > free.shape[0] or foot.shape[1] > free.shape[1]:
        return None  # larger than the plate: no valid position (FFT "valid" mode would swap roles)
    bad = (~free).astype(np.float32)
    count = fftconvolve(bad, foot[::-1, ::-1].astype(np.float32), mode="valid")
    valid = count < 0.5
    if not valid.any():
        return None
    fh, fw = foot.shape
    ys, xs = np.nonzero(valid)
    cy, cx = ys + fh / 2, xs + fw / 2
    height, width = free.shape
    if strategy == "centre":
        key = np.hypot(cy - height / 2, cx - width / 2)
    else:
        ky = cy if strategy.startswith("bottom") else height - cy
        kx = cx if strategy.endswith("left") else width - cx
        key = (ky + fh / 2) * (width + fw) * 2 + kx  # lowest top edge first, then nearest side
    k = int(np.argmin(key))
    return float(key[k]), int(ys[k]), int(xs[k])


def _occupy(
    free: np.ndarray, foot: np.ndarray, iy: int, ix: int, spacing: float, res: float
) -> None:
    """Mark the footprint, grown by the spacing, as taken (the growth reaches past its array)."""
    pad = int(math.ceil(spacing / res)) + 1
    grown = _dilate(np.pad(foot, pad), spacing, res)
    y0, x0 = iy - pad, ix - pad
    ys, xs = max(0, y0), max(0, x0)
    ye, xe = min(free.shape[0], y0 + grown.shape[0]), min(free.shape[1], x0 + grown.shape[1])
    free[ys:ye, xs:xe] &= ~grown[ys - y0 : ye - y0, xs - x0 : xe - x0]


def pack_copies(
    tris: np.ndarray,
    plate: Plate,
    copies: int | None,
    spacing: float = 3.0,
    margin: float = 0.0,
    rotations: tuple[float, ...] = (0, 90, 180, 270, 45, 135, 225, 315),
    obstacles_xy: np.ndarray | None = None,
    time_budget: float = 25.0,
) -> PackResult:
    """Place `copies` of the part (None = as many as fit). Nothing overlaps; spacing is the gap.

    `margin` grows each footprint (brim or raft). The best of several start corners is returned.
    """
    xmin, ymin, xmax, ymax = plate.rect
    extent = float(np.ptp(tris[..., :2].reshape(-1, 2), axis=0).max())
    res = float(min(1.0, max(0.4, extent / 80)))
    points = surface_points(tris, res)
    feet: list[tuple[float, np.ndarray]] = []
    for angle in rotations:
        foot = _grow(silhouette(points, angle, res), margin, res)
        if not any(f.shape == foot.shape and np.array_equal(f, foot) for _, f in feet):
            feet.append((angle, foot))
    stop_at = min(copies, MAX_COPIES) if copies else MAX_COPIES
    started = time.monotonic()
    best: PackResult | None = None
    for strategy in STRATEGIES:
        grid = make_grid(plate, obstacles_xy, spacing, res)
        free = grid.free.copy()
        placed: list[Placement] = []
        while len(placed) < stop_at:
            options = []
            for angle, foot in feet:
                spot = _best_spot(free, foot, strategy)
                if spot:
                    options.append((spot[0], angle, foot, spot[1], spot[2]))
            if not options:
                break
            _, angle, foot, iy, ix = min(options, key=lambda o: o[0])
            fh, fw = foot.shape
            _occupy(free, foot, iy, ix, spacing, res)
            placed.append(
                Placement(
                    rotation_deg=angle,
                    x=grid.x0 + (ix + fw / 2) * res,
                    y=grid.y0 + (iy + fh / 2) * res,
                )
            )
        if best is None or len(placed) > len(best.placements):
            best = PackResult(
                placements=placed, requested=copies, strategy=strategy, resolution_mm=res
            )
        if len(placed) >= stop_at or time.monotonic() - started > time_budget:
            break
    assert best is not None and xmax > xmin and ymax > ymin
    return best
