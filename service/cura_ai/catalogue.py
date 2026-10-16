"""Catalogue of Cura settings: what exists, its type, limits and current value.

Built either from Cura's definition files (fdmprinter + printer + extruder) or from the
export the Cura plugin writes, where Cura has already evaluated every formula.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cura_ai.formula import Evaluator, Unevaluable

BOUND_ATTRS = ("minimum_value", "maximum_value", "minimum_value_warning", "maximum_value_warning")


class DefinitionNotFound(Exception):
    pass


@dataclass
class SettingSpec:
    key: str
    label: str = ""
    description: str = ""
    type: str = "str"
    unit: str = ""
    category: str = ""
    default_value: Any = None
    value_expr: Any = None  # formula (str) or literal that wins over default_value
    enabled: Any = True
    options: dict[str, str] | None = None
    bounds: dict[str, Any] = field(default_factory=dict)


def find_cura_resources() -> Path:
    """Cura's resources dir: $CURA_AI_CURA_RESOURCES or the newest Program Files install."""
    env = os.environ.get("CURA_AI_CURA_RESOURCES")
    if env and Path(env).is_dir():
        return Path(env)
    found = sorted(glob.glob(r"C:\Program Files\UltiMaker Cura *\share\cura\resources"))
    if not found:
        raise DefinitionNotFound("Cura resources not found; set CURA_AI_CURA_RESOURCES")
    return Path(found[-1])


def _locate(def_id: str, dirs: list[Path]) -> Path:
    for base in dirs:
        for sub in ("definitions", "extruders", ""):
            candidate = base / sub / f"{def_id}.def.json"
            if candidate.is_file():
                return candidate
    raise DefinitionNotFound(f"definition '{def_id}' not found in {[str(d) for d in dirs]}")


def _walk(settings: dict, category: str, out: dict[str, dict]) -> None:
    for key, node in settings.items():
        if node.get("type") == "category":
            _walk(node.get("children", {}), key, out)
            continue
        attrs = {k: v for k, v in node.items() if k != "children"}
        attrs["_category"] = category
        out[key] = {**out.get(key, {}), **attrs}
        if "children" in node:
            _walk(node["children"], category, out)


def _load_chain(def_id: str, dirs: list[Path], out: dict[str, dict]) -> dict:
    """Load a definition and everything it inherits; later definitions override earlier ones."""
    path = _locate(def_id, dirs)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("inherits"):
        _load_chain(data["inherits"], dirs, out)
    _walk(data.get("settings", {}), "", out)
    for key, attrs in data.get("overrides", {}).items():
        if key in out:
            out[key] = {**out[key], **attrs}
    return data


def _spec(key: str, a: dict) -> SettingSpec:
    return SettingSpec(
        key=key,
        label=a.get("label", key),
        description=a.get("description", ""),
        type=a.get("type", "str"),
        unit=a.get("unit", ""),
        category=a.get("_category", ""),
        default_value=a.get("default_value"),
        value_expr=a.get("value"),
        enabled=a.get("enabled", True),
        options=a.get("options"),
        bounds={b: a[b] for b in BOUND_ATTRS if b in a},
    )


class Catalogue:
    """Settings by key, with value and limit evaluation against a set of current values."""

    def __init__(self, specs: dict[str, SettingSpec], current: dict[str, Any] | None = None):
        self.specs = specs
        self.current = dict(current or {})
        self._memo: dict[str, Any] = {}
        self._active: set[str] = set()

    @classmethod
    def from_definitions(
        cls, printer_id: str, extruder_id: str | None = None, extra_dirs: list[Path] | None = None
    ) -> Catalogue:
        dirs = [*(extra_dirs or []), find_cura_resources()]
        raw: dict[str, dict] = {}
        printer = _load_chain(printer_id, dirs, raw)
        extruder_id = extruder_id or printer.get("metadata", {}).get(
            "machine_extruder_trains", {}
        ).get("0")
        if extruder_id:
            _load_chain(extruder_id, dirs, raw)
        return cls({k: _spec(k, v) for k, v in raw.items()})

    @classmethod
    def from_export(cls, settings: dict[str, dict]) -> Catalogue:
        """Build from the plugin export: every entry already holds Cura's evaluated value."""
        specs: dict[str, SettingSpec] = {}
        current: dict[str, Any] = {}
        for key, e in settings.items():
            specs[key] = SettingSpec(
                key=key,
                label=e.get("label", key),
                description=e.get("description", ""),
                type=e.get("type", "str"),
                unit=e.get("unit", ""),
                category=e.get("category", ""),
                enabled=e.get("enabled", True),
                options=e.get("options"),
                bounds={b: e[b] for b in BOUND_ATTRS if e.get(b) is not None},
            )
            current[key] = e.get("value")
        return cls(specs, current)

    def with_current(self, current: dict[str, Any]) -> Catalogue:
        return Catalogue(self.specs, {**self.current, **current})

    def value(self, key: str) -> Any:
        """Current value: explicit current, else formula, else definition default."""
        if key in self.current:
            return self.current[key]
        if key in self._memo:
            return self._memo[key]
        spec = self.specs.get(key)
        if spec is None:
            raise KeyError(key)
        if key in self._active:
            raise KeyError(f"cycle at {key}")
        self._active.add(key)
        try:
            result = spec.default_value
            expr = spec.value_expr
            if isinstance(expr, str):
                with contextlib.suppress(Unevaluable):
                    result = Evaluator(self.value).evaluate(expr)
            elif expr is not None:
                result = expr
        finally:
            self._active.discard(key)
        self._memo[key] = result
        return result

    def all_values(self) -> dict[str, Any]:
        """Every setting's current value; settings that cannot be resolved are left out."""
        values: dict[str, Any] = {}
        for key in self.specs:
            with contextlib.suppress(KeyError):
                values[key] = self.value(key)
        return values

    def evaluate(self, expr: Any) -> Any:
        """Evaluate a limit or `enabled` expression; None if it cannot be evaluated."""
        if not isinstance(expr, str):
            return expr
        try:
            return float(expr)
        except ValueError:
            pass
        try:
            return Evaluator(self.value).evaluate(expr)
        except Unevaluable:
            return None

    def bound(self, key: str, attr: str) -> float | None:
        raw = self.specs[key].bounds.get(attr)
        if raw is None:
            return None
        v = self.evaluate(raw)
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

    def is_enabled(self, key: str) -> bool | None:
        v = self.evaluate(self.specs[key].enabled)
        return bool(v) if v is not None else None

    def describe(self, keys: list[str]) -> list[dict[str, Any]]:
        """Compact facts about settings for the model's prompt."""
        rows = []
        for key in keys:
            spec = self.specs.get(key)
            if spec is None:
                continue
            row: dict[str, Any] = {"key": key, "label": spec.label, "type": spec.type}
            with contextlib.suppress(KeyError):
                row["current"] = self.value(key)
            if spec.unit:
                row["unit"] = spec.unit
            if spec.options:
                row["options"] = list(spec.options)
            for attr in BOUND_ATTRS:
                b = self.bound(key, attr)
                if b is not None:
                    row[attr] = b
            rows.append(row)
        return rows
