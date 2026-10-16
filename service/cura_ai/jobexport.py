"""The file the Cura plugin writes: printer, material and every resolved setting."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from cura_ai.catalogue import BOUND_ATTRS, Catalogue


class PrinterInfo(BaseModel):
    id: str
    name: str = ""
    extruder_id: str | None = None


class MaterialInfo(BaseModel):
    id: str = ""
    name: str = ""


class JobExport(BaseModel):
    """`settings` maps each Cura key to {value, type, unit, label, limits, options, enabled}."""

    format: int = 1
    cura_version: str = ""
    printer: PrinterInfo
    material: MaterialInfo = Field(default_factory=MaterialInfo)
    quality: str = ""
    settings: dict[str, dict[str, Any]]
    user_set: list[str] = Field(default_factory=list)  # keys the user changed on purpose

    @classmethod
    def from_catalogue(
        cls, catalogue: Catalogue, printer: PrinterInfo, material: MaterialInfo | None = None
    ) -> JobExport:
        """What the plugin would write, built from definition files (for use without Cura)."""
        settings: dict[str, dict[str, Any]] = {}
        for key, spec in catalogue.specs.items():
            try:
                value = catalogue.value(key)
            except KeyError:
                continue
            entry: dict[str, Any] = {
                "value": value,
                "type": spec.type,
                "unit": spec.unit,
                "label": spec.label,
                "category": spec.category,
                "options": spec.options,
                "enabled": catalogue.is_enabled(key) is not False,
            }
            for attr in BOUND_ATTRS:
                entry[attr] = catalogue.bound(key, attr)
            settings[key] = entry
        return cls(printer=printer, material=material or MaterialInfo(), settings=settings)

    def catalogue(self) -> Catalogue:
        return Catalogue.from_export(self.settings)

    def values(self) -> dict[str, Any]:
        return {k: v.get("value") for k, v in self.settings.items()}
