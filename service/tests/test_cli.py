import json

import pytest
from meshes import cube, l_bracket

from cura_ai.cli import handle_request, main, parse_request, run_chat
from cura_ai.engine import EngineError, find_engine
from cura_ai.jobexport import JobExport, PrinterInfo
from cura_ai.llm import LlmError
from cura_ai.mesh import write_stl
from cura_ai.orientation import analyse, score_candidates

HISTORY = [{"role": "user", "content": "Stabil bitte."}]


class FakeClient:
    def __init__(self, *answers):
        self.answers = list(answers)

    def complete(self, system, prompt, schema):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(scope="module")
def job(flsun):
    printer = PrinterInfo(id="flsun_q5", name="Flsun Q5", extruder_id="flsun_q5_extruder")
    return JobExport.from_catalogue(flsun, printer).model_dump(mode="json")


def proposal(*pairs):
    return {
        "kind": "proposal",
        "summary": "Stronger.",
        "overrides": [{"key": k, "value": v, "reason": "r"} for k, v in pairs],
    }


def test_export_built_from_definitions_validates_like_the_definitions(job):
    mixed = proposal(("infill_sparse_density", "40"), ("support_angle", "95"), ("x", "1"))
    out = handle_request(  # the model gets one chance to correct, here it repeats itself
        {"action": "settings_turn", "job": job, "history": HISTORY}, FakeClient(mixed, mixed)
    )
    assert out["kind"] == "proposal"
    assert [a["key"] for a in out["accepted"]] == ["infill_sparse_density"]
    assert out["accepted"][0]["previous"] == 20
    codes = {r["key"]: r["code"] for r in out["rejected"]}
    assert codes == {"support_angle": "out_of_range", "x": "unknown_key"}


def test_settings_questions_are_returned(job):
    q = {"kind": "questions", "questions": [{"id": "load", "text": "Wie belastet?"}]}
    out = handle_request({"action": "settings_turn", "job": job, "history": HISTORY}, FakeClient(q))
    assert out["kind"] == "questions" and out["questions"][0]["text"] == "Wie belastet?"
    assert out["accepted"] == []


def test_expected_failures_become_error_objects(job):
    out = handle_request(
        {"action": "settings_turn", "job": job, "history": HISTORY}, FakeClient(LlmError("offline"))
    )
    assert out == {"error": "offline", "type": "LlmError"}
    assert handle_request({"action": "nope"})["type"] == "ValueError"
    bad = handle_request({"action": "orient_turn", "stl": "missing.stl", "history": HISTORY})
    assert "error" in bad


def test_orient_turn_over_a_real_file(tmp_path, job):
    stl = tmp_path / "bracket.stl"
    write_stl(stl, l_bracket())
    weights = {"load_in_plane": 5, "low_height": 3}
    a = analyse(l_bracket(), load_direction=[0, 0, 1])
    best = score_candidates(a.candidates, a.metrics, weights)[0].id
    answer = {
        "kind": "choice",
        "weights": weights,
        "chosen_candidate_id": best,
        "reasoning": "Profile face down.",
    }
    out = handle_request(
        {"action": "orient_turn", "stl": str(stl), "history": HISTORY, "load_direction": [0, 0, 1]},
        FakeClient(answer),
    )
    assert out["kind"] == "choice" and out["chosen"]["id"] == best and out["chosen_rank"] == 1
    echoed = json.loads(out["turn"])  # what the plugin stores as the assistant's history entry
    assert echoed["chosen_candidate_id"] == best and "ranking" not in echoed
    assert out["ranking"][0]["id"] == best and len(out["ranking"]) <= 8


def test_real_engine_requests(tmp_path, job):
    try:
        find_engine()
    except EngineError:
        pytest.skip("CuraEngine not installed")
    stl = tmp_path / "cube.stl"
    write_stl(stl, cube())
    out = handle_request(
        {
            "action": "verify_settings",
            "stl": str(stl),
            "job": job,
            "overrides": {"layer_height": 0.3},
        }
    )
    assert out["time_change_pct"] < -20 and out["current"]["layers"] > out["proposed"]["layers"]
    rows = handle_request(
        {
            "action": "verify_orientations",
            "stl": str(stl),
            "job": job,
            "weights": {"low_height": 1},
            "top_n": 2,
        }
    )["rows"]
    assert len(rows) == 2 and rows[0]["verified"]["result"]["print_time_s"] > 0


@pytest.mark.parametrize(
    ("requested", "passed"),
    [("opus", "opus"), (None, "sonnet"), ("default", None), ("fable", "fable")],
)
def test_requested_model_reaches_the_client(monkeypatch, job, requested, passed):
    seen = []

    class Recorder(FakeClient):
        def __init__(self, model, effort=None):
            seen.append(model)
            super().__init__({"kind": "questions", "questions": [{"id": "a", "text": "b"}]})

    monkeypatch.setattr("cura_ai.providers.ClaudeCliClient", Recorder)
    request = {"action": "settings_turn", "job": job, "history": HISTORY, "model": requested}
    assert handle_request(request)["kind"] == "questions"
    assert seen == [passed]


