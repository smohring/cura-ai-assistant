# Changelog

## 0.1.0

First release.

- Planner: one message is split into up to three tasks (part and setup, orientation, print
  settings); the model adds what the goal needs, tasks run in turn, questions pause the queue,
  a helper that gets a foreign request hands it on, a failed step does not stop the others,
  Cancel stops a running request.
- Print settings: complete proposals for a goal in one answer (about 160 settings in view, think
  first, then propose), validated against Cura's definitions and limits, test slices before and
  after, apply and undo; per-part settings; the user's own values are marked and left alone.
- Orientation: generated and measured candidates, the model weighs the goal and picks one, ranked
  with numbers, optional test slices of the best three.
- Part and setup: scale (percent, size, per axis), copies and fill-the-plate with silhouette
  packing, turn, mirror, lay flat, move (matches Cura's move panel), resets, lock, mesh types,
  slicing with time and material, G-code export, switching printer, material and quality
  (filament diameter respected), discarding changes.
- Providers: Claude CLI (default), OpenAI-compatible API, Gemini API, Codex CLI, Gemini CLI,
  Ollama. Only Claude and the Ollama model list were tried with a live account.
- The assistant dialog does not block Cura's main window.
- Calls to Claude ignore the user's own Claude settings and instructions (`--setting-sources local`).
- Tested with Cura 5.13 on Windows and one delta printer.
