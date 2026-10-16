# Project knowledge

Assistant inside UltiMaker Cura: you describe what you want in plain language; a planner splits
it into tasks for three helpers (part and setup, orientation, print settings) which ask, propose
and apply. Plan and status: `docs/PLAN.md`. Verified facts about Cura/CuraEngine/`claude -p`:
`docs/FINDINGS.md`. Setup and use: `README.md`.

## Layout

| What | Where |
|---|---|
| Standalone service (own venv, no Cura imports) | `service/cura_ai/` |
| Service tests (`pytest`, `ruff`) | `service/tests/` |
| Thin Cura plugin, installed by script | `cura_extension/CuraAiAssistant/` |
| Plugin install, release archive, UI-test helpers | `scripts/` |
| Scratch exchange folder plugin <-> service (git-ignored) | `.work/` |

Flow: dialog (QML) -> `CuraAiAssistant.py` (task queue) -> `service_client.py` ->
`python -m cura_ai request` (one JSON in, one JSON out) -> `cli.handle_request`. The service never
imports Cura; the plugin never imports the service. Files exchanged: the plugin writes
`.work/model.stl` in printer axes.

Key modules: `planner.py` (message -> tasks), `assistant.py` / `orient_assistant.py` /
`part_assistant.py` (one dialogue turn each), `catalogue.py` (settings, limits, formulas via
`formula.py`), `validation.py` (drops invalid overrides), `orientation.py` (candidates and
scoring), `packing.py` + `partplan.py` + `transform.py` (copies and part operations),
`engine.py` + `verify.py` (headless slices), `jobexport.py` (the plugin's export format),
`resolution.py` (overrides without stale derived values), `providers.py` / `llm.py` (clients).

## Rules that carry the design

- The LLM never sees raw meshes and never invents angles; it picks weights and a candidate id.
- Every LLM answer is validated against a strict schema; off-schema output is rejected, not
  repaired. A rejected answer is sent back to the model once with the reason.
- LLM output is never executed. Formulas in definition files are evaluated through an AST
  whitelist (`formula.py`), not `eval`.
- Machine hardware settings (`machine_*`, categories `machine_settings`, `command_line_settings`)
  and non-scalar types are never changed by the assistant.
- Network access only to the provider the user chose (Claude CLI by default; OpenAI-compatible,
  Gemini, Codex/Gemini CLI or local Ollama). API keys are read from environment variables, never
  stored.
- Values the user set themselves are marked for the model and left unticked in proposals.
- Keep live model calls in scripts and tests on a cheap setting (Sonnet, medium effort); that is
  also the plugin's default. The opt-in live test uses the client defaults.

## Traps

- Cura 5.13 lives in `C:\Program Files\UltiMaker Cura 5.13.0`; user data in
  `%APPDATA%\cura\5.13\`. Its Python (3.12.2) is embedded in the exe: no `python.exe`, no pip.
  Uranium cannot be imported outside Cura (pulls in Qt), so plugin code that touches Cura
  cannot be run or unit-tested; keep it thin and put logic in `geometry.py` (tested).
- The scene is Y-up; the engine path converts to Z-up (`StartSliceJob.py:494`). The service works
  in printer axes (Z up). Rotation to Cura: `RotateOperation(node, q, rotate_around_point=
  node.getPosition())` like Cura's RotateTool, plus a `TranslateOperation` to drop to the plate.
- `CuraEngine slice` cannot evaluate formula settings and does not know the user's profile. Pass a
  generated definition file with `overrides` (`engine.write_overrides`); `-s` arguments for ~1000
  settings exceed the Windows command-line limit. G-code header `;TIME`/`;Filament used` are
  placeholders; read the engine log and the G-code body.
- `claude.exe` is not on PATH (`%APPDATA%\Claude\claude-code\<ver>\<hash>\`). Run it from an
  empty directory. `--bare` needs an API key. The sign-in expires; `OAuth session expired` means
  sign in to Claude Code again.
- **The Microsoft Store Python (`...\WindowsApps\python.exe`) cannot see
  `%APPDATA%\Claude` or other Roaming files** (MSIX file virtualization:
  `Path.exists()` is False, starting `claude.exe` fails with WinError 2) although
  PowerShell sees them. A junction does not help (it resolves to the same
  hidden path); a **hardlink** does. The service finds `.tools\claude.exe`
  (git-ignored) before looking in AppData. Create it after each Claude Code
  update:
  `New-Item -ItemType HardLink -Path .tools\claude.exe -Target <newest claude.exe> -Force`.
  Anything that must read `%APPDATA%\cura` has to run inside Cura's own Python.
- **Overrides must be applied through `resolution.resolve_with_overrides`**, never a plain dict
  merge: the export holds Cura's resolved values, so a derived setting (`infill_line_distance`
  from `infill_sparse_density`) would keep its old value and the engine, which uses the derived
  one, would ignore the change. Settings whose value differs from their formula count as set
  explicitly and are kept.
- The dialogue turn and the verification must score orientations with the same `support_angle`;
  the plugin sends it with both requests.
- PowerShell pipes add a UTF-8 BOM to stdin; `cli.parse_request` tolerates it.
- Commit messages go through a file (`git commit -F`); keep `.git-commit-msg.tmp` out of `git add`.
- Verify every API detail against the installed sources or by running it.
