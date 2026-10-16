# Plan and status (2026-10-13)

Supersedes the milestone order of the original spec. M0 is done (`FINDINGS.md`). The settings
assistant is a core feature and works as a dialogue: clarifying questions first, then a proposal.

## Principles

- Every LLM turn is exactly one of: `questions`, or a result (`proposal` / `choice`).
  Off-schema output is rejected with a clear error, never repaired by guessing.
- The service is stateless: the plugin sends the whole conversation each turn.
- Proposed setting keys and values are validated against Cura's own definitions (min/max, enum
  options, types); invalid ones are dropped and listed. An invalid proposal goes back to the model
  once; the new answer replaces the old one only if it is not worse.
- "Optimal" is never claimed beyond what is checked (see README, Limits).
- Nothing is applied without confirmation, per value or as a whole.

## Status

| # | Content | State |
|---|---|---|
| M0 | verification spike | done, `FINDINGS.md` |
| S1 | dialogue core: turn schema, LLM client, strict validation | done, mocked tests; live test needs a fresh Claude Code sign-in |
| S2 | setting catalogue from definitions or plugin export, validator | done, tested on the real Flsun Q5 stack |
| S3 | dialogue loop (`chat`, `request settings_turn`) | done, scripted conversation tests |
| S4 | Cura dialog: chat, confirm, apply, undo, options (model, auto-apply) | done; exercised in Cura 5.13 with a stand-in for the model (skill `cura-ui-test`) |
| S5 | test slices of proposals and orientations | done, real CuraEngine in tests |
| O1 | orientation candidates and scoring | done, cube / L-bracket / T with hand-derived expectations |
| O2 | orientation dialogue with the LLM | done, mocked tests; live untested |
| O3 | orientation in the extension (apply rotation, slice table) | done; rotation, drop to plate and the test slices exercised in Cura. Undo (Ctrl+Z) not verified through the GUI; the log shows one grouped operation |

## Verified (2026-10-14)

- Inside Cura 5.13, driven through the real window (skill `cura-ui-test`): dialog, options page,
  chat, suggestion cards, test slices, apply, undo, rotation, drop to plate.
- Against the real model (Sonnet, medium effort): `structured_output` works; vague goals give
  questions, concrete goals give valid proposals (about 8-13 s); orientation picks match the
  hand-derived best ones (L-bracket profile face down, T upside down or on its side).
- Test and live calls use Sonnet on medium only (cost); see `CLAUDE.md`.

## Still open

- Ctrl+Z after a rotation was not pressed through the GUI (keystrokes did not reach Cura). Read in
  Uranium's source instead: the rotation and the drop are one `GroupedOperation`, its `undo`
  runs the children in reverse and each restores the transformation it saw on creation, so one
  undo returns the part to its pose.
- Opus, Fable and Haiku were never called; the CLI aliases `fable` and `haiku` are taken from the
  documentation.
- Prompt quality over many goals: only a handful of real goals were tried.

## Part operations (2026-10-14): scale, copies, arrange

Third action "Edit part": scale by percent or to a target size, N copies or "fill the plate",
spacing, turning allowed. The model only fills `PartTurn` (whitelisted operations); the program
computes the positions (`packing.py`, `partplan.py`): the plate comes from Cura (rectangle minus
`getDisallowedAreas`, which also holds the corners of a round plate), other parts are obstacles,
brim or raft widens each footprint, footprints are real silhouettes so concave parts nest. The
plugin applies the result as one undoable step (`placement_matrix` is tested against the packer's
conventions). Checked in Cura 5.13: round Flsun plate, 5 scaled copies without overlap, one undo.
Real model (Sonnet, medium): correct plans for percent, target size, copy counts, and honest
refusals for move and mirror. Not yet compared head to head with Cura's own arranger.

### Toolbar operations, per-part settings, slice and export (2026-10-14, later)

`PartTurn` grew the rest of the left toolbar: per-axis scale, free rotation about X/Y/Z, mirror,
lay flat (largest planar face group down), move to / by plate coordinates, reset rotation / scale,
lock, auto drop, plus mesh type (support blocker, support, infill, cutting mesh), `slice_now` and
`export_gcode`. Shape operations are one 3x3 matrix in printer axes (`transform.py`, order: lay
flat, mirror, scale, turn); the packer sees the reshaped silhouette; `placement_matrix` takes that
matrix and keeps the part on the plate. Resets are plugin-side (`reset_transform`) because only
Cura knows the node's own transformation. Per-part settings are a `scope` on the settings
proposal ("part" -> the node's SettingOverrideDecorator, undoable with "Undo last change"; test
slicing is refused for them). Slicing uses `forceSlice` and the backend state signal; G-code is
written from `scene.gcode_dict` to `export_dir` (config, default Documents/CuraAi), never over an
existing file. Status: service side and geometry unit-tested; Cura-side code not run in Cura yet.

### Setup: printer, material, quality, discard changes (2026-10-14, later)

