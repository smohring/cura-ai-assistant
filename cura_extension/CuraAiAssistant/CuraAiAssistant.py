"""Extension entry: menu item, dialog state and the calls to the standalone service."""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from UM.Application import Application
from UM.Backend.Backend import BackendState
from UM.Extension import Extension
from UM.Logger import Logger
from UM.PluginRegistry import PluginRegistry
from UM.Scene.Camera import Camera
from UM.Scene.Selection import Selection

from . import cura_bridge, service_client
from .geometry import DIRECTIONS

PLUGIN_ID = "CuraAiAssistant"
MODEL_PREFERENCE = "ai_assistant/model"
EFFORT_PREFERENCE = "ai_assistant/effort"
AUTO_APPLY_PREFERENCE = "ai_assistant/auto_apply"
PROVIDER_PREFERENCE = "ai_assistant/provider"
PROVIDER_SETTINGS_PREFERENCE = "ai_assistant/provider_settings"  # JSON: model, address per provider
# Aliases the Claude CLI resolves to the newest model of that family; "default" lets it choose.
MODELS = [
    {"text": "Sonnet - fast, good all-rounder", "key": "sonnet", "name": "Sonnet"},
    {"text": "Opus - thorough, slower", "key": "opus", "name": "Opus"},
    {"text": "Fable - most capable", "key": "fable", "name": "Fable"},
    {"text": "Haiku - quickest, simple goals", "key": "haiku", "name": "Haiku"},
    {"text": "CLI default", "key": "default", "name": "CLI default"},
]
# `claude --effort` levels. "default" passes nothing: the model's own default applies.
EFFORTS = [
    {"text": "AI model default", "key": "default"},
    {"text": "Low - quick answers", "key": "low"},
    {"text": "Medium - balanced", "key": "medium"},
    {"text": "High - thinks longer", "key": "high"},
    {"text": "Extra high - thinks much longer", "key": "xhigh"},
    {"text": "Max - deepest, slowest", "key": "max"},
]
# Default effort per alias, from the Claude Code model configuration docs. The user's own Claude
# Code settings (effortLevel) can override it, so it is shown as "default", not as a promise.
DEFAULT_EFFORT = {"sonnet": "medium", "opus": "medium", "haiku": "medium", "fable": "high"}
LOAD_CHOICES = [
    {"text": "No special direction", "key": "none"},
    {"text": "Left - right (X)", "key": "x"},
    {"text": "Front - back (Y)", "key": "y"},
    {"text": "Up - down (Z)", "key": "z"},
]
VISIBLE_CHOICES = [
    {"text": "Not important", "key": "none"},
    {"text": "Front", "key": "front"},
    {"text": "Back", "key": "back"},
    {"text": "Left", "key": "left"},
    {"text": "Right", "key": "right"},
    {"text": "Top", "key": "top"},
    {"text": "Bottom", "key": "bottom"},
]
# The two things the assistant can suggest; one conversation serves both.
ACTIONS = ("settings", "orientation", "parts")
# Starter goals for an empty chat; each runs the action it fits.
# Who does the thinking. Keys are never stored, only the name of the environment variable that
# holds one. "needs_model": the model name has no default and must be given.
PROVIDERS = [
    {
        "key": "claude", "text": "Claude (Claude Code CLI)", "short": "Claude",
        "fields": [], "defaults": {}, "needs_model": False, "model_hint": "",
        "note": "Runs through your Claude Code sign-in, so it uses the same subscription as "
        "the Claude desktop app.",
    },
    {
        "key": "openai", "text": "OpenAI-compatible API", "short": "OpenAI",
        "fields": ["base_url", "key_variable"], "needs_model": True, "model_hint": "gpt-4o-mini",
        "defaults": {"base_url": "https://api.openai.com/v1", "key_variable": "OPENAI_API_KEY"},
        "note": "Needs an API key in an environment variable (its name below; the key itself is "
        "never stored). Also fits LM Studio and other servers with the OpenAI API. Your goal, "
        "setting values and part sizes go to that server, never the meshes.",
    },
    {
        "key": "gemini", "text": "Gemini API (Google)", "short": "Gemini",
        "fields": ["base_url", "key_variable"], "needs_model": False,
        "model_hint": "gemini-2.5-flash",
        "defaults": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "key_variable": "GEMINI_API_KEY",
        },
        "note": "Needs an API key in an environment variable. Written from Google's "
        "documentation, not tried with a live key. Your goal, setting values and part sizes go "
        "to Google, never the meshes.",
    },
    {
        "key": "codex", "text": "Codex CLI (OpenAI)", "short": "Codex",
        "fields": [], "defaults": {}, "needs_model": False, "model_hint": "optional",
        "note": "Uses the codex command and its own sign-in. Written from the documentation, not "
        "tried with a live account.",
    },
    {
        "key": "gemini-cli", "text": "Gemini CLI (Google)", "short": "Gemini CLI",
        "fields": [], "defaults": {}, "needs_model": False, "model_hint": "optional",
        "note": "Uses the gemini command and its own sign-in. Written from the documentation, "
        "not tried with a live account.",
    },
    {
        "key": "ollama", "text": "Ollama (local)", "short": "Ollama",
        "fields": ["host"], "needs_model": True, "model_hint": "e.g. llama3.1:8b",
        "defaults": {"host": "http://127.0.0.1:11434"},
        "note": "Runs on this computer, nothing leaves it. Smaller models follow the schemas less "
        "reliably: a wrong answer is rejected, never applied.",
    },
]  # fmt: skip

