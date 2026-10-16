import pytest

from cura_ai.catalogue import Catalogue
from cura_ai.schemas import Override
from cura_ai.validation import validate_overrides


def ov(key, value, reason="because"):
    return Override(key=key, value=value, reason=reason)


def check(catalogue, key, value):
    report = validate_overrides(catalogue, [ov(key, value)])
    return report.accepted, report.rejected


def test_catalogue_reads_real_definitions(flsun):
    assert len(flsun.specs) > 700
    assert flsun.specs["infill_sparse_density"].type == "float"
    assert flsun.value("machine_center_is_zero") is True  # printer override
    assert flsun.value("material_diameter") == pytest.approx(1.75)  # extruder override


def test_every_relevant_key_exists_and_is_settable(flsun):
    from cura_ai.relevant import RELEVANT_KEYS
    from cura_ai.validation import SUPPORTED_TYPES

    missing = [k for k in RELEVANT_KEYS if k not in flsun.specs]
    assert not missing
    assert all(flsun.specs[k].type in SUPPORTED_TYPES for k in RELEVANT_KEYS)
    assert len(RELEVANT_KEYS) == len(set(RELEVANT_KEYS))


def test_formula_settings_resolve(flsun):
    assert isinstance(flsun.value("roofing_layer_count"), int | float)
    assert isinstance(flsun.value("flooring_layer_count"), int | float)


def test_most_value_formulas_evaluate(flsun):
    formulas = [k for k, s in flsun.specs.items() if isinstance(s.value_expr, str)]
    failed = []
    for k in formulas:
        try:
            if flsun.value(k) is None:
                failed.append(k)
        except KeyError:
            failed.append(k)
    assert len(failed) / len(formulas) < 0.1, failed[:20]


def test_valid_float_is_accepted_with_previous_value(flsun):
    accepted, rejected = check(flsun, "infill_sparse_density", "40")
    assert not rejected
    assert accepted[0].value == 40.0
    assert accepted[0].previous == 20
    assert accepted[0].unit == "%"


def test_unknown_key_is_rejected(flsun):
    _, rejected = check(flsun, "make_it_strong", "1")
    assert rejected[0].code == "unknown_key"


@pytest.mark.parametrize("key", ["machine_width", "machine_start_gcode", "extruder_nr"])
def test_machine_hardware_is_never_changed(flsun, key):
    _, rejected = check(flsun, key, "10")
    assert rejected[0].code == "not_allowed"


def test_below_hard_minimum_is_rejected(flsun):
    _, rejected = check(flsun, "infill_sparse_density", "-5")
    assert rejected[0].code == "out_of_range"


def test_above_hard_maximum_is_rejected(flsun):
    _, rejected = check(flsun, "support_angle", "95")
    assert rejected[0].code == "out_of_range"


def test_beyond_recommended_range_warns_but_is_accepted(flsun):
    accepted, rejected = check(flsun, "support_angle", "85")
    assert not rejected
    assert any("recommended maximum" in w for w in accepted[0].warnings)


def test_enum_must_be_a_defined_option(flsun):
    accepted, rejected = check(flsun, "infill_pattern", "gyroid")
    assert accepted and accepted[0].value == "gyroid"
    _, rejected = check(flsun, "infill_pattern", "swirly")
    assert rejected[0].code == "bad_value"


def test_bool_and_int_parsing(flsun):
    accepted, _ = check(flsun, "support_enable", "True")
    assert accepted[0].value is True
    _, rejected = check(flsun, "support_enable", "yes")
    assert rejected[0].code == "bad_value"
    _, rejected = check(flsun, "wall_line_count", "2.5")
    assert rejected[0].code == "bad_value"
    accepted, _ = check(flsun, "wall_line_count", "3")
    assert accepted[0].value == 3


def test_units_and_garbage_are_not_guessed(flsun):
    _, rejected = check(flsun, "infill_sparse_density", "40%")
    assert rejected[0].code == "bad_value"
    _, rejected = check(flsun, "infill_sparse_density", "nan")
    assert rejected[0].code == "bad_value"


def test_unsupported_types_are_rejected(flsun):
    _, rejected = check(flsun, "infill_angles", "[45]")
    assert rejected[0].code == "unsupported_type"
    _, rejected = check(flsun, "mesh_rotation_matrix", "[[1,0,0],[0,1,0],[0,0,1]]")
    assert rejected[0].code == "not_allowed"


def test_unchanged_value_is_reported_not_applied(flsun):
    _, rejected = check(flsun, "infill_sparse_density", "20")
    assert rejected[0].code == "no_change"


def test_current_values_drive_limits_and_previous(flsun):
    cat = flsun.with_current({"infill_sparse_density": 35})
    accepted, _ = check(cat, "infill_sparse_density", "40")
    assert accepted[0].previous == 35


def test_catalogue_from_export_uses_cura_evaluated_limits():
    cat = Catalogue.from_export(
        {
            "layer_height": {
                "value": 0.2,
                "type": "float",
                "unit": "mm",
                "label": "Layer Height",
                "minimum_value": 0.04,
                "maximum_value": 0.3,
            },  # fmt: skip
        }
    )
    _, rejected = check(cat, "layer_height", "0.5")
    assert rejected[0].code == "out_of_range"
    accepted, _ = check(cat, "layer_height", "0.12")
    assert accepted[0].previous == 0.2
