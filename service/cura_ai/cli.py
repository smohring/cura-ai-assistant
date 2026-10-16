"""Command line: `request` (JSON in, JSON out; used by the Cura plugin), `chat` and `orient`."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from cura_ai.assistant import TurnRejected, settings_turn
from cura_ai.catalogue import Catalogue, DefinitionNotFound
from cura_ai.engine import EngineError
from cura_ai.jobexport import JobExport
from cura_ai.llm import ClaudeCliClient, LlmClient, LlmError
from cura_ai.mesh import MeshError, load_stl
from cura_ai.orient_assistant import orient_turn
from cura_ai.orientation import OrientationError, analyse, score_candidates
from cura_ai.packing import Plate, surface_points
from cura_ai.part_assistant import build_context as part_context
from cura_ai.part_assistant import part_turn
from cura_ai.partplan import resolve_part_plan
from cura_ai.planner import plan as plan_message
from cura_ai.providers import DEFAULT_OLLAMA_HOST, list_ollama_models, make_client
from cura_ai.resolution import naive_merge, resolve_with_overrides
from cura_ai.schemas import Message
from cura_ai.validation import ValidationReport
from cura_ai.verify import Resolver, verify_orientations, verify_settings

TOP_RANKING = 8
EXPECTED_ERRORS = (
    LlmError,
    TurnRejected,
    OrientationError,
    MeshError,
    EngineError,
    DefinitionNotFound,
    OSError,
    KeyError,
    ValueError,  # includes pydantic.ValidationError
)


def _client(payload: dict) -> LlmClient:
    """The client for the provider, model and effort the request names (Claude CLI by default)."""
    return make_client(
        payload.get("provider"),
        payload.get("model"),
        payload.get("effort"),
        payload.get("provider_options"),
    )


def _list_models(payload: dict) -> dict:
    """Models a provider offers right now; only Ollama can be asked."""
    if payload.get("provider") != "ollama":
        raise ValueError("only Ollama can list its models")
    host = (payload.get("provider_options") or {}).get("host") or DEFAULT_OLLAMA_HOST
    return {"models": list_ollama_models(host)}


def _history(payload: dict) -> list[Message]:
    return [Message.model_validate(m) for m in payload["history"]]


def _job(payload: dict) -> JobExport:
    return JobExport.model_validate(payload["job"])


def _resolver(job: JobExport) -> Resolver:
    """Overrides that also update derived settings; plain merging if the definitions are gone."""
    try:
        definitions = Catalogue.from_definitions(job.printer.id, job.printer.extruder_id)
    except DefinitionNotFound:
        return naive_merge
    return lambda values, overrides: resolve_with_overrides(definitions, values, overrides)


def _analysis(payload: dict, job: JobExport | None):
    """Same support angle in the dialogue turn and in the verification, whatever else is sent."""
    angle = payload.get("support_angle") or (job.values().get("support_angle") if job else None)
    return analyse(
        load_stl(payload["stl"]),
        load_direction=payload.get("load_direction"),
        visible_direction=payload.get("visible_direction"),
        support_angle_deg=float(angle or 50.0),
    )


def _settings_turn(payload: dict, client: LlmClient) -> dict:
    job = _job(payload)
    result = settings_turn(
        client,
        job.catalogue(),
        job.printer.name or job.printer.id,
        job.material.name or job.material.id,
        _history(payload),
        job.quality,
        user_set=frozenset(job.user_set),
    )
    report = result.report or ValidationReport(accepted=[], rejected=[])
    for accepted in report.accepted:
        accepted.user_set = accepted.key in job.user_set
    return {
        "turn": result.turn.model_dump_json(),
        "kind": result.turn.kind,
        "questions": [q.model_dump() for q in result.turn.questions],
        "summary": result.turn.summary,
        "scope": result.turn.scope,
        "redirect_to": result.turn.redirect_to,
        "accepted": [a.model_dump(mode="json") for a in report.accepted],
        "rejected": [r.model_dump(mode="json") for r in report.rejected],
    }


def _orient_turn(payload: dict, client: LlmClient) -> dict:
    job = _job(payload) if payload.get("job") else None
    analysis = _analysis(payload, job)
    result = orient_turn(
        client,
        analysis,
        _history(payload),
        load_given=payload.get("load_direction") is not None,
        visible_given=payload.get("visible_direction") is not None,
    )
    turn_fields = {
        "kind",
        "questions",
        "weights",
        "chosen_candidate_id",
        "reasoning",
        "redirect_to",
    }
    return {
        "turn": result.model_dump_json(include=turn_fields),
        "kind": result.kind,
        "redirect_to": result.redirect_to,
        "questions": [q.model_dump() for q in result.questions],
        "weights": result.weights.model_dump() if result.weights else None,
        "reasoning": result.reasoning,
        "chosen": result.chosen.model_dump(mode="json") if result.chosen else None,
        "chosen_rank": result.chosen_rank,
        "ranking": [s.model_dump(mode="json") for s in result.ranking[:TOP_RANKING]],
    }


def adhesion_margin(values: dict) -> float:
    """Room each copy needs around itself for its brim or raft."""
    kind = values.get("adhesion_type")
    if kind == "brim":
        return float(values.get("brim_width") or 0)
    if kind == "raft":
        return float(values.get("raft_margin") or 0)
    return 0.0


def _part_turn(payload: dict, client: LlmClient) -> dict:
    plate = Plate.model_validate(payload["plate"])
    tris = load_stl(payload["stl"]) if payload.get("stl") else None  # no part: setup, slice only
    size = [0.0, 0.0, 0.0]
    if tris is not None:
        size = [
            float(v) for v in (tris.reshape(-1, 3).max(axis=0) - tris.reshape(-1, 3).min(axis=0))
        ]
    obstacles_xy = None
    if payload.get("obstacles_stl"):
        obstacles_xy = surface_points(load_stl(payload["obstacles_stl"]), 0.4)
    free = [plate.rect[2] - plate.rect[0], plate.rect[3] - plate.rect[1]]
    context = part_context(
        size,
        free,
        payload.get("build_height"),
        int(payload.get("other_parts", 0)),
        payload.get("inventory"),
    )
    turn = part_turn(client, context, _history(payload))
    out: dict = {
        "turn": turn.model_dump_json(),
        "kind": turn.kind,
        "redirect_to": turn.redirect_to,
        "questions": [q.model_dump() for q in turn.questions],
        "summary": turn.summary,
        "plan": None,
    }
    if turn.kind == "plan":
        values = payload.get("adhesion") or (_job(payload).values() if payload.get("job") else {})
        plan = resolve_part_plan(
            turn,
            tris,
            plate,
            margin=adhesion_margin(values),
            build_height=payload.get("build_height"),
            obstacles_xy=obstacles_xy,
            inventory=payload.get("inventory"),
        )
        out["plan"] = plan.model_dump(mode="json")
    return out


def _plan_turn(payload: dict, client: LlmClient) -> dict:
    result = plan_message(client, _history(payload), payload.get("context"))
    return result.model_dump()


def _verify_orientations(payload: dict, _client: LlmClient) -> dict:
    job = _job(payload)
    analysis = _analysis(payload, job)
    ranking = score_candidates(analysis.candidates, analysis.metrics, payload["weights"])
    top = ranking[: int(payload.get("top_n", 3))]
    verified = verify_orientations(
        load_stl(payload["stl"]),
        top,
        job.values(),
        job.printer.id,
        job.printer.extruder_id,
        resolve=_resolver(job),
    )
    return {
        "support_forced": True,
        "rows": [
            {"scored": s.model_dump(mode="json"), "verified": v.model_dump(mode="json")}
            for s, v in zip(top, verified, strict=True)
        ],
    }


def _verify_settings(payload: dict, _client: LlmClient) -> dict:
    job = _job(payload)
    comparison = verify_settings(
        load_stl(payload["stl"]),
        job.values(),
        payload["overrides"],
        job.printer.id,
        job.printer.extruder_id,
        resolve=_resolver(job),
    )
    return comparison.model_dump(mode="json")


ACTIONS: dict[str, Callable[[dict, LlmClient], dict]] = {
    "settings_turn": _settings_turn,
    "orient_turn": _orient_turn,
    "part_turn": _part_turn,
    "plan_turn": _plan_turn,
    "verify_orientations": _verify_orientations,
    "verify_settings": _verify_settings,
}


def handle_request(payload: dict, client: LlmClient | None = None) -> dict:
    """Run one request; expected failures become {"error": ...} instead of a traceback."""
    action = payload.get("action")
    if action not in ACTIONS and action != "list_models":
        return {"error": f"unknown action {action!r}", "type": "ValueError"}
    try:
        if action == "list_models":
            return _list_models(payload)
        return ACTIONS[action](payload, client or _client(payload))
    except EXPECTED_ERRORS as exc:
        return {
            "error": str(exc.args[0] if isinstance(exc, KeyError) else exc),
            "type": type(exc).__name__,
        }


def run_chat(
    client: LlmClient,
    catalogue: Catalogue,
    printer: str,
    material: str,
    read: Callable[[str], str],
    write: Callable[[str], None],
) -> None:
    """Interactive settings dialogue; an empty line ends it."""
    history: list[Message] = []
    prompt = "Goal"
    while True:
        text = read(f"{prompt}> ").strip()
        if not text:
            return
        history.append(Message(role="user", content=text))
        try:
            result = settings_turn(client, catalogue, printer, material, history)
        except (LlmError, TurnRejected) as exc:
            write(f"! {exc}")
            history.pop()
            continue
        history.append(Message(role="assistant", content=result.turn.model_dump_json()))
        if result.turn.kind == "questions":
            for q in result.turn.questions:
                write(f"? {q.text}")
            prompt = "Answer"
            continue
        write(result.turn.summary)
        for a in result.report.accepted:  # type: ignore[union-attr]
            warn = f"  [{'; '.join(a.warnings)}]" if a.warnings else ""
            write(f"  {a.key}: {a.previous} -> {a.value} {a.unit}  ({a.reason}){warn}")
        for r in result.report.rejected:  # type: ignore[union-attr]
            write(f"  x {r.key} = {r.value}: {r.message}")
        prompt = "Refine (empty to finish)"


def _vector(text: str | None):
    return None if text is None else [float(x) for x in text.split(",")]


def parse_request(raw: bytes) -> dict:
    """UTF-8 JSON from stdin; a leading BOM (added by PowerShell pipes) is tolerated."""
    return json.loads(raw.decode("utf-8-sig"))


def _emit(data: Any) -> None:
    sys.stdout.buffer.write(json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cura_ai")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("request", help="read one JSON request from stdin, write the JSON answer")
    chat = sub.add_parser("chat", help="interactive settings dialogue")
    chat.add_argument("--printer", required=True, help="definition id, e.g. flsun_q5")
    chat.add_argument("--material", default="generic_pla_175")
    orient = sub.add_parser("orient", help="rank candidate orientations of an STL")
    orient.add_argument("stl")
    orient.add_argument("--goal-weights", required=True, help='JSON, e.g. {"low_support": 1}')
    orient.add_argument("--load", help="load direction x,y,z (printer axes, Z up)")
    orient.add_argument("--visible", help="visible-face direction x,y,z")
    orient.add_argument("--top", type=int, default=5)
    args = parser.parse_args(argv)

    if args.command == "request":
        _emit(handle_request(parse_request(sys.stdin.buffer.read())))
        return 0
    if args.command == "chat":
        catalogue = Catalogue.from_definitions(args.printer)
        run_chat(ClaudeCliClient(), catalogue, args.printer, args.material, input, print)
        return 0
    try:
        analysis = analyse(load_stl(args.stl), _vector(args.load), _vector(args.visible))
        ranked = score_candidates(
            analysis.candidates, analysis.metrics, json.loads(args.goal_weights)
        )
    except (OrientationError, MeshError, json.JSONDecodeError) as exc:
        _emit({"error": str(exc)})
        return 1
    _emit([s.model_dump(mode="json") for s in ranked[: args.top]])
    return 0
