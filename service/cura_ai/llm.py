"""LLM access: a small protocol and the Claude CLI implementation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class LlmError(Exception):
    """The model could not be reached or did not deliver structured output."""


class LlmClient(Protocol):
    def complete(self, system: str, prompt: str, schema: dict) -> object:
        """Return the model's JSON answer (already parsed) for `prompt`."""
        ...


def _version_key(path: Path) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", path.parent.parent.name))


def find_claude() -> list[str]:
    """The command that starts claude.

    $CURA_AI_CLAUDE (a path, or a JSON list such as a stand-in script for tests), PATH, repo
    `.tools/`, then the newest desktop-app install.
    """
    env = os.environ.get("CURA_AI_CLAUDE")
    if env:
        return json.loads(env) if env.startswith("[") else [env]
    on_path = shutil.which("claude")
    if on_path:
        return [on_path]
    linked = Path(__file__).resolve().parents[2] / ".tools" / "claude.exe"
    if linked.exists():
        return [str(linked)]
    appdata = os.environ.get("APPDATA")
    if appdata:
        root = Path(appdata, "Claude", "claude-code")
        found = sorted(root.glob("*/*/claude.exe"), key=_version_key)
        if found:
            return [str(found[-1])]
    raise LlmError("claude executable not found; set CURA_AI_CLAUDE")


Runner = Callable[..., subprocess.CompletedProcess]
# An alias ("sonnet", "opus", "fable") or a full model id, optionally with a context suffix.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,63}(\[[0-9a-z]+\])?")
DEFAULT_MODEL = "sonnet"
CLI_DEFAULT = "default"  # request value meaning: do not pass --model / --effort at all
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")  # `claude --help`


class ClaudeCliClient:
    """Calls `claude -p` with schema-constrained JSON output and no tools."""

    def __init__(
        self,
        model: str | None = "sonnet",
        timeout: float = 180.0,
        executable: str | list[str] | None = None,
        runner: Runner = subprocess.run,
        effort: str | None = None,
    ) -> None:
        if model is not None and not MODEL_NAME.fullmatch(model):
            raise ValueError(f"invalid model name {model!r}")
        if effort is not None and effort not in EFFORT_LEVELS:
            raise ValueError(f"invalid effort level {effort!r}")
        self._model = model
        self._effort = effort
        self._timeout = timeout
        self._executable = [executable] if isinstance(executable, str) else executable
        self._runner = runner

    def build_args(self, command: list[str], system: str, schema: dict) -> list[str]:
        return [
            *command,
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(schema),
            "--system-prompt", system,
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            *(["--model", self._model] if self._model else []),
            *(["--effort", self._effort] if self._effort else []),
        ]  # fmt: skip

    def complete(self, system: str, prompt: str, schema: dict) -> object:
        executable = self._executable or find_claude()
        args = self.build_args(executable, system, schema)
        with tempfile.TemporaryDirectory() as empty_cwd:
            try:
                proc = self._runner(
                    args,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=empty_cwd,
                    timeout=self._timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise LlmError(f"claude timed out after {self._timeout:.0f}s") from exc
            except OSError as exc:
                raise LlmError(f"could not start claude: {exc}") from exc
        return self._extract(proc)

    @staticmethod
    def _extract(proc: subprocess.CompletedProcess) -> Any:
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            raise LlmError(f"claude returned no JSON (exit {proc.returncode}): {detail}") from exc
        if not isinstance(result, dict):
            raise LlmError("claude result is not a JSON object")
        if result.get("is_error") or proc.returncode != 0:
            raise LlmError(f"claude reported an error: {result.get('result', 'unknown error')}")
        if "structured_output" not in result:
            raise LlmError("claude result has no structured_output field")
        return result["structured_output"]
