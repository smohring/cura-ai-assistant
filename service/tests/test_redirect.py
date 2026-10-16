import pytest

from cura_ai.assistant import TurnRejected
from cura_ai.cli import handle_request


class Client:
    def __init__(self, answer):
        self.answer = answer

    def complete(self, system, prompt, schema):
        return self.answer


# --- a helper that is asked something that is not its own passes it on ---------------------------

from cura_ai.assistant import parse_turn  # noqa: E402
from cura_ai.orient_assistant import _parse as parse_orient  # noqa: E402
from cura_ai.part_assistant import parse_turn as parse_part  # noqa: E402


def test_each_helper_accepts_a_redirect_to_another_one_only():
    assert parse_turn({"kind": "redirect", "redirect_to": "parts"}).redirect_to == "parts"
    assert parse_orient({"kind": "redirect", "redirect_to": "settings"}).redirect_to == "settings"
    assert (
        parse_part({"kind": "redirect", "redirect_to": "orientation"}).redirect_to == "orientation"
    )
    for parser, own in (
        (parse_turn, "settings"),
        (parse_orient, "orientation"),
        (parse_part, "parts"),
    ):
        with pytest.raises(TurnRejected):
            parser({"kind": "redirect", "redirect_to": own})  # to itself
        with pytest.raises(TurnRejected):
            parser({"kind": "redirect"})  # to nowhere
    with pytest.raises(TurnRejected):
        parse_part({"kind": "redirect", "redirect_to": "settings", "summary": "x"})
    with pytest.raises(TurnRejected):
        parse_turn(
            {
                "kind": "proposal",
                "summary": "x",
                "redirect_to": "parts",
                "overrides": [{"key": "a", "value": "1", "reason": "r"}],
            }
        )


def test_requests_report_the_redirect_for_every_helper(tmp_path):
    from meshes import cube

    from cura_ai.mesh import write_stl

    stl = tmp_path / "c.stl"
    write_stl(stl, cube(20))
    history = [{"role": "user", "content": "mach es 200 % gross"}]
    out = handle_request(
        {
            "action": "orient_turn",
            "stl": str(stl),
            "history": history,
        },
        Client({"kind": "redirect", "redirect_to": "parts"}),
    )
    assert out["kind"] == "redirect" and out["redirect_to"] == "parts"
    assert out["chosen"] is None and out["ranking"] == []
    out = handle_request(
        {
            "action": "part_turn",
            "stl": str(stl),
            "plate": {"rect": [-100, -100, 100, 100], "disallowed": []},
            "history": history,
        },
        Client({"kind": "redirect", "redirect_to": "settings"}),
    )
    assert out["kind"] == "redirect" and out["redirect_to"] == "settings" and out["plan"] is None
