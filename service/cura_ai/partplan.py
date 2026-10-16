"""From a validated part-operations turn to numbers: the shape matrix, size, placements."""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from cura_ai.packing import Placement, Plate, pack_copies
from cura_ai.schemas import PartTurn
from cura_ai.transform import shape_matrix


class PartPlan(BaseModel):
    linear: list[list[float]]  # 3x3, printer axes: lay flat, mirror, scale, turn in one matrix
    scale_xyz: list[float]  # the scale part alone, for display
    size_mm: list[float]  # x, y, z after the change (unchanged for resets)
    count: int  # parts that end up on the plate (original included), 1 if only changed
    requested: int | None  # copies asked for; None = as many as fit
    placements: list[Placement]  # one per part when copies are involved, the original first
    move: list[float] | None  # x, y of the part centre after a move
    notes: list[str]
    steps: list[str]  # one human-readable line per operation, for the confirmation card
    rotations_used: bool
    reshaped: bool  # turned, mirrored or laid flat (not only scaled)
    reset_rotation: bool = False
    reset_scale: bool = False
    lock: bool | None = None
    auto_drop: bool | None = None
    mesh_type: str | None = None
    slice_now: bool = False
    export_gcode: bool = False
    setup: dict | None = None  # ids to switch to: printer, material, quality; discard flag


MESH_TYPE_TEXT = {
    "normal": "Print the part normally",
    "support_blocker": "Use the part as a support blocker (no support inside it)",
    "support": "Use the part as a support structure",
    "infill": "Use the part as an infill region (own infill, no walls or skin)",
    "cutting": "Use the part to cut away from the others",
}


def _move_target(turn: PartTurn, tris: np.ndarray) -> list[float] | None:
    if turn.move_mode is None:
        return None
    pts = tris.reshape(-1, 3)
    cx, cy = (pts[:, 0].min() + pts[:, 0].max()) / 2, (pts[:, 1].min() + pts[:, 1].max()) / 2
    dx, dy = turn.move_x_mm, turn.move_y_mm
    # The user's Y is the one in Cura's move panel, which is the printer's Y mirrored.
    if turn.move_mode == "to":
        return [cx if dx is None else dx, cy if dy is None else -dy]
    return [cx + (dx or 0.0), cy - (dy or 0.0)]


def _steps(turn: PartTurn, scales: np.ndarray, size: list[float], move) -> list[str]:
    """The operations of a turn as short lines; the size is shown with the shape change."""
    shape: list[str] = []
    if turn.lay_flat:
        shape.append("Lay the largest flat face on the plate")
    if turn.mirror_axes:
        shape.append("Mirror across " + ", ".join(a.upper() for a in turn.mirror_axes))
    if max(abs(scales - 1)) > 1e-9:
        if np.ptp(scales) < 1e-9:
            shape.append(f"Scale {scales[0] * 100:g} %")
        else:
            shape.append("Scale X, Y, Z by " + ", ".join(f"{s * 100:g} %" for s in scales))
    for axis, degrees in (
        ("X", turn.rotate_x_deg),
        ("Y", turn.rotate_y_deg),
        ("Z", turn.rotate_z_deg),
    ):
        if degrees is not None:
            shape.append(f"Turn {degrees:g} degrees about {axis}")
    lines = []
    if shape:
        lines.append(f"{', '.join(shape)}  ->  {size[0]:g} x {size[1]:g} x {size[2]:g} mm")
    if move:
        lines.append(f"Move the part centre to x {move[0]:g}, y {-move[1]:g} mm")
    if turn.reset_rotation:
        lines.append("Undo all turning")
    if turn.reset_scale:
        lines.append("Back to 100 % size")
    if turn.lock is not None:
        lines.append("Lock the part" if turn.lock else "Unlock the part")
    if turn.auto_drop is not None:
        lines.append("Always sink to the plate" if turn.auto_drop else "Do not sink to the plate")
    if turn.mesh_type is not None:
        lines.append(MESH_TYPE_TEXT[turn.mesh_type])
    if turn.export_gcode:
        lines.append("Slice and save the G-code to a file")
    elif turn.slice_now:
        lines.append("Slice and report the print time and material")
    return lines


_NO_PART = np.zeros((1, 3, 3))  # stands in while no part is loaded; no part operation may use it


def _pick(kind: str, wanted: str | None, options: list[dict]) -> dict | None:
    """The inventory entry whose name matches (case-insensitive); an unknown name is an error."""
    if wanted is None:
        return None
    for option in options:
        if option["name"].casefold() == wanted.casefold():
            return option
    names = ", ".join(o["name"] for o in options[:30]) or "none available"
    raise ValueError(f"unknown {kind} '{wanted}'; available: {names}")


