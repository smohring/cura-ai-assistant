"""Candidate orientations of a mesh and their deterministic scoring.

Everything works in printer coordinates (Z up, bed at Z = 0). A candidate is a rotation that
puts one chosen direction of the model downwards; no angle is ever invented by a language
model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel
from scipy.spatial import ConvexHull, QhullError

from cura_ai.mesh import normals_and_areas, rotated_on_bed

METRICS = (
    "bed_contact",
    "low_overhang",
    "low_support",
    "low_height",
    "load_in_plane",
    "visible_surface",
)
DOWN = np.array([0.0, 0.0, -1.0])
# Cura scene (Y up, Z towards the viewer) from printer coordinates: (x, y, z) -> (x, z, -y)
PRINTER_TO_SCENE = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])


class OrientationError(ValueError):
    pass


@dataclass
class Candidate:
    id: str
    source: str  # "current", "face" or "sphere"
    down: np.ndarray  # model direction that ends up pointing at the bed
    rotation: np.ndarray  # 3x3, applied to model coordinates


class AxisAngle(BaseModel):
    axis: list[float]
    angle_deg: float


class Quaternion(BaseModel):
    x: float
    y: float
    z: float
    w: float


class RawMetrics(BaseModel):
    height_mm: float
    bed_contact_mm2: float
    overhang_area_mm2: float
    support_mm3: float
    load_z_fraction: float | None = None
    visible_quality: float | None = None


class ScoredCandidate(BaseModel):
    id: str
    source: str
    down: list[float]
    rotation_axis_angle: AxisAngle
    rotation_quaternion_scene: Quaternion
    metrics: RawMetrics
    scores: dict[str, float]
    total: float
    rank: int


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise OrientationError("zero-length direction")
    return v / n


def rotation_between(a, b) -> np.ndarray:
    """Smallest rotation taking direction a onto direction b."""
    a, b = _unit(a), _unit(b)
    c = float(a @ b)
    if c > 1 - 1e-12:
        return np.eye(3)
    if c < -1 + 1e-12:
        axis = _unit(np.cross(a, [1.0, 0, 0] if abs(a[0]) < 0.9 else [0, 1.0, 0]))
        return 2 * np.outer(axis, axis) - np.eye(3)
    v = np.cross(a, b)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)


def axis_angle(rotation: np.ndarray) -> AxisAngle:
    angle = math.acos(max(-1.0, min(1.0, (np.trace(rotation) - 1) / 2)))
    if angle < 1e-9:
        return AxisAngle(axis=[0.0, 0.0, 1.0], angle_deg=0.0)
    if abs(angle - math.pi) < 1e-6:
        sym = rotation + np.eye(3)
        axis = _unit(sym[:, int(np.argmax(np.linalg.norm(sym, axis=0)))])
    else:
        axis = _unit([rotation[2, 1] - rotation[1, 2], rotation[0, 2] - rotation[2, 0],
                      rotation[1, 0] - rotation[0, 1]])  # fmt: skip
    return AxisAngle(axis=[float(x) for x in axis], angle_deg=math.degrees(angle))


def quaternion_for_scene(rotation: np.ndarray) -> Quaternion:
    """The printer-space rotation expressed in Cura's scene axes, as (x, y, z, w)."""
    r = PRINTER_TO_SCENE @ rotation @ PRINTER_TO_SCENE.T
    t = np.trace(r)
    if t > 0:
        s = math.sqrt(t + 1) * 2
        w, x, y, z = (
            s / 4,
            (r[2, 1] - r[1, 2]) / s,
            (r[0, 2] - r[2, 0]) / s,
            (r[1, 0] - r[0, 1]) / s,
        )
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        w, x, y, z = (
            (r[2, 1] - r[1, 2]) / s,
            s / 4,
            (r[0, 1] + r[1, 0]) / s,
            (r[0, 2] + r[2, 0]) / s,
        )
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        w, x, y, z = (
            (r[0, 2] - r[2, 0]) / s,
            (r[0, 1] + r[1, 0]) / s,
            s / 4,
            (r[1, 2] + r[2, 1]) / s,
        )
    else:
        s = math.sqrt(1 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        w, x, y, z = (
            (r[1, 0] - r[0, 1]) / s,
            (r[0, 2] + r[2, 0]) / s,
            (r[1, 2] + r[2, 1]) / s,
            s / 4,
        )
    return Quaternion(x=float(x), y=float(y), z=float(z), w=float(w))


def _sphere_directions(n: int) -> list[np.ndarray]:
    golden = math.pi * (3 - math.sqrt(5))
    out = []
    for i in range(n):
        z = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(1 - z * z)
        out.append(np.array([r * math.cos(golden * i), r * math.sin(golden * i), z]))
    return out


def _face_directions(tris: np.ndarray, max_faces: int, min_share: float) -> list[np.ndarray]:
    """Outward normals of the convex hull's flat faces, coplanar facets merged, largest first."""
    try:
        hull = ConvexHull(tris.reshape(-1, 3))
    except QhullError:
        return []
    pts = hull.points[hull.simplices]
    _, areas = normals_and_areas(pts)
    normals = hull.equations[:, :3]
    clusters: list[list] = []  # [normal of largest facet, area, largest facet area]
    for i in np.argsort(-areas):
        for c in clusters:
            if float(c[0] @ normals[i]) > math.cos(math.radians(1.0)):
                c[1] += areas[i]
                break
        else:
            clusters.append([normals[i], areas[i]])
    total = float(areas.sum())
    clusters = [c for c in clusters if c[1] >= min_share * total]
    clusters.sort(key=lambda c: -c[1])
    return [c[0] for c in clusters[:max_faces]]


def generate_candidates(
    tris: np.ndarray, max_faces: int = 12, sphere_n: int = 26, merge_deg: float = 5.0
) -> list[Candidate]:
    """The current pose, the hull's flat faces and a coarse sphere sampling, merged by angle."""
    wanted: list[tuple[str, np.ndarray]] = [("current", DOWN)]
    wanted += [("face", d) for d in _face_directions(tris, max_faces, min_share=0.01)]
    wanted += [("sphere", d) for d in _sphere_directions(sphere_n)]
    cos_merge = math.cos(math.radians(merge_deg))
    kept: list[tuple[str, np.ndarray]] = []
    for source, d in wanted:
        d = _unit(d)
        if all(float(d @ k[1]) < cos_merge for k in kept):
            kept.append((source, d))
    return [
        Candidate(f"c{i:02d}", source, d, rotation_between(d, DOWN))
        for i, (source, d) in enumerate(kept)
    ]


@dataclass
class _Samples:
    tri: np.ndarray
    u: np.ndarray
    v: np.ndarray
    weight_area: float


def _make_samples(areas: np.ndarray, n: int) -> _Samples:
    rng = np.random.default_rng(0)
    total = float(areas.sum())
    tri = rng.choice(len(areas), size=n, p=areas / total)
    r1, r2 = rng.random(n), rng.random(n)
    flip = r1 + r2 > 1
    r1[flip], r2[flip] = 1 - r1[flip], 1 - r2[flip]
    return _Samples(tri, r1, r2, total / n)


def _support_volume(
    rot: np.ndarray, normals: np.ndarray, overhang: np.ndarray, s: _Samples, extent: float
) -> float:
    """Material column below each overhang sample, down to the first surface or the bed."""
    a, b, c = rot[s.tri, 0], rot[s.tri, 1], rot[s.tri, 2]
    pts = a + s.u[:, None] * (b - a) + s.v[:, None] * (c - a)
    grid = 96
    cell = max(extent, 1e-6) / grid
    mins = pts[:, :2].min(axis=0)
    ij = np.minimum(((pts[:, :2] - mins) / cell).astype(np.int64), grid - 1)
    cell_id = ij[:, 0] * grid + ij[:, 1]
    big = 1.0e6
    keys = np.sort(cell_id * big + pts[:, 2])
    mask = overhang[s.tri]
    if not mask.any():
        return 0.0
    q = cell_id[mask] * big + (pts[mask, 2] - 1.5 * cell)
    pos = np.searchsorted(keys, q, side="left") - 1
    ok = pos >= 0
    found = keys[np.maximum(pos, 0)]
    same_cell = ok & (found >= cell_id[mask] * big)
    z_below = np.where(same_cell, found - cell_id[mask] * big, 0.0)
    weight = s.weight_area * np.abs(normals[s.tri[mask], 2])
    return float(np.sum(weight * (pts[mask, 2] - np.maximum(z_below, 0.0))))


def _visible_quality(v: np.ndarray, overhang_cos: float, on_bed: bool) -> float:
    """Heuristic surface quality of a face looking along v: walls and flat tops are best."""
    if on_bed:
        return 0.9
    if -v[2] > overhang_cos:
        return 0.0
    phi = math.asin(max(-1.0, min(1.0, abs(v[2]))))
    return max(0.0, 1 - math.sin(2 * phi))


def evaluate(
    tris: np.ndarray,
    candidates: list[Candidate],
    load_direction=None,
    visible_direction=None,
    support_angle_deg: float = 50.0,
    samples: int = 40000,
) -> dict[str, RawMetrics]:
    """Raw geometric numbers for every candidate."""
    load = None if load_direction is None else _unit(load_direction)
    visible = None if visible_direction is None else _unit(visible_direction)
    overhang_cos = math.cos(math.radians(support_angle_deg))
    _, areas = normals_and_areas(tris)
    if float(areas.sum()) <= 0:
        raise OrientationError("mesh has no surface area")
    s = _make_samples(areas, samples)
    result: dict[str, RawMetrics] = {}
    for cand in candidates:
        rot = rotated_on_bed(tris, cand.rotation)
        normals, _ = normals_and_areas(rot)
        z = rot[..., 2]
        height = float(z.max())
        on_bed = (normals[:, 2] < -0.9962) & (z.max(axis=1) < 0.2)
        overhang = (-normals[:, 2] > overhang_cos) & ~on_bed
        extent = float(np.ptp(rot[..., :2].reshape(-1, 2), axis=0).max())
        load_z = None if load is None else float(abs((cand.rotation @ load)[2]))
        vis = None
        if visible is not None:
            v = cand.rotation @ visible
            vis = _visible_quality(v, overhang_cos, bool(v[2] < -0.9962 and on_bed.any()))
        result[cand.id] = RawMetrics(
            height_mm=height,
            bed_contact_mm2=float(areas[on_bed].sum()),
            overhang_area_mm2=float(areas[overhang].sum()),
            support_mm3=_support_volume(rot, normals, overhang, s, extent),
            load_z_fraction=load_z,
            visible_quality=vis,
        )
    return result


def _relative(values: list[float], higher_is_better: bool) -> list[float]:
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [1.0] * len(values)
    return [((v - lo) if higher_is_better else (hi - v)) / (hi - lo) for v in values]


def score_candidates(
    candidates: list[Candidate], metrics: dict[str, RawMetrics], weights: dict[str, float]
) -> list[ScoredCandidate]:
    """Weighted ranking; every metric is normalised to 0..1 across the candidates, 1 = best."""
    unknown = set(weights) - set(METRICS)
    if unknown:
        raise OrientationError(f"unknown metrics in weights: {sorted(unknown)}")
    if any(w < 0 for w in weights.values()) or not any(w > 0 for w in weights.values()):
        raise OrientationError("weights must be non-negative with at least one above zero")
    rows = [metrics[c.id] for c in candidates]
    per_metric: dict[str, list[float]] = {
        "bed_contact": _relative([r.bed_contact_mm2 for r in rows], True),
        "low_overhang": _relative([r.overhang_area_mm2 for r in rows], False),
        "low_support": _relative([r.support_mm3 for r in rows], False),
        "low_height": _relative([r.height_mm for r in rows], False),
    }
    if all(r.load_z_fraction is not None for r in rows):
        per_metric["load_in_plane"] = [1 - r.load_z_fraction for r in rows]  # type: ignore[operator]
    if all(r.visible_quality is not None for r in rows):
        per_metric["visible_surface"] = [r.visible_quality for r in rows]  # type: ignore[misc]
    for name, w in weights.items():
        if w > 0 and name not in per_metric:
            need = "load_direction" if name == "load_in_plane" else "visible_direction"
            raise OrientationError(f"weight '{name}' needs a {need}")
    total_w = sum(w for w in weights.values() if w > 0)
    scored = []
    for i, cand in enumerate(candidates):
        scores = {m: per_metric[m][i] for m in per_metric}
        total = sum(w * scores[m] for m, w in weights.items() if w > 0) / total_w
        scored.append(
            ScoredCandidate(
                id=cand.id,
                source=cand.source,
                down=[float(x) for x in cand.down],
                rotation_axis_angle=axis_angle(cand.rotation),
                rotation_quaternion_scene=quaternion_for_scene(cand.rotation),
                metrics=rows[i],
                scores=scores,
                total=total,
                rank=0,
            )
        )
    scored.sort(key=lambda s: -s.total)  # stable: ties keep generation order, "current" first
    for rank, s in enumerate(scored, start=1):
        s.rank = rank
    return scored


@dataclass
class Analysis:
    """Candidates with their raw numbers; independent of any weights."""

    candidates: list[Candidate]
    metrics: dict[str, RawMetrics]


def analyse(
    tris: np.ndarray,
    load_direction=None,
    visible_direction=None,
    support_angle_deg: float = 50.0,
) -> Analysis:
    cands = generate_candidates(tris)
    metrics = evaluate(tris, cands, load_direction, visible_direction, support_angle_deg)
    return Analysis(candidates=cands, metrics=metrics)


def orient(tris: np.ndarray, weights: dict[str, float], **kwargs) -> list[ScoredCandidate]:
    a = analyse(tris, **kwargs)
    return score_candidates(a.candidates, a.metrics, weights)
