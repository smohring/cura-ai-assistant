"""Gemini API, Codex CLI, Gemini CLI, strict schemas: from the documentation, not a live account."""

import json
import subprocess
from pathlib import Path

import pytest
from test_providers import Server

from cura_ai.llm import LlmError
from cura_ai.providers import (
    PROVIDERS,
    CodexCliClient,
    GeminiCliClient,
    GeminiClient,
    _parse_content,
    make_client,
    strictify,
)
from cura_ai.schemas import PartTurn

SCHEMA = {"type": "object", "properties": {"kind": {"type": "string"}}}


@pytest.fixture
def server():
    made = []

    def start(answer):
        s = Server(answer)
        made.append(s)
        return s

    yield start
    for s in made:
        s.close()


def gemini_answer(text):
    return lambda path, body: (200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


# --- Gemini API --------------------------------------------------------------------------------


def test_gemini_request_follows_the_documented_shape(server, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    s = server(gemini_answer(json.dumps({"kind": "questions"})))
    client = GeminiClient("gemini-x", base_url=s.url + "/v1beta")
    assert client.complete("sys", "prompt", SCHEMA) == {"kind": "questions"}
    path, headers, body = s.requests[0]
    assert path == "/v1beta/models/gemini-x:generateContent"
    assert {k.lower(): v for k, v in headers.items()}["x-goog-api-key"] == "g-key"
    instruction = body["systemInstruction"]["parts"][0]["text"]
    assert "sys" in instruction and "JSON Schema" in instruction
    assert body["contents"] == [{"role": "user", "parts": [{"text": "prompt"}]}]
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == SCHEMA


def test_gemini_falls_back_to_plain_json_mode_when_the_schema_field_is_refused(server, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")

    def answer(path, body):
        if "responseJsonSchema" in body["generationConfig"]:
            return 400, {"error": {"message": "Unknown name responseJsonSchema"}}
        return 200, {"candidates": [{"content": {"parts": [{"text": '{"kind": "x"}'}]}}]}

    s = server(answer)
    out = GeminiClient("m", base_url=s.url).complete("s", "p", SCHEMA)
    assert out == {"kind": "x"} and len(s.requests) == 2
    assert "responseJsonSchema" not in s.requests[1][2]["generationConfig"]


def test_gemini_other_errors_are_not_retried_and_a_key_is_required(server, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    s = server(lambda p, b: (403, {"error": {"message": "key not valid"}}))
    with pytest.raises(LlmError, match="answered 403"):
        GeminiClient("m", base_url=s.url).complete("s", "p", SCHEMA)
    assert len(s.requests) == 1
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(LlmError, match="GEMINI_API_KEY is not set"):
        GeminiClient("m").complete("s", "p", SCHEMA)


def test_gemini_answer_without_candidates_is_an_error(server, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    s = server(lambda p, b: (200, {"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(LlmError, match="no candidate text"):
        GeminiClient("m", base_url=s.url).complete("s", "p", SCHEMA)


# --- agent CLIs (a fake runner stands in for the program) ----------------------------------------


def test_codex_command_line_and_schema_file():
    seen = {}

    def runner(args, **kwargs):
        seen["args"], seen["input"] = args, kwargs["input"]
        seen["schema"] = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
        Path(args[args.index("-o") + 1]).write_text('{"kind": "choice"}')
        return completed("progress is on stderr; this is the final message")

    client = CodexCliClient("gpt-x", executable="codex", runner=runner)
    assert client.complete("sys", "prompt", PartTurn.model_json_schema()) == {"kind": "choice"}
    args = seen["args"]
    assert args[:2] == ["codex", "exec"] and args[-1] == "-"
    for flag in ("--skip-git-repo-check", "--ephemeral"):
        assert flag in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[args.index("-m") + 1] == "gpt-x"
    assert "sys" in seen["input"] and "prompt" in seen["input"]
    assert seen["schema"]["additionalProperties"] is False
    assert set(seen["schema"]["required"]) == set(seen["schema"]["properties"])


def test_codex_without_model_passes_no_model_flag_and_reads_stdout_if_no_file():
    def runner(args, **kwargs):
        assert "-m" not in args
        return completed('{"kind": "plan"}')

    out = CodexCliClient(executable="codex", runner=runner).complete("s", "p", SCHEMA)
    assert out == {"kind": "plan"}


def test_codex_failures_are_clear():
    failing = CodexCliClient(
        executable="codex",
        runner=lambda *a, **k: completed("", 2, "not logged in\nrun codex login"),
    )
    with pytest.raises(LlmError, match="codex failed.*not logged in"):
        failing.complete("s", "p", SCHEMA)

    def boom(*a, **k):
        raise FileNotFoundError("no such file")

    with pytest.raises(LlmError, match="could not start codex"):
        CodexCliClient(executable="codex", runner=boom).complete("s", "p", SCHEMA)


def test_missing_cli_is_reported(monkeypatch):
    monkeypatch.delenv("CURA_AI_CODEX", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(LlmError, match="codex command was not found"):
        CodexCliClient().complete("s", "p", SCHEMA)


def test_gemini_cli_reads_the_response_field_and_strips_a_code_fence():
    seen = {}

    def runner(args, **kwargs):
        seen["args"], seen["input"] = args, kwargs["input"]
        reply = {"response": '```json\n{"kind": "questions"}\n```', "stats": {}}
        return completed(json.dumps(reply))

    client = GeminiCliClient("gemini-x", executable="gemini", runner=runner)
    assert client.complete("s", "p", SCHEMA) == {"kind": "questions"}
    args = seen["args"]
    assert args[0] == "gemini" and "-p" in args
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("-m") + 1] == "gemini-x"
    assert "JSON Schema" in seen["input"]  # the schema goes into the prompt


def test_gemini_cli_error_object_and_non_json_output():
    error = completed(json.dumps({"error": "quota"}))
    with pytest.raises(LlmError, match="gemini reported an error: quota"):
        GeminiCliClient(executable="g", runner=lambda *a, **k: error).complete("s", "p", SCHEMA)
    with pytest.raises(LlmError, match="did not print JSON"):
        GeminiCliClient(executable="g", runner=lambda *a, **k: completed("hello")).complete(
            "s", "p", SCHEMA
        )


# --- strict schemas and tolerant JSON text -----------------------------------------------------


def test_strictify_makes_every_property_required_and_optional_ones_nullable():
    strict = strictify(PartTurn.model_json_schema())
    assert strict["additionalProperties"] is False
    assert set(strict["required"]) == set(strict["properties"])
    scale = strict["properties"]["scale_percent"]
    assert any(o.get("type") == "null" for o in scale["anyOf"])  # may be absent: nullable
    assert strict["properties"]["spacing_mm"].get("type") == "number"  # a real default: required
    assert "default" not in json.dumps(strict)


def test_a_strict_answer_with_nulls_still_validates_as_the_model():
    answer = {
        "kind": "plan",
        "summary": "x",
        "questions": [],
        "scale_percent": 150.0,
        "target_axis": None,
        "target_mm": None,
        "copies": None,
        "fill_plate": False,
        "spacing_mm": 3.0,
        "allow_rotation": True,
    }
    assert PartTurn.model_validate(answer, strict=True).scale_percent == 150.0


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', '```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '  ```json {"a": 1}```  '],
)
def test_a_code_fence_around_the_json_is_taken_off(text):
    assert _parse_content(text) == {"a": 1}


def test_prose_around_json_is_still_refused():
    with pytest.raises(LlmError):
        _parse_content('Here you go: {"a": 1}')


def test_factory_knows_all_six_providers():
    choices = [
        ("claude", "sonnet"),
        ("openai", "m"),
        ("gemini", None),
        ("codex", None),
        ("gemini-cli", "default"),
        ("ollama", "m"),
    ]
    for provider, model in choices:
        assert make_client(provider, model) is not None
    assert tuple(p for p, _ in choices) == PROVIDERS
