"""Validation of proposed overrides against the catalogue. Invalid ones are dropped, listed."""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from cura_ai.catalogue import Catalogue
from cura_ai.schemas import Override

SCALAR = bool | int | float | str
SUPPORTED_TYPES = {"float", "int", "bool", "enum", "extruder", "optional_extruder"}
BLOCKED_CATEGORIES = {"machine_settings", "command_line_settings"}
BLOCKED_PREFIXES = ("machine_",)


class AcceptedOverride(BaseModel):
    key: str
    label: str
    unit: str
    value: SCALAR
    previous: SCALAR | None
    reason: str
    warnings: list[str]
    user_set: bool = False  # the user chose the current value themselves


class RejectedOverride(BaseModel):
    key: str
    value: str
    code: str
    message: str


class ValidationReport(BaseModel):
    accepted: list[AcceptedOverride]
    rejected: list[RejectedOverride]


class _Reject(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _parse(text: str, type_: str, options: dict[str, str] | None) -> SCALAR:
    t = text.strip()
    if type_ == "bool":
        if t.lower() in ("true", "false"):
            return t.lower() == "true"
        raise _Reject("bad_value", f"expected true or false, got {text!r}")
    if type_ == "enum":
        if options and t in options:
            return t
        raise _Reject("bad_value", f"expected one of {list(options or [])}, got {text!r}")
    try:
        number = float(t)
    except ValueError:
        raise _Reject("bad_value", f"expected a plain number, got {text!r}") from None
    if not math.isfinite(number):
        raise _Reject("bad_value", f"{text!r} is not a finite number")
    if type_ == "float":
        return number
    if not number.is_integer():
        raise _Reject("bad_value", f"expected a whole number, got {text!r}")
    return int(number)


def same_value(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def _check_one(catalogue: Catalogue, o: Override) -> AcceptedOverride:
    spec = catalogue.specs.get(o.key)
    if spec is None:
        raise _Reject("unknown_key", "no such Cura setting")
    if (
        spec.category in BLOCKED_CATEGORIES
        or o.key.startswith(BLOCKED_PREFIXES)
        or o.key == "extruder_nr"
    ):
        raise _Reject("not_allowed", "machine hardware settings are never changed by the assistant")
    if spec.type not in SUPPORTED_TYPES:
        raise _Reject("unsupported_type", f"settings of type '{spec.type}' are not supported")
    value = _parse(o.value, spec.type, spec.options)
    warnings: list[str] = []
    if spec.type in ("float", "int", "extruder", "optional_extruder"):
        lo, hi = catalogue.bound(o.key, "minimum_value"), catalogue.bound(o.key, "maximum_value")
        if lo is not None and value < lo:
            raise _Reject("out_of_range", f"{value} is below the minimum {lo:g}")
        if hi is not None and value > hi:
            raise _Reject("out_of_range", f"{value} is above the maximum {hi:g}")
        wlo = catalogue.bound(o.key, "minimum_value_warning")
        whi = catalogue.bound(o.key, "maximum_value_warning")
        if wlo is not None and value < wlo:
            warnings.append(f"below the recommended minimum {wlo:g}")
        if whi is not None and value > whi:
            warnings.append(f"above the recommended maximum {whi:g}")
    try:
        previous: SCALAR | None = catalogue.value(o.key)
    except KeyError:
        previous = None
    if previous is not None and same_value(previous, value):
        raise _Reject("no_change", "already has this value")
    if catalogue.is_enabled(o.key) is False:
        warnings.append("currently disabled by other settings; it only takes effect once enabled")
    return AcceptedOverride(
        key=o.key,
        label=spec.label,
        unit=spec.unit,
        value=value,
        previous=previous if isinstance(previous, bool | int | float | str) else None,
        reason=o.reason,
        warnings=warnings,
    )


def validate_overrides(catalogue: Catalogue, overrides: list[Override]) -> ValidationReport:
    accepted: list[AcceptedOverride] = []
    rejected: list[RejectedOverride] = []
    for o in overrides:
        try:
            accepted.append(_check_one(catalogue, o))
        except _Reject as r:
            rejected.append(RejectedOverride(key=o.key, value=o.value, code=r.code, message=str(r)))
    return ValidationReport(accepted=accepted, rejected=rejected)
