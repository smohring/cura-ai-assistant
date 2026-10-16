# M0 findings

Verified on 2026-10-13. `$CURA` = `C:\Program Files\UltiMaker Cura 5.13.0`.
Items marked **UNVERIFIED** could not be confirmed and need a decision.

## 1. Environment

| Item | Value | Evidence |
|---|---|---|
| Cura | UltiMaker Cura 5.13.0 (Uranium 5.13.0, CuraEngine 5.13.0) | install dir name; `CuraEngine.exe` banner `Cura_SteamEngine version 5.13.0`; `cura.log` "Using conan dependencies" |
| OS | Windows 11 Pro 10.0.26300 | `[Environment]::OSVersion` |
| Active printer | Flsun Q5 (delta, `machine_shape=elliptic`, 200x200x200), PLA `generic_pla_175`, quality `fast`, plus a custom profile `flsun_q5_claude_test` | `%APPDATA%\cura\5.13\machine_instances\Flsun+Q5.global.cfg`, `extruders\flsun_q5_extruder+%232.extruder.cfg` |
| User config root | `%APPDATA%\cura\5.13\` | contains `cura.cfg`, `cura.log`, `plugins\` |
| **User plugin dir** | `%APPDATA%\cura\5.13\plugins\` (exists, empty) | `UM/Resources.py:716,728` maps `Resources.Plugins` to the folder `plugins` under the storage root; storage root resolved from `APPDATA` at `UM/Resources.py:410` |
| Shipped plugins | `$CURA\share\cura\plugins\*` (49 folders) | directory listing |
| Workstation Python | 3.13.14 (WindowsApps), used for the service venv | `python --version` |

## 2. Cura's Python

- Bundled interpreter: **CPython 3.12.2** (`cura.log`: `'cpython': {'version': '3.12.2'}`; `python312.dll` in `$CURA`).
- It is embedded in `UltiMaker-Cura.exe`. There is **no `python.exe`, no `Lib\`, no `pip`** in `$CURA` (`Test-Path "$CURA\Lib"` is False; no `python*.exe` found recursively).
- Packages already importable inside Cura: numpy 1.26.4, scipy 1.11.3, trimesh 3.9.36, shapely 2.0.6, numpy-stl 2.10.1, PyQt6 6.6.0, requests 2.32.3 (`cura.log` "Using python dependencies").
- Consequence: **nothing can be pip-installed into Cura's interpreter.** A plugin can only use the packages above or pure-Python code vendored into the plugin folder.
  The architecture therefore stands as specified: the heavy work (and any dependency) lives in the separate service venv; the plugin stays thin.

## 3. Extension plugin structure (Cura 5.13)

Based on `$CURA\share\cura\plugins\ModelChecker` and `PostProcessingPlugin`.

```
<plugin folder>/
  plugin.json     {"name", "author", "version", "api": 8, "description", "i18n-catalog"}
  __init__.py     def getMetaData(): return {}
                  def register(app): return {"extension": MyExtension()}
  MyExtension.py  class MyExtension(QObject, Extension)
  *.qml           dialog UI (optional)
```

- `api` is `8` for Cura 5.x (`ModelChecker/plugin.json`).
- Menu registration (`UM/Extension.py:21-36`, used at `PostProcessingPlugin.py:35-36`):
  `self.setMenuName("Name")` then `self.addMenuItem("Entry", self.callback)`; the entries appear under the *Extensions* menu.
- Scene/application hooks used by shipped extensions: `Application.getInstance().getController().getScene().sceneChanged`, `Application.getInstance().initializationFinished`.

## 4. Reading the selected mesh and applying a rotation

- Selection: `UM.Scene.Selection.Selection.getAllSelectedObjects()` (`UM/Scene/Selection.py:111`), `hasSelection()`, `getCount()`.
- Mesh: `node.getMeshData()` (local coordinates), `node.getMeshDataTransformedVertices()` (world, `UM/Scene/SceneNode.py:364-401`); `MeshData.getVertices()` / `getIndices()` return numpy arrays (`UM/Mesh/MeshData.py:195,228`).
  Indices can be `None` for unindexed meshes.
- **Coordinate system:** the scene is Y-up. The engine path converts with `verts[:, [1, 2]] = verts[:, [2, 1]]; verts[:, 1] *= -1` (`CuraEngineBackend/StartSliceJob.py:494-495`),
  i.e. printer Z = scene Y, printer Y = -scene Z. Scoring must use the printer convention (Z up) and convert at the plugin boundary.
- Rotation: `UM.Operations.RotateOperation(node, Quaternion)` (`UM/Operations/RotateOperation.py`) applies `node.rotate(q, TransformSpace.World)` and is undoable.
  `Quaternion.setByAngleAxis(angle, Vector)` exists (`UM/Math/Quaternion.py:49`); a matrix to quaternion helper is **not** listed there, so the service should return axis-angle (or a quaternion) rather than a matrix.
  Run it through `Selection.applyOperation` / `GroupedOperation` so it joins the undo stack. After the rotation the model must be dropped to the plate: `UM/Operations/GravityOperation.py` is available, and `LayFlatOperation.py` shows a related pattern.

## 5. Calling CuraEngine headless

Cura itself starts the engine as `CuraEngine connect 127.0.0.1:<port>` and streams settings over Arcus/protobuf (`CuraEngineBackend.py:302`).
The engine also has a command-line mode that works without Cura; verified with a generated 20 mm cube and the Flsun Q5 definitions:

```bash
export CURA_ENGINE_SEARCH_PATH="$R\definitions;$R\extruders"     # R = $CURA\share\cura\resources
CuraEngine.exe slice \
  -j "$R\definitions\fdmprinter.def.json" \
  -j "$R\definitions\flsun_q5.def.json" \
  -e0 -j "$R\extruders\flsun_q5_extruder.def.json" \
  -s roofing_layer_count=1 -s flooring_layer_count=1 \
  -e0 -l cube.stl -o cube.gcode
