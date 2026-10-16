"""Pure numpy geometry for the plugin: scene <-> printer axes, rotation, drop, STL writing.

No Cura imports, so it is unit-tested from the service's test suite.
"""

from __future__ import annotations

import struct

import numpy


def scene_to_printer(vertices: numpy.ndarray) -> numpy.ndarray:
    """Cura scene (Y up) to printer axes (Z up): (x, y, z) -> (x, -z, y). Same as StartSliceJob."""
    out = numpy.array(vertices, dtype=float)
    out[:, [1, 2]] = out[:, [2, 1]]
    out[:, 1] *= -1
    return out


def quaternion_to_matrix(x: float, y: float, z: float, w: float) -> numpy.ndarray:
    """3x3 rotation matrix of a quaternion, the same formula as Uranium's Quaternion.toMatrix."""
    s = 2.0 / (x * x + y * y + z * z + w * w)
    xs, ys, zs = s * x, s * y, s * z
    wx, wy, wz = w * xs, w * ys, w * zs
    xx, xy, xz = x * xs, x * ys, x * zs
    yy, yz, zz = y * ys, y * zs, z * zs
    return numpy.array(
        [
            [1 - (yy + zz), xy - wz, xz + wy],
            [xy + wz, 1 - (xx + zz), yz - wx],
            [xz - wy, yz + wx, 1 - (xx + yy)],
        ]
    )


def rotate_about(vertices: numpy.ndarray, pivot: numpy.ndarray, rotation: numpy.ndarray):
    """Rotate scene vertices about a pivot point."""
    return pivot + (numpy.asarray(vertices) - pivot) @ rotation.T


def drop_distance(vertices: numpy.ndarray, pivot: numpy.ndarray, rotation: numpy.ndarray) -> float:
    """Vertical shift (scene Y) that puts the rotated model's lowest point back on the plate."""
    return float(-rotate_about(vertices, pivot, rotation)[:, 1].min())


def triangles(vertices: numpy.ndarray, indices: numpy.ndarray | None) -> numpy.ndarray:
    """(n, 3, 3) triangle array from a vertex array and optional index array."""
    if indices is not None:
        vertices = numpy.take(vertices, numpy.asarray(indices).flatten(), axis=0)
    return numpy.asarray(vertices, dtype=float).reshape(-1, 3, 3)


def write_stl(path: str, tris: numpy.ndarray) -> None:
    record = numpy.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    record["v"] = tris
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
        f.write(record.tobytes())


# Cura scene (Y up, Z towards the viewer) from printer axes: (x, y, z) -> (x, z, -y)
PRINTER_TO_SCENE = numpy.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])


def placement_matrix(
    vertices: numpy.ndarray,
    linear,
    rotation_deg: float,
    x: float | None = None,
    y: float | None = None,
) -> numpy.ndarray:
    """4x4 world matrix that reshapes, turns and moves a part (premultiply onto its transformation).

    `linear` is a scale factor or a 3x3 matrix in printer axes (scale, mirror, free rotation).
    It acts about the centre of the part's footprint at plate level; the part is then turned about
    the vertical axis through the same point by `rotation_deg` (counter-clockwise seen from above,
    as in the service), set back on the plate, and its footprint's bounding-box centre moved to
    (x, y) in printer axes (None keeps the current centre).
    """
    v = numpy.asarray(vertices, dtype=float)
    cx, cz = (v[:, 0].min() + v[:, 0].max()) / 2, (v[:, 2].min() + v[:, 2].max()) / 2
    pivot = numpy.array([cx, v[:, 1].min(), cz])
    lin = numpy.asarray(linear, dtype=float)
    if lin.ndim == 0:
        lin = lin * numpy.eye(3)
    lin = PRINTER_TO_SCENE @ lin @ PRINTER_TO_SCENE.T
    scale = numpy.eye(4)
    scale[:3, :3] = lin
    scale[:3, 3] = pivot - lin @ pivot
    t = numpy.radians(rotation_deg)
    rz = numpy.array([[numpy.cos(t), -numpy.sin(t), 0], [numpy.sin(t), numpy.cos(t), 0], [0, 0, 1]])
    spin = numpy.eye(4)
    spin[:3, :3] = PRINTER_TO_SCENE @ rz @ PRINTER_TO_SCENE.T
    axis = numpy.array([cx, 0.0, cz])
    spin[:3, 3] = axis - spin[:3, :3] @ axis
    turned = (v - pivot) @ lin.T + pivot
    turned = (turned - axis) @ spin[:3, :3].T + axis
    nx = (turned[:, 0].min() + turned[:, 0].max()) / 2
    nz = (turned[:, 2].min() + turned[:, 2].max()) / 2
    move = numpy.eye(4)
    move[:3, 3] = [
        (cx if x is None else x) - nx,
        pivot[1] - turned[:, 1].min(),
        (cz if y is None else -y) - nz,
    ]
    return move @ spin @ scale


def reset_transform(
    local: numpy.ndarray, vertices: numpy.ndarray, reset_rotation: bool, reset_scale: bool
) -> numpy.ndarray:
    """New local 4x4 with the rotation and/or scale taken out, keeping footprint centre and base.

    `local` is the node's transformation (translation in the last column), `vertices` its world
    vertices now, in scene axes.
    """
    old_lin, t_old = local[:3, :3], local[:3, 3]
    scales = numpy.maximum(numpy.linalg.norm(old_lin, axis=0), 1e-12)
    rotation = old_lin / scales
    new_lin = (numpy.eye(3) if reset_rotation else rotation) * (
        numpy.ones(3) if reset_scale else scales
    )
    moved = (numpy.asarray(vertices, dtype=float) - t_old) @ (new_lin @ numpy.linalg.inv(old_lin)).T
    v = numpy.asarray(vertices, dtype=float)

    def centre(a, axis):
        return (a[:, axis].min() + a[:, axis].max()) / 2

    shift = numpy.array(
        [
            centre(v, 0) - centre(moved, 0),
            v[:, 1].min() - moved[:, 1].min(),
            centre(v, 2) - centre(moved, 2),
        ]
    )
    out = numpy.eye(4)
    out[:3, :3], out[:3, 3] = new_lin, shift
    return out


DIRECTIONS = {
    "x": [1.0, 0.0, 0.0],
    "y": [0.0, 1.0, 0.0],
    "z": [0.0, 0.0, 1.0],
    "front": [0.0, -1.0, 0.0],
    "back": [0.0, 1.0, 0.0],
    "left": [-1.0, 0.0, 0.0],
    "right": [1.0, 0.0, 0.0],
    "top": [0.0, 0.0, 1.0],
    "bottom": [0.0, 0.0, -1.0],
}
