---
name: cura-ui-test
description: Drive the running UltiMaker Cura window from PowerShell to test the AI Assistant plugin end to end - open the dialog, click, type, read values, take window screenshots. Use after any change under cura_extension/ and before reporting a UI change as done.
---

# Testing the plugin inside Cura

Cura is a Qt Quick app. It exposes ~850 controls through Windows UI Automation (names and
rectangles), but not usable Invoke patterns, so: **find with UIA, click with the mouse, look
with a screenshot of the Cura window.** Helpers: `scripts/cura_ui.ps1` (dot-source it).

## Loop

1. `powershell -File scripts\install_plugin.ps1 -FakeClaude` installs the plugin with
   `scripts/fake_claude.py` standing in for `claude -p` (canned questions, proposals, choices;
   logs the `--model` it got to `.work/fake_claude.log`). Needed because the real CLI needs a
   signed-in user. **Reinstall without `-FakeClaude` afterwards** - the stand-in must never stay.
2. Start Cura with a test model: `Start-Process UltiMaker-Cura.exe '"...\.work\t.stl"'`, wait
   ~30 s. QML and Python are loaded once per start: every plugin change needs a restart.
3. `. .\scripts\cura_ui.ps1; $dlg = Open-Assistant` - **never go through the menu bar**: Cura's
   Qt menus open only now and then for synthetic mouse or key input (hours lost). The stand-in
   install (`-FakeClaude`) makes the plugin watch `.work\open_assistant.trigger`;
   `Open-Assistant` writes it and waits for the dialog. Then `Ask-Assistant $dlg 'text'
   'Edit part'` types into the input (not exposed by UI Automation, clicked by position) and
   waits for the answer; buttons are found with `Find-Elements` and filtered to
   `ControlType.Button` (a name also matches the text inside it, so take the Button).
   The stand-in picks its canned plan by a word in your text (`PART_ANSWERS` in
   `scripts/fake_claude.py`; "this part only" gives a per-part settings proposal).
4. Buttons are named by their text: `^Suggest settings$`, `^Suggest orientation$`, `^Options$`,
   `^AI: ` (the chip), `^Test slice$`, `^Apply selected$`, `^Apply rotation$`. There are no tabs;
   idea chips ("Minimal support", ...) start a request. Wait for the answer by polling for the
   text `^Thinking` to disappear. `Find-Elements $dlg ...`, `Click-Element`, `Send-Text`,
   `Send-Keys '{ENTER}'`,
   `Save-Shot $dlg path.png`, then read the PNG. Read values instead of guessing: the model size
   (`' x .* mm$'`), the infill (`'^\d+%$'`), a CheckBox's `TogglePattern`.
5. Check `%APPDATA%\cura\5.13\cura.log` for `AI Assistant:` lines and QML warnings.

## Pitfalls that cost time

- **Check `Get-Process UltiMaker-Cura` before starting your own Cura.** The user may be working
  in one (started at a time you did not start it): a second instance shares the configuration, and
  `install_plugin.ps1 -FakeClaude` would point *their* plugin at the stand-in (config.json is read
  on every call). If one runs that is not yours, do not install the stand-in; ask. Close only the
  instance you started (`Get-Process -Id`), then reinstall the real plugin and check
  `config.json` has no `fake_claude` / `open_trigger`.

- **Clicks and screenshots use screen coordinates, so another app on top gets the click.** The
  helpers now refuse to click, type or take a screenshot unless the window under the pointer (or
  the foreground window) is Cura's, and `Get-CuraWindow` brings Cura to the front and checks it.
  If you see "Refusing to act ... belongs to 'claude'", the user's own window is in the way: do
  not work around it, wait or ask. Never hard-code coordinates; the window moves and resizes
  (read `BoundingRectangle`, e.g. 886,18,1718x1360 one run and 0,23,3440x1369 the next).
- `Find-Elements` returns nothing while Cura is still loading; check the result before
  `Click-Element` (a `$null` becomes a click at 0,0, which the guard catches).
- Starting Cura while one runs leaves **two instances** (shared config). Quit the old one first,
  and `Get-CuraProcess` picks the newest; `Get-CuraWindows -ProcessId` addresses the other.
- Theme colours can be unusable as text colours: `setting_validation_ok` is almost transparent in
  the dark theme (the "was ..." lines vanished). Look at a screenshot, do not trust the name.
- **Only use Sonnet on medium for model calls in tests** (cost; see CLAUDE.md). Check the "AI:"
  chip before sending.

- **The assistant is its own top-level window of the same process.** `Get-CuraWindow` must pick
  the one ending in "UltiMaker Cura"; with the dialog in front, menu clicks on the main window
  silently do nothing (menus open only when no dialog has the focus - close it with ESC first).
- `Process.CloseMainWindow()` closes the *dialog* while it is open (its title becomes the
  process's main window) - and then a new start leaves **two instances**, the old one eating the
  trigger file. Close the dialog first (ESC, or `CloseMainWindow` twice), check
  `Get-Process UltiMaker-Cura` shows one process whose title ends in "UltiMaker Cura", then
  start. With no dialog open `CloseMainWindow()` quits Cura cleanly (user changes are saved).
- Switching printer, material or quality changes the machine stack files, not only the user
  profile: copy `%APPDATA%\cura\5.13\machine_instances\*` and `extruders\*` to
  `.work\baseline2` before such a test and compare after quitting (byte-identical = restored;
  if not, copy the baseline back with Cura closed). Never test `discard_changes` on the real
  profile. The stand-in dumps the context it was given to `.work\fake_context.json`.
- A QML error in the dialog shows only in `cura.log` (`createQmlComponent ... Cannot assign
  object`); a non-visual `Connections` must sit inside the layout, not directly in the Dialog.
- Cura's own move panel shows Y as the printer's Y mirrored (scene z); the assistant's "y" follows
  the panel, so read results there.
- **Cura writes user changes to the profile when it exits.** Revert what a test applied (the
  dialog's "Undo last change") *before* quitting, then read
  `%APPDATA%\cura\5.13\user\*.inst.cfg`: only the pre-existing `layer_height = 0.1` may remain.
  Reset the dialog options (model Sonnet, auto-apply off), they are stored in `cura.cfg`.
- `UM.Dialog` closes itself on Return unless `closeOnAccept: false`.
- `Cura.ComboBox` needs a `catalog` property on the enclosing dialog, else it logs `i18nc`.
- Ctrl+Z through SendKeys did not reach Cura; use the Bearbeiten menu with no dialog open.
- Screenshots are of the Cura window rectangle only, never the whole screen.
- The mouse moves by itself while this runs; tell the user first and do not run it while they
  work in Cura. Stop immediately if they say so.
