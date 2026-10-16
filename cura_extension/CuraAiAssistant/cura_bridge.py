"""Everything that touches Cura's scene and setting stacks. Runs on the main thread only.

Patterns are taken from Cura 5.13's own code: StartSliceJob (vertex conversion, setting
export), RotateTool (rotation around the node position) and MachineManager (userChanges).
"""

from __future__ import annotations

import copy
import json
import math
import os
from typing import Any

import numpy
from cura.Machines.ContainerTree import ContainerTree
from cura.Settings.ContainerManager import ContainerManager
from cura.Settings.CuraContainerRegistry import CuraContainerRegistry
from cura.Settings.SettingOverrideDecorator import SettingOverrideDecorator
from UM.Application import Application
from UM.Logger import Logger
from UM.Math.Matrix import Matrix
from UM.Math.Quaternion import Quaternion
from UM.Math.Vector import Vector
from UM.Operations.AddSceneNodeOperation import AddSceneNodeOperation
from UM.Operations.GroupedOperation import GroupedOperation
from UM.Operations.Operation import Operation
from UM.Operations.RotateOperation import RotateOperation
from UM.Operations.TranslateOperation import TranslateOperation
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Scene.SceneNodeSettings import SceneNodeSettings
from UM.Scene.Selection import Selection
from UM.Settings.SettingInstance import SettingInstance

from . import geometry

# How a part can serve the print besides being printed (Cura's "mesh type" of a part).
MESH_TYPES = {
    "support_blocker": "anti_overhang_mesh",
    "support": "support_mesh",
    "infill": "infill_mesh",
    "cutting": "cutting_mesh",
}

# What Cura's own per-model tool sets next to "infill mesh": no walls or skin on the infill region.
_LAYERS = "math.ceil(round({} / resolveOrValue('layer_height'), 4))"
INFILL_MESH_EXTRAS = {
    "top_bottom_thickness": 0,
    "top_thickness": "=top_bottom_thickness",
    "bottom_thickness": "=top_bottom_thickness",
    "top_layers": "=0 if infill_sparse_density == 100 else " + _LAYERS.format("top_thickness"),
    "bottom_layers": "=0 if infill_sparse_density == 100 else "
    + _LAYERS.format("bottom_thickness"),
    "wall_thickness": 0,
    "wall_line_count": (
        "=max(1, round((wall_thickness - wall_line_width_0) / wall_line_width_x) + 1)"
        " if wall_thickness != 0 else 0"
    ),
}

LIMIT_PROPERTIES = (
    "minimum_value",
    "maximum_value",
    "minimum_value_warning",
    "maximum_value_warning",
)


class BridgeError(Exception):
    """A problem the user can act on (no selection, no printer, ...)."""


def _stacks():
    app = Application.getInstance()
    global_stack = app.getGlobalContainerStack()
    if global_stack is None or not global_stack.extruderList:
        raise BridgeError("No printer is active in Cura.")
    return app, global_stack, global_stack.extruderList[0]


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int | float):
        return value if math.isfinite(value) else None
    if isinstance(value, list | tuple | dict):
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)
    return str(value)


def _user_set_keys(global_stack, extruder) -> set[str]:
    """Keys the user changed: unsaved changes and the values of a custom profile of theirs."""
    keys: set[str] = set()
    for stack in (global_stack, extruder):
        keys |= set(stack.userChanges.getAllKeys())
        keys |= set(stack.qualityChanges.getAllKeys())
    return keys


def collect_job() -> dict:
    """Printer, material, quality and every resolved setting with Cura's own limits."""
    app, global_stack, extruder = _stacks()
    settings: dict[str, dict] = {}
    for key in set(global_stack.getAllKeys()) | set(extruder.getAllKeys()):
        per_extruder = global_stack.getProperty(key, "settable_per_extruder")
        in_global = global_stack.getProperty(key, "type") is not None
        stack = extruder if (per_extruder or not in_global) else global_stack
        entry = {
            "value": _plain(stack.getProperty(key, "value")),
            "type": stack.getProperty(key, "type"),
            "unit": stack.getProperty(key, "unit") or "",
            "label": stack.getProperty(key, "label") or key,
            "options": stack.getProperty(key, "options"),
            "enabled": bool(stack.getProperty(key, "enabled")),
        }
        for prop in LIMIT_PROPERTIES:
            entry[prop] = _plain(stack.getProperty(key, prop))
        settings[key] = entry
    material = extruder.material
    return {
        "format": 1,
        "cura_version": app.getVersion(),
        "printer": {
            "id": global_stack.definition.getId(),
            "name": global_stack.getName(),
            "extruder_id": extruder.definition.getId(),
        },
        "material": {
            "id": material.getMetaDataEntry("base_file", ""),
            "name": material.getName(),
        },
        "quality": app.getMachineManager().activeQualityOrQualityChangesName,
        "settings": settings,
        "user_set": sorted(_user_set_keys(global_stack, extruder)),
    }


