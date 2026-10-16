"""Triangle meshes as (n, 3, 3) float arrays in printer coordinates (X/Y bed, Z up)."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


class MeshError(Exception):
    pass


def load_stl(path: str | Path) -> np.ndarray:
    data = Path(path).read_bytes()
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if len(data) == 84 + 50 * count:
            rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
            return np.frombuffer(data, dtype=rec, count=count, offset=84)["v"].astype(float)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise MeshError(f"{path}: neither a valid binary nor an ASCII STL") from exc
    verts = [
        [float(x) for x in line.split()[1:4]]
        for line in text.splitlines()
        if line.strip().startswith("vertex")
    ]
    if not verts or len(verts) % 3:
        raise MeshError(f"{path}: no triangles found")
    return np.array(verts, dtype=float).reshape(-1, 3, 3)


def write_stl(path: str | Path, tris: np.ndarray) -> None:
    out = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    out["v"] = tris
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
        f.write(out.tobytes())


def normals_and_areas(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cross = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(cross, axis=1)
    areas = length / 2
    normals = np.divide(cross, length[:, None], out=np.zeros_like(cross), where=length[:, None] > 0)
    return normals, areas


def rotated_on_bed(tris: np.ndarray, rotation: np.ndarray) -> np.ndarray:
    """Rotate about the origin, then slide down so the lowest point touches Z = 0."""
    out = tris @ rotation.T
    return out - np.array([0.0, 0.0, out[..., 2].min()])


def signed_volume(tris: np.ndarray) -> float:
    return float(np.einsum("ij,ij->", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])) / 6)