```

- `--help` is not supported (`Unknown option: --help`). `CURA_ENGINE_SEARCH_PATH` is a string present in the binary (`grep -a`); the variable's exact semantics beyond that are inferred from behaviour.
- Order matters: `fdmprinter.def.json` first, then the printer, then `-e<n>` before the extruder definition. Add `-v` for verbose output.
- Overrides are `-s key=value` (global) or after `-e<n>` (per extruder); per-mesh settings come after `-l`.
- **Not self-contained:** settings whose value in the definition is a formula are unresolved without Cura's Python evaluator. The engine aborted with
  `Trying to retrieve setting with no value given: roofing_layer_count`, then the same for `flooring_layer_count`; with both supplied it sliced. Placeholder values were used, so the result below only proves the plumbing.
- **The user's actual profile (quality `fast`, material, user changes) lives in Cura's container stacks, not in the definition files.** For M4 the plugin must export the resolved settings, the same way `StartSliceJob` does (`for key in stack.getAllKeys(): result[key] = stack.getProperty(key, "value")`, lines 541-542), and the service passes them as `-s`.
- One non-fatal message remains unexplained: `Couldn't find definition file with ID: flsun_q5_extruder`; the slice still completes.
- **Reading results:** the engine log prints `Print time (s): 2740`, `Print time (hr|min|s): 0h 45m 40s` and `Filament (mm^3): 4559`.
  In the G-code the header `;TIME:` / `;Filament used:` / `;MIN*` / `;MAX*` lines are **placeholders** (`;TIME:6666`, `;Filament used: 0m`, `;MINX:2.14748e+06`); the real `;TIME:2740` and `;Filament used: 1.89543m` are repeated at the end of the file. Parse the log lines, not the header.
- Cost: a 20 mm cube at 0.1 mm layers slices in about 0.05 s.
- Support volume is not reported as a number; M4 has to derive it (e.g. from `;TYPE:SUPPORT` extrusion in the G-code) - to be verified in M4.

## 6. `claude -p` for non-interactive use

Binary: `%APPDATA%\Claude\claude-code\<version>\<hash>\claude.exe` (found: 2.1.293 and 2.1.295; not on PATH). Version string `2.1.295 (Claude Code)`.
Sources: `claude --help` (saved output) and <https://code.claude.com/docs/en/headless>.

| Need | Option |
|---|---|
| non-interactive | `-p` / `--print` |
| single JSON result object | `--output-format json` (`text` default, `stream-json` needs `--verbose`) |
| schema-constrained output | `--json-schema '<schema>'`; validated object arrives in the **`structured_output`** field of the result; an invalid schema makes the CLI exit with `Error: --json-schema is not a valid JSON Schema` |
| no tools | `--tools ""` |
| no MCP servers | `--strict-mcp-config` |
| no skills/slash commands | `--disable-slash-commands` |
| no transcript on disk | `--no-session-persistence` |
| model | `--model sonnet` (alias or full name) |
| cost cap | `--max-budget-usd <n>` |

- Run it from an **empty working directory** so no project files or `CLAUDE.md` are loaded.
- `--bare` is the documented way to get a reproducible run, but it never reads OAuth credentials and needs `ANTHROPIC_API_KEY`. With a subscription login it cannot be used, and an API key would be a new billing path - **needs your decision.**
- The result object carries `is_error`, `result` (text, or the error message), `total_cost_usd`, `terminal_reason`, `api_error_status`.
- **UNVERIFIED live:** the real call failed before reaching the model: `is_error: true`, `result: "Failed to authenticate: OAuth session expired and could not be refreshed"`, `terminal_reason: "api_error"`. So the `structured_output` field and the behaviour on schema-violating answers are taken from the docs, not observed. They need one successful run after you sign in again (`claude` interactive login).
- The service must still validate the object itself (e.g. `jsonschema`); the CLI schema is a first line, not the only one.

## 7. Deviations and open decisions

1. **M4 needs one more plugin duty:** exporting Cura's resolved settings to JSON, because the engine's `slice` mode cannot evaluate formula settings or see the user's profile. The service-side architecture is unchanged; the plugin gains a small "dump settings" function. This is within the spec's split (UI/scene vs. computation), but it is a new responsibility, so I am flagging it.
2. **Rotation as axis-angle / quaternion, not matrix** (section 4).
3. **`--bare` is unusable on a subscription login** (section 6). Proposed substitute: `--tools "" --strict-mcp-config --disable-slash-commands --no-session-persistence` from an empty directory. No decision needed unless you prefer an API key.
4. **Please re-authenticate Claude Code** so M2's live check can be run; until then the `claude -p` structured-output behaviour is documentation-only.
