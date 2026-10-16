import pytest

from cura_ai.assistant import TurnRejected
from cura_ai.cli import handle_request
from cura_ai.planner import SYSTEM_PROMPT, plan
from cura_ai.schemas import Message

HISTORY = [Message(role="user", content="mach es 200 % gross und nimm stabile Einstellungen")]
TWO = {
    "announcement": "Ich skaliere das Teil und schlage Einstellungen vor.",
    "tasks": [
        {"helper": "parts", "instruction": "Mach das Teil 200 % gross."},
        {"helper": "settings", "instruction": "Waehle stabile Einstellungen."},
    ],
}


class Client:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def complete(self, system, prompt, schema):
        self.calls.append((system, prompt, schema))
        return self.answer


def test_a_message_with_two_wishes_becomes_two_tasks_in_order():
    client = Client(TWO)
    result = plan(client, HISTORY, {"part": "t.stl 40 x 20 x 40 mm"})
    assert [t.helper for t in result.tasks] == ["parts", "settings"]
    assert result.announcement.startswith("Ich skaliere")
    system, prompt, schema = client.calls[0]
    assert system == SYSTEM_PROMPT and "t.stl 40 x 20 x 40 mm" in prompt
    assert schema["properties"]["tasks"]["maxItems"] == 3


@pytest.mark.parametrize(
    "answer",
    [
        {"tasks": []},
        {"tasks": [{"helper": "scale", "instruction": "x"}]},
        {"tasks": [{"helper": "parts", "instruction": ""}]},
        {
            "tasks": [
                {"helper": "parts", "instruction": "a"},
                {"helper": "parts", "instruction": "b"},
            ]
        },
        {"tasks": [{"helper": "parts", "instruction": "a"}] * 4},
        {"tasks": [{"helper": "parts", "instruction": "a"}], "extra": 1},
        {},
    ],
)
def test_an_answer_outside_the_schema_is_rejected(answer):
    with pytest.raises(TurnRejected):
        plan(Client(answer), HISTORY)


def test_history_must_end_with_a_user_message():
    with pytest.raises(ValueError):
        plan(Client(TWO), [Message(role="assistant", content="hi")])


def test_request_interface_returns_tasks():
    out = handle_request(
        {"action": "plan_turn", "history": [{"role": "user", "content": "x"}]}, Client(TWO)
    )
    assert out["tasks"][1] == {"helper": "settings", "instruction": "Waehle stabile Einstellungen."}
    assert out["announcement"]
