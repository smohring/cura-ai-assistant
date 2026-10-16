# Proposal: other agents besides the Claude CLI

Status: proposal, nothing built yet. Decision wanted: which of these you would actually use.

## What stays fixed

The model is only the translator. It never sees meshes, never invents angles, and every answer is
a JSON object that is validated against a schema and rejected, not repaired, when it does not fit.
Everything a provider has to do is therefore one call: `complete(system, prompt, schema) -> JSON`
(`LlmClient` in `service/cura_ai/llm.py`). A new provider is a new class behind that protocol; the
dialogue, the validation, the geometry and the Cura plugin do not change.

## Two directions

**A. Other agents as the brain of the assistant** (replace the Claude CLI).

| Provider | How | Structured output | Cost / network |
|---|---|---|---|
| OpenAI-compatible HTTP API | one adapter with base URL, model name, key from an environment variable; covers OpenAI, Azure, LM Studio, vLLM and Ollama's `/v1` | `response_format: json_schema` | paid, or local |
| Ollama (local) | `POST /api/chat` on `127.0.0.1:11434` with `format: <schema>` | yes (schema-constrained) | free, nothing leaves the machine |
| Codex CLI | `codex exec` with an output schema | yes | subscription |
| Gemini CLI | `gemini -p` with JSON output | partly; the service must validate harder | subscription / free tier |

**B. Cura-MCP as an MCP server** (the project's namesake): `python -m cura_ai mcp` exposes the
deterministic parts as tools: `rank_orientations`, `validate_settings`, `plan_parts`,
`compare_by_slicing`. Any MCP-capable agent (Claude Desktop, Cursor, Codex, ...) can then be the
brain itself and call our tools, with our validation as the safety net. No provider adapter is
needed for this direction; the agent brings its own model and sign-in.

## Proposed order

1. **Provider choice in the options page** (dropdown next to the AI model), with the Claude CLI as
   the default. Settings per provider stay in Cura's preferences; keys are never stored there,
   only the *name* of the environment variable that holds them.
2. **OpenAI-compatible adapter** first: it reaches the most (cloud and local) with one class.
3. **Ollama** as the free local option. This machine already runs Ollama; the open question is how
   reliably a small local model fills our schemas (see the eval below).
4. **MCP server mode** (direction B) when you want to drive Cura from another agent.
5. Codex/Gemini CLI adapters only if you have those subscriptions and want them.

## Qualifying a provider

Models differ a lot in how well they follow a schema and pick sensible weights. Before a provider
counts as "supported", it runs a small golden set (about 10 goals with the L-bracket and the T
shape, and a handful of settings goals with known sensible answers) through `settings_turn` and
`orient_turn`. The suite reports schema failures, rejected values and whether the chosen
orientation matches the hand-derived best one. The result is shown next to the provider in the
options ("tested: 9/10") so a weak model is a visible choice, not a surprise.

## Rules that carry over

- The only network access stays what the chosen provider needs; the local provider needs none.
  Every provider is an explicit opt-in in the options, never a silent fallback.
- Tests and live checks keep using Sonnet on medium unless you say otherwise (cost).
- Effort is a Claude CLI concept; other providers map it (or ignore it) in their adapter, and the
  chip in the header shows what is really used.

## Open questions

- Which of Codex, Gemini, local Ollama, or an OpenAI key would you use? That decides step 2 vs 3.
- Is direction B (driving Cura from another agent) wanted, or only direction A?
