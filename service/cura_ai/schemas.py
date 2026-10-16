"""Wire format of the settings dialogue: what the model may answer with."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Message(_Strict):
    """One chat message of the conversation, as kept by the caller."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


HELPERS = ("settings", "orientation", "parts")
Helper = Literal["settings", "orientation", "parts"]


def check_redirect(kind: str, redirect_to: str | None, own: str, has_content: bool) -> None:
    """A redirect names another helper and carries nothing else; other kinds name none."""
    if kind != "redirect":
        if redirect_to is not None:
            raise ValueError("redirect_to is only for kind 'redirect'")
        return
    if redirect_to is None or redirect_to == own:
        raise ValueError(f"kind 'redirect' needs redirect_to naming another helper than '{own}'")
    if has_content:
        raise ValueError("kind 'redirect' must not carry anything else")


class Question(_Strict):
    id: str = Field(min_length=1, description="Short stable id, e.g. 'load_case'.")
    text: str = Field(min_length=1, description="The question, addressed to the user.")


class Override(_Strict):
    key: str = Field(min_length=1, description="Cura setting key, e.g. 'infill_sparse_density'.")
    value: str = Field(min_length=1, description="New value as text; typed later by the validator.")
    reason: str = Field(min_length=1, description="Why this value serves the stated goal.")


class Turn(_Strict):
    """One model answer: either questions or a proposal, never both."""

    kind: Literal["questions", "proposal", "redirect"]
    questions: list[Question] = Field(default_factory=list)
    summary: str = Field(default="", description="Proposal: one or two sentences on the approach.")
    analysis: str = Field(
        default="",
        description="Proposal: think first - which aspects of the goal matter and what follows "
        "for each (walls, skin, support interface, speeds, cooling, temperatures, travel ...).",
    )
    overrides: list[Override] = Field(default_factory=list)
    redirect_to: Helper | None = Field(
        default=None, description="Kind 'redirect': the helper this request belongs to."
    )
    scope: Literal["print", "part"] = Field(
        default="print", description="'part': only the selected part gets these settings."
    )

    @model_validator(mode="after")
    def _kind_matches_content(self) -> Turn:
        check_redirect(
            self.kind,
            self.redirect_to,
            "settings",
            bool(self.questions or self.overrides or self.summary),
        )
        if self.kind == "redirect":
            return self
        if self.kind == "questions":
            if not self.questions:
                raise ValueError("kind 'questions' needs at least one question")
            if self.overrides or self.summary:
                raise ValueError("kind 'questions' must not carry overrides or a summary")
        else:
            if not self.overrides:
                raise ValueError("kind 'proposal' needs at least one override")
            if self.questions:
                raise ValueError("kind 'proposal' must not carry questions")
            if not self.summary:
                raise ValueError("kind 'proposal' needs a summary")
            keys = [o.key for o in self.overrides]
            if len(keys) != len(set(keys)):
                raise ValueError("an override key appears more than once")
        return self


class Weights(_Strict):
    """How much each orientation metric matters for the user's goal (0 = ignore)."""

    bed_contact: float = Field(default=0, ge=0, le=10, description="Large flat contact on the bed.")
    low_overhang: float = Field(default=0, ge=0, le=10, description="Little overhanging surface.")
    low_support: float = Field(default=0, ge=0, le=10, description="Little support material.")
    low_height: float = Field(default=0, ge=0, le=10, description="A low part (fast, stable).")
    load_in_plane: float = Field(default=0, ge=0, le=10, description="Load along the layers.")
    visible_surface: float = Field(default=0, ge=0, le=10, description="Clean visible side.")