def support_angle() -> float:
    """Cura's current support overhang angle; the service scores overhangs with the same one."""
    _, global_stack, _ = _stacks()
    return float(global_stack.getProperty("support_angle", "value") or 50.0)


def scene_parts() -> list:
    """The parts on the plate: printable objects and groups directly under the scene root."""
    root = Application.getInstance().getController().getScene().getRoot()
    return [
        n
        for n in root.getChildren()
        if (n.callDecoration("isSliceable") or n.callDecoration("isGroup"))
        and not n.callDecoration("isNonPrintingMesh")
    ]


def current_part() -> tuple:
    """(node, note): the selected part, else the only part on the plate, else (None, why not)."""
    count = Selection.getCount()
    if count == 1:
        return Selection.getSelectedObject(0), "selected"
    if count > 1:
        return None, "several selected - select just one"
    parts = scene_parts()
    if len(parts) == 1:
        return parts[0], "the only part"
    if not parts:
        return None, "none on the plate - load a part first"
    return None, f"none selected - click one of the {len(parts)} parts"


def selected_node():
    """The part to work on; the only part on the plate is taken and selected for the user."""
    node, note = current_part()
    if node is None:
        raise BridgeError(f"Part: {note}.")
    if note == "the only part":
        Selection.add(node)
    return node


def _mesh_nodes(node) -> list:
    return [
        n
        for n in DepthFirstIterator(node)
        if n.getMeshData() is not None and (n is node or not n.callDecoration("isNonPrintingMesh"))
    ]


def _scene_geometry(node) -> tuple[numpy.ndarray, numpy.ndarray]:
    """World-space vertices and triangles of a node and its children, in scene axes."""
    all_vertices, all_tris = [], []
    for n in _mesh_nodes(node):
        mesh = n.getMeshData()
        world = n.getWorldTransformation()
        rot_scale = world.getTransposed().getData()[0:3, 0:3]
        translate = world.getData()[:3, 3]
        vertices = mesh.getVertices().dot(rot_scale) + translate
        all_vertices.append(vertices)
        all_tris.append(geometry.triangles(vertices, mesh.getIndices()))
    if not all_tris:
        raise BridgeError("The selected object has no printable mesh.")
    return numpy.concatenate(all_vertices), numpy.concatenate(all_tris)


def export_model(work_dir: str) -> str:
    """Write the selected model, as placed now, to an STL in printer axes. Returns its path."""
    _, tris_scene = _scene_geometry(selected_node())
    tris = geometry.scene_to_printer(tris_scene.reshape(-1, 3)).reshape(-1, 3, 3)
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, "model.stl")
    geometry.write_stl(path, tris)
    return path


def _target(key: str):
    _, global_stack, extruder = _stacks()
    return extruder if global_stack.getProperty(key, "settable_per_extruder") else global_stack


def apply_overrides(items: list[dict]) -> list[dict]:
    """Set accepted overrides as user changes, like editing them in Cura's settings panel.

    Returns what is needed to take them back: per key, whether a user change existed and its value.
    """
    records = []
    for item in items:
        key = item["key"]
        user_changes = _target(key).userChanges
        had = bool(user_changes.hasProperty(key, "value"))
        old = user_changes.getProperty(key, "value") if had else None
        user_changes.setProperty(key, "value", item["value"])
        records.append({"key": key, "had": had, "old": old})
    return records


def apply_object_overrides(items: list[dict]) -> list[dict]:
    """Give the selected part its own settings (Cura's per-model settings); returns undo records."""
    node = selected_node()
    stack = node.callDecoration("getStack")
    if stack is None:
        node.addDecorator(SettingOverrideDecorator())
        stack = node.callDecoration("getStack")
    settings = stack.getTop()
    records = []
    for item in items:
        key = item["key"]
        definition = stack.getSettingDefinition(key)
        if definition is None or not getattr(definition, "settable_per_mesh", False):
            raise BridgeError(f"Cura cannot set {key} for a single part.")
        instance = settings.getInstance(key)
        had = instance is not None
        old = settings.getProperty(key, "value") if had else None
        if instance is None:
            instance = SettingInstance(definition, settings)
            instance.setProperty("value", item["value"])
            instance.resetState()  # not a user state, like Cura's own per-model settings
            settings.addInstance(instance)
        else:
            instance.setProperty("value", item["value"])
        records.append({"key": key, "had": had, "old": old, "node": node})
    return records


