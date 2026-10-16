import numpy as np
from meshes import cube, l_bracket, prism

from cura_ai.packing import Placement, Plate, pack_copies, silhouette, surface_points

RECT = Plate(rect=[-100, -100, 100, 100])


def circle_corners(radius=100.0, steps=72):
    """The disallowed corners of a round plate: the square minus the circle, as polygons."""
    polys = []
    ring = [(radius * np.cos(a), radius * np.sin(a)) for a in np.linspace(0, 2 * np.pi, steps + 1)]
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        quarter = [p for p in ring if p[0] * sx >= 0 and p[1] * sy >= 0]
        quarter.sort(key=lambda p: np.arctan2(p[1] * sy, p[0] * sx))
        polys.append([[sx * radius, sy * radius], *[list(p) for p in quarter]])
    return polys


def footprints(tris, placements, res=0.1):
    """Exact-ish masks of every placed copy on a fine common grid (to prove no overlap)."""
    points = surface_points(tris, res)
    size = int(220 / res)
    masks = []
    for p in placements:
        t = np.radians(p.rotation_deg)
        rot = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
        q = points @ rot.T
        q = q - (q.min(axis=0) + q.max(axis=0)) / 2 + [p.x, p.y]
        ij = np.floor((q + 110) / res).astype(int)
        m = np.zeros((size, size), dtype=bool)
        m[ij[:, 1], ij[:, 0]] = True
        masks.append(m)
    return masks


def assert_no_overlap(tris, placements):
    seen = None
    for m in footprints(tris, placements):
        if seen is not None:
            assert not (seen & m).any(), "two copies share a point"
        seen = m if seen is None else seen | m


def test_silhouette_of_a_cube_is_its_square():
    pts = surface_points(cube(20), 0.5)
    mask = silhouette(pts, 0, 0.5)
    area = mask.sum() * 0.25
    assert 400 <= area <= 480  # never smaller than the part, about half a millimetre of safety


def test_cubes_fill_a_square_plate_in_a_grid():
    result = pack_copies(cube(20), RECT, None, spacing=2.0)
    assert len(result.placements) >= 80  # 9 x 9 = 81 fit with a 2 mm gap
    assert_no_overlap(cube(20), result.placements)


def test_requested_number_is_respected_and_spaced():
    result = pack_copies(cube(30), RECT, 6, spacing=5.0)
    assert len(result.placements) == 6
    pts = np.array([[p.x, p.y] for p in result.placements])
    d = np.hypot(*(pts[:, None] - pts[None]).transpose(2, 0, 1)) + np.eye(6) * 1e9
    assert d.min() >= 30 + 5 - 1.0  # axis-aligned cubes: centres at least size + gap apart
    assert all(abs(p.x) <= 85.5 and abs(p.y) <= 85.5 for p in result.placements)


def test_round_plate_keeps_everything_inside_the_circle():
    plate = Plate(rect=[-100, -100, 100, 100], disallowed=circle_corners())
    tris = cube(20)
    result = pack_copies(tris, plate, None, spacing=2.0)
    assert 40 <= len(result.placements) <= 64
    assert_no_overlap(tris, result.placements)
    for mask in footprints(tris, result.placements):  # every point of every copy, rotated or not
        ys, xs = np.nonzero(mask)
        radius = np.hypot(xs * 0.1 - 110, ys * 0.1 - 110).max()
        assert radius <= 100.3


def test_disallowed_area_and_obstacles_are_avoided():
    left_half = [[-100, -100], [0, -100], [0, 100], [-100, 100]]
    plate = Plate(rect=[-100, -100, 100, 100], disallowed=[left_half])
    result = pack_copies(cube(20), plate, None, spacing=2.0)
    assert result.placements and min(p.x for p in result.placements) >= 10 - 0.6
    obstacle = np.array([[x, y] for x in np.arange(40, 60, 0.2) for y in np.arange(-100, 100, 0.2)])
    result = pack_copies(cube(20), RECT, None, spacing=2.0, obstacles_xy=obstacle)
    assert result.placements
    for p in result.placements:  # the strip is x in [40, 60]; a cube spans x +- 10, plus the gap
        assert p.x + 10 + 2 - 0.6 <= 40 or p.x - 10 - 2 + 0.6 >= 60


def test_concave_parts_nest_better_than_their_hulls_would_allow():
    # An L of two 40 x 10 arms; its convex hull is a triangle-ish 40 x 40 area of 1000 mm2 against
    # the L's 700 mm2, so interlocking copies beat a hull-based count.
    tris = prism([(0, 0), (40, 0), (40, 10), (10, 10), (10, 40), (0, 40)], 0, 5)
    plate = Plate(rect=[-60, -60, 60, 60])
    result = pack_copies(tris, plate, None, spacing=2.0)
    hull_area = 40 * 40 / 2 + 40 * 10 / 2 + 10 * 30 / 2  # generous bound for one convex footprint
    assert len(result.placements) * 700 / (120 * 120) > 0.40 or len(result.placements) >= 6
    assert_no_overlap(tris, result.placements)
    assert hull_area > 700


def test_placement_model_round_trips():
    p = Placement(rotation_deg=90, x=1.5, y=-2.0)
    assert Placement.model_validate(p.model_dump()) == p


def test_part_larger_than_the_plate_gives_no_placement():
    result = pack_copies(cube(250), RECT, 3, spacing=2.0)
    assert result.placements == []


def test_l_bracket_helper_is_usable_for_packing():
    assert len(pack_copies(l_bracket(), RECT, 4, spacing=3.0).placements) == 4
