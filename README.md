# Cura AI Assistant

An assistant for [UltiMaker Cura](https://ultimaker.com/software/ultimaker-cura/) that you talk to
like a voice assistant. Describe what you want in your own words - "it is a small figure, the
surface must be as good as possible", "make it 150 % and find the best orientation", "switch to
PETG and print four of them" - and it works out what to do, asks when something is unclear, and
either applies the result or shows it to you as a card to confirm.

It runs inside Cura as a plugin. A small local service does the numbers; a language model only
decides *what* to do and explains why.

> Status: version 0.1.0, developed and tested with Cura 5.13 on Windows and a delta printer
> (Flsun Q5). Read [Limits](#limits) before trusting it with anything important.

## What it can do

| You say | What happens |
|---|---|
| "Strong enough to hang on a wall" | Asks about load and mounting if needed, then proposes the **best orientation** (measured, ranked) **and** the **print settings** that fit |
| "The surface has to be perfect" | A complete settings proposal in one answer: walls, skin, ironing, speeds and accelerations, cooling, temperatures, and the support interface if the part needs supports |
| "Make it 150 %", "80 mm tall", "stretch X to 200 %" | Scales uniformly, to a size, or per axis |
| "6 copies, 5 mm apart", "fill the plate" | Packs copies on the real silhouette of the part (round plates, no-go areas, brim or raft, other parts) |
| "Turn it 90 degrees", "lay it flat", "mirror it", "move it to x 30, y -20" | The left-toolbar operations; positions match Cura's own move panel |
| "Make it a support blocker", "lock it", "reset the rotation" | Mesh types, lock, resets |
| "More walls on this part only" | Per-part settings instead of global ones |
| "Switch to ABS", "use my profile ...", "switch to the other printer" | Printer, material and quality (your own profiles included) |
| "Slice it and save the G-code" | Slices, reports print time and material, writes the G-code file |

Every request is evaluated by the model: a planning step splits a message into up to three tasks
(handling the part and setup, orientation, print settings), adds what the goal needs even if you did
not name it, and runs them one after another. Anything you do not want, you untick or undo.

## How it stays trustworthy

- **The model never touches the mesh and never executes anything.** It picks from a fixed list of
  operations and fills in a JSON schema. Orientation candidates are generated and scored by
  deterministic code; the model only weighs the goal and chooses one candidate id.
- **Every answer is validated, never repaired.** Setting keys and values are checked against Cura's
  own definitions and limits; an answer off the schema is sent back once, and rejected if it is
  still wrong.
- **Nothing happens behind your back.** Results are cards you apply (or switch on "apply
  automatically"). Part changes are one undo step (Ctrl+Z), settings have "Undo last change".
- **Your own settings are spared.** Values you changed yourself - unsaved changes and the values of a
  custom profile of yours - are marked for the model, which leaves them alone unless the goal cannot
  be met otherwise. If it still touches one, the card says so and comes unticked.
- **Optional check by slicing.** Proposals can be test-sliced headlessly with CuraEngine to show
  print time, material and support before and after.
- **Cancel** stops a running request and takes your message back.

## Requirements

- UltiMaker Cura 5.13 (plugin API 8). Other versions are untested.
- Windows with PowerShell (the install script and the UI tests are PowerShell). The Python service is
  portable, the installer is not yet.
- Python 3.11 or newer for the service.
- A language model, one of: the **Claude Code CLI** signed in (default), an OpenAI-compatible API,
  the Gemini API, the Codex or Gemini CLI, or a local **Ollama**. Only Claude (and the Ollama model
  list) has been tried with a live account; the others are written from the vendors' documentation
  and tested against mock servers.

## Install

```powershell
git clone <this repository> cura-ai-assistant
cd cura-ai-assistant\service
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd ..
powershell -ExecutionPolicy Bypass -File scripts\install_plugin.ps1
```

From a release archive instead of a clone: unzip it somewhere permanent and run the same commands
from the unzipped folder.

Restart Cura. The assistant is under **Extensions > AI Assistant > Open assistant...**.

The plugin calls the service in `service\.venv` where you cloned it, so keep the folder where it is
(or run the install script again after moving it). After changing anything under
`cura_extension\`, run the script again and restart Cura.

## Use

The dialog has one input line and a **Send** button. Type what you want and press Enter. The line
above the conversation shows printer, material, quality and the selected part (with only one part on
the plate it is picked for you). The dialog does not block Cura: select parts and move the view
while it is open.

The three buttons below the input ("Run one helper directly") skip the planning and ask exactly one
helper - settings, orientation, or part and setup - on the conversation so far.

**Options** (button or the "AI: ..." chip) chooses the AI provider and model, the effort for Claude
(low to max), and whether suggestions are applied automatically. API keys are never stored: the page
takes the *name* of an environment variable that holds the key.

### What is sent where

Your messages, the names and values of print settings, and the size of the part are sent to the
provider you chose. The mesh itself is never sent; orientation and packing are computed locally. With
Ollama nothing leaves your computer. Test slices and G-code stay local.

## Troubleshooting

- **"claude executable not found"**: install Claude Code and sign in, or point the plugin to it with
  the environment variable `CURA_AI_CLAUDE` (a path). The service looks there first, then on `PATH`,
  then in `.tools\claude.exe` of the checkout, then in the Claude desktop app's folder. A Python
  from the Microsoft Store cannot see `%APPDATA%`; a hard link `.tools\claude.exe` to the CLI solves
  that (see `CLAUDE.md`).
- **The dialog does not open or shows an error in red**: details are in Cura's log,
  `%APPDATA%\cura\5.13\cura.log`; search for "AI Assistant".
- **Nothing changed after an update**: run `scripts\install_plugin.ps1` again and restart Cura.

## Limits

- "Optimal" means: valid for your printer, justified against the goal you stated and, if you ask,
  compared by test slices. How a print turns out is only known after printing.
- Orientation metrics are heuristics (support volume is an estimate, surface quality is a model).
- Tested on one machine and one printer type. Sliced estimates come from CuraEngine as installed.
- Proposals can replace values; you see old and new value on every card and can decline or undo.
- Not built: adding or removing printers, saving profiles, post-processing scripts.
- A planned message can only *propose* orientation and settings; slicing and export come after you
  applied them ("then say: export it").

## Problems and ideas

Please open an [issue](../../issues) in this repository (there are templates for a bug report and
for a feature request). Include the Cura version, the AI provider you use and the lines with "AI
Assistant" from Cura's log; that is usually enough to find the cause.

## Development

```powershell
cd service
.\.venv\Scripts\python.exe -m pytest          # 300+ tests, mock servers and fake runners
.\.venv\Scripts\ruff.exe check .
```

Real-engine tests need Cura installed and skip themselves otherwise. A live test against the real
Claude CLI is opt-in: `$env:CURA_AI_LIVE = "1"; python -m pytest -m live`.

The plugin's Cura-facing code cannot run outside Cura. Its geometry is cross-checked against the
service in `service/tests/test_plugin_geometry.py`, and the dialog is exercised end to end with a
stand-in for the language model (`scripts/fake_claude.py`) through the skill
`.claude/skills/cura-ui-test` - read its pitfalls before driving Cura from a script.

| Path | What |
|---|---|
| `service/cura_ai/` | the service: setting catalogue, validation, orientation, packing, engine runs, planner, helper dialogues, provider clients |
| `cura_extension/CuraAiAssistant/` | the thin plugin: dialog (QML), selection, applying settings, rotations, copies |
| `scripts/` | installer, release build, UI-test helpers, the stand-in model |
| `docs/PLAN.md` | design, decisions with their reasons, status |
| `docs/FINDINGS.md` | verified facts about Cura 5.13, CuraEngine and the Claude CLI |
| `docs/AGENTS.md`, `docs/VISION.md` | providers and the road to a tool-calling assistant |

Build a release archive with `powershell -File scripts\build_release.ps1` (result in `dist\`).
Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

MIT, see [LICENSE](LICENSE). UltiMaker Cura and CuraEngine are separate software under their own
licenses; this repository contains none of their files.