IDEAS = [
    {"text": "Strong and durable, it will carry load"},
    {"text": "Fast draft, quality does not matter"},
    {"text": "Smooth surface for a display piece"},
    {"text": "Use as little material as possible"},
    {"text": "Minimal support"},
    {"text": "Best surface on the top side"},
    {"text": "Make it 150 %"},
    {"text": "Fill the plate with copies"},
]
# Sent for an action when the input is empty: the goal is already in the conversation.
FOLLOW_UP = {
    "settings": "Please suggest the print settings for this goal.",
    "orientation": "Please suggest the best orientation for this goal.",
    "parts": "Please change the part as discussed.",
}


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:g}"
    return "" if value is None else str(value)


def _with_unit(value: Any, unit: str) -> str:
    return f"{_fmt(value)} {unit}".strip()


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"


def _delta(new: float, old: float) -> str:
    return f"{(new - old) / old * 100:+.0f} %" if old else ""


def _tone(new: float, old: float) -> str:
    """Less time and material is better; nearly equal is neutral."""
    if not old or abs(new - old) / old < 0.02:
        return "neutral"
    return "better" if new < old else "worse"


def _truthy(value: Any) -> bool:
    return str(value).lower() in ("true", "1")


class CuraAiAssistant(QObject, Extension):
    stateChanged = pyqtSignal()
    _finished = pyqtSignal(object)
    _backendState = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setMenuName("AI Assistant")
        self.addMenuItem("Open assistant...", self.showDialog)
        self._view = None
        self._job: dict | None = None
        self._watched: list = []
        self._busy = False
        self._status = ""
        self._error = ""
        self._action = "settings"  # what Enter asks for: the action used last
        self._load = "none"
        self._visible = "none"
        preferences = Application.getInstance().getPreferences()
        preferences.addPreference(MODEL_PREFERENCE, "sonnet")
        preferences.addPreference(EFFORT_PREFERENCE, "medium")
        preferences.addPreference(AUTO_APPLY_PREFERENCE, False)
        stored = preferences.getValue(MODEL_PREFERENCE)
        self._model = stored if stored in [m["key"] for m in MODELS] else "sonnet"
        stored = preferences.getValue(EFFORT_PREFERENCE)
        self._effort = stored if stored in [e["key"] for e in EFFORTS] else "default"
        self._auto_apply = _truthy(preferences.getValue(AUTO_APPLY_PREFERENCE))
        preferences.addPreference(PROVIDER_PREFERENCE, "claude")
        preferences.addPreference(PROVIDER_SETTINGS_PREFERENCE, "{}")
        stored = preferences.getValue(PROVIDER_PREFERENCE)
        self._provider = stored if stored in [p["key"] for p in PROVIDERS] else "claude"
        try:
            loaded = json.loads(preferences.getValue(PROVIDER_SETTINGS_PREFERENCE))
            self._provider_settings: dict = loaded if isinstance(loaded, dict) else {}
        except (TypeError, ValueError):
            self._provider_settings = {}
        self._detected_models: list[str] = []
        self._last_applied: list[dict] = []
        self._clear_conversation()
        self._finished.connect(self._onFinished)
        self._backendState.connect(self._onBackendState)
        self._slice_export = False
        self._slice_waiting = False
        Application.getInstance().globalContainerStackChanged.connect(self._invalidate)
        Selection.selectionChanged.connect(self._changed)
        # A part added or removed changes the header; moves are coalesced like ModelChecker does.
        self._trigger_timer = None
        self._start_test_trigger()
        self._scene_timer = QTimer()
        self._scene_timer.setSingleShot(True)
        self._scene_timer.setInterval(250)
        self._scene_timer.timeout.connect(self._changed)
        Application.getInstance().getController().getScene().sceneChanged.connect(
            self._onSceneChanged
        )

    def _onSceneChanged(self, node=None) -> None:
        if not isinstance(node, Camera):
            self._scene_timer.start()

    def _start_test_trigger(self) -> None:
        """Only with the stand-in install: a file lets the UI tests open the dialog (Cura's menus
        do not react to synthetic input reliably)."""
        try:
            path = service_client.load_config().get("open_trigger")
        except service_client.ServiceError:
            return
        if not path:
            return

        def check() -> None:
            if os.path.exists(path):
                os.remove(path)
                self.showDialog()

        self._trigger_timer = QTimer()
        self._trigger_timer.setInterval(1000)
        self._trigger_timer.timeout.connect(check)
        self._trigger_timer.start()

    # ---- state ------------------------------------------------------------------------------

    def _clear_conversation(self) -> None:
        self._chat: list[dict] = []
        self._history: list[dict] = []
        self._proposal: list[dict] = []
        self._rejected: list[str] = []
        self._ranking: list[dict] = []
        self._weights: dict | None = None
        self._quaternion: dict | None = None
        self._rotation_text = ""
        self._tiles: list[dict] = []
        self._rows: list[dict] = []
        self._part_plan: dict | None = None
        self._proposal_scope = "print"
        self._hops = 0  # redirects between helpers for the current message
        self._queue: list[dict] = []  # tasks of the current message still to run
        self._turn_start = 0  # history index of the user's own message
        self._last_user = 0  # history index of the latest user entry (own message or a step)
        self._awaiting_answer = False  # the last turn was a question of the current helper
        self._part_lines: list[str] = []

    def _changed(self, *_args) -> None:
        self.stateChanged.emit()

    def _invalidate(self, *_args) -> None:
        self._job = None

    def _watch_stacks(self) -> None:
        _, global_stack, extruder = cura_bridge._stacks()
        current = [global_stack, extruder]
        if [id(s) for s in current] == [id(s) for s in self._watched]:
            return
        for stack in self._watched:
            with contextlib.suppress(TypeError, RuntimeError):  # already disconnected
                stack.propertyChanged.disconnect(self._invalidate)
        for stack in current:
            stack.propertyChanged.connect(self._invalidate)
        self._watched = current

    def _job_export(self) -> dict:
        self._watch_stacks()
        if self._job is None:
            self._job = cura_bridge.collect_job()
        return self._job

    # ---- properties for QML -------------------------------------------------------------------

    @pyqtProperty(bool, notify=stateChanged)
    def busy(self) -> bool:
        return self._busy

    @pyqtProperty(str, notify=stateChanged)
    def status(self) -> str:
        return self._status

    @pyqtProperty(str, notify=stateChanged)
    def error(self) -> str:
        return self._error

    @pyqtProperty(str, notify=stateChanged)
    def action(self) -> str:
        """The action Enter runs: "settings" or "orientation"."""
        return self._action

    @pyqtProperty(str, notify=stateChanged)
    def contextLine(self) -> str:
        return cura_bridge.context_line()

    @pyqtProperty(str, notify=stateChanged)
    def selectionLine(self) -> str:
        return cura_bridge.selection_line()

    @pyqtProperty("QVariantList", constant=True)
    def models(self) -> list:
        return MODELS

    @pyqtProperty("QVariantList", constant=True)
    def loadChoices(self) -> list:
        return LOAD_CHOICES

    @pyqtProperty("QVariantList", constant=True)
    def visibleChoices(self) -> list:
        return VISIBLE_CHOICES

    @pyqtProperty("QVariantList", constant=True)
    def ideas(self) -> list:
        return IDEAS

    @pyqtProperty("QVariantList", constant=True)
    def efforts(self) -> list:
        return EFFORTS

    @pyqtProperty(str, notify=stateChanged)
    def effortKey(self) -> str:
        return self._effort

    @pyqtProperty(str, notify=stateChanged)
    def modelKey(self) -> str:
        return self._model

    @pyqtProperty("QVariantList", constant=True)
    def providers(self) -> list:
        return PROVIDERS

    @pyqtProperty(str, notify=stateChanged)
    def providerKey(self) -> str:
        return self._provider

    @pyqtProperty("QVariantList", notify=stateChanged)
    def detectedModels(self) -> list:
        return self._detected_models

    @pyqtSlot(str, str, result=str)
    def settingFor(self, provider: str, name: str) -> str:
        """A provider's stored model or address, else its default; for the options page."""
        info = next((p for p in PROVIDERS if p["key"] == provider), None)
        if info is None:
            return ""
        stored = self._provider_settings.get(provider, {}).get(name)
        return stored if stored is not None else info["defaults"].get(name, "")

    @pyqtProperty(str, notify=stateChanged)
    def modelSummary(self) -> str:
        """What the next request will use, e.g. "Sonnet - medium effort (default)"."""
        own = self._provider != "claude"
        model = self.settingFor(self._provider, "model") if own else self._model
        return self.summaryFor(self._provider, model, self._effort)

    @pyqtSlot(str, str, str, result=str)
    def summaryFor(self, provider: str, model: str, effort: str) -> str:
        """The same line for a choice that is not applied yet (the options page preview)."""
        if provider != "claude":
            info = next((p for p in PROVIDERS if p["key"] == provider), {"short": provider})
            return f"{info['short']}  -  {model or 'its default model'}"
        name = next((m["name"] for m in MODELS if m["key"] == model), model)
        default = DEFAULT_EFFORT.get(model)
        if effort != "default":
            return f"{name}  -  {effort} effort"
        if default:
            return f"{name}  -  {default} effort (default)"
        return f"{name}  -  effort from your Claude Code settings"

    @pyqtSlot(str, result=str)
    def effortHintFor(self, model: str) -> str:
        default = DEFAULT_EFFORT.get(model)
        known = f"The default for this AI model is {default}. " if default else ""
        return (
            "Higher effort lets the AI think longer: better decisions on hard goals, but "
            "slower answers and more of your subscription used. "
            + known
            + "Your own Claude Code settings can change the default."
        )

    @pyqtProperty(bool, notify=stateChanged)
    def autoApply(self) -> bool:
        return self._auto_apply

    @pyqtProperty("QVariantList", notify=stateChanged)
    def chat(self) -> list:
        return self._chat

    @pyqtProperty("QVariantList", notify=stateChanged)
    def proposal(self) -> list:
        return self._proposal

    @pyqtProperty("QVariantList", notify=stateChanged)
    def rejected(self) -> list:
        return self._rejected

    @pyqtProperty("QVariantList", notify=stateChanged)
    def ranking(self) -> list:
        return self._ranking

    @pyqtProperty(str, notify=stateChanged)
    def rotationText(self) -> str:
        return self._rotation_text

    @pyqtProperty("QVariantList", notify=stateChanged)
    def tiles(self) -> list:
        return self._tiles

    @pyqtProperty("QVariantList", notify=stateChanged)
    def rows(self) -> list:
        return self._rows

    @pyqtProperty(bool, notify=stateChanged)
    def hasSelectedProposal(self) -> bool:
        return any(p["selected"] for p in self._proposal)

    @pyqtProperty(bool, notify=stateChanged)
    def hasRotation(self) -> bool:
        return self._quaternion is not None

    @pyqtProperty(bool, notify=stateChanged)
    def canRevert(self) -> bool:
        return bool(self._last_applied)

    @pyqtProperty("QVariantList", notify=stateChanged)
    def partLines(self) -> list:
        return self._part_lines

    @pyqtProperty(bool, notify=stateChanged)
    def hasPartPlan(self) -> bool:
        return self._part_plan is not None

    # ---- slots ---------------------------------------------------------------------------------

    @pyqtSlot()
    def showDialog(self) -> None:
        try:
            if self._view is None:
                path = os.path.join(
                    str(PluginRegistry.getInstance().getPluginPath(PLUGIN_ID)),
                    "CuraAiAssistant.qml",
                )
                self._view = Application.getInstance().createQmlComponent(path, {"manager": self})
            if self._view is None:
                Logger.log("e", "AI Assistant: the dialog could not be created")
                return
            self._changed()
            self._view.show()
        except Exception:
            Logger.logException("e", "AI Assistant: opening the dialog failed")

    @pyqtSlot(str, str, str, bool, "QVariantMap")
    def saveOptions(
        self, provider: str, claude_model: str, effort: str, auto_apply: bool, fields: dict
    ) -> None:
        """Store the options page: provider, Claude model and effort, the provider's own fields."""
        preferences = Application.getInstance().getPreferences()
        if provider in [p["key"] for p in PROVIDERS]:
            self._provider = provider
            preferences.setValue(PROVIDER_PREFERENCE, provider)
        if claude_model in [m["key"] for m in MODELS]:
            self._model = claude_model
            preferences.setValue(MODEL_PREFERENCE, claude_model)
        if effort in [e["key"] for e in EFFORTS]:
            self._effort = effort
            preferences.setValue(EFFORT_PREFERENCE, effort)
        info = next(p for p in PROVIDERS if p["key"] == self._provider)
        names = info["fields"] if self._provider == "claude" else ["model", *info["fields"]]
        wanted = {name: str(fields.get(name, "")).strip() for name in names}
        self._provider_settings[self._provider] = wanted
        preferences.setValue(PROVIDER_SETTINGS_PREFERENCE, json.dumps(self._provider_settings))
        self._auto_apply = auto_apply
        preferences.setValue(AUTO_APPLY_PREFERENCE, auto_apply)
        Logger.log("i", f"AI Assistant: provider {self._provider}, auto apply {auto_apply}")
        self._changed()

    @pyqtSlot(str)
    def detectModels(self, host: str) -> None:
        """Ask a local Ollama server which models it has installed."""
        if self._busy:
            return
        request = {"action": "list_models", "provider": "ollama"}
        if host.strip():
            request["provider_options"] = {"host": host.strip()}
        threading.Thread(target=self._work, args=(request, self._onModels), daemon=True).start()

    def _onModels(self, result: dict) -> None:
        self._detected_models = result.get("models", [])
        if not self._detected_models:
            self._error = "The server answered, but no models are installed."

    @pyqtSlot(str)
    def setLoad(self, name: str) -> None:
        self._load = name

    @pyqtSlot(str)
    def setVisible(self, name: str) -> None:
        self._visible = name

    @pyqtSlot()
    def reset(self) -> None:
        if not self._busy:
            self._error = ""
            self._clear_conversation()
            self._changed()

    @pyqtSlot(int, bool)
    def toggle(self, index: int, selected: bool) -> None:
        if 0 <= index < len(self._proposal):
            self._proposal[index]["selected"] = selected
            self._changed()

    @pyqtSlot(str, str)
    def ask(self, action: str, text: str) -> None:
        """Ask in the one shared conversation; "auto" lets the assistant plan the work.

        With an empty `text` the goal already in the conversation is used.
        """
        text = text.strip()
        Logger.log("i", f"AI Assistant: ask {action}, {len(text)} chars, busy={self._busy}")
        if action not in (*ACTIONS, "auto") or self._busy:
            return
        if not text:
            if not self._history:
                self._error = "Describe what you want first."
                self._changed()
                return
            text = FOLLOW_UP[self._action if action == "auto" else action]
        answering = self._awaiting_answer and action == "auto"
        self._awaiting_answer = False
        self._turn_start = len(self._history)
        self._last_user = self._turn_start
        self._history.append({"role": "user", "content": text})
        self._chat.append({"role": "user", "text": text})
        self._hops = 0
        self._error = ""
        if action != "auto" or answering:
            if action != "auto":
                self._queue = []
            self._dispatch(self._action if answering else action)  # an answer stays with its asker
            return
        self._queue = []
        context = {"setup": cura_bridge.context_line(), "part": cura_bridge.selection_line()}
        self._start(
            {"action": "plan_turn", "history": self._history, "context": context}, self._onPlanTurn
        )

    def _onPlanTurn(self, result: dict) -> None:
        """The message is split into tasks; the first runs now, the others follow one by one."""
        tasks = result["tasks"]
        if result["announcement"] and len(tasks) > 1:
            self._chat.append({"role": "assistant", "text": result["announcement"]})
        self._queue = tasks[1:]
        self._run_task(tasks[0], len(tasks) > 1)

    def _run_task(self, task: dict, step: bool) -> None:
        if step:  # the helper sees its own instruction, not the wishes meant for the others
            self._last_user = len(self._history)
            self._history.append({"role": "user", "content": task["instruction"]})
        self._hops = 0
        self._dispatch(task["helper"])

    def _continue_queue(self) -> None:
        """After a helper's answer: start the next task, unless a question or a slice is open."""
        if self._queue and not self._busy and not self._awaiting_answer:
            self._run_task(self._queue.pop(0), True)

    def _discard_turn(self, keep_queue: bool = False) -> None:
        """Take back what a failed or cancelled step added; the later steps go too unless kept."""
        del self._history[self._last_user :]
        if self._last_user == self._turn_start and self._chat and self._chat[-1]["role"] == "user":
            self._chat.pop()
        if not keep_queue:
            self._queue = []

    def _dispatch(self, action: str) -> None:
        """Build the request for one helper from the conversation and start it."""
        try:
            request = self._request_for_turn(action)
        except (cura_bridge.BridgeError, service_client.ServiceError) as exc:
            self._discard_turn()
            self._error = str(exc)
            self._changed()
            return
        self._error = ""
        self._action = action
        handlers = {
            "settings": self._onSettingsTurn,
            "orientation": self._onOrientTurn,
            "parts": self._onPartTurn,
        }
        self._start(request, handlers[action])

    def _redirect(self, helper: str) -> None:
        """A helper says the message belongs to another one: hand it over, at most twice."""
        self._hops += 1
        if self._hops > 2:
            self._discard_turn()
            self._error = "The assistant could not decide who should answer. Please rephrase."
            return
        Logger.log("i", f"AI Assistant: redirected to {helper}")
        self._dispatch(helper)

    @pyqtSlot()
    def cancelRequest(self) -> None:
        """Stop what is running: the service call, or a slice Cura is doing for us."""
        if not self._busy:
            return
        if self._slice_waiting:
            self._slice_waiting = False
            cura_bridge.backend().backendStateChange.disconnect(self._relayBackendState)
            cura_bridge.backend().stopSlicing()
            self._busy, self._status = False, ""
            self._chat.append({"role": "note", "text": "Cancelled the slice."})
            self._changed()
            return
        self._status = "Cancelling..."
        self._changed()
        service_client.cancel()

    @pyqtSlot()
    def applySettings(self) -> None:
        picked = [p for p in self._proposal if p["selected"]]
        if not picked:
            return
        try:
            if self._proposal_scope == "part":
                records = cura_bridge.apply_object_overrides(picked)
            else:
                records = cura_bridge.apply_overrides(picked)
        except Exception as exc:
            Logger.logException("e", "AI Assistant: applying settings failed")
            self._error = f"Applying failed: {exc}"
            self._changed()
            return
        self._invalidate()
        self._last_applied = records
        lines = "\n".join(f"{p['label']}: {p['from']} -> {p['to']}" for p in picked)
        self._chat.append({"role": "note", "text": f"Applied {len(picked)} setting(s)\n{lines}"})
        self._proposal, self._rejected, self._tiles = [], [], []
        self._changed()

    @pyqtSlot()
    def revertSettings(self) -> None:
        if not self._last_applied:
            return
        try:
            cura_bridge.revert_overrides(self._last_applied)
        except Exception as exc:
            Logger.logException("e", "AI Assistant: reverting settings failed")
            self._error = f"Reverting failed: {exc}"
            self._changed()
            return
        self._invalidate()
        self._chat.append(
            {"role": "note", "text": f"Reverted {len(self._last_applied)} setting(s) to before."}
        )
        self._last_applied = []
        self._changed()

    @pyqtSlot()
    def checkSettings(self) -> None:
        overrides = {p["key"]: p["value"] for p in self._proposal if p["selected"]}
        if not overrides or self._busy:
            return
        if self._proposal_scope == "part":
            self._error = "Test slicing is only available for settings that change the whole print."
            self._changed()
            return
        try:
            request = {
                "action": "verify_settings",
                "stl": cura_bridge.export_model(service_client.load_config()["work_dir"]),
                "job": self._job_export(),
                "overrides": overrides,
            }
        except (cura_bridge.BridgeError, service_client.ServiceError) as exc:
            self._error = str(exc)
            self._changed()
            return
        self._start(request, self._onSettingsCheck)

    @pyqtSlot()
    def applyRotation(self) -> None:
        if self._quaternion is None:
            return
        try:
            cura_bridge.apply_rotation(self._quaternion)
        except Exception as exc:
            Logger.logException("e", "AI Assistant: rotating failed")
            self._error = f"Rotating failed: {exc}"
            self._changed()
            return
        self._chat.append({"role": "note", "text": "Rotated the part. Undo with Ctrl+Z."})
        self._quaternion = None
        self._ranking, self._rows = [], []
        self._changed()

    @pyqtSlot()
    def checkOrientations(self) -> None:
        if self._weights is None or self._busy:
            return
        try:
            request = {
                "action": "verify_orientations",
                "stl": cura_bridge.export_model(service_client.load_config()["work_dir"]),
                "job": self._job_export(),
                "weights": self._weights,
                "top_n": 3,
                "support_angle": cura_bridge.support_angle(),
                "load_direction": DIRECTIONS.get(self._load),
                "visible_direction": DIRECTIONS.get(self._visible),
            }
        except (cura_bridge.BridgeError, service_client.ServiceError) as exc:
            self._error = str(exc)
            self._changed()
            return
        self._start(request, self._onOrientCheck)

    # ---- service calls -------------------------------------------------------------------------

    def _request_for_turn(self, action: str) -> dict:
        work_dir = service_client.load_config()["work_dir"]
        if action == "settings":
            return {"action": "settings_turn", "job": self._job_export(), "history": self._history}
        if action == "parts":
            node, _ = cura_bridge.current_part()
            if node is None:  # setup and slicing need no part
                return {
                    "action": "part_turn",
                    "stl": None,
                    "other_parts": len(cura_bridge.scene_parts()),
                    **cura_bridge.plate_geometry(),
                    "adhesion": cura_bridge.adhesion(),
                    "inventory": cura_bridge.setup_inventory(),
                    "history": self._history,
                }
            others, other_count = cura_bridge.export_other_parts(work_dir, node)
            return {
                "action": "part_turn",
                "stl": cura_bridge.export_model(work_dir),
                "obstacles_stl": others,
                "other_parts": other_count,
                **cura_bridge.plate_geometry(),
                "adhesion": cura_bridge.adhesion(),
                "inventory": cura_bridge.setup_inventory(),
                "history": self._history,
            }
        return {
            "action": "orient_turn",
            "stl": cura_bridge.export_model(work_dir),
            "history": self._history,
            "support_angle": cura_bridge.support_angle(),
            "load_direction": DIRECTIONS.get(self._load),
            "visible_direction": DIRECTIONS.get(self._visible),
        }

    def _provider_fields(self) -> dict:
        """The provider part of a request: who answers, with which model and addresses."""
        if self._provider == "claude":
            return {"provider": "claude", "model": self._model, "effort": self._effort}
        stored = self._provider_settings.get(self._provider, {})
        model = self.settingFor(self._provider, "model") or None
        options = {k: v for k, v in stored.items() if k != "model" and v}
        return {"provider": self._provider, "model": model, "provider_options": options}

    def _start(self, request: dict, handler: Callable[[dict], None]) -> None:
        request.update(self._provider_fields())
        self._busy = True
        self._status = "Thinking..." if "turn" in handler.__name__.lower() else "Test slicing..."
        self._changed()
        threading.Thread(target=self._work, args=(request, handler), daemon=True).start()

    def _work(self, request: dict, handler: Callable[[dict], None]) -> None:
        try:
            result = service_client.call(request)
        except service_client.ServiceCancelled:
            result = {"cancelled": True}
        except service_client.ServiceError as exc:
            result = {"error": str(exc)}
        except Exception as exc:  # never let a worker thread die silently
            result = {"error": f"unexpected failure: {exc}"}
        self._finished.emit((handler, result))

    @pyqtSlot(object)
    def _onFinished(self, payload: tuple) -> None:
        handler, result = payload
        Logger.log("i", f"AI Assistant: answer {result.get('error') or result.get('kind') or 'ok'}")
        self._busy = False
        self._status = ""
        if "error" in result or result.get("cancelled"):
            if (
                self._history
                and self._history[-1]["role"] == "user"
                and handler
                in (
                    self._onSettingsTurn,
                    self._onOrientTurn,
                    self._onPartTurn,
                    self._onPlanTurn,
                )
            ):
                self._discard_turn(keep_queue=not result.get("cancelled") and bool(self._queue))
            if result.get("cancelled"):
                self._error = ""
                self._chat.append({"role": "note", "text": "Cancelled. Nothing was changed."})
            elif self._queue and handler != self._onPlanTurn:  # one step failed, the rest go on
                self._error = ""
                self._chat.append({"role": "note", "text": f"One step failed: {result['error']}"})
                self._changed()
                self._continue_queue()
                return
            else:
                self._error = result["error"]
        else:
            self._error = ""
            try:
                handler(result)
            except Exception as exc:
                Logger.logException("e", "AI Assistant: handling the answer failed")
                self._error = f"Could not show the answer: {exc}"
        self._changed()

    # ---- answers -------------------------------------------------------------------------------

    def _questions_text(self, result: dict) -> str:
        self._awaiting_answer = True
        return "\n".join(f"- {q['text']}" for q in result["questions"])

    def _onSettingsTurn(self, result: dict) -> None:
        if result["kind"] == "redirect":
            self._redirect(result["redirect_to"])
            return
        self._history.append({"role": "assistant", "content": result["turn"]})
        self._proposal, self._rejected, self._tiles = [], [], []
        if result["kind"] == "questions":
            self._chat.append({"role": "assistant", "text": self._questions_text(result)})
            return
        self._proposal_scope = result.get("scope", "print")
        text = result["summary"]
        if self._proposal_scope == "part":
            text += "\n(These settings are for the selected part only.)"
        if not result["accepted"]:
            text += "\nNone of the suggested values could be used with this printer."
        self._chat.append({"role": "assistant", "text": text})
        for a in result["accepted"]:
            warnings = list(a["warnings"])
            if a["user_set"]:
                warnings.insert(0, "You set this yourself - left unticked")
            self._proposal.append(
                {
                    **a,
                    "selected": not a["user_set"],
                    "from": _with_unit(a["previous"], a["unit"]),
                    "to": _with_unit(a["value"], a["unit"]),
                    "warn": "; ".join(warnings),
                }
            )
        self._rejected = [f"{r['key']} = {r['value']}: {r['message']}" for r in result["rejected"]]
        if self._auto_apply and self._proposal:
            self.applySettings()
        self._continue_queue()

    def _onSettingsCheck(self, result: dict) -> None:
        cur, new = result["current"], result["proposed"]
        self._tiles = [
            {
                "name": "Print time",
                "before": _duration(cur["print_time_s"]),
                "after": _duration(new["print_time_s"]),
                "delta": _delta(new["print_time_s"], cur["print_time_s"]),
                "tone": _tone(new["print_time_s"], cur["print_time_s"]),
            },
            {
                "name": "Material",
                "before": f"{cur['filament_mm3'] / 1000:.1f} cm3",
                "after": f"{new['filament_mm3'] / 1000:.1f} cm3",
                "delta": _delta(new["filament_mm3"], cur["filament_mm3"]),
                "tone": _tone(new["filament_mm3"], cur["filament_mm3"]),
            },
            {
                "name": "Support",
                "before": f"{cur['support_mm3'] / 1000:.1f} cm3",
                "after": f"{new['support_mm3'] / 1000:.1f} cm3",
                "delta": _delta(new["support_mm3"], cur["support_mm3"]),
                "tone": _tone(new["support_mm3"], cur["support_mm3"]),
            },
        ]

    def _onOrientTurn(self, result: dict) -> None:
        if result["kind"] == "redirect":
            self._redirect(result["redirect_to"])
            return
        self._history.append({"role": "assistant", "content": result["turn"]})
        self._ranking, self._rows, self._quaternion = [], [], None
        if result["kind"] == "questions":
            self._chat.append({"role": "assistant", "text": self._questions_text(result)})
            return
        chosen = result["chosen"]
        self._chat.append({"role": "assistant", "text": result["reasoning"]})
        self._weights = result["weights"]
        self._quaternion = chosen["rotation_quaternion_scene"]
        angle = chosen["rotation_axis_angle"]["angle_deg"]
        self._rotation_text = (
            "Keep the current orientation" if angle < 0.5 else f"Rotate by {angle:.0f} degrees"
        )
        for s in result["ranking"]:
            m = s["metrics"]
            support = (
                f"~{m['support_mm3'] / 1000:.1f} cm3 support"
                if m["support_mm3"] >= 50
                else "no support"
            )
            self._ranking.append(
                {
                    "id": s["id"],
                    "rank": s["rank"],
                    "chosen": s["id"] == chosen["id"],
                    "score": s["total"],
                    "title": f"{m['height_mm']:.0f} mm high",
                    "detail": f"{m['bed_contact_mm2']:.0f} mm2 on the bed, {support}",
                }
            )
        if self._auto_apply:
            self.applyRotation()
        self._continue_queue()

    def _onPartTurn(self, result: dict) -> None:
        if result["kind"] == "redirect":
            self._redirect(result["redirect_to"])
            return
        self._history.append({"role": "assistant", "content": result["turn"]})
        self._part_plan, self._part_lines = None, []
        if result["kind"] == "questions":
            self._chat.append({"role": "assistant", "text": self._questions_text(result)})
            return
        plan = result["plan"]
        self._chat.append({"role": "assistant", "text": result["summary"]})
        self._part_lines += plan["steps"]
        if plan["placements"]:
            wanted = (
                f" (asked for {plan['requested']})" if plan["requested"] else " (as many as fit)"
            )
            turned = ", some turned" if plan["rotations_used"] else ""
            self._part_lines.append(f"{plan['count']} parts on the plate{wanted}{turned}")
        self._part_lines += [f"! {note}" for note in plan["notes"]]
        self._part_plan = plan
        if self._auto_apply and not plan["notes"]:
            self.applyPartPlan()
        self._continue_queue()

    @pyqtSlot()
    def applyPartPlan(self) -> None:
        if self._part_plan is None:
            return
        try:
            count = cura_bridge.apply_part_plan(self._part_plan)
        except Exception as exc:
            Logger.logException("e", "AI Assistant: changing the part failed")
            self._error = f"Changing the part failed: {exc}"
            self._changed()
            return
        plan = self._part_plan
        moves_geometry = (
            plan["reshaped"]
            or plan["placements"]
            or plan["move"]
            or plan["reset_rotation"]
            or plan["reset_scale"]
            or max(abs(s - 1) for s in plan["scale_xyz"]) > 1e-6
        )
        summary = ", ".join(line for line in self._part_lines if not line.startswith("!"))
        text = f"Done: {summary or 'nothing to do'}."
        undo_hint = " Undo with Ctrl+Z." if moves_geometry else ""
        self._chat.append({"role": "note", "text": text + undo_hint})
        wants_slice, wants_export = self._part_plan["slice_now"], self._part_plan["export_gcode"]
        self._part_plan, self._part_lines = None, []
        self._ranking, self._rows, self._quaternion = [], [], None  # the geometry changed
        self._changed()
        Logger.log("i", f"AI Assistant: part plan applied, {count} part(s) on the plate")
        if wants_slice:
            self._begin_slice(wants_export)

    def _begin_slice(self, export: bool) -> None:
        """Slice now; the answer comes through the backend's state signal."""
        self._slice_export, self._slice_waiting = export, True
        self._busy, self._status = True, "Slicing..."
        backend = cura_bridge.backend()
        backend.backendStateChange.connect(self._relayBackendState)
        self._changed()
        cura_bridge.start_slice()

    def _relayBackendState(self, state) -> None:
        self._backendState.emit(state)

    @pyqtSlot(object)
    def _onBackendState(self, state) -> None:
        if not self._slice_waiting or state not in (BackendState.Done, BackendState.Error):
            return
        self._slice_waiting = False
        cura_bridge.backend().backendStateChange.disconnect(self._relayBackendState)
        self._busy, self._status = False, ""
        if state == BackendState.Error:
            self._error = "Cura could not slice the part (see its own slice message)."
        else:
            try:
                text = f"Sliced: {cura_bridge.print_summary()}."
                if self._slice_export:
                    folder = service_client.load_config().get("export_dir") or os.path.join(
                        os.path.expanduser("~"), "Documents", "CuraAi"
                    )
                    text += f"\nG-code saved to {cura_bridge.export_gcode(folder)}"
                self._chat.append({"role": "note", "text": text})
            except Exception as exc:
                Logger.logException("e", "AI Assistant: reading the slice result failed")
                self._error = f"Could not read the slice result: {exc}"
        self._changed()
        self._continue_queue()

    def _onOrientCheck(self, result: dict) -> None:
        self._rows = []
        for row in result["rows"]:
            v = row["verified"]
            if v["error"]:
                self._rows.append({"id": v["id"], "text": f"slicing failed: {v['error']}"})
                continue
            r = v["result"]
            self._rows.append(
                {
                    "id": v["id"],
                    "text": f"{_duration(r['print_time_s'])}  |  "
                    f"{r['filament_mm3'] / 1000:.1f} cm3 material  |  "
                    f"{r['support_mm3'] / 1000:.1f} cm3 support",
                }
            )
