"""Safe evaluator for the small expression language used in Cura definition files.

Definitions carry formulas such as `0 if infill_sparse_density == 0 else ...`. They come from
Cura's own files, never from the model, but are still evaluated through an AST whitelist
instead of `eval`.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any


class Unevaluable(Exception):
    """The expression uses something outside the supported subset or a value is missing."""


_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_CMP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}
_FUNCS: dict[str, Callable[..., Any]] = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "int": int,
    "float": float,
    "str": str,
    "len": len,
    "sum": sum,
    "any": any,
    "all": all,
    "sorted": sorted,
}
_MATH_NAMES = (
    "ceil", "floor", "sqrt", "sin", "cos", "tan", "radians", "degrees", "log", "exp",
    "atan", "atan2", "pi", "fabs",
)  # fmt: skip
_MATH = {n: getattr(math, n) for n in _MATH_NAMES}


class Evaluator:
    """Evaluates formulas; `resolve(name)` supplies setting values and may raise KeyError."""

    def __init__(self, resolve: Callable[[str], Any]) -> None:
        self._resolve = resolve

    def evaluate(self, expression: str) -> Any:
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except SyntaxError as exc:
            raise Unevaluable(f"syntax error in {expression!r}") from exc
        try:
            return self._eval(tree.body)
        except Unevaluable:
            raise
        except (KeyError, TypeError, ValueError, ZeroDivisionError, IndexError, OverflowError) as e:
            raise Unevaluable(f"{type(e).__name__}: {e} in {expression!r}") from e

    def _eval(self, node: ast.AST) -> Any:  # noqa: C901 - one branch per supported node type
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in ("True", "False", "None"):
                return {"True": True, "False": False, "None": None}[node.id]
            if node.id in _FUNCS:
                return _FUNCS[node.id]
            return self._resolve(node.id)
        if isinstance(node, ast.UnaryOp):
            v = self._eval(node.operand)
            if isinstance(node.op, ast.Not):
                return not v
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.UAdd):
                return +v
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](self._eval(node.left), self._eval(node.right))
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result: Any = True
                for v in node.values:
                    result = self._eval(v)
                    if not result:
                        return result
                return result
            result = False
            for v in node.values:
                result = self._eval(v)
                if result:
                    return result
            return result
        if isinstance(node, ast.Compare):
            left = self._eval(node.left)
            for op, comp in zip(node.ops, node.comparators, strict=True):
                right = self._eval(comp)
                if type(op) not in _CMP or not _CMP[type(op)](left, right):
                    if type(op) not in _CMP:
                        raise Unevaluable("unsupported comparison")
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._eval(node.body if self._eval(node.test) else node.orelse)
        if isinstance(node, ast.List | ast.Tuple):
            return [self._eval(e) for e in node.elts]
        if isinstance(node, ast.Dict):
            return {
                self._eval(k): self._eval(v) for k, v in zip(node.keys, node.values, strict=True)
            }  # type: ignore[arg-type]
        if isinstance(node, ast.Subscript):
            return self._eval(node.value)[self._eval(node.slice)]
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == "math" and node.attr in _MATH:
                return _MATH[node.attr]
            raise Unevaluable(f"unsupported attribute {node.attr}")
        if isinstance(node, ast.Call):
            return self._call(node)
        raise Unevaluable(f"unsupported syntax {type(node).__name__}")

    def _call(self, node: ast.Call) -> Any:
        args = [self._eval(a) for a in node.args]
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
            if name in _FUNCS:
                return _FUNCS[name](*args)
            # Single-extruder semantics: extruder lookups all read the one stack.
            if name in ("extruderValue", "extruderValues", "resolveOrValue", "value"):
                key = args[-1]
                if name == "extruderValues":
                    return [self._resolve(key)]
                return self._resolve(key)
            if name == "defaultExtruderPosition":
                return "0"
            if name == "anyExtruderWithMaterial":
                return False
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "math"
            and func.attr in _MATH
            and callable(_MATH[func.attr])
        ):
            return _MATH[func.attr](*args)
        raise Unevaluable("unsupported call")
