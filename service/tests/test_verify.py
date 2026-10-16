import pytest
from meshes import cube, t_shape

from cura_ai.engine import EngineError, SliceResult, find_engine
from cura_ai.orientation import orient
from cura_ai.verify import SUPPORT_FOR_COMPARISON, verify_orientations, verify_settings


def fake_slicer(calls):
    def slicer(stl, values, printer_id, extruder_id):
        calls.append((stl.name, dict(values)))
        if values.get("explode"):
            raise EngineError("boom")
        time = int(values.get("layer_height", 0.2) * -1000) + 5000
        return SliceResult(print_time_s=time, filament_mm3=100.0, support_mm3=0.0, layers=1)

    return slicer


def test_orientations_are_sliced_with_support_forced_and_errors_are_kept():
    calls = []
    ranked = orient(t_shape(), {"low_support": 1})[:3]
    out = verify_orientations(
        t_shape(), ranked, {"layer_height": 0.2}, "p", "e", slicer=fake_slicer(calls)
    )
    assert [v.id for v in out] == [c.id for c in ranked]
    assert all(v.result and not v.error for v in out)
    assert all(values.get(k) == v for _, values in calls for k, v in SUPPORT_FOR_COMPARISON.items())

    out = verify_orientations(
        t_shape(), ranked[:1], {"explode": True}, "p", "e", slicer=fake_slicer([])
    )
    assert out[0].result is None and out[0].error == "boom"


def test_settings_comparison_reports_percent_changes():
    cmp = verify_settings(
        cube(), {"layer_height": 0.2}, {"layer_height": 0.3}, "p", "e", slicer=fake_slicer([])
    )
    assert cmp.current.print_time_s == 4800 and cmp.proposed.print_time_s == 4700
    assert cmp.time_change_pct == pytest.approx(-100 / 4800 * 100)
    assert cmp.material_change_pct == 0


def test_real_engine_support_differs_between_upright_and_upside_down_t(flsun):
    try:
        find_engine()
    except EngineError:
        pytest.skip("CuraEngine not installed")
    ranked = orient(t_shape(), {"low_support": 1})
    upright = next(s for s in ranked if s.id == "c00")
    best = ranked[0]
    out = {
        v.id: v
        for v in verify_orientations(
            t_shape(), [upright, best], flsun.all_values(), "flsun_q5", "flsun_q5_extruder"
        )
    }
    assert out[upright.id].result.support_mm3 > 2 * out[best.id].result.support_mm3 + 50
