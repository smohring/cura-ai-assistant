import pytest
from meshes import cube

from cura_ai.catalogue import DefinitionNotFound
from cura_ai.engine import EngineError, find_engine, gcode_stats, slice_model, write_overrides
from cura_ai.mesh import write_stl

GCODE = """\
;TIME:100
;LAYER_COUNT:2
M82
G92 E0
;TYPE:WALL-OUTER
G1 F1500 X1 Y1 E10
G1 F1500 E8
G1 F1500 E10
G1 F1500 X2 Y2 E15
;TYPE:SUPPORT
G1 X3 Y3 E25
;TYPE:SUPPORT-INTERFACE
G1 X4 Y4 E30
"""


def test_gcode_stats_nets_out_retraction_and_splits_by_type():
    stats = gcode_stats(GCODE, filament_diameter=2.0)  # area = pi mm^2
    pi = 3.141592653589793
    assert stats.layers == 2 and stats.time_s == 100
    assert stats.extruded_mm3["WALL-OUTER"] == pytest.approx(15 * pi)
    assert stats.extruded_mm3["SUPPORT"] == pytest.approx(10 * pi)
    assert stats.extruded_mm3["SUPPORT-INTERFACE"] == pytest.approx(5 * pi)


def test_relative_extrusion_mode():
    stats = gcode_stats("M83\n;TYPE:FILL\nG1 E2\nG1 E-1\nG1 E3\n", filament_diameter=2.0)
    assert stats.extruded_mm3["FILL"] == pytest.approx(4 * 3.141592653589793)


def test_write_overrides_pins_values_and_skips_none(tmp_path):
    p = tmp_path / "o.def.json"
    write_overrides(p, {"layer_height": 0.2, "gone": None})
    text = p.read_text()
    assert '"layer_height": {"default_value": 0.2}' in text and "gone" not in text


@pytest.fixture(scope="module")
def engine_ready():
    try:
        find_engine()
    except EngineError:
        pytest.skip("CuraEngine not installed")


def test_real_engine_slices_a_cube_and_layer_height_matters(flsun, engine_ready, tmp_path):
    stl = tmp_path / "cube.stl"
    write_stl(stl, cube())
    values = flsun.all_values()
    fine = slice_model(stl, {**values, "layer_height": 0.1}, "flsun_q5", "flsun_q5_extruder")
    coarse = slice_model(stl, {**values, "layer_height": 0.3}, "flsun_q5", "flsun_q5_extruder")
    assert fine.layers > 2 * coarse.layers
    assert fine.print_time_s > coarse.print_time_s > 0
    assert fine.filament_mm3 > 0 and fine.support_mm3 == 0


def test_engine_failure_is_reported(flsun, engine_ready, tmp_path):
    bad = tmp_path / "missing.stl"
    with pytest.raises((EngineError, DefinitionNotFound)):
        slice_model(bad, flsun.all_values(), "flsun_q5", "flsun_q5_extruder")