def _mesh_settings(node) -> list[str]:
    """Names of the per-part settings the node carries now (for the log)."""
    stack = node.callDecoration("getStack")
    if stack is None:
        return []
    return sorted(stack.getTop().getAllKeys())


def set_mesh_type(node, mesh_type: str) -> None:
    """Turn the part into a support blocker / support / infill / cutting mesh, or back to normal."""
    stack = node.callDecoration("getStack")
    if stack is None:
        node.addDecorator(SettingOverrideDecorator())
        stack = node.callDecoration("getStack")
    settings = stack.getTop()
    for key in MESH_TYPES.values():
        if key != MESH_TYPES.get(mesh_type) and settings.getInstance(key):
            settings.removeInstance(key)
    wanted = MESH_TYPES.get(mesh_type)
    if wanted and not settings.getInstance(wanted):
        instance = SettingInstance(stack.getSettingDefinition(wanted), settings)
        instance.setProperty("value", True)
        instance.resetState()
        settings.addInstance(instance)
    for key, value in INFILL_MESH_EXTRAS.items():
        if wanted == "infill_mesh" and settings.getInstance(key) is None:
            instance = SettingInstance(stack.getSettingDefinition(key), settings)
            instance.setProperty("value", value)
            instance.resetState()
            settings.addInstance(instance)
        elif wanted != "infill_mesh" and settings.getInstance(key):
            settings.removeInstance(key)


def revert_overrides(records: list[dict]) -> None:
    for record in records:
        if "node" in record:
            settings = record["node"].callDecoration("getStack").getTop()
            if record["had"]:
                settings.getInstance(record["key"]).setProperty("value", record["old"])
            else:
                settings.removeInstance(record["key"])
            continue
        user_changes = _target(record["key"]).userChanges
        if record["had"]:
            user_changes.setProperty(record["key"], "value", record["old"])
        else:
            user_changes.removeInstance(record["key"])


def context_line() -> str:
    """ "Printer - material - quality" for the dialog header; empty if no printer is active."""
    try:
        app, global_stack, extruder = _stacks()
    except BridgeError:
        return "No printer selected"
    quality = app.getMachineManager().activeQualityOrQualityChangesName
    return f"{global_stack.getName()}  -  {extruder.material.getName()}  -  {quality}"


def selection_line() -> str:
    """Name and size of the selected part (the 3D object), for the dialog header."""
    node, note = current_part()
    if node is None:
        return f"Part: {note}"
    box = node.getBoundingBox()
    size = f"{box.width:.0f} x {box.depth:.0f} x {box.height:.0f} mm" if box else ""
    suffix = "  (the only part)" if note == "the only part" else ""
    return f"Part: {node.getName()}  -  {size}{suffix}"


def apply_rotation(quaternion: dict) -> None:
    """Rotate the selected model to the chosen orientation and put it back on the plate."""
    node = selected_node()
    vertices, _ = _scene_geometry(node)
    q = Quaternion(quaternion["x"], quaternion["y"], quaternion["z"], quaternion["w"])
    matrix = geometry.quaternion_to_matrix(q.x, q.y, q.z, q.w)
    pivot = node.getWorldPosition().getData()
    drop = geometry.drop_distance(vertices, pivot, matrix)
    operation = GroupedOperation()
    operation.addOperation(RotateOperation(node, q, rotate_around_point=node.getPosition()))
    operation.addOperation(TranslateOperation(node, Vector(0, drop, 0)))
    operation.push()


class _SetTransformation(Operation):
    """Give a node a new transformation; undo puts the one it had back."""

    def __init__(self, node, matrix: numpy.ndarray) -> None:
        super().__init__()
        self._node = node
        self._old = node.getLocalTransformation()
        self._new = Matrix(matrix)

    def undo(self) -> None:
        self._node.setTransformation(self._old)

    def redo(self) -> None:
        self._node.setTransformation(self._new)


