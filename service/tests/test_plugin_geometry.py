"""The plugin's geometry against the service: the chain scene -> rotate -> printer must agree."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from meshes import l_bracket, t_shape

from cura_ai.mesh import load_stl, rotated_on_bed
from cura_ai.orientation import DOWN, orient, rotation_between

spec = importlib.util.spec_from_file_location(
    "plugin_geometry",
    Path(__file__).parents[2] / "cura_extension" / "CuraAiAssistant" / "geometry.py",
)
geo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(geo)


def printer_to_scene(tris):
    flat = tris.reshape(-1, 3)
    return np.stack([flat[:, 0], flat[:, 2], -flat[:, 1]], axis=1).reshape(-1, 3, 3)


def test_scene_printer_round_trip():
    tris = l_bracket()
    scene = printer_to_scene(tris)
    assert np.allclose(geo.scene_to_printer(scene.reshape(-1, 3)).reshape(-1, 3, 3), tris)
    assert np.allclose(geo.scene_to_printer(np.array([[0, 5.0, 0]])), [[0, 0, 5.0]])  # Y up -> Z up


def test_triangles_with_and_without_indices():
    verts = np.arange(18, dtype=float).reshape(6, 3)
    assert geo.triangles(verts, None).shape == (2, 3, 3)
    assert np.allclose(geo.triangles(verts, np.array([[0, 1, 2], [2, 1, 0]]))[1, 0], verts[2])


@pytest.mark.parametrize("mesh", [l_bracket(), t_shape()])
def test_service_rotation_applied_the_way_the_plugin_does_matches_the_service(mesh):
    """Every candidate: rotate scene vertices by the returned quaternion about a pivot, drop to
    the plate, convert to printer axes - this must equal the service's own rotated_on_bed."""
    ranked = orient(mesh, {"low_height": 1, "bed_contact": 1})
    scene = printer_to_scene(mesh).reshape(-1, 3)
    pivot = np.array([3.0, 11.0, -7.0])  # node position; any value must work
    for s in ranked:
        q = s.rotation_quaternion_scene
        rot = geo.quaternion_to_matrix(q.x, q.y, q.z, q.w)
        moved = geo.rotate_about(scene, pivot, rot)
        moved[:, 1] += geo.drop_distance(scene, pivot, rot)
        got = geo.scene_to_printer(moved).reshape(-1, 3, 3)
        want = rotated_on_bed(mesh, rotation_between(np.array(s.down), DOWN))
        # same shape and pose up to a horizontal shift (the rotation pivot differs)
        assert got[..., 2].min() == pytest.approx(0, abs=1e-6)
        assert np.allclose(np.sort(got[..., 2].ravel()), np.sort(want[..., 2].ravel()), atol=1e-6)
        shift = got.reshape(-1, 3).mean(axis=0)[:2] - want.reshape(-1, 3).mean(axis=0)[:2]
        assert np.allclose(got[..., :2] - shift, want[..., :2], atol=1e-6)


def apply(matrix, verts):
    return verts @ matrix[:3, :3].T + matrix[:3, 3]


@pytest.mark.parametrize("rotation", [0, 90, 37, 180, 270])
@pytest.mark.parametrize("factor", [1.0, 1.5, 0.5])
def test_placement_matrix_scales_turns_and_places_like_the_packer_expects(factor, rotation):
    """The footprint after the matrix is the printer-axes footprint turned counter-clockwise by
    `rotation`, scaled, with its bounding-box centre at (x, y) and the part still on the plate."""
    mesh = l_bracket()
    scene = printer_to_scene(mesh).reshape(-1, 3) + [30.0, 0.0, -12.0]  # the part sits off-centre
    matrix = geo.placement_matrix(scene, factor, rotation, x=-50.0, y=70.0)
    out = geo.scene_to_printer(apply(matrix, scene))
    start = geo.scene_to_printer(scene)
    t = np.radians(rotation)
    rot = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    mid = (start[:, :2].min(axis=0) + start[:, :2].max(axis=0)) / 2
    expected = ((start[:, :2] - mid) * factor) @ rot.T
    expected = expected - (expected.min(axis=0) + expected.max(axis=0)) / 2 + [-50.0, 70.0]
    assert np.allclose(out[:, :2], expected, atol=1e-6)
    assert out[:, 2].min() == pytest.approx(start[:, 2].min(), abs=1e-9)  # still on the plate
    assert np.ptp(out[:, 2]) == pytest.approx(np.ptp(start[:, 2]) * factor)


