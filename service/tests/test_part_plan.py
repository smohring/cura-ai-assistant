import json

import numpy as np
import pytest
from meshes import cube, l_bracket
from pydantic import ValidationError

from cura_ai.assistant import TurnRejected
from cura_ai.cli import adhesion_margin, handle_request
from cura_ai.mesh import write_stl
from cura_ai.packing import Plate
from cura_ai.part_assistant import SYSTEM_PROMPT, build_context, part_turn
from cura_ai.partplan import resolve_part_plan
from cura_ai.schemas import Message, PartTurn, part_json_schema

RECT = Plate(rect=[-100, -100, 100, 100])
HISTORY = [Message(role="user", content="mach es 150 Prozent gross")]


def plan(**fields):
    return PartTurn.model_validate({"kind": "plan", "summary": "Do it.", **fields}, strict=True)


class FakeClient:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def complete(self, system, prompt, schema):
        self.calls.append((system, prompt, schema))
        return self.answers.pop(0)


# --- schema ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "questions"},
        {"kind": "questions", "questions": [{"id": "a", "text": "b"}], "scale_percent": 150},
        {"kind": "plan", "summary": "x"},
        {"kind": "plan", "scale_percent": 150},
        {"kind": "plan", "summary": "x", "scale_percent": 0},
        {"kind": "plan", "summary": "x", "scale_percent": 5000},
        {"kind": "plan", "summary": "x", "copies": 0},
        {"kind": "plan", "summary": "x", "copies": 501},
        {"kind": "plan", "summary": "x", "target_axis": "z"},
        {"kind": "plan", "summary": "x", "target_mm": 80},
        {"kind": "plan", "summary": "x", "scale_percent": 150, "target_axis": "z", "target_mm": 5},
        {"kind": "plan", "summary": "x", "copies": 3, "spacing_mm": -1},
        {"kind": "plan", "summary": "x", "copies": 3, "delete": True},
    ],
)
def test_invalid_part_turns_are_rejected(payload):
    with pytest.raises(ValidationError):
        PartTurn.model_validate(payload, strict=True)


def test_valid_turns_and_flat_schema():
    assert plan(scale_percent=150).scale_percent == 150
    assert plan(target_axis="z", target_mm=80).target_mm == 80
    assert plan(fill_plate=True).spacing_mm == 3
    text = json.dumps(part_json_schema())
    assert "$ref" not in text and "target_axis" in text


# --- numbers -----------------------------------------------------------------------------------


def test_scale_by_percent_and_by_target_size():
    r = resolve_part_plan(plan(scale_percent=150), cube(20), RECT)
    assert r.scale_xyz == [1.5] * 3 and r.size_mm == [30.0, 30.0, 30.0] and r.count == 1
    r = resolve_part_plan(plan(target_axis="z", target_mm=80), l_bracket(), RECT)
    assert r.scale_xyz == pytest.approx([2.0] * 3) and r.size_mm == [80.0, 40.0, 80.0]


def test_warnings_for_height_and_for_a_part_too_big_for_the_plate():
    r = resolve_part_plan(plan(scale_percent=1000), cube(30), RECT, build_height=200)
    assert any("taller than the build height" in n for n in r.notes)
    assert any("does not fit on the plate" in n for n in r.notes)


def test_copies_and_fill_plate_give_placements_with_the_scale_applied():
    r = resolve_part_plan(plan(scale_percent=150, copies=6), cube(20), RECT)
    assert r.count == 6 and len(r.placements) == 6 and r.notes == []
    full = resolve_part_plan(plan(fill_plate=True, spacing_mm=2), cube(20), RECT)
    assert full.count >= 80 and full.requested is None


def test_not_everything_fits_is_reported():
    r = resolve_part_plan(plan(copies=100), cube(40), RECT)
    assert r.count < 100 and any("only" in n and "of 100" in n for n in r.notes)


def test_rotation_can_be_forbidden():
    bar = np.array(l_bracket())
    r = resolve_part_plan(plan(copies=4, allow_rotation=False), bar, RECT)
    assert r.rotations_used is False


def test_adhesion_margin_follows_the_adhesion_type():
    assert adhesion_margin({"adhesion_type": "brim", "brim_width": 8.0}) == 8.0
    assert adhesion_margin({"adhesion_type": "raft", "raft_margin": 15.0}) == 15.0
    assert adhesion_margin({"adhesion_type": "none", "brim_width": 8.0}) == 0.0


# --- the dialogue and the request ----------------------------------------------------------------


def test_prompt_lists_only_the_allowed_operations_and_carries_the_context():
    client = FakeClient({"kind": "plan", "summary": "x", "scale_percent": 150})
    ctx = build_context([20, 20, 20], [200, 200], 200, 0)
    turn = part_turn(client, ctx, HISTORY)
    system, prompt, _ = client.calls[0]
    assert turn.scale_percent == 150 and system == SYSTEM_PROMPT
    assert json.loads(prompt)["context"]["part_size_mm"] == [20.0, 20.0, 20.0]
    assert "computed by the program" in system


def test_off_schema_answer_is_rejected():
    with pytest.raises(TurnRejected):
        bad = {"kind": "plan", "summary": "x", "rotate_z": 90}
        part_turn(FakeClient(bad, bad), {}, HISTORY)


def test_an_off_schema_answer_gets_one_correction_round():
    client = FakeClient(
        {"kind": "plan", "summary": "x"},  # no operation
        {"kind": "plan", "summary": "x", "scale_percent": 150},
    )
    assert part_turn(client, {}, HISTORY).scale_percent == 150
    assert len(client.calls) == 2 and "Rejected" in client.calls[1][1]


def request(tmp_path, answer, **extra):
    stl = tmp_path / "part.stl"
    write_stl(stl, cube(20))
    payload = {
        "action": "part_turn",
        "stl": str(stl),
        "plate": RECT.model_dump(),
        "build_height": 200,
        "history": [{"role": "user", "content": "x"}],
        **extra,
    }
    return handle_request(payload, FakeClient(answer))


def test_request_returns_the_plan_with_placements(tmp_path):
    out = request(
        tmp_path,
        {"kind": "plan", "summary": "Scale and fill.", "scale_percent": 150, "fill_plate": True},
    )
    assert out["kind"] == "plan" and out["plan"]["scale_xyz"] == [1.5] * 3
    assert out["plan"]["count"] == len(out["plan"]["placements"]) >= 20
    assert json.loads(out["turn"])["fill_plate"] is True


def test_request_with_questions_has_no_plan(tmp_path):
    out = request(tmp_path, {"kind": "questions", "questions": [{"id": "n", "text": "How many?"}]})
    assert out["kind"] == "questions" and out["plan"] is None


def test_brim_width_from_the_request_costs_room(tmp_path):
    answer = {"kind": "plan", "summary": "Fill.", "fill_plate": True, "spacing_mm": 2}
    plain = request(tmp_path, answer)["plan"]["count"]
    brim = request(tmp_path, answer, adhesion={"adhesion_type": "brim", "brim_width": 8.0})
    assert 0 < brim["plan"]["count"] < plain


def test_obstacles_reduce_the_room(tmp_path):
    other = tmp_path / "other.stl"
    write_stl(other, np.array(cube(100)) + [-100, -100, 0])  # a 100 mm block over a quarter
    answer = {"kind": "plan", "summary": "Fill.", "fill_plate": True, "spacing_mm": 2}
    free = request(tmp_path, answer)["plan"]["count"]
    blocked = request(tmp_path, answer, obstacles_stl=str(other), other_parts=1)["plan"]["count"]
    assert 0 < blocked < free