def plate_geometry() -> dict:
    """The free plate for the packer, in printer axes: its rectangle minus Cura's own no-go areas.

    Cura's arranger uses the same data (edge margin, `getDisallowedAreas`, which also hold the
    corners of a round plate). Areas are in scene (x, z), so y = -z here.
    """
    volume = Application.getInstance().getBuildVolume()
    edge = volume.getEdgeDisallowedSize()
    half_w, half_d = (volume.getWidth() - 2 * edge) / 2, (volume.getDepth() - 2 * edge) / 2
    polygons = []
    for area in volume.getDisallowedAreas():
        points = area.getPoints()
        if points is not None and len(points) > 2:
            polygons.append([[float(x), float(-z)] for x, z in points])
    return {
        "plate": {"rect": [-half_w, -half_d, half_w, half_d], "disallowed": polygons},
        "build_height": float(volume.getHeight()),
    }


def adhesion() -> dict:
    """What the copies need around themselves: adhesion type and its widths."""
    _, global_stack, _ = _stacks()
    return {
        key: global_stack.getProperty(key, "value")
        for key in ("adhesion_type", "brim_width", "raft_margin")
    }


def export_other_parts(work_dir: str, node) -> tuple[str | None, int]:
    """Write all parts except `node` as one STL (obstacles for the packer); (path, how many)."""
    others = [p for p in scene_parts() if p is not node]
    if not others:
        return None, 0
    tris = numpy.concatenate([_scene_geometry(p)[1] for p in others])
    printer = geometry.scene_to_printer(tris.reshape(-1, 3)).reshape(-1, 3, 3)
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, "others.stl")
    geometry.write_stl(path, printer)
    return path, len(others)


def apply_part_plan(plan: dict) -> int:
    """Reshape, move and copy the selected part as one undoable step. Returns the count."""
    if plan["setup"] and plan["setup"].get("printer"):
        apply_setup(plan["setup"])  # a new printer starts from an empty plate
        return 0
    node, _ = current_part()
    if node is None:  # only setup (and slicing) can be planned without a part
        if plan["setup"]:
            apply_setup(plan["setup"])
        return 0
    node = selected_node()
    vertices, _ = _scene_geometry(node)
    old = node.getLocalTransformation().getData()
    if plan["setup"]:
        apply_setup(plan["setup"])
    if plan["mesh_type"] is not None:
        set_mesh_type(node, plan["mesh_type"])
    if plan["lock"] is not None:
        node.setSetting(SceneNodeSettings.LockPosition, str(plan["lock"]))
    if plan["auto_drop"] is not None:
        node.setSetting(SceneNodeSettings.AutoDropDown, str(plan["auto_drop"]))
    Logger.log(
        "i",
        f"AI Assistant: part state lock={node.getSetting(SceneNodeSettings.LockPosition, '-')} "
        f"auto_drop={node.getSetting(SceneNodeSettings.AutoDropDown, '-')} "
        f"mesh_settings={_mesh_settings(node)}",
    )
    linear = numpy.array(plan["linear"])
    placements = plan["placements"] or [None]
    if plan["move"] and not plan["placements"]:
        placements = [{"x": plan["move"][0], "y": plan["move"][1], "rotation_deg": 0.0}]
    if plan["reset_rotation"] or plan["reset_scale"]:
        new = geometry.reset_transform(old, vertices, plan["reset_rotation"], plan["reset_scale"])
        operation = _SetTransformation(node, new)
        operation.push()
        return 1
    if numpy.allclose(linear, numpy.eye(3)) and not plan["placements"] and not plan["move"]:
        return 1  # only lock, auto-drop or mesh type
    root = node.getParent()
    group = GroupedOperation()
    for index, spot in enumerate(placements):
        target = node
        if index > 0:
            target = copy.deepcopy(node)
            plate_number = node.callDecoration("getBuildPlateNumber")
            target.callDecoration("setBuildPlateNumber", plate_number)
            for child in target.getChildren():
                child.callDecoration("setBuildPlateNumber", plate_number)
            group.addOperation(AddSceneNodeOperation(target, root))
        if spot is None:
            move = geometry.placement_matrix(vertices, linear, 0.0)
        else:
            move = geometry.placement_matrix(
                vertices, linear, spot["rotation_deg"], spot["x"], spot["y"]
            )
        group.addOperation(_SetTransformation(target, move @ old))
    group.push()
    return len(placements)


# ---- slicing and export -------------------------------------------------------------------------


def start_slice() -> None:
    """Slice the plate now, also when Cura's automatic slicing is off."""
    Application.getInstance().getBackend().forceSlice()


def backend():
    return Application.getInstance().getBackend()