class OrientTurn(_Strict):
    """One model answer for the orientation dialogue: questions or a choice."""

    kind: Literal["questions", "choice", "redirect"]
    questions: list[Question] = Field(default_factory=list)
    weights: Weights | None = None
    chosen_candidate_id: str = Field(default="", description="An id from the candidate list.")
    reasoning: str = Field(default="", description="Why this candidate fits the goal.")
    redirect_to: Helper | None = Field(
        default=None, description="Kind 'redirect': the helper this request belongs to."
    )

    @model_validator(mode="after")
    def _kind_matches_content(self) -> OrientTurn:
        check_redirect(
            self.kind,
            self.redirect_to,
            "orientation",
            bool(self.questions or self.weights or self.chosen_candidate_id or self.reasoning),
        )
        if self.kind == "redirect":
            return self
        if self.kind == "questions":
            if not self.questions:
                raise ValueError("kind 'questions' needs at least one question")
            if self.weights or self.chosen_candidate_id or self.reasoning:
                raise ValueError("kind 'questions' must not carry a choice")
        else:
            if self.questions:
                raise ValueError("kind 'choice' must not carry questions")
            if self.weights is None or not any(v > 0 for v in self.weights.model_dump().values()):
                raise ValueError("kind 'choice' needs weights with at least one above zero")
            if not self.chosen_candidate_id or not self.reasoning:
                raise ValueError("kind 'choice' needs chosen_candidate_id and reasoning")
        return self


