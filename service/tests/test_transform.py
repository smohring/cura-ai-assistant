import numpy as np
import pytest
from meshes import cube, l_bracket
from pydantic import ValidationError

from cura_ai.packing import Plate
from cura_ai.partplan import resolve_part_plan
from cura_ai.schemas import PartTurn
from cura_ai.transform import lay_flat_matrix, rotation_matrix, shape_matrix

RECT = Plate(rect=[-100, -100, 100, 100])


def plan(**fields):
    return PartTurn.model_validate({"kind": "plan", "summary": "Do it.", **fields}, strict=True)


def box(x, y, z):
    return np.array(cube(1)) * np.array([x, y, z])


def tilted(tris, rx=30, ry=20):
    m = rotation_matrix("y", ry) @ rotation_matrix("x", rx)
    return tris @ m.T


def test_rotations_are_right_handed_about_the_printer_axes():
    x = np.array([1.0, 0, 0])
    assert rotation_matrix("z", 90) @ x == pytest.approx([0, 1, 0], abs=1e-9)
    assert rotation_matrix("y", 90) @ x == pytest.approx([0, 0, -1], abs=1e-9)
    assert rotation_matrix("x", 90) @ np.array([0, 1.0, 0]) == pytest.approx([0, 0, 1], abs=1e-9)


def test_lay_flat_puts_the_largest_face_down():
    part = box(10, 30, 50)  # largest faces are the 30 x 50 ones
    laid = tilted(part) @ lay_flat_matrix(tilted(part)).T
    size = np.ptp(laid.reshape(-1, 3), axis=0)
    assert size[2] == pytest.approx(10.0)  # the 10 mm direction is up
    low = laid.reshape(-1, 3)[:, 2]
    assert np.sum(low < low.min() + 1e-6) >= 4  # four corners on the plate


def test_lay_flat_leaves_a_flat_part_alone():
    part = box(40, 30, 10)
    assert lay_flat_matrix(part) == pytest.approx(np.eye(3), abs=1e-9)


def test_lay_flat_from_upside_down_turns_it_over():
    part = box(40, 30, 10)
    m = lay_flat_matrix(part @ rotation_matrix("x", 180).T)
    assert np.ptp((part @ rotation_matrix("x", 180).T @ m.T).reshape(-1, 3)[:, 2]) == pytest.approx(
        10.0
    )


def test_mirror_keeps_the_size_and_flips_the_side():
    part = np.array(l_bracket())
    m, _ = shape_matrix(plan(mirror_axes=["x"]), part)
    assert m == pytest.approx(np.diag([-1.0, 1, 1]))
    r = resolve_part_plan(plan(mirror_axes=["x", "z"]), part, RECT)
    assert r.size_mm == [round(float(v), 2) for v in np.ptp(part.reshape(-1, 3), axis=0)]
    assert r.reshaped


def test_per_axis_scale_and_single_axis_target():
    part = box(10, 20, 30)
    r = resolve_part_plan(plan(scale_x_percent=200, scale_z_percent=50), part, RECT)
    assert r.size_mm == [20.0, 20.0, 15.0] and r.scale_xyz == [2.0, 1.0, 0.5]
    r = resolve_part_plan(plan(target_axis="y", target_mm=40, target_uniform=False), part, RECT)
    assert r.size_mm == [10.0, 40.0, 30.0]
    r = resolve_part_plan(plan(target_axis="y", target_mm=40), part, RECT)
    assert r.size_mm == [20.0, 40.0, 60.0]


def test_free_rotation_swaps_the_footprint():
    part = box(10, 20, 30)
    r = resolve_part_plan(plan(rotate_z_deg=90), part, RECT)
    assert r.size_mm == pytest.approx([20.0, 10.0, 30.0])
    r = resolve_part_plan(plan(rotate_x_deg=90), part, RECT)
    assert r.size_mm == pytest.approx([10.0, 30.0, 20.0])


def test_the_scale_target_is_measured_after_laying_flat():
    part = box(10, 30, 50)
    r = resolve_part_plan(plan(lay_flat=True, target_axis="z", target_mm=20), part, RECT)
    assert r.size_mm[2] == pytest.approx(20.0)  # the 10 mm side was up, so the factor is 2
    assert r.scale_xyz == pytest.approx([2.0] * 3)


def test_move_to_and_by_use_the_current_centre():
    part = box(20, 20, 20) - np.array([10.0, 10.0, 0]) + np.array([40.0, -10.0, 0])
    r = resolve_part_plan(plan(move_mode="to", move_x_mm=0), part, RECT)
    assert r.move == [0.0, -10.0]  # the unnamed coordinate stays (printer axes)
    r = resolve_part_plan(plan(move_mode="by", move_x_mm=5, move_y_mm=-5), part, RECT)
    assert r.move == [45.0, -5.0]  # panel y -5 is printer y +5: it moves "by" in panel terms


def test_a_move_off_the_plate_is_reported():
    r = resolve_part_plan(plan(move_mode="to", move_x_mm=95, move_y_mm=0), box(20, 20, 20), RECT)
    assert any("stick out" in n for n in r.notes)


def test_copies_use_the_reshaped_footprint():
    bar = np.array(l_bracket())
    r = resolve_part_plan(plan(rotate_z_deg=90, copies=3, allow_rotation=False), bar, RECT)
    assert r.count == 3 and r.rotations_used is False


def test_resets_lock_and_auto_drop_pass_through():
    r = resolve_part_plan(plan(reset_rotation=True, lock=True, auto_drop=False), box(5, 5, 5), RECT)
    assert r.reset_rotation and r.lock is True and r.auto_drop is False and r.size_mm == [5, 5, 5]


