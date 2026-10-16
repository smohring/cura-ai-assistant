import json
import os
import subprocess

import pytest

from cura_ai.assistant import settings_turn
from cura_ai.catalogue import Catalogue
from cura_ai.llm import ClaudeCliClient, LlmError, find_claude
from cura_ai.schemas import Message

SCHEMA = {"type": "object"}


def proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def client_with(result: subprocess.CompletedProcess | Exception):
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, Exception):
            raise result
        return result

    return ClaudeCliClient(executable="claude.exe", runner=runner), calls


def test_structured_output_is_returned():
    stdout = json.dumps({"is_error": False, "structured_output": {"kind": "x"}})
    client, _ = client_with(proc(stdout))
    assert client.complete("sys", "prompt", SCHEMA) == {"kind": "x"}


def test_call_uses_no_tools_no_shell_and_prompt_on_stdin():
    stdout = json.dumps({"structured_output": {}})
    client, calls = client_with(proc(stdout))
    client.complete("sys", "the prompt", SCHEMA)
    args, kwargs = calls[0]
    assert args[:2] == ["claude.exe", "-p"]
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--json-schema") + 1] == json.dumps(SCHEMA)
    assert args[args.index("--system-prompt") + 1] == "sys"
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args
    assert kwargs["input"] == "the prompt"
    assert kwargs.get("shell") is not True
    assert os.path.basename(kwargs["cwd"]) != "Cura-MCP"


@pytest.mark.parametrize("model", ["sonnet", "opus", "fable", "claude-opus-5-5", "sonnet[1m]"])
def test_requested_model_is_passed_to_the_cli(model):
    client = ClaudeCliClient(model=model, executable="claude.exe")
    args = client.build_args(["claude.exe"], "s", {})
    assert args[args.index("--model") + 1] == model


@pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max"])
def test_requested_effort_is_passed_to_the_cli(level):
    args = ClaudeCliClient(effort=level, executable="c").build_args(["c"], "s", {})
    assert args[args.index("--effort") + 1] == level


def test_no_effort_means_no_flag_and_odd_levels_are_refused():
    assert "--effort" not in ClaudeCliClient(executable="c").build_args(["c"], "s", {})
    for bad in ("", "ultracode", "HIGH", "--max", "auto"):
        with pytest.raises(ValueError, match="invalid effort level"):
            ClaudeCliClient(effort=bad)


def test_no_model_leaves_the_choice_to_the_cli():
    args = ClaudeCliClient(model=None, executable="c").build_args(["c"], "s", {})
    assert "--model" not in args


@pytest.mark.parametrize("bad", ["", "--help", "a b", "opus; calc", "-x", "../x", "a" * 80])
def test_odd_model_names_are_refused(bad):
    with pytest.raises(ValueError, match="invalid model name"):
        ClaudeCliClient(model=bad)


def test_error_result_raises_with_message():
    stdout = json.dumps({"is_error": True, "result": "Failed to authenticate: OAuth expired"})
    client, _ = client_with(proc(stdout, returncode=1))
    with pytest.raises(LlmError, match="OAuth expired"):
        client.complete("s", "p", SCHEMA)


def test_missing_structured_output_raises():
    client, _ = client_with(proc(json.dumps({"is_error": False, "result": "free text"})))
    with pytest.raises(LlmError, match="structured_output"):
        client.complete("s", "p", SCHEMA)


def test_non_json_stdout_raises_with_stderr():
    client, _ = client_with(proc("oops", returncode=2, stderr="bad flag"))
    with pytest.raises(LlmError, match="bad flag|oops"):
        client.complete("s", "p", SCHEMA)


def test_timeout_raises():
    client, _ = client_with(subprocess.TimeoutExpired("claude", 1))
    with pytest.raises(LlmError, match="timed out"):
        client.complete("s", "p", SCHEMA)


def test_missing_executable_raises():
    client, _ = client_with(FileNotFoundError("nope"))
    with pytest.raises(LlmError, match="could not start"):
        client.complete("s", "p", SCHEMA)


def test_find_claude_prefers_environment(monkeypatch):
    monkeypatch.setenv("CURA_AI_CLAUDE", "C:/x/claude.exe")
    assert find_claude() == ["C:/x/claude.exe"]


def test_find_claude_accepts_a_json_command_for_stand_in_scripts(monkeypatch):
    monkeypatch.setenv("CURA_AI_CLAUDE", '["python.exe", "C:/x/fake claude.py"]')
    assert find_claude() == ["python.exe", "C:/x/fake claude.py"]
    client = ClaudeCliClient(executable=find_claude())
    assert client.build_args(find_claude(), "s", {})[:3] == [
        "python.exe",
        "C:/x/fake claude.py",
        "-p",
    ]


def test_find_claude_picks_newest_numeric_version(monkeypatch, tmp_path):
    monkeypatch.delenv("CURA_AI_CLAUDE", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("cura_ai.llm.__file__", str(tmp_path / "a" / "b" / "llm.py"))  # no .tools
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for version in ("2.1.99", "2.1.222"):
        exe = tmp_path / "Claude" / "claude-code" / version / "abc" / "claude.exe"
        exe.parent.mkdir(parents=True)
        exe.touch()
    assert "2.1.222" in find_claude()[0]


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("CURA_AI_LIVE") != "1", reason="set CURA_AI_LIVE=1")
def test_live_ambiguous_goal_asks_questions():
    catalogue = Catalogue.from_definitions("flsun_q5")
    result = settings_turn(
        ClaudeCliClient(),
        catalogue,
        "Flsun Q5",
        "generic_pla_175",
        [Message(role="user", content="Mach es stabil.")],
    )
    assert result.turn.kind == "questions"
