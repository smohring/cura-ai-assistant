import json

import pytest

from cura_ai.assistant import (
    SYSTEM_PROMPT,
    TurnRejected,
    build_context,
    next_turn,
    parse_turn,
    settings_turn,
)
from cura_ai.catalogue import Catalogue
from cura_ai.schemas import Message, turn_json_schema

CATALOGUE = Catalogue.from_export(
    {
        "layer_height": {"value": 0.2, "type": "float", "unit": "mm", "minimum_value": 0.04},
        "infill_sparse_density": {
            "value": 20, "type": "float", "unit": "%",
            "minimum_value": 0, "maximum_value_warning": 100,
        },
        "wall_line_count": {"value": 2, "type": "int", "minimum_value": 1},
    }
)  # fmt: skip
CONTEXT = build_context(CATALOGUE, "Flsun Q5", "generic_pla_175")
HISTORY = [Message(role="user", content="Make it stable.")]


class FakeClient:
    """Returns the queued answers in order and records every call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def complete(self, system, prompt, schema):
        self.calls.append((system, prompt, schema))
        return self.answers.pop(0)


def questions(text="Which load?"):
    return {"kind": "questions", "questions": [{"id": "load", "text": text}]}


def proposal(*pairs):
    return {
        "kind": "proposal",
        "summary": "Stronger part.",
        "overrides": [{"key": k, "value": v, "reason": "strength"} for k, v in pairs],
    }


def test_questions_answer_is_returned_without_report():
    result = settings_turn(FakeClient(questions()), CATALOGUE, "P", "M", HISTORY)
    assert result.turn.kind == "questions"
    assert result.report is None


def test_prompt_carries_limits_conversation_and_schema():
    client = FakeClient(questions())
    settings_turn(client, CATALOGUE, "Flsun Q5", "PLA", HISTORY)
    system, prompt, schema = client.calls[0]
    payload = json.loads(prompt)
    assert system == SYSTEM_PROMPT
    assert payload["context"]["printer"] == "Flsun Q5"
    row = next(r for r in payload["context"]["settings"] if r["key"] == "layer_height")
    assert row["current"] == 0.2 and row["minimum_value"] == 0.04
    assert payload["conversation"] == [{"role": "user", "content": "Make it stable."}]
    assert schema == turn_json_schema()


def test_valid_proposal_is_validated():
    client = FakeClient(proposal(("infill_sparse_density", "40"), ("wall_line_count", "3")))
    result = settings_turn(client, CATALOGUE, "P", "M", HISTORY)
    assert [a.key for a in result.report.accepted] == ["infill_sparse_density", "wall_line_count"]
    assert not result.report.rejected
    assert len(client.calls) == 1


def test_invalid_override_goes_back_to_the_model_once():
    client = FakeClient(
        proposal(("infill_sparse_density", "40"), ("layer_height", "0.01")),
        proposal(("infill_sparse_density", "40"), ("layer_height", "0.12")),
    )
    result = settings_turn(client, CATALOGUE, "P", "M", HISTORY)
    assert [a.key for a in result.report.accepted] == ["infill_sparse_density", "layer_height"]
    feedback = json.loads(client.calls[1][1])["conversation"][-1]["content"]
    assert "layer_height = 0.01" in feedback and "below the minimum" in feedback


def test_correction_that_makes_things_worse_is_discarded():
    client = FakeClient(
        proposal(("infill_sparse_density", "40"), ("layer_height", "0.01")),
        proposal(("layer_height", "0.01")),
    )
    result = settings_turn(client, CATALOGUE, "P", "M", HISTORY)
    assert [a.key for a in result.report.accepted] == ["infill_sparse_density"]
    assert [r.key for r in result.report.rejected] == ["layer_height"]


def test_no_change_is_not_sent_back():
    client = FakeClient(proposal(("infill_sparse_density", "20"), ("wall_line_count", "3")))
    result = settings_turn(client, CATALOGUE, "P", "M", HISTORY)
    assert [r.code for r in result.report.rejected] == ["no_change"]
    assert len(client.calls) == 1


def test_off_schema_answer_is_rejected_with_reason():
    client = FakeClient({"kind": "proposal", "summary": "x", "overrides": []})
    with pytest.raises(TurnRejected, match="at least one override"):
        next_turn(client, CONTEXT, HISTORY)


def test_non_json_text_is_rejected():
    with pytest.raises(TurnRejected):
        parse_turn("sure, set infill to 40")


def test_json_text_answer_is_accepted():
    assert parse_turn(json.dumps(questions())).kind == "questions"


def test_unknown_field_is_rejected_not_dropped():
    with pytest.raises(TurnRejected, match="note: Extra inputs"):
        parse_turn({**questions(), "note": "hi"})


@pytest.mark.parametrize("history", [[], [Message(role="assistant", content="Hi")]])
def test_history_must_end_with_user_message(history):
    with pytest.raises(ValueError, match="end with a user message"):
        next_turn(FakeClient(), CONTEXT, history)