def resolve_setup(turn: PartTurn, inventory: dict | None) -> tuple[dict | None, list[str]]:
    """Ids for the printer, material and quality the turn names, plus one step line each."""
    if not (turn.printer or turn.material or turn.quality or turn.discard_changes):
        return None, []
    inventory = inventory or {}
    printer = _pick("printer", turn.printer, inventory.get("printers", []))
    material = _pick("material", turn.material, inventory.get("materials", []))
    quality = _pick("quality", turn.quality, inventory.get("qualities", []))
    steps = []
    for label, entry in (("printer", printer), ("material", material), ("quality", quality)):
        if entry:
            steps.append(f"Switch the {label} to {entry['name']}")
    if turn.discard_changes:
        steps.append("Discard all your changes to the settings (cannot be undone)")
    setup = {
        "printer": printer and printer["id"],
        "material": material and material["id"],
        "quality": quality and quality["id"],
        "discard": turn.discard_changes,
    }
    return setup, steps


def resolve_part_plan(
    turn: PartTurn,
    tris: np.ndarray | None,
    plate: Plate,
    margin: float = 0.0,
    build_height: float | None = None,
    obstacles_xy: np.ndarray | None = None,
    inventory: dict | None = None,
) -> PartPlan:
    """The matrix and the placements the plan leads to, with notes about what does not fit."""
    if tris is None:
        tris = _NO_PART
        if turn.needs_part():
            raise ValueError(
                "this needs exactly one part on the plate (load one, or select the one you mean)"
            )
    matrix, scales = shape_matrix(turn, tris)
    shaped = tris @ matrix.T
    size = np.ptp(shaped.reshape(-1, 3), axis=0)
    new_size = [round(float(v), 2) for v in size]
    original_size = np.ptp(tris.reshape(-1, 3), axis=0)
    resets = turn.reset_rotation or turn.reset_scale
    if resets:
        new_size = [round(float(v), 2) for v in original_size]
    notes: list[str] = []
    xmin, ymin, xmax, ymax = plate.rect
    if build_height is not None and new_size[2] > build_height:
        notes.append(f"{new_size[2]:g} mm is taller than the build height of {build_height:g} mm")
    wants_copies = turn.fill_plate or (turn.copies is not None and turn.copies > 1)
    placements: list[Placement] = []
    requested = None if turn.fill_plate else turn.copies
    rotations = (0, 90, 180, 270, 45, 135, 225, 315) if turn.allow_rotation else (0,)
    move = _move_target(turn, tris)
    setup, setup_steps = resolve_setup(turn, inventory)
    if wants_copies:
        result = pack_copies(
            shaped,
            plate,
            requested,
            spacing=turn.spacing_mm,
            margin=margin,
            rotations=rotations,
            obstacles_xy=obstacles_xy,
        )
        placements = result.placements
        if requested is not None and len(placements) < requested:
            notes.append(f"only {len(placements)} of {requested} copies fit on the plate")
        if not placements:
            notes.append("not even one copy fits on the free plate at this size")
    elif new_size[0] > xmax - xmin or new_size[1] > ymax - ymin:
        notes.append(f"{new_size[0]:g} x {new_size[1]:g} mm does not fit on the plate")
    if move is not None:
        half = [new_size[0] / 2, new_size[1] / 2]
        if (
            move[0] - half[0] < xmin
            or move[0] + half[0] > xmax
            or move[1] - half[1] < ymin
            or move[1] + half[1] > ymax
        ):
            notes.append(f"the part would stick out of the plate at x={move[0]:g}, y={move[1]:g}")
    return PartPlan(
        linear=[[round(float(v), 9) for v in row] for row in matrix],
        scale_xyz=[round(float(v), 6) for v in scales],
        size_mm=new_size,
        count=len(placements) if wants_copies else 1,
        requested=requested,
        placements=placements,
        move=[round(v, 3) for v in move] if move else None,
        notes=notes,
        steps=_steps(turn, scales, new_size, move) + setup_steps,
        setup=setup,
        rotations_used=any(p.rotation_deg % 360 for p in placements),
        reshaped=turn.lay_flat
        or bool(turn.mirror_axes)
        or any(a is not None for a in (turn.rotate_x_deg, turn.rotate_y_deg, turn.rotate_z_deg)),
        reset_rotation=turn.reset_rotation,
        reset_scale=turn.reset_scale,
        lock=turn.lock,
        auto_drop=turn.auto_drop,
        mesh_type=turn.mesh_type,
        slice_now=turn.slice_now or turn.export_gcode,
        export_gcode=turn.export_gcode,
    )
