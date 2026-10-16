"""Stand-in for `claude -p` with canned answers, to exercise the Cura dialog without a model.

Used by `install_plugin.ps1 -FakeClaude`; never the default. It reads the same arguments and
stdin prompt as the real CLI and prints a `--output-format json` result.
"""

import json
import sys
import time
from pathlib import Path


def settings_answer(conversation: list[dict]) -> dict:
    user_turns = [m for m in conversation if m["role"] == "user"]
    if user_turns[-1]["content"].startswith("Cura's validation rejected"):
        return proposal(valid_only=True)
    if "this part only" in user_turns[-1]["content"].lower():
        return {
            "kind": "proposal",
            "summary": "More infill for this part.",
            "scope": "part",
            "overrides": [
                {"key": "infill_sparse_density", "value": "55", "reason": "Stronger part."},
                {"key": "wall_line_count", "value": "4", "reason": "Thicker walls."},
            ],
        }
    if len(user_turns) == 1:
        return {
            "kind": "questions",
            "questions": [
                {"id": "load", "text": "Which direction is the part loaded, and how strongly?"},
                {"id": "look", "text": "Does the surface finish matter?"},
            ],
        }
    return proposal(valid_only=False)


def proposal(valid_only: bool) -> dict:
    overrides = [
        {"key": "infill_sparse_density", "value": "40", "reason": "More infill for strength."},
        {"key": "wall_line_count", "value": "3", "reason": "Thicker walls carry the load."},
    ]
    if not valid_only:
        overrides.append(
            {"key": "support_angle", "value": "95", "reason": "Out of range on purpose."}
        )
    return {"kind": "proposal", "summary": "Stronger part with more walls and infill.",
            "overrides": overrides}  # fmt: skip


def orientation_answer(prompt: dict) -> dict:
    users = [m for m in prompt["conversation"] if m["role"] == "user"]
    if len(users) == 1 and (
        "stabil" in users[0]["content"].lower() or "stable" in users[0]["content"].lower()
    ):
        return {"kind": "questions", "questions": [{"id": "x", "text": "Which side is visible?"}]}
    best = min(prompt["candidates"], key=lambda c: (c["support_mm3"], c["height_mm"]))
    return {
        "kind": "choice",
        "weights": {"low_support": 5, "low_height": 2},
        "chosen_candidate_id": best["id"],
        "reasoning": f"{best['id']} needs the least support ({best['support_mm3']} mm3) "
        f"and is {best['height_mm']} mm high.",
    }


PART_ANSWERS = {  # a word in the user's last message picks the canned plan
    "flat": {"summary": "Lay the part flat.", "lay_flat": True},
    "mirror": {"summary": "Mirror across X.", "mirror_axes": ["x"]},
    "turn": {"summary": "Turn 90 degrees about Z.", "rotate_z_deg": 90},
    "stretch": {"summary": "Stretch X to 200 %.", "scale_x_percent": 200},
    "move": {
        "summary": "Move to x 30, y 20.",
        "move_mode": "to",
        "move_x_mm": 30,
        "move_y_mm": 20,
    },
    "reset": {
        "summary": "Undo all turning and scaling.",
        "reset_rotation": True,
        "reset_scale": True,
    },
    "normal": {"summary": "Print it normally again.", "mesh_type": "normal"},
    "blocker": {"summary": "Use as support blocker.", "mesh_type": "support_blocker"},
    "lock": {"summary": "Lock the part.", "lock": True},
    "export": {"summary": "Slice and save the G-code.", "export_gcode": True},
    "slice": {"summary": "Slice it.", "slice_now": True},
}


PART_WORDS = tuple(PART_ANSWERS) + ("scale", "copies", "bigger", "material", "quality", "printer")


def plan_answer(last: str, text: str) -> dict:
    """Tasks for the helpers named by words in the message, in the planner's order."""
    helpers = []
    if any(w in last for w in PART_WORDS):
        helpers.append("parts")
    if "orient" in last or "misroute" in last or "strong" in last:
        helpers.append("orientation")
    if any(w in last for w in ("infill", "setting", "strong", "surface")):
        helpers.append("settings")
    helpers = helpers or ["parts"]
    return {
        "announcement": "I will do this in steps." if len(helpers) > 1 else "",
        "tasks": [{"helper": h, "instruction": text} for h in helpers],
    }


def part_answer(text: str, context: dict) -> dict:
    active = context.get("active", {})
    if "restore" in text:  # back to the generic PLA material and a custom (non built-in) profile
        built_in = {"Coarse", "Extra Coarse", "Normal", "Extra Fast", "Draft", "Fine", "Extra Fine"}
        qualities = context.get("available_qualities", [])
        custom = next((n for n in qualities if n not in built_in), None)
        materials = context.get("available_materials", [])
        material = next((n for n in materials if n == "Generic - PLA"), None)
        if material and active.get("material") != material:
            return {"kind": "plan", "summary": "Restore the material.", "material": material}
        return {"kind": "plan", "summary": "Restore the profile.", "quality": custom}
    for word, key in (("material", "materials"), ("quality", "qualities")):
        if word in text:  # "set material" picks another one than the active; "back" the active
            names = context.get(f"available_{key}", [])
            current = active.get(word)
            other = next((n for n in names if n != current), current)
            pick = current if "back" in text else other
            return {"kind": "plan", "summary": f"Switch the {word}.", word: pick}
    for word, fields in PART_ANSWERS.items():
        if word in text:
            return {"kind": "plan", **fields}
    return {
        "kind": "plan",
        "summary": "Scale to 150 % and fill the plate with copies.",
        "scale_percent": 150,
        "fill_plate": True,
        "spacing_mm": 3,
        "allow_rotation": True,
    }


def main() -> None:
    args = sys.argv[1:]
    model = args[args.index("--model") + 1] if "--model" in args else "(none)"
    effort = args[args.index("--effort") + 1] if "--effort" in args else "(none)"
    with open(Path(__file__).parents[1] / ".work" / "fake_claude.log", "a", encoding="utf-8") as f:
        f.write(f"model={model} effort={effort}\n")
    schema = json.loads(args[args.index("--json-schema") + 1])
    prompt = json.loads(sys.stdin.read())
    context_file = Path(__file__).parents[1] / ".work" / "fake_context.json"
    with open(context_file, "w", encoding="utf-8") as f:
        json.dump(prompt.get("context"), f, indent=1)  # lets tests see what the model was told
    last = (prompt.get("conversation") or [{}])[-1].get("content", "").lower()
    if "slow" in last:  # lets tests press Cancel
        time.sleep(40)
    if "tasks" in schema.get("properties", {}):  # the planner: one task per helper the words name
        answer = plan_answer(last, prompt["conversation"][-1]["content"])
    elif "weights" in schema.get("properties", {}) and "misroute" in last:
        answer = {"kind": "redirect", "redirect_to": "parts"}  # a helper hands the message on
    elif "target_axis" in schema.get("properties", {}):
        answer = part_answer(prompt["conversation"][-1]["content"].lower(), prompt["context"])
    elif "weights" in schema.get("properties", {}):
        answer = orientation_answer(prompt)
    else:
        answer = settings_answer(prompt["conversation"])
    sys.stdout.write(json.dumps({"is_error": False, "structured_output": answer}))


main()
