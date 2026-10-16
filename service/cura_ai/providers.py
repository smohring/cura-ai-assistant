"""Other language-model providers behind the same `LlmClient` protocol.

Every provider does one thing: send a system prompt, a user prompt and a JSON schema, return the
parsed JSON object. What it returns is validated by the dialogue code exactly as for the Claude
CLI, so a weaker model shows up as rejected answers, never as silent wrong actions.

Network use is explicit: a provider is only used when chosen in the options, and Ollama defaults
to the local machine. API keys are read from an environment variable named in the options, never
stored.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cura_ai.llm import CLI_DEFAULT, DEFAULT_MODEL, ClaudeCliClient, LlmClient, LlmError

PROVIDERS = ("claude", "openai", "gemini", "codex", "gemini-cli", "ollama")
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_KEY_VARIABLE = "OPENAI_API_KEY"
DEFAULT_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_KEY_VARIABLE = "GEMINI_API_KEY"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
# Model ids of Ollama and OpenAI-style servers: "llama3.1:8b", "hf.co/org/repo:Q4", "gpt-4o-mini"
FREE_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\-]{0,150}")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

Post = Callable[[str, dict, dict, float], dict]


def _check_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"invalid server address {url!r} (use http:// or https://)")
    return url.rstrip("/")


def _check_model(model: str) -> str:
    if not FREE_MODEL_NAME.fullmatch(model):
        raise ValueError(f"invalid model name {model!r}")
    return model


def post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    """POST a JSON body, return the parsed JSON answer; failures become `LlmError`."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (http/s only)
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise LlmError(f"the server answered {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise LlmError(
            f"could not reach {urllib.parse.urlsplit(url).netloc}: {exc.reason}"
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise LlmError(f"no answer from {urllib.parse.urlsplit(url).netloc}: {exc}") from exc
    except ValueError as exc:
        raise LlmError("the server did not answer with JSON") from exc


def get_json(url: str, timeout: float = 5.0) -> dict:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise LlmError(f"could not read {url}: {exc}") from exc


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _parse_content(content: Any) -> object:
    """The JSON object in a model's text; a markdown code fence around it is taken off."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        fenced = _FENCE.match(content)
        content = fenced.group(1) if fenced else content
    try:
        return json.loads(content)
    except (TypeError, ValueError) as exc:
        raise LlmError("the model did not answer with a JSON object") from exc


def with_schema(system: str, schema: dict) -> str:
    """The system prompt plus the schema as text, for providers that cannot enforce a schema."""
    return (
        system
        + "\n\nReply with ONLY a JSON object that conforms to this JSON Schema, no prose and no "
        + "code fence:\n"
        + json.dumps(schema)
    )


def strictify(schema: dict) -> dict:
    """A copy for OpenAI's strict structured outputs: every property required, none extra.

    Properties that may be absent (default null) become nullable; those with another default
    stay required, so the model must give a value (the dialogue code validates it anyway).
    """

    def allows_null(prop: dict) -> bool:
        return prop.get("type") == "null" or any(
            isinstance(o, dict) and o.get("type") == "null" for o in prop.get("anyOf", [])
        )

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        node = {k: walk(v) for k, v in node.items()}
        props = node.get("properties")
        if node.get("type") == "object" and isinstance(props, dict):
            required = set(node.get("required", []))
            for name, prop in list(props.items()):
                optional = name not in required and prop.get("default") is None
                prop.pop("default", None)
                if optional and not allows_null(prop):
                    props[name] = {"anyOf": [prop, {"type": "null"}]}
            node["required"] = list(props)
            node["additionalProperties"] = False
        return node

    return walk(schema)


class OllamaClient:
    """A model served by Ollama; the schema is passed as `format`, which constrains the output."""

    def __init__(
        self,
        model: str,
        host: str = DEFAULT_OLLAMA_HOST,
        timeout: float = 600.0,
        post: Post = post_json,
    ) -> None:
        self._model = _check_model(model)
        self._host = _check_url(host or DEFAULT_OLLAMA_HOST)
        self._timeout = timeout
        self._post = post

    def complete(self, system: str, prompt: str, schema: dict) -> object:
        body = {
            "model": self._model,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        answer = self._post(f"{self._host}/api/chat", body, {}, self._timeout)
        try:
            return _parse_content(answer["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise LlmError("unexpected answer from Ollama (no message content)") from exc


class OpenAICompatibleClient:
    """Any server that speaks the OpenAI chat API with `response_format: json_schema`."""

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_OPENAI_URL,
        key_variable: str = DEFAULT_KEY_VARIABLE,
        timeout: float = 180.0,
        post: Post = post_json,
    ) -> None:
        self._model = _check_model(model)
        self._base = _check_url(base_url or DEFAULT_OPENAI_URL)
        if key_variable and not ENV_NAME.fullmatch(key_variable):
            raise ValueError(f"invalid environment variable name {key_variable!r}")
        self._key_variable = key_variable
        self._timeout = timeout
        self._post = post

    def _headers(self) -> dict:
        key = os.environ.get(self._key_variable, "") if self._key_variable else ""
        local = urllib.parse.urlsplit(self._base).hostname in ("127.0.0.1", "localhost", "::1")
        if not key and not local:
            raise LlmError(f"the environment variable {self._key_variable} is not set")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def complete(self, system: str, prompt: str, schema: dict) -> object:
        body = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema},
            },
        }
        answer = self._post(f"{self._base}/chat/completions", body, self._headers(), self._timeout)
        try:
            return _parse_content(answer["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("unexpected answer from the server (no message content)") from exc


class GeminiClient:
    """Google's Gemini API (`generateContent`), JSON mode with the schema when it is accepted.

    Written from Google's documentation, not tried against a live key. The schema field is
    `responseJsonSchema`; the documentation is not clear that every API version knows it, so on
    a 400 the request is repeated with JSON mode alone. The schema is also in the system prompt,
    and the answer is validated by the dialogue code either way.
    """

    def __init__(
        self,
        model: str = DEFAULT_GEMINI_MODEL,
        base_url: str = DEFAULT_GEMINI_URL,
        key_variable: str = DEFAULT_GEMINI_KEY_VARIABLE,
        timeout: float = 180.0,
        post: Post = post_json,
    ) -> None:
        self._model = _check_model(model or DEFAULT_GEMINI_MODEL)
        self._base = _check_url(base_url or DEFAULT_GEMINI_URL)
        if key_variable and not ENV_NAME.fullmatch(key_variable):
            raise ValueError(f"invalid environment variable name {key_variable!r}")
        self._key_variable = key_variable
        self._timeout = timeout
        self._post = post

    def _headers(self) -> dict:
        key = os.environ.get(self._key_variable, "") if self._key_variable else ""
        if not key:
            raise LlmError(f"the environment variable {self._key_variable} is not set")
        return {"x-goog-api-key": key}

    def complete(self, system: str, prompt: str, schema: dict) -> object:
        url = f"{self._base}/models/{self._model}:generateContent"
        config = {"temperature": 0, "responseMimeType": "application/json"}
        body = {
            "systemInstruction": {"parts": [{"text": with_schema(system, schema)}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {**config, "responseJsonSchema": schema},
        }
        headers = self._headers()
        try:
            answer = self._post(url, body, headers, self._timeout)
        except LlmError as exc:
            if "answered 400" not in str(exc):
                raise
            body["generationConfig"] = config
            answer = self._post(url, body, headers, self._timeout)
        try:
            parts = answer["candidates"][0]["content"]["parts"]
            return _parse_content("".join(p.get("text", "") for p in parts))
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("unexpected answer from Gemini (no candidate text)") from exc


class _CliProvider:
    """Shared plumbing for agent CLIs: run in an empty directory, prompt on stdin, no shell."""

    name = ""
    env_var = ""

    def __init__(
        self,
        model: str | None = None,
        executable: str | None = None,
        timeout: float = 300.0,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self._model = _check_model(model) if model else None
        self._executable = executable
        self._timeout = timeout
        self._runner = runner

    def _find(self) -> str:
        found = self._executable or os.environ.get(self.env_var) or shutil.which(self.name)
        if not found:
            raise LlmError(f"the {self.name} command was not found; set {self.env_var}")
        return found

    def _run(self, args: list[str], stdin: str, cwd: str) -> subprocess.CompletedProcess:
        try:
            return self._runner(
                args,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=cwd,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LlmError(f"{self.name} timed out after {self._timeout:.0f}s") from exc
        except OSError as exc:
            raise LlmError(f"could not start {self.name}: {exc}") from exc

    def _failed(self, proc: subprocess.CompletedProcess) -> LlmError:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return LlmError(f"{self.name} failed (exit {proc.returncode}): " + " | ".join(tail))


class CodexCliClient(_CliProvider):
    """OpenAI's Codex CLI in non-interactive mode, schema-constrained final answer.

    Flags are from the Codex documentation ("non-interactive mode"): `codex exec` with the prompt
    on stdin, `--output-schema`, `-o` for the last message, read-only sandbox, no git check, no
    saved session. `-m` for the model is not in the pages read; it is only passed when a model
    is chosen. Not tried against a live account.
    """

    name = "codex"
    env_var = "CURA_AI_CODEX"

    def complete(self, system: str, prompt: str, schema: dict) -> object:
        with tempfile.TemporaryDirectory() as tmp:
            schema_file, answer_file = Path(tmp, "schema.json"), Path(tmp, "answer.json")
            schema_file.write_text(json.dumps(strictify(schema)), encoding="utf-8")
            args = [
                self._find(), "exec",
                "--output-schema", str(schema_file),
                "-o", str(answer_file),
                "--sandbox", "read-only",
                "--skip-git-repo-check",
                "--ephemeral",
                *(["-m", self._model] if self._model else []),
                "-",
            ]  # fmt: skip
            proc = self._run(args, f"{system}\n\n{prompt}", tmp)
            if proc.returncode != 0:
                raise self._failed(proc)
            text = answer_file.read_text(encoding="utf-8") if answer_file.exists() else proc.stdout
        return _parse_content(text)


class GeminiCliClient(_CliProvider):
    """Google's Gemini CLI headless mode (`-p`, `--output-format json`, answer in `response`).

    The CLI cannot enforce a schema, so it is part of the prompt. Not tried against a live
    account; the documentation names no flag that turns its tools off.
    """

    name = "gemini"
    env_var = "CURA_AI_GEMINI"

    def complete(self, system: str, prompt: str, schema: dict) -> object:
        with tempfile.TemporaryDirectory() as tmp:
            args = [
                self._find(), "-p", "Answer the request given on stdin.",
                "--output-format", "json",
                *(["-m", self._model] if self._model else []),
            ]  # fmt: skip
            proc = self._run(args, f"{with_schema(system, schema)}\n\n{prompt}", tmp)
        if proc.returncode != 0:
            raise self._failed(proc)
        try:
            result = json.loads(proc.stdout)
        except ValueError as exc:
            raise LlmError("gemini did not print JSON") from exc
        if not isinstance(result, dict) or "response" not in result:
            raise LlmError(f"gemini reported an error: {result.get('error', 'no response field')}")
        return _parse_content(result["response"])


def list_ollama_models(host: str = DEFAULT_OLLAMA_HOST) -> list[str]:
    """Names of the models installed in an Ollama server."""
    answer = get_json(f"{_check_url(host or DEFAULT_OLLAMA_HOST)}/api/tags")
    return sorted(m["name"] for m in answer.get("models", []) if "name" in m)


def make_client(
    provider: str | None,
    model: str | None,
    effort: str | None = None,
    options: dict | None = None,
) -> LlmClient:
    """The client for a provider choice. Effort only exists for the Claude CLI."""
    options = options or {}
    provider = provider or "claude"
    if provider == "claude":
        return ClaudeCliClient(
            model=None if model == CLI_DEFAULT else (model or DEFAULT_MODEL),
            effort=None if effort in (None, CLI_DEFAULT) else effort,
        )
    if provider in ("gemini", "codex", "gemini-cli") and model == CLI_DEFAULT:
        model = None  # the provider's own default
    if provider in ("ollama", "openai") and not model:
        raise ValueError(f"choose a model for the provider '{provider}'")
    if provider == "ollama":
        return OllamaClient(model, host=options.get("host") or DEFAULT_OLLAMA_HOST)
    if provider == "gemini":
        return GeminiClient(
            model,
            base_url=options.get("base_url") or DEFAULT_GEMINI_URL,
            key_variable=options.get("key_variable", DEFAULT_GEMINI_KEY_VARIABLE),
        )
    if provider == "codex":
        return CodexCliClient(model)
    if provider == "gemini-cli":
        return GeminiCliClient(model)
    if provider == "openai":
        return OpenAICompatibleClient(
            model,
            base_url=options.get("base_url") or DEFAULT_OPENAI_URL,
            key_variable=options.get("key_variable", DEFAULT_KEY_VARIABLE),
        )
    raise ValueError(f"unknown provider {provider!r}")