def test_placement_matrix_is_a_similarity_transform():
    scene = printer_to_scene(t_shape()).reshape(-1, 3)
    m = geo.placement_matrix(scene, 2.0, 45, 10.0, -10.0)
    assert np.allclose(m[:3, :3] @ m[:3, :3].T, 4.0 * np.eye(3))
    assert np.linalg.det(m[:3, :3]) == pytest.approx(8.0)


def test_general_linear_part_matches_the_service_shape_and_keeps_the_part_on_the_plate():
    from cura_ai.transform import rotation_matrix

    mesh = l_bracket()
    scene = printer_to_scene(mesh).reshape(-1, 3) + [30.0, 0.0, -12.0]
    start = geo.scene_to_printer(scene)
    linear = rotation_matrix("x", 90) @ np.diag([1.0, 2.0, 0.5])
    matrix = geo.placement_matrix(scene, linear, 0, x=5.0, y=-8.0)
    out = geo.scene_to_printer(apply(matrix, scene))
    assert np.ptp(out, axis=0) == pytest.approx(np.ptp(start @ linear.T, axis=0))
    assert out[:, 2].min() == pytest.approx(start[:, 2].min(), abs=1e-9)
    centre = (out[:, :2].min(axis=0) + out[:, :2].max(axis=0)) / 2
    assert centre == pytest.approx([5.0, -8.0])


def test_mirror_in_place_keeps_the_footprint_centre_and_flips_handedness():
    scene = printer_to_scene(t_shape()).reshape(-1, 3) + [30.0, 0.0, -12.0]
    start = geo.scene_to_printer(scene)
    m = geo.placement_matrix(scene, np.diag([-1.0, 1.0, 1.0]), 0)
    out = geo.scene_to_printer(apply(m, scene))
    assert np.linalg.det(m[:3, :3]) < 0
    after = (out[:, :2].min(axis=0) + out[:, :2].max(axis=0)) / 2
    before = (start[:, :2].min(axis=0) + start[:, :2].max(axis=0)) / 2
    assert after == pytest.approx(before)
    assert np.ptp(out, axis=0) == pytest.approx(np.ptp(start, axis=0))


def test_reset_takes_out_rotation_and_scale_but_keeps_centre_and_plate_contact():
    scene = printer_to_scene(l_bracket()).reshape(-1, 3)
    scale = geo.placement_matrix(scene, 2.0, 0.0, 10.0, 20.0)
    turn = geo.placement_matrix(apply(scale, scene), np.eye(3), 90.0, 10.0, 20.0)
    local = turn @ scale
    verts = apply(local, scene)
    new = geo.reset_transform(local, verts, reset_rotation=True, reset_scale=False)
    out = apply(new, apply(np.linalg.inv(local), verts))  # back to the node's own frame first
    assert np.ptp(out, axis=0) == pytest.approx(np.ptp(scene, axis=0) * 2)  # still 2x, unturned
    assert out[:, 1].min() == pytest.approx(verts[:, 1].min(), abs=1e-9)
    for axis in (0, 2):
        assert (out[:, axis].min() + out[:, axis].max()) / 2 == pytest.approx(
            (verts[:, axis].min() + verts[:, axis].max()) / 2
        )
    both = geo.reset_transform(local, verts, True, True)
    assert both[:3, :3] == pytest.approx(np.eye(3))


def test_quaternion_matrix_is_a_proper_rotation():
    m = geo.quaternion_to_matrix(0.3, -0.2, 0.5, 0.7)
    assert np.allclose(m @ m.T, np.eye(3)) and np.linalg.det(m) == pytest.approx(1)


def test_stl_written_by_the_plugin_is_read_by_the_service(tmp_path):
    tris = t_shape()
    geo.write_stl(str(tmp_path / "m.stl"), tris)
    assert np.allclose(load_stl(tmp_path / "m.stl"), tris)