class PartTurn(_Strict):
    """One model answer for part operations: questions, or a plan of the allowed operations."""

    kind: Literal["questions", "plan", "redirect"]
    questions: list[Question] = Field(default_factory=list)
    summary: str = Field(default="", description="Plan: what will be done, in one sentence.")
    redirect_to: Helper | None = Field(
        default=None, description="Kind 'redirect': the helper this request belongs to."
    )
    scale_percent: float | None = Field(
        default=None, ge=1, le=1000, description="Uniform scale, 100 = unchanged."
    )
    target_axis: Literal["x", "y", "z"] | None = Field(
        default=None, description="With target_mm: make this dimension that long (uniform)."
    )
    target_mm: float | None = Field(default=None, gt=0, le=1000)
    copies: int | None = Field(
        default=None, ge=1, le=500, description="Total number of parts wanted, original included."
    )
    fill_plate: bool = Field(default=False, description="As many copies as fit on the plate.")
    spacing_mm: float = Field(default=3.0, ge=0, le=50, description="Gap between the copies.")
    allow_rotation: bool = Field(default=True, description="Copies may be turned to fit more.")
    target_uniform: bool = Field(
        default=True, description="With target_axis: scale all axes (true) or only that one."
    )
    scale_x_percent: float | None = Field(default=None, ge=1, le=1000, description="X only.")
    scale_y_percent: float | None = Field(default=None, ge=1, le=1000, description="Y only.")
    scale_z_percent: float | None = Field(default=None, ge=1, le=1000, description="Z only.")
    rotate_x_deg: float | None = Field(default=None, ge=-360, le=360, description="Around X.")
    rotate_y_deg: float | None = Field(default=None, ge=-360, le=360, description="Around Y.")
    rotate_z_deg: float | None = Field(default=None, ge=-360, le=360, description="Around Z.")
    mirror_axes: list[Literal["x", "y", "z"]] = Field(
        default_factory=list, description="Axes to mirror across (the part is flipped)."
    )
    lay_flat: bool = Field(default=False, description="Put the largest flat face on the plate.")
    move_mode: Literal["to", "by"] | None = Field(
        default=None, description="'to': plate coordinates of the part centre; 'by': shift."
    )
    move_x_mm: float | None = Field(default=None, ge=-2000, le=2000)
    move_y_mm: float | None = Field(default=None, ge=-2000, le=2000)
    reset_rotation: bool = Field(default=False, description="Undo all turning (not with copies).")
    reset_scale: bool = Field(default=False, description="Back to 100 % (not with copies).")
    lock: bool | None = Field(default=None, description="Lock (true) or unlock the part.")
    auto_drop: bool | None = Field(
        default=None, description="Let the part always sink to the plate (true) or not."
    )
    printer: str | None = Field(default=None, description="Switch to this printer (listed name).")
    material: str | None = Field(default=None, description="Switch to this material (listed name).")
    quality: str | None = Field(default=None, description="Switch to this quality (listed name).")
    discard_changes: bool = Field(
        default=False, description="Throw away the user's setting changes of the active profile."
    )
    slice_now: bool = Field(default=False, description="Slice and report time and material.")
    export_gcode: bool = Field(default=False, description="Slice and save the G-code to a file.")
    mesh_type: Literal["normal", "support_blocker", "support", "infill", "cutting"] | None = Field(
        default=None, description="What the part is for: printed normally or a helper shape."
    )

    def shape_changes(self) -> bool:
        """Whether the part's shape or orientation changes (what the packer must account for)."""
        return any(
            [
                self.scale_percent is not None,
                self.target_axis is not None,
                self.scale_x_percent is not None,
                self.scale_y_percent is not None,
                self.scale_z_percent is not None,
                self.rotate_x_deg is not None,
                self.rotate_y_deg is not None,
                self.rotate_z_deg is not None,
                bool(self.mirror_axes),
                self.lay_flat,
            ]
        )

    def needs_part(self) -> bool:
        """Whether any operation acts on the part itself (not just on the setup or the slice)."""
        return any(
            [
                self.shape_changes(),
                self.copies is not None,
                self.fill_plate,
                self.move_mode is not None,
                self.reset_rotation,
                self.reset_scale,
                self.lock is not None,
                self.auto_drop is not None,
                self.mesh_type is not None,
            ]
        )

    @model_validator(mode="after")
    def _kind_matches_content(self) -> PartTurn:
        wants = [
            self.shape_changes(),
            self.copies is not None,
            self.fill_plate,
            self.move_mode is not None,
            self.reset_rotation,
            self.reset_scale,
            self.lock is not None,
            self.auto_drop is not None,
            self.mesh_type is not None,
            self.slice_now,
            self.export_gcode,
            self.printer is not None,
            self.material is not None,
            self.quality is not None,
            self.discard_changes,
        ]
        check_redirect(
            self.kind, self.redirect_to, "parts", bool(self.questions or self.summary or any(wants))
        )
        if self.kind == "redirect":
            return self
        if self.kind == "questions":
            if not self.questions:
                raise ValueError("kind 'questions' needs at least one question")
            if any(wants) or self.summary:
                raise ValueError("kind 'questions' must not carry a plan")
            return self
        if self.questions:
            raise ValueError("kind 'plan' must not carry questions")
        if not self.summary:
            raise ValueError("kind 'plan' needs a summary")
        if not any(wants):
            raise ValueError("kind 'plan' needs at least one operation")
        if (self.target_axis is None) != (self.target_mm is None):
            raise ValueError("target_axis and target_mm go together")
        axis_scales = [self.scale_x_percent, self.scale_y_percent, self.scale_z_percent]
        uniform = [self.scale_percent is not None, self.target_axis is not None]
        if sum(uniform) + any(s is not None for s in axis_scales) > 1:
            raise ValueError(
                "use only one of scale_percent, target_axis with target_mm, or the per-axis scales"
            )
        if (self.move_mode is None) != (self.move_x_mm is None and self.move_y_mm is None):
            raise ValueError("move_mode goes with move_x_mm and/or move_y_mm")
        if self.printer is not None and (self.material or self.quality):
            raise ValueError("switch the printer alone; material and quality come in a later step")
        resets = self.reset_rotation or self.reset_scale
        if resets and (self.shape_changes() or self.copies or self.fill_plate):
            raise ValueError("reset_rotation and reset_scale stand alone (move and lock allowed)")
        if self.move_mode and (self.copies or self.fill_plate):
            raise ValueError("a move cannot be combined with copies: the program places those")
        return self


def part_json_schema() -> dict:
    """JSON Schema of the part-operations dialogue."""
    return _flat_schema(PartTurn)


def _inline_refs(node, defs):
    """Replace `$ref` by the referenced definition and drop titles; the CLI gets a flat schema."""
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline_refs(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k not in ("$defs", "title")}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _flat_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    return _inline_refs(schema, schema.get("$defs", {}))


def turn_json_schema() -> dict:
    """JSON Schema of the settings dialogue, handed to `claude --json-schema`."""
    return _flat_schema(Turn)


def orient_json_schema() -> dict:
    """JSON Schema of the orientation dialogue."""
    return _flat_schema(OrientTurn)
