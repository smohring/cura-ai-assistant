# Vision: operating Cura through the assistant

The goal in one sentence: say what you want in plain words, and the assistant does the Cura work
for you, with you confirming anything that matters.

## How it can work: a registry of small, safe tools

Not one special dialogue per feature, but one **tool** per Cura capability. A tool has

1. a name and a JSON schema for its arguments (what the language model may ask for),
2. a validator against the current state (is it possible, does it fit the plate, is the value
   within Cura's own limits),
3. a preview (what would change, in numbers) shown as a card,
4. an apply step that is one undoable Cura operation, and an undo.

The model sees the list of tools and a short digest of the current state, and answers with
questions or a **plan**: an ordered list of tool calls. The plugin validates the plan, shows it as
cards, and runs it after your confirmation (or at once for tools you marked as safe in the
options). The model still never touches meshes and never executes anything itself; it only picks
from the whitelist. This is what the settings, orientation and part tools already do, in three
separate shapes; the registry is their common form.

Not through UI automation: driving Cura's menus with clicks is brittle and breaks with every
release. Everything goes through Cura's own Python API (scene, operations, stacks), which is what
the plugin already uses.

## What is in reach, and roughly what it costs

Measured in the effort of this project so far (each tool: schema, validator, preview, apply, tests,
a check inside Cura).

| Area | Examples | Effort per tool | Notes |
|---|---|---|---|
| Part operations | scale (percent or target size), copies, arrange/fill the plate, move, rotate, mirror, delete, group, centre | small to medium; arrange is the hard one | in progress |
| Settings and profiles | settings (done), switch quality profile, save a profile, pick material, per-part overrides, support blockers | small to medium | settings already generic over all ~900 keys |
| Slice, preview, export | start the real slice, read time and material, open the preview, save G-code to a folder | medium | uses Cura's `PrintInformation` and output devices |
| Analysis | thin walls, non-manifold mesh, overhang report, repair suggestions | medium to large | `trimesh` is already inside Cura |
| Printer and network | choose printer, send to a printer, start a print | not planned by default | physical risk; the user presses the button |
| Marketplace, plugins, accounts | install and update | not planned | side effects and trust |

A useful "everyday Cura" (parts, arrange, scale, materials and profiles, slice, estimates, export)
is roughly 15 to 25 tools: a matter of days of work in this style, not weeks, once the registry
exists. "Everything Cura can do" has no end, because Cura keeps growing and much of it exists only
as UI; the sensible target is the everyday 80 percent plus a clear rule for adding a tool.

## What gets harder with more tools

- **Prompt size and reliability.** Dozens of tools in every request cost tokens and make choices
  worse. A first step picks the few relevant tools (a router), then the planning call sees only
  those. A golden set of goals (see `AGENTS.md`) keeps this honest.
- **Safety.** Reversible and local tools can run automatically if you allow it. Destructive ones
  (delete, overwrite files) always ask. Anything that moves hardware is out of scope.
- **Cura updates.** The API is checked against 5.13; each new Cura version needs a check run
  (the UI test skill exists for that).
- **Latency.** One model call is 5 to 13 seconds; a plan with many steps is still one call.

## Proposed order

1. Part operations: scale, copies, arrange, as asked (now).
2. The registry itself: turn settings, orientation and part operations into tools with one planning
   turn and one set of plan cards; one conversation already exists.
3. Slice, estimates, export; profile and material switching.
4. Router and golden-set tests; provider adapters (`AGENTS.md`).
5. Analysis tools; per-part overrides and supports.
