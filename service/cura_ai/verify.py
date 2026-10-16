"""Test slices that put numbers behind a proposal: orientations and setting changes."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel

from cura_ai.engine import EngineError, SliceResult, slice_model
from cura_ai.mesh import rotated_on_bed, write_stl
from cura_ai.orientation import DOWN, ScoredCandidate, rotation_between
from cura_ai.resolution import naive_merge

Slicer = Callable[..., SliceResult]
Resolver = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
SUPPORT_FOR_COMPARISON = {"support_enable": True, "support_type": "everywhere"}


class VerifiedCandidate(BaseModel):
    id: str
    result: SliceResult | None = None
    error: str | None = None


class SettingsComparison(BaseModel):
    current: SliceResult
    proposed: SliceResult
    time_change_pct: float
    material_change_pct: float


def _pct(new: float, old: float) -> float:
    return 0.0 if old == 0 else (new - old) / old * 100


def verify_orientations(
    tris: np.ndarray,
    candidates: list[ScoredCandidate],
    values: dict[str, Any],
    printer_id: str,
    extruder_id: str | None,
    slicer: Slicer = slice_model,
    workers: int = 2,
    resolve: Resolver = naive_merge,
) -> list[VerifiedCandidate]:
    """Slice each candidate with support forced on, so support volume is comparable."""
    settings = resolve(values, SUPPORT_FOR_COMPARISON)

    def run(cand: ScoredCandidate, tmp: Path) -> VerifiedCandidate:
        rot = rotation_between(np.array(cand.down), DOWN)
        stl = tmp / f"{cand.id}.stl"
        write_stl(stl, rotated_on_bed(tris, rot))
        try:
            return VerifiedCandidate(
                id=cand.id, result=slicer(stl, settings, printer_id, extruder_id)
            )
        except EngineError as exc:
            return VerifiedCandidate(id=cand.id, error=str(exc))

    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda c: run(c, Path(tmp)), candidates))


def verify_settings(
    tris: np.ndarray,
    values: dict[str, Any],
    overrides: dict[str, Any],
    printer_id: str,
    extruder_id: str | None,
    slicer: Slicer = slice_model,
    resolve: Resolver = naive_merge,
) -> SettingsComparison:
    """Slice the model with the current settings and with the overrides applied."""
    with tempfile.TemporaryDirectory() as tmp:
        stl = Path(tmp) / "model.stl"
        write_stl(stl, tris)
        with ThreadPoolExecutor(2) as pool:
            now = pool.submit(slicer, stl, values, printer_id, extruder_id)
            then = pool.submit(slicer, stl, resolve(values, overrides), printer_id, extruder_id)
            current, proposed = now.result(), then.result()
    return SettingsComparison(
        current=current,
        proposed=proposed,
        time_change_pct=_pct(proposed.print_time_s, current.print_time_s),
        material_change_pct=_pct(proposed.filament_mm3, current.filament_mm3),
    )
