"""Headless CuraEngine runs: slice a model with resolved settings and read the numbers."""

from __future__ import annotations

import glob
import json
import math
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from cura_ai.catalogue import find_cura_resources


class EngineError(Exception):
    pass


class SliceResult(BaseModel):
    print_time_s: int
    filament_mm3: float
    support_mm3: float
    layers: int
    gcode: str | None = None


def find_engine() -> Path:
    env = os.environ.get("CURA_AI_ENGINE")
    if env and Path(env).is_file():
        return Path(env)
    found = sorted(glob.glob(r"C:\Program Files\UltiMaker Cura *\CuraEngine.exe"))
    if not found:
        raise EngineError("CuraEngine.exe not found; set CURA_AI_ENGINE")
    return Path(found[-1])


def write_overrides(path: Path, values: dict[str, Any]) -> None:
    """A definition file that pins every given setting, so no formula needs evaluating."""
    overrides = {k: {"default_value": v} for k, v in values.items() if v is not None}
    path.write_text(json.dumps({"version": 2, "name": "resolved", "overrides": overrides}))


@dataclass
class GcodeStats:
    extruded_mm3: dict[str, float]
    layers: int
    time_s: int | None


_E = re.compile(r"\bE(-?\d+\.?\d*)")


def gcode_stats(gcode: str, filament_diameter: float) -> GcodeStats:
    """Filament volume per feature type (`;TYPE:`), with retractions netted out."""
    area = math.pi * (filament_diameter / 2) ** 2
    by_type: dict[str, float] = {}
    current = "NONE"
    e_pos = 0.0
    retracted = 0.0
    relative = False
    layers = 0
    time_s: int | None = None
    for line in gcode.splitlines():
        if line.startswith(";TYPE:"):
            current = line[6:].strip()
            continue
        if line.startswith(";LAYER_COUNT:"):
            layers = int(line.split(":")[1])
            continue
        if line.startswith(";TIME:"):
            time_s = int(float(line.split(":")[1]))
            continue
        if line.startswith("M82"):
            relative = False
            continue
        if line.startswith("M83"):
            relative = True
            continue
        if line.startswith("G92"):
            m = _E.search(line)
            if m:
                e_pos = float(m.group(1))
            continue
        if not line.startswith(("G1", "G0")):
            continue
        m = _E.search(line.split(";")[0])
        if not m:
            continue
        e = float(m.group(1))
        delta = e if relative else e - e_pos
        e_pos = e_pos + delta if relative else e
        if delta < 0:
            retracted += -delta
            continue
        used = max(0.0, delta - retracted)
        retracted = max(0.0, retracted - delta)
        by_type[current] = by_type.get(current, 0.0) + used * area
    return GcodeStats(by_type, layers, time_s)


def slice_model(
    stl: Path,
    values: dict[str, Any],
    printer_id: str,
    extruder_id: str | None,
    workdir: Path | None = None,
    keep_gcode: bool = False,
    timeout: float = 600.0,
) -> SliceResult:
    """Slice `stl` (printer coordinates, Z up) with the resolved `values` and report the numbers."""
    resources = find_cura_resources()
    defs, extr = resources / "definitions", resources / "extruders"
    engine = find_engine()
    with tempfile.TemporaryDirectory() as tmp:
        work = workdir or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        overrides = work / "resolved.def.json"
        write_overrides(overrides, {**values, "center_object": True, "mesh_position_z": 0})
        gcode = work / (stl.stem + ".gcode")
        args = [str(engine), "slice", "-j", str(defs / "fdmprinter.def.json")]
        args += ["-j", str(_find(printer_id, defs, extr)), "-j", str(overrides)]
        args += ["-e0"]
        if extruder_id:
            args += ["-j", str(_find(extruder_id, defs, extr))]
        args += ["-j", str(overrides), "-e0", "-l", str(stl), "-o", str(gcode)]
        env = {**os.environ, "CURA_ENGINE_SEARCH_PATH": f"{defs};{extr}"}
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=env, timeout=timeout,
            )  # fmt: skip
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"CuraEngine timed out after {timeout:.0f}s") from exc
        log = proc.stdout + proc.stderr
        if proc.returncode != 0 or not gcode.exists() or gcode.stat().st_size == 0:
            errors = [
                ln for ln in log.splitlines() if "[error]" in ln and "definition file" not in ln
            ]
            raise EngineError(
                "CuraEngine failed: " + (errors[-1] if errors else f"exit {proc.returncode}")
            )
        text = gcode.read_text(encoding="utf-8", errors="replace")
        diameter = float(values.get("material_diameter") or 1.75)
        stats = gcode_stats(text, diameter)
        time_match = re.search(r"Print time \(s\): (\d+)", log)
        fil_match = re.search(r"Filament \(mm\^3\): (\d+)", log)
        total = sum(stats.extruded_mm3.values())
        support = sum(v for k, v in stats.extruded_mm3.items() if k.startswith("SUPPORT"))
        return SliceResult(
            print_time_s=int(time_match.group(1)) if time_match else (stats.time_s or 0),
            filament_mm3=float(fil_match.group(1)) if fil_match else total,
            support_mm3=support,
            layers=stats.layers,
            gcode=text if keep_gcode else None,
        )


def _find(def_id: str, *dirs: Path) -> Path:
    for d in dirs:
        p = d / f"{def_id}.def.json"
        if p.is_file():
            return p
    raise EngineError(f"definition '{def_id}' not found")
