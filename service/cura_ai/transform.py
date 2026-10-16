"""The linear part of the part operations: lay flat, mirror, scale and turn, in printer axes."""

from __future__ import annotations

import numpy as np

from cura_ai.schemas import PartTurn

_AXES = "xyz"


def rotation_matrix(axis: str, degrees: float) -> np.ndarray:
    """Right-handed rotation about a printer axis."""
    t = np.radians(degrees)
    c, s = np.cos(t), np.sin(t)
    i, j = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[axis]
    m = np.eye(3)
    m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
    return m


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation taking the unit vector a onto the unit vector b."""
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-9:
        if c > 0:
            return np.eye(3)
        axis = np.array([1.0, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1.0, 0])
        axis = np.cross(a, axis)
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)  # half turn about a perpendicular axis
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1 + c)


def lay_flat_matrix(tris: np.ndarray, tolerance_deg: float = 2.0) -> np.ndarray:
    """Rotation that puts the largest flat face (faces within a tolerance count as one) down."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    cross = np.cross(b - a, c - a)
    area = np.linalg.norm(cross, axis=1) / 2
    keep = area > 1e-12
    if not keep.any():
        return np.eye(3)
    normals = cross[keep] / (2 * area[keep, None])
    area = area[keep]
    cos_tol = np.cos(np.radians(tolerance_deg))
    best, best_area = None, -1.0
    unused = np.ones(len(normals), dtype=bool)
    for index in np.argsort(-area):  # seeds by face size; each face joins the first group it fits
        if not unused[index]:
            continue
        group = unused & (normals @ normals[index] >= cos_tol)
        unused &= ~group
        total = float(area[group].sum())
        mean = (normals[group] * area[group, None]).sum(axis=0)
        mean /= np.linalg.norm(mean)
        bigger = total > best_area * (1 + 1e-6)
        tie_lower = (
            best is not None and abs(total - best_area) <= best_area * 1e-6 and mean[2] < best[2]
        )
        if best is None or bigger or tie_lower:  # equal faces: the one already facing down wins
            best, best_area = mean, total
    return rotation_between(best, np.array([0.0, 0.0, -1.0]))


def mirror_matrix(axes: list[str]) -> np.ndarray:
    m = np.eye(3)
    for axis in axes:
        m[_AXES.index(axis), _AXES.index(axis)] *= -1
    return m


def scale_diagonal(turn: PartTurn, size: np.ndarray) -> np.ndarray:
    """The per-axis scale factors the turn asks for, for a part of the given size."""
    f = np.ones(3)
    if turn.scale_percent is not None:
        f[:] = turn.scale_percent / 100.0
    elif turn.target_axis is not None and turn.target_mm is not None:
        i = _AXES.index(turn.target_axis)
        ratio = turn.target_mm / float(size[i]) if size[i] > 0 else 1.0
        if turn.target_uniform:
            f[:] = ratio
        else:
            f[i] = ratio
    else:
        per_axis = (turn.scale_x_percent, turn.scale_y_percent, turn.scale_z_percent)
        for i, value in enumerate(per_axis):
            if value is not None:
                f[i] = value / 100.0
    return f


def shape_matrix(turn: PartTurn, tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """One 3x3 matrix for lay flat, mirror, scale, then free rotation, plus the scale alone."""
    m = np.eye(3)
    if turn.lay_flat:
        m = lay_flat_matrix(tris) @ m
    if turn.mirror_axes:
        m = mirror_matrix(turn.mirror_axes) @ m
    size = np.ptp((tris.reshape(-1, 3) @ m.T), axis=0)
    scales = scale_diagonal(turn, size)
    m = np.diag(scales) @ m
    for axis, degrees in (
        ("x", turn.rotate_x_deg),
        ("y", turn.rotate_y_deg),
        ("z", turn.rotate_z_deg),
    ):
        if degrees is not None:
            m = rotation_matrix(axis, degrees) @ m
    return m, scales
