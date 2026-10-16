import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from cura_ai.assistant import settings_turn
from cura_ai.catalogue import Catalogue
from cura_ai.cli import handle_request
from cura_ai.llm import ClaudeCliClient, LlmError
from cura_ai.providers import (
    OllamaClient,
    OpenAICompatibleClient,
    list_ollama_models,
    make_client,
)
from cura_ai.schemas import Message

SCHEMA = {"type": "object", "properties": {"kind": {"type": "string"}}}
PROPOSAL = {
    "kind": "proposal",
    "summary": "Fast.",
    "overrides": [{"key": "infill_sparse_density", "value": "10", "reason": "less"}],
}


class Server:
    """A throw-away HTTP server on 127.0.0.1 that records requests and answers from a function."""

    def __init__(self, answer):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                outer.requests.append((self.path, dict(self.headers), body))
                status, reply = answer(self.path, body)
                self._reply(status, reply)

            def do_GET(self):
                outer.requests.append((self.path, dict(self.headers), None))
                status, reply = answer(self.path, None)
                self._reply(status, reply)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


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


def ollama_answer(content):
    return lambda path, body: (200, {"message": {"role": "assistant", "content": content}})


def openai_answer(content):
    return lambda path, body: (200, {"choices": [{"message": {"content": content}}]})


# --- Ollama ----------------------------------------------------------------------------------


def test_ollama_sends_the_schema_as_format_and_parses_the_content(server):
    s = server(ollama_answer(json.dumps({"kind": "questions"})))
    out = OllamaClient("gemma:1b", host=s.url).complete("sys", "prompt", SCHEMA)
    path, _, body = s.requests[0]
    assert out == {"kind": "questions"} and path == "/api/chat"
    assert body["format"] == SCHEMA and body["model"] == "gemma:1b" and body["stream"] is False
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "prompt"},
    ]
    assert body["options"]["temperature"] == 0


def test_ollama_lists_installed_models(server):
    s = server(lambda p, b: (200, {"models": [{"name": "b:2"}, {"name": "a:1"}, {"x": 1}]}))
    assert list_ollama_models(s.url) == ["a:1", "b:2"]


def test_a_server_that_is_not_there_is_a_clear_error():
    with pytest.raises(LlmError, match="could not reach 127.0.0.1"):
        OllamaClient("m", host="http://127.0.0.1:9", timeout=2).complete("s", "p", SCHEMA)


def test_text_that_is_not_json_is_an_error_not_a_guess(server):
    s = server(ollama_answer("Sure! Set the infill to 10."))
    with pytest.raises(LlmError, match="JSON object"):
        OllamaClient("m", host=s.url).complete("s", "p", SCHEMA)


def test_unexpected_answer_shape_is_an_error(server):
    s = server(lambda p, b: (200, {"unexpected": True}))
    with pytest.raises(LlmError, match="no message content"):
        OllamaClient("m", host=s.url).complete("s", "p", SCHEMA)


# --- OpenAI-compatible ------------------------------------------------------------------------


def test_openai_style_request_and_key_from_the_environment(server, monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret-value")
    s = server(openai_answer(json.dumps({"kind": "plan"})))
    client = OpenAICompatibleClient("gpt-x", base_url=s.url + "/v1", key_variable="MY_KEY")
    assert client.complete("sys", "prompt", SCHEMA) == {"kind": "plan"}
    path, headers, body = s.requests[0]
    assert path == "/v1/chat/completions" and headers["Authorization"] == "Bearer secret-value"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["model"] == "gpt-x" and body["temperature"] == 0


def test_remote_server_without_a_key_is_refused_before_any_request(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LlmError, match="OPENAI_API_KEY is not set"):
        OpenAICompatibleClient("gpt-x").complete("s", "p", SCHEMA)


def test_local_server_needs_no_key(server, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = server(openai_answer(json.dumps({"kind": "x"})))
    client = OpenAICompatibleClient("local", base_url=s.url)
    assert client.complete("s", "p", SCHEMA) == {"kind": "x"}
    assert "Authorization" not in s.requests[0][1]


def test_http_errors_carry_the_servers_message(server, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    s = server(lambda p, b: (401, {"error": {"message": "bad key"}}))
    with pytest.raises(LlmError, match="answered 401.*bad key"):
        OpenAICompatibleClient("m", base_url=s.url).complete("s", "p", SCHEMA)


# --- validation and the factory -------------------------------------------------------------


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x", "localhost:11434", "http://"])
def test_server_addresses_must_be_http(url):
    with pytest.raises(ValueError, match="invalid server address"):
        OllamaClient("m", host=url)
    with pytest.raises(ValueError, match="invalid server address"):
        OpenAICompatibleClient("m", base_url=url)


@pytest.mark.parametrize("model", ["", "-x", "a b", "a;b", "x" * 200])
def test_odd_model_names_are_refused(model):
    with pytest.raises(ValueError, match="invalid model name"):
        OllamaClient(model)


def test_good_model_names_pass():
    for name in ("llama3.1:8b", "hf.co/unsloth/gemma-4-26B-A4B-it-qat-GGUF:UD-Q4_K_XL", "gpt-4o"):
        OllamaClient(name)


def test_environment_variable_names_are_checked():
    with pytest.raises(ValueError, match="invalid environment variable name"):
        OpenAICompatibleClient("m", key_variable="KEY; calc")


def test_factory_picks_the_provider_and_only_claude_has_effort():
    assert isinstance(make_client(None, "sonnet", "medium"), ClaudeCliClient)
    assert isinstance(make_client("ollama", "m:1"), OllamaClient)
    assert isinstance(make_client("openai", "m", "high"), OpenAICompatibleClient)
    with pytest.raises(ValueError, match="unknown provider"):
        make_client("somewhere", "m")
    with pytest.raises(ValueError, match="choose a model"):
        make_client("ollama", None)


# --- end to end: through the dialogue code and the request interface -----------------------------


def test_a_dialogue_turn_through_ollama_is_validated_like_any_other(server):
    s = server(ollama_answer(json.dumps(PROPOSAL)))
    catalogue = Catalogue.from_definitions("flsun_q5")
    result = settings_turn(
        OllamaClient("m", host=s.url), catalogue, "P", "M", [Message(role="user", content="fast")]
    )
    assert [a.key for a in result.report.accepted] == ["infill_sparse_density"]


def test_a_weak_models_off_schema_answer_is_rejected(server):
    s = server(ollama_answer(json.dumps({"kind": "proposal", "summary": "x", "overrides": []})))
    out = handle_request(
        {
            "action": "settings_turn",
            "provider": "ollama",
            "model": "m",
            "provider_options": {"host": s.url},
            "history": [{"role": "user", "content": "x"}],
            "job": {
                "printer": {"id": "p"},
                "settings": {"layer_height": {"value": 0.2, "type": "float"}},
            },
        }
    )
    assert out["type"] == "TurnRejected" and "at least one override" in out["error"]


def test_request_can_list_ollama_models(server):
    s = server(lambda p, b: (200, {"models": [{"name": "m:1"}]}))
    out = handle_request(
        {"action": "list_models", "provider": "ollama", "provider_options": {"host": s.url}}
    )
    assert out == {"models": ["m:1"]}
    assert handle_request({"action": "list_models", "provider": "claude"})["type"] == "ValueError"
