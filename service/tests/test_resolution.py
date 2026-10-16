import pytest
from meshes import cube

from cura_ai import cli
from cura_ai.engine import EngineError, find_engine
from cura_ai.resolution import naive_merge, resolve_with_overrides
from cura_ai.verify import verify_settings


@pytest.fixture(scope="module")
def values(flsun):
    return flsun.all_values()


def test_derived_settings_follow_an_overridden_input(flsun, values):
    out = resolve_with_overrides(flsun, values, {"infill_sparse_density": 40})
    assert out["infill_sparse_density"] == 40
    assert out["infill_line_distance"] == pytest.approx(1.0)  # 0.4 / 0.4 * 2
    assert naive_merge(values, {"infill_sparse_density": 40})["infill_line_distance"] == 4.0


def test_explicitly_set_derived_settings_are_kept(flsun, values):
    pinned = {**values, "infill_line_distance": 3.3}
    out = resolve_with_overrides(flsun, pinned, {"layer_height": 0.3})
    assert out["infill_line_distance"] == 3.3
    assert out["layer_height"] == 0.3


def test_an_override_of_a_derived_setting_wins(flsun, values):
    out = resolve_with_overrides(flsun, values, {"infill_line_distance": 5.0})
    assert out["infill_line_distance"] == 5.0


def test_unchanged_inputs_change_nothing(flsun, values):
    assert resolve_with_overrides(flsun, values, {}) == values


def test_unknown_keys_pass_through_untouched(flsun, values):
    out = resolve_with_overrides(flsun, {**values, "some_plugin_key": 7}, {})
    assert out["some_plugin_key"] == 7


def test_density_change_is_visible_in_a_real_test_slice(flsun, values):
    try:
        find_engine()
    except EngineError:
        pytest.skip("CuraEngine not installed")
    args = (cube(), values, {"infill_sparse_density": 40}, "flsun_q5", "flsun_q5_extruder")
    stale = verify_settings(*args)
    fixed = verify_settings(*args, resolve=lambda v, o: resolve_with_overrides(flsun, v, o))
    assert stale.material_change_pct < 1  # the bug: the engine kept the old line distance
    assert fixed.material_change_pct > 5


def test_cli_resolver_uses_definitions_when_available(flsun, values):
    from cura_ai.jobexport import JobExport, PrinterInfo

    printer = PrinterInfo(id="flsun_q5", extruder_id="flsun_q5_extruder")
    job = JobExport.from_catalogue(flsun, printer)
    out = cli._resolver(job)(job.values(), {"infill_sparse_density": 40})
    assert out["infill_line_distance"] == pytest.approx(1.0)
    unknown = JobExport(printer=PrinterInfo(id="no_such_printer"), settings={})
    assert cli._resolver(unknown) is naive_merge