Part of "Verwaltung": switching only, no creating or deleting. The plugin sends an inventory
(printers, materials of the active nozzle, built-in qualities and the user's own profiles); the
model answers with listed names, the service maps them to ids and refuses unknown names.
Findings: material names repeat across brands (labels are "Brand - Name"); Cura lists 1.75 and
2.85 mm variants of the same material, and picking by root id silently chose the 2.85 mm one, so
the list is filtered to the nozzle's filament diameter. Checked in Cura: switch material, quality
and own profile and back; the stack files and the user profile were byte-identical afterwards.
`discard_changes` is unit-tested only (it would wipe the user's profile changes).
Setup and slicing work without a part on the plate (the request then carries no STL; every part
operation is refused with a clear message). Not built: adding or removing printers, saving
profiles, post-processing scripts.

Real model (Sonnet, medium, German requests, about 4 s each): 12 of 12 plans were correct - turn
and lay flat, mirror, one-axis scale, move, 6 copies with 5 mm, material by name, own profile by
name, slice and export, both resets, support blocker, lock plus 80 mm; "delete the part" was
refused with a question that lists what is possible. Weak spots not probed: ambiguous material
names ("PETG" exists under many brands), and a printer switch combined with other operations.

### One input, the model plans (2026-10-14, later)

A request for scaling went to the orientation helper (the user had pressed that button) and was
refused. The user's goal: write a sentence, the model evaluates everything and does or proposes
what is best. Now: `planner.py` reads the message plus context and returns up to three tasks
(one per helper: parts, settings, orientation), each with a self-contained instruction, and adds
what the goal needs unasked (real Sonnet, medium, German: "must be stable, hangs on a wall" gave
orientation + settings; "150 % and best orientation" gave parts + orientation; "just 80 mm high"
only parts; "what can you do" went to parts, which answers with a question). The plugin keeps a
task queue: first task now, the rest after each result; an answer to a question stays with the
helper that asked; a helper may answer `redirect`. Cancel kills the service process tree. Helpers
stay separate underneath because each needs its own data and strict schema (setting catalogue,
mesh analysis, plate and inventory). Open: tasks only propose (cards to apply), so "slice after
applying" is a second message; a real tool-calling loop (docs/VISION.md) would remove that. The
queue, redirect and quick-prompt code is not yet run in Cura (the user's own Cura was open).

### Providers (2026-10-14)

Options page chooses Claude CLI, OpenAI-compatible API, Gemini API, Codex CLI, Gemini CLI or
Ollama (`providers.py`). Everything except Claude and the Ollama model list is written from the
documentation and tested against mock servers; no live account was used.

Proposals written down for the next steps: `docs/AGENTS.md` (other providers, MCP server mode) and
`docs/VISION.md` (operating Cura through a registry of tools, with effort estimates).

## Done after the first real use (2026-10-14): both items below, option (a) for the second

Checked in Cura 5.13 with the stand-in model: the only part is used and selected without a click
and the header follows scene changes; one conversation with "Suggest settings" and "Suggest
orientation" over the same history (a goal said once drives both; an empty input reuses it).
Options (b) router and (c) combined schema are still open ideas if (a) turns out clumsy.

Original notes of the two requests:

1. **Default part.** With exactly one part in the scene, use it without asking: today the user
   has to close the dialog, click the part and reopen it. Plan: `selected_node()` falls back to
   the only printable part (and selects it, so the header and the rotation agree); with several
   parts and none selected, say so in the header. The dialog should also follow scene changes
   (part added or removed), not only selection changes.
2. **One conversation instead of two tabs.** The tabs are a fine entry point, but the user
   describes the goal twice ("nice surface" under Print settings, again under Orientation).
   Direction: one chat with one history; the goal said once is used by both. Options, cheapest
   first: (a) keep both actions but share the history, so `orient_turn` already sees the earlier
   goal, and replace the tabs by two actions on the same chat ("Suggest settings", "Suggest
   orientation"); (b) a router step that decides which of the two the message needs; (c) one
   combined turn schema with optional settings and orientation parts (heavier prompt, since the
   candidate table is sent even for settings-only goals). Start with (a); the shared history is
   already in the requests, only the UI and the per-mode state (`_clear_conversation` on tab
   switch) have to change. Tests: a goal given once drives both proposals; stand-in model for
   the UI run.

## Deviations from the original spec

- Order: settings first (S1-S5), orientation after (O1-O3).
- The plugin exports Cura's resolved settings and limits (`jobexport.py` format) because
  CuraEngine's `slice` mode cannot evaluate formula settings and does not know the user's profile.
- Rotations are returned as axis-angle and as a quaternion in Cura's scene axes (Uranium has no
  matrix-to-quaternion helper).
- Test slices go through a generated definition file with `overrides`, not `-s` arguments (the
  command line would exceed the Windows length limit with ~1000 settings).
- Test slices re-derive formula settings after applying overrides (`resolution.py`); a plain
  merge left `infill_line_distance` stale, so a density change had no effect in the slice.
  Found in the final plan check, covered by a real-engine test.
- Dependencies: pydantic (schemas), numpy and scipy (mesh math, convex hull), pytest and ruff.
