import json

import pytest
from meshes import l_bracket
from pydantic import ValidationError

from cura_ai.assistant import TurnRejected
from cura_ai.orient_assistant import orient_turn
from cura_ai.orientation import analyse, score_candidates
from cura_ai.schemas import Message, OrientTurn, orient_json_schema, turn_json_schema

HISTORY = [Message(role="user", content="Sehr belastbar bitte.")]
WEIGHTS = {"load_in_plane": 5, "low_height": 3}


class FakeClient:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def complete(self, system, prompt, schema):
        self.calls.append((system, prompt, schema))
        return self.answers.pop(0)


@pytest.fixture(scope="module")
def analysis():
    return analyse(l_bracket(), load_direction=(0, 0, 1))


def choice(candidate_id, weights=WEIGHTS):
    return {
        "kind": "choice",
        "weights": weights,
        "chosen_candidate_id": candidate_id,
        "reasoning": "Layers run along the load.",
    }


def ranking_ids(analysis, weights=WEIGHTS):
    return [s.id for s in score_candidates(analysis.candidates, analysis.metrics, weights)]


def test_questions_pass_through(analysis):
    client = FakeClient({"kind": "questions", "questions": [{"id": "load", "text": "Which way?"}]})
    result = orient_turn(client, analysis, HISTORY, load_given=True, visible_given=False)
    assert result.kind == "questions" and result.chosen is None


def test_valid_choice_is_ranked_with_the_models_weights(analysis):
    best = ranking_ids(analysis)[0]
    result = orient_turn(FakeClient(choice(best)), analysis, HISTORY, True, False)
    assert result.chosen.id == best and result.chosen_rank == 1
    assert [s.id for s in result.ranking] == ranking_ids(analysis)
    assert result.chosen.rotation_quaternion_scene.w != 0


def test_prompt_lists_candidates_and_which_directions_exist(analysis):
    client = FakeClient(choice(ranking_ids(analysis)[0]))
    orient_turn(client, analysis, HISTORY, load_given=True, visible_given=False)
    payload = json.loads(client.calls[0][1])
    assert payload["directions_given"] == {"load": True, "visible": False}
    assert payload["candidates"][0]["id"] == "c00"
    assert "load_across_layers" in payload["candidates"][0]
    assert "visible_quality" not in payload["candidates"][0]


def test_unlikely_choice_goes_back_once_then_is_accepted(analysis):
    order = ranking_ids(analysis)
    client = FakeClient(choice(order[-1]), choice(order[0]))
    result = orient_turn(client, analysis, HISTORY, True, False)
    assert result.chosen.id == order[0]
    feedback = json.loads(client.calls[1][1])["conversation"][-1]["content"]
    assert "under your own weights" in feedback


def test_persistently_inconsistent_choice_is_rejected(analysis):
    order = ranking_ids(analysis)
    client = FakeClient(choice(order[-1]), choice(order[-1]))
    with pytest.raises(TurnRejected, match="under your own weights"):
        orient_turn(client, analysis, HISTORY, True, False)


def test_invented_candidate_id_is_rejected(analysis):
    client = FakeClient(choice("c99"), choice("c98"))
    with pytest.raises(TurnRejected, match="unknown candidate id"):
        orient_turn(client, analysis, HISTORY, True, False)


def test_weight_on_a_missing_direction_is_rejected():
    no_load = analyse(l_bracket())
    answer = choice("c00", {"load_in_plane": 5})
    with pytest.raises(TurnRejected, match="load_direction"):
        orient_turn(FakeClient(answer, answer), no_load, HISTORY, False, False)


def test_off_schema_answer_is_rejected(analysis):
    with pytest.raises(TurnRejected, match="Extra inputs"):
        bad = {**choice("c00"), "angle": 37}  # still wrong after the one correction round
        orient_turn(FakeClient(bad, bad), analysis, HISTORY, True, False)


def test_an_off_schema_answer_gets_one_correction_round(analysis):
    both = {**choice("c01"), "questions": [{"id": "a", "text": "b"}]}  # questions and a choice
    client = FakeClient(both, choice("c01"))
    result = orient_turn(client, analysis, HISTORY, True, False)
    assert result.kind == "choice" and len(client.calls) == 2
    retry = json.loads(client.calls[1][1])["conversation"]
    assert retry[-1]["role"] == "user" and "Rejected" in retry[-1]["content"]
    assert retry[-2]["role"] == "assistant" and "questions" in retry[-2]["content"]


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "questions"},
        {"kind": "choice"},
        choice("c00", {"low_height": 0}),
        choice("c00", {"low_height": 11}),
        choice("c00", {"sparkle": 1}),
        {**choice("c00"), "reasoning": ""},
        {**choice("c00"), "questions": [{"id": "a", "text": "b"}]},
    ],
)
def test_invalid_orient_turns_are_rejected(payload):
    with pytest.raises(ValidationError):
        OrientTurn.model_validate(payload, strict=True)


def test_schemas_are_flat_for_the_cli():
    for schema in (orient_json_schema(), turn_json_schema()):
        text = json.dumps(schema)
        assert "$ref" not in text and "$defs" not in text
        assert schema["type"] == "object"
