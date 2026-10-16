import math

import numpy as np
import pytest
from meshes import cube, l_bracket, t_shape, write_ascii_stl

from cura_ai.mesh import load_stl, signed_volume, write_stl
from cura_ai.orientation import (
    PRINTER_TO_SCENE,
    OrientationError,
    analyse,
    axis_angle,
    generate_candidates,
    orient,
    quaternion_for_scene,
    rotation_between,
    score_candidates,
)


def by_down(cands, direction):
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    return next(c for c in cands if float(c.down @ d) > 0.99)


def quat_rotate(q, v):
    u, w = np.array([q.x, q.y, q.z]), q.w
    return v + 2 * np.cross(u, np.cross(u, v) + w * v)


# --- test meshes and file formats -------------------------------------------------------


@pytest.mark.parametrize(
    ("mesh", "volume"), [(cube(), 8000), (l_bracket(), 14000), (t_shape(), 14000)]
)
def test_test_meshes_are_closed_and_outward_facing(mesh, volume):
    assert signed_volume(mesh) == pytest.approx(volume)


def test_stl_round_trip_binary_and_ascii(tmp_path):
    mesh = l_bracket()
    write_stl(tmp_path / "b.stl", mesh)
    write_ascii_stl(tmp_path / "a.stl", mesh)
    assert np.allclose(load_stl(tmp_path / "b.stl"), mesh)
    assert np.allclose(load_stl(tmp_path / "a.stl"), mesh)


# --- rotations -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a", [(1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, -1), (1, 2, 3), (-3, 1, 0.2)]
)
def test_rotation_between_maps_direction_onto_target(a):
    b = np.array([0, 0, -1.0])
    r = rotation_between(a, b)
    assert np.allclose(r @ (np.array(a) / np.linalg.norm(a)), b)
    assert np.allclose(r @ r.T, np.eye(3)) and np.linalg.det(r) == pytest.approx(1)


def test_axis_angle_reproduces_the_rotation():
    r = rotation_between((1, 0, 0), (0, 0, -1))
    aa = axis_angle(r)
    k = np.array(aa.axis)
    t = math.radians(aa.angle_deg)
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    assert np.allclose(np.eye(3) + math.sin(t) * kx + (1 - math.cos(t)) * kx @ kx, r)
    assert axis_angle(np.eye(3)).angle_deg == 0
    assert axis_angle(rotation_between((0, 0, 1), (0, 0, -1))).angle_deg == pytest.approx(180)


@pytest.mark.parametrize("a", [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 2, 3)])
def test_scene_quaternion_equals_the_conjugated_rotation(a):
    r = rotation_between(a, (0, 0, -1))
    q = quaternion_for_scene(r)
    assert q.x**2 + q.y**2 + q.z**2 + q.w**2 == pytest.approx(1)
    expected = PRINTER_TO_SCENE @ r @ PRINTER_TO_SCENE.T
    for v in np.eye(3):
        assert np.allclose(quat_rotate(q, v), expected @ v)


def test_printer_up_is_scene_up():
    assert np.allclose(PRINTER_TO_SCENE @ [0, 0, 1], [0, 1, 0])  # Z up -> Y up


# --- candidates and metrics ----------------------------------------------------------------


def test_cube_has_six_face_candidates_plus_current_and_no_overhang():
    cands = generate_candidates(cube())
    assert cands[0].id == "c00" and cands[0].source == "current"
    assert sum(c.source == "face" for c in cands) == 5  # the sixth equals "current"
    m = analyse(cube()).metrics
    for r in m.values():
        if r.bed_contact_mm2 > 0:
            assert r.height_mm == pytest.approx(20) and r.bed_contact_mm2 == pytest.approx(400)
            assert r.overhang_area_mm2 == 0 and r.support_mm3 == 0


def test_upright_t_needs_support_under_its_bar():
    a = analyse(t_shape())
    current = a.metrics["c00"]
    assert current.overhang_area_mm2 == pytest.approx(600)
    assert current.support_mm3 == pytest.approx(600 * 30, rel=0.15)  # two wings, 30 mm tall


def test_upside_down_and_side_lying_t_need_no_support():
    a = analyse(t_shape())
    top_down = a.metrics[by_down(a.candidates, (0, 0, 1)).id]
    on_side = a.metrics[by_down(a.candidates, (0, -1, 0)).id]
    assert top_down.support_mm3 < 100 and top_down.overhang_area_mm2 == 0
    assert on_side.support_mm3 < 100 and on_side.height_mm == pytest.approx(20)


def test_low_support_puts_a_supportless_pose_first_and_the_upright_t_far_behind():
    ranked = orient(t_shape(), {"low_support": 1})
    assert ranked[0].metrics.support_mm3 < 100
    upright = next(s for s in ranked if s.id == "c00")
    assert upright.metrics.support_mm3 > 10000
    assert upright.scores["low_support"] < 0.5


def test_l_bracket_best_orientation_for_an_axial_load_is_the_profile_face_down():
    """Hand-derived: tension along Z in the upright arm must lie in the layer plane, so the
    L-shaped face goes on the bed (normal +-Y); that also gives the lowest part (20 mm)."""
    ranked = orient(l_bracket(), {"load_in_plane": 1, "low_height": 1}, load_direction=(0, 0, 1))
    best = ranked[0]
    assert abs(best.down[1]) > 0.99
    assert best.metrics.height_mm == pytest.approx(20)
    assert best.metrics.load_z_fraction == pytest.approx(0, abs=1e-6)
    assert best.metrics.bed_contact_mm2 == pytest.approx(700)
    current = next(s for s in ranked if s.id == "c00")  # upright: load across the layers
    assert current.metrics.load_z_fraction == pytest.approx(1)
    assert current.rank > best.rank


def test_l_bracket_largest_contact_is_the_outer_base_or_back():
    best = orient(l_bracket(), {"bed_contact": 1})[0]
    assert best.metrics.bed_contact_mm2 == pytest.approx(800)


def test_visible_face_quality_prefers_walls_and_tops_over_slopes_and_overhangs():
    a = analyse(l_bracket(), visible_direction=(0, -1, 0))
    wall = a.metrics[by_down(a.candidates, (0, 0, -1)).id]  # visible face vertical
    top = a.metrics[by_down(a.candidates, (0, -1, 0)).id]  # visible face on the bed
    sloped = [r.visible_quality for c, r in zip(a.candidates, a.metrics.values(), strict=True)
              if c.source == "sphere"]  # fmt: skip
    assert wall.visible_quality == pytest.approx(1)
    assert top.visible_quality == pytest.approx(0.9)
    assert min(sloped) < 0.2


def test_scores_are_deterministic():
    one = [s.model_dump() for s in orient(t_shape(), {"low_support": 1, "low_height": 1})]
    two = [s.model_dump() for s in orient(t_shape(), {"low_support": 1, "low_height": 1})]
    assert one == two


# --- weights ----------------------------------------------------------------------------------


def test_invalid_weights_are_rejected():
    a = analyse(cube())
    for weights in ({"nonsense": 1}, {"low_height": 0}, {"low_height": -1}):
        with pytest.raises(OrientationError):
            score_candidates(a.candidates, a.metrics, weights)


def test_weight_for_missing_direction_is_rejected():
    a = analyse(cube())
    with pytest.raises(OrientationError, match="load_direction"):
        score_candidates(a.candidates, a.metrics, {"load_in_plane": 1})
    with pytest.raises(OrientationError, match="visible_direction"):
        score_candidates(a.candidates, a.metrics, {"visible_surface": 1})