@pytest.mark.parametrize(
    "fields",
    [
        {"scale_percent": 150, "scale_x_percent": 200},
        {"target_axis": "z", "target_mm": 5, "scale_y_percent": 50},
        {"move_mode": "to"},
        {"move_x_mm": 3},
        {"move_mode": "to", "move_x_mm": 3, "copies": 3},
        {"reset_rotation": True, "copies": 2},
        {"reset_scale": True, "rotate_z_deg": 10},
        {"rotate_x_deg": 400},
        {"mirror_axes": ["w"]},
    ],
)
def test_contradicting_or_out_of_range_operations_are_rejected(fields):
    with pytest.raises(ValidationError):
        plan(**fields)


def test_only_a_lock_is_a_valid_plan_on_its_own():
    assert plan(lock=False).lock is False


def test_steps_describe_each_operation_once():
    r = resolve_part_plan(
        plan(lay_flat=True, scale_percent=150, move_mode="to", move_x_mm=0, move_y_mm=5, lock=True),
        box(10, 30, 50),
        RECT,
    )
    assert r.steps[0].startswith("Lay the largest flat face on the plate, Scale 150 %  ->")
    assert "Move the part centre to x 0, y 5 mm" in r.steps and "Lock the part" in r.steps
    r = resolve_part_plan(plan(scale_x_percent=200), box(10, 30, 50), RECT)
    assert r.steps == ["Scale X, Y, Z by 200 %, 100 %, 100 %  ->  20 x 30 x 50 mm"]


def test_a_mesh_type_alone_is_a_plan_and_explains_itself():
    r = resolve_part_plan(plan(mesh_type="support_blocker"), box(5, 5, 5), RECT)
    assert r.mesh_type == "support_blocker" and "support blocker" in r.steps[0]
    with pytest.raises(ValidationError):
        plan(mesh_type="wall")


def test_slice_and_export_are_plans_and_export_implies_slice():
    r = resolve_part_plan(plan(export_gcode=True), box(5, 5, 5), RECT)
    assert r.slice_now and r.export_gcode and "save the G-code" in r.steps[0]
    r = resolve_part_plan(plan(slice_now=True), box(5, 5, 5), RECT)
    assert r.slice_now and not r.export_gcode


INVENTORY = {
    "active": {"printer": "Flsun Q5", "material": "Generic PLA", "quality": "Normal"},
    "printers": [{"id": "p1", "name": "Flsun Q5"}, {"id": "p2", "name": "Ender 3"}],
    "materials": [
        {"id": "generic_pla", "name": "Generic PLA"},
        {"id": "generic_petg", "name": "Generic PETG"},
    ],
    "qualities": [{"id": "normal", "name": "Normal"}, {"id": "fast", "name": "Fast"}],
}


def test_setup_names_are_mapped_to_ids_case_insensitively():
    r = resolve_part_plan(
        plan(material="generic petg", quality="FAST"), box(5, 5, 5), RECT, inventory=INVENTORY
    )
    assert r.setup == {
        "printer": None,
        "material": "generic_petg",
        "quality": "fast",
        "discard": False,
    }
    assert r.steps == ["Switch the material to Generic PETG", "Switch the quality to Fast"]


def test_unknown_names_are_an_error_listing_the_choices():
    with pytest.raises(ValueError, match="unknown material 'wood'.*Generic PLA"):
        resolve_part_plan(plan(material="wood"), box(5, 5, 5), RECT, inventory=INVENTORY)
    with pytest.raises(ValueError, match="unknown printer"):
        resolve_part_plan(plan(printer="Ender 3"), box(5, 5, 5), RECT, inventory={})


def test_printer_switch_stands_alone_and_discard_warns():
    with pytest.raises(ValidationError):
        plan(printer="Ender 3", material="Generic PLA")
    r = resolve_part_plan(plan(discard_changes=True), box(5, 5, 5), RECT)
    assert r.setup["discard"] and "cannot be undone" in r.steps[0]


def test_context_carries_names_only():
    from cura_ai.part_assistant import build_context

    ctx = build_context([1, 2, 3], [100, 100], 200, 0, INVENTORY)
    assert ctx["available_materials"] == ["Generic PLA", "Generic PETG"]
    assert ctx["active"]["printer"] == "Flsun Q5" and "id" not in str(ctx["available_printers"])


def test_without_a_part_only_setup_and_slicing_can_be_planned():
    r = resolve_part_plan(plan(material="Generic PLA"), None, RECT, inventory=INVENTORY)
    assert r.setup["material"] == "generic_pla" and r.placements == []
    assert resolve_part_plan(plan(slice_now=True), None, RECT).slice_now
    for fields in ({"scale_percent": 150}, {"copies": 3}, {"lock": True}, {"lay_flat": True}):
        with pytest.raises(ValueError, match="exactly one part"):
            resolve_part_plan(plan(**fields), None, RECT)


def test_request_without_a_stl_works_for_setup(tmp_path):
    from cura_ai.cli import handle_request

    class Client:
        def complete(self, system, prompt, schema):
            return {"kind": "plan", "summary": "Switch.", "quality": "Fast"}

    out = handle_request(
        {
            "action": "part_turn",
            "stl": None,
            "plate": RECT.model_dump(),
            "inventory": INVENTORY,
            "history": [{"role": "user", "content": "fast please"}],
        },
        Client(),
    )
    assert out["kind"] == "plan" and out["plan"]["setup"]["quality"] == "fast"
