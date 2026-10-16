"""Apply overrides to a resolved settings set without leaving derived values stale."""

from __future__ import annotations

from typing import Any

from cura_ai.catalogue import Catalogue
from cura_ai.validation import same_value


def naive_merge(values: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    return {**values, **overrides}


def resolve_with_overrides(
    definitions: Catalogue, values: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """`values` plus `overrides`, with every formula-derived setting recomputed.

    A setting whose value in `values` differs from what its formula yields was set explicitly
    (by the profile or the user) and is kept; the others follow their inputs, like in Cura.
    """
    formula_keys = {k for k, s in definitions.specs.items() if isinstance(s.value_expr, str)}
    inputs = {k: v for k, v in values.items() if k in definitions.specs and k not in formula_keys}
    baseline = Catalogue(definitions.specs, inputs)
    explicit: dict[str, Any] = {}
    for key in formula_keys & values.keys():
        try:
            derived = baseline.value(key)
        except KeyError:
            derived = None
        if derived is None or not same_value(derived, values[key]):
            explicit[key] = values[key]
    updated = Catalogue(definitions.specs, {**inputs, **explicit, **overrides})
    result = dict(values)
    for key in formula_keys - explicit.keys() - overrides.keys():
        try:
            value = updated.value(key)
        except KeyError:
            continue
        if value is not None:
            result[key] = value
    result.update(overrides)
    return result