@pytest.mark.parametrize(
    ("requested", "passed"),
    [("high", "high"), (None, None), ("default", None), ("xhigh", "xhigh")],
)
def test_requested_effort_reaches_the_client(monkeypatch, job, requested, passed):
    seen = []

    class Recorder(FakeClient):
        def __init__(self, model, effort):
            seen.append(effort)
            super().__init__({"kind": "questions", "questions": [{"id": "a", "text": "b"}]})

    monkeypatch.setattr("cura_ai.providers.ClaudeCliClient", Recorder)
    request = {"action": "settings_turn", "job": job, "history": HISTORY, "effort": requested}
    assert handle_request(request)["kind"] == "questions"
    assert seen == [passed]


def test_invalid_effort_becomes_an_error_object(job):
    out = handle_request(
        {"action": "settings_turn", "job": job, "history": HISTORY, "effort": "ultracode"}
    )
    assert out["type"] == "ValueError" and "invalid effort level" in out["error"]


def test_invalid_model_name_becomes_an_error_object(job):
    out = handle_request({"action": "settings_turn", "job": job, "history": HISTORY, "model": "-x"})
    assert out["type"] == "ValueError" and "invalid model name" in out["error"]


def test_support_angle_is_taken_from_the_request_first_then_the_job(tmp_path, job, monkeypatch):
    stl = tmp_path / "c.stl"
    write_stl(stl, cube())
    seen = []
    monkeypatch.setattr(
        "cura_ai.cli.analyse", lambda *a, **k: seen.append(k["support_angle_deg"]) or "analysis"
    )
    base = {"stl": str(stl)}
    from cura_ai.cli import _analysis

    _analysis({**base, "support_angle": 30}, JobExport.model_validate(job))
    _analysis(base, JobExport.model_validate(job))  # falls back to the job's own value
    _analysis(base, None)
    assert seen == [30.0, 50.0, 50.0]  # Flsun Q5 profile: support_angle 50


def test_request_parsing_accepts_utf8_with_and_without_bom():
    raw = '{"action": "x", "text": "äöü"}'.encode()
    assert (
        parse_request(raw) == parse_request(b"\xef\xbb\xbf" + raw) == {"action": "x", "text": "äöü"}
    )


def test_orient_command_prints_ranked_json(tmp_path, capsysbinary):
    stl = tmp_path / "b.stl"
    write_stl(stl, l_bracket())
    code = main(["orient", str(stl), "--goal-weights", '{"low_height": 1}', "--top", "2"])
    ranked = json.loads(capsysbinary.readouterr().out)
    assert code == 0 and len(ranked) == 2 and ranked[0]["rank"] == 1
    code = main(["orient", str(stl), "--goal-weights", '{"load_in_plane": 1}'])
    assert code == 1 and "load_direction" in json.loads(capsysbinary.readouterr().out)["error"]


def test_chat_asks_then_proposes_then_ends(flsun):
    client = FakeClient(
        {"kind": "questions", "questions": [{"id": "l", "text": "Welche Last?"}]},
        proposal(("infill_sparse_density", "40")),
    )
    typed = iter(["Mach es stabil", "Zug entlang Z", ""])
    shown = []
    run_chat(client, flsun, "Flsun Q5", "PLA", lambda _p: next(typed), shown.append)
    assert shown[0] == "? Welche Last?"
    assert any("infill_sparse_density: 20 -> 40.0 %" in line for line in shown)


def test_settings_turn_reports_whether_the_proposal_is_for_one_part(job):
    part = {**proposal(("infill_sparse_density", "40")), "scope": "part"}
    out = handle_request(
        {"action": "settings_turn", "job": job, "history": HISTORY}, FakeClient(part)
    )
    assert out["scope"] == "part" and len(out["accepted"]) == 1
    whole = handle_request(
        {"action": "settings_turn", "job": job, "history": HISTORY},
        FakeClient(proposal(("infill_sparse_density", "40"))),
    )
    assert whole["scope"] == "print"


def test_settings_the_user_chose_are_marked_to_the_model_and_left_unticked_in_the_answer(job):
    seen = []

    class Spy(FakeClient):
        def complete(self, system, prompt, schema):
            seen.append(prompt)
            return super().complete(system, prompt, schema)

    marked = {**job, "user_set": ["infill_sparse_density", "wall_line_count"]}
    out = handle_request(
        {"action": "settings_turn", "job": marked, "history": HISTORY},
        Spy(proposal(("infill_sparse_density", "40"), ("layer_height", "0.15"))),
    )
    rows = {r["key"]: r for r in json.loads(seen[0])["context"]["settings"]}
    assert rows["infill_sparse_density"]["set_by_user"] is True
    assert rows["wall_line_count"]["set_by_user"] is True  # listed even if not a usual key
    assert "set_by_user" not in rows["layer_height"]
    flags = {a["key"]: a["user_set"] for a in out["accepted"]}
    assert flags == {"infill_sparse_density": True, "layer_height": False}