def print_summary() -> str:
    """Print time and material of the last slice, as Cura's own summary shows them."""
    info = Application.getInstance().getPrintInformation()
    duration = info.currentPrintTime
    time = f"{int(duration.days) * 24 + int(duration.hours)} h {int(duration.minutes)} min"
    length = sum(float(m) for m in info.materialLengths)
    weight = sum(float(w) for w in info.materialWeights)
    return f"{time}, {length:.1f} m of filament, {weight:.0f} g"


def export_gcode(directory: str) -> str:
    """Write the sliced G-code into `directory` under the job name; never overwrites a file."""
    app = Application.getInstance()
    plate = app.getMultiBuildPlateModel().activeBuildPlate
    chunks = getattr(app.getController().getScene(), "gcode_dict", {}).get(plate)
    if not chunks:
        raise BridgeError("There is no G-code yet: the slice did not finish.")
    job_name = app.getPrintInformation().jobName
    name = "".join(c for c in job_name if c.isalnum() or c in "-_ ") or "print"
    os.makedirs(directory, exist_ok=True)
    path, n = os.path.join(directory, f"{name}.gcode"), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(directory, f"{name} ({n}).gcode")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(chunks))
    return path


# ---- printer, material and quality ---------------------------------------------------------------


def _material_label(brand: str | None, name: str | None) -> str:
    """ "Brand - Name" as in Cura's material list; the same name exists under many brands."""
    if not name:
        return ""
    return f"{brand} - {name}" if brand else name


def setup_inventory() -> dict:
    """What can be switched to: printers, materials of the active nozzle, quality profiles."""
    manager = Application.getInstance().getMachineManager()
    stack = manager.activeMachine
    if stack is None:
        raise BridgeError("No printer is set up in Cura.")
    printers = [
        {"id": s.getId(), "name": s.getName()}
        for s in CuraContainerRegistry.getInstance().findContainerStacks(type="machine")
    ]
    machine = ContainerTree.getInstance().machines[stack.definition.getId()]
    nozzle = stack.extruderList[0].variant.getName()
    materials, seen = [], set()
    diameter = stack.extruderList[0].getApproximateMaterialDiameter()  # 1.75 and 2.85 mm differ
    for root_id, node in machine.variants[nozzle].materials.items():
        if float(node.getMetaDataEntry("approximate_diameter", -1)) != diameter:
            continue
        label = _material_label(node.getMetaDataEntry("brand"), node.getMetaDataEntry("name"))
        if label and label not in seen and node.getMetaDataEntry("compatible", True):
            seen.add(label)
            materials.append({"id": root_id, "name": label})
    tree = ContainerTree.getInstance()
    qualities = [
        {"id": f"type:{key}", "name": group.name}
        for key, group in tree.getCurrentQualityGroups().items()
        if group.is_available
    ] + [
        {"id": f"custom:{group.name}", "name": group.name}  # the user's own profiles
        for group in tree.getCurrentQualityChangesGroups()
    ]
    return {
        "active": {
            "printer": stack.getName(),
            "material": _material_label(
                stack.extruderList[0].material.getMetaDataEntry("brand"),
                stack.extruderList[0].material.getName(),
            ),
            "quality": manager.activeQualityOrQualityChangesName,
        },
        "printers": printers,
        "materials": sorted(materials, key=lambda m: (m["name"][:7] != "Generic", m["name"]))[:400],
        "qualities": qualities,
    }


def apply_setup(setup: dict) -> list[str]:
    """Switch printer, material and quality, or drop the user's changes; returns what was done."""
    manager = Application.getInstance().getMachineManager()
    done = []
    if setup.get("printer"):
        manager.setActiveMachine(setup["printer"])
        done.append("printer")
    if setup.get("material"):
        if not manager.setMaterialById("0", setup["material"]):
            raise BridgeError("Cura does not offer that material for the current nozzle.")
        done.append("material")
    if setup.get("quality"):
        tree = ContainerTree.getInstance()
        kind, _, name = setup["quality"].partition(":")
        if kind == "custom":
            group = next((g for g in tree.getCurrentQualityChangesGroups() if g.name == name), None)
            if group is None:
                raise BridgeError("That profile of yours is not available now.")
            manager.setQualityChangesGroup(group, no_dialog=True)
        else:
            group = tree.getCurrentQualityGroups().get(name)
            if group is None or not group.is_available:
                raise BridgeError("That quality profile is not available now.")
            manager.setQualityGroup(group, no_dialog=True)
        done.append("quality")
    if setup.get("discard"):
        ContainerManager.getInstance().clearUserContainers()
        done.append("changes discarded")
    return done
