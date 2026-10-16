import pytest
from pydantic import ValidationError

from cura_ai.schemas import Turn, turn_json_schema

QUESTION = {"id": "load", "text": "Which direction is the load?"}
OVERRIDE = {"key": "infill_sparse_density", "value": "40", "reason": "More infill for strength."}


def test_valid_questions_turn():
    turn = Turn.model_validate({"kind": "questions", "questions": [QUESTION]}, strict=True)
    assert turn.questions[0].id == "load"


def test_valid_proposal_turn():
    turn = Turn.model_validate(
        {"kind": "proposal", "summary": "Stronger part.", "overrides": [OVERRIDE]}, strict=True
    )
    assert turn.overrides[0].key == "infill_sparse_density"


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "questions"},
        {"kind": "questions", "questions": [QUESTION], "overrides": [OVERRIDE]},
        {"kind": "questions", "questions": [QUESTION], "summary": "x"},
        {"kind": "proposal", "summary": "x"},
        {"kind": "proposal", "overrides": [OVERRIDE]},
        {"kind": "proposal", "summary": "x", "overrides": [OVERRIDE], "questions": [QUESTION]},
        {"kind": "proposal", "summary": "x", "overrides": [OVERRIDE, OVERRIDE]},
        {"kind": "maybe", "questions": [QUESTION]},
        {"kind": "questions", "questions": [QUESTION], "extra": 1},
        {"kind": "proposal", "summary": "x", "overrides": [{**OVERRIDE, "value": 40}]},
        {"kind": "proposal", "summary": "x", "overrides": [{**OVERRIDE, "reason": ""}]},
    ],
)
def test_invalid_turns_are_rejected(payload):
    with pytest.raises(ValidationError):
        Turn.model_validate(payload, strict=True)


def test_json_schema_is_an_object_schema():
    schema = turn_json_schema()
    assert schema["type"] == "object"
    assert schema["required"] == ["kind"]
    assert schema["additionalProperties"] is False
