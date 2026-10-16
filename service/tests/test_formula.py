import pytest

from cura_ai.formula import Evaluator, Unevaluable


def ev(expr, **values):
    def resolve(name):
        return values[name]

    return Evaluator(resolve).evaluate(expr)


def test_arithmetic_and_conditional():
    assert ev("0 if a == 0 else (b * 2) / 4", a=1, b=10) == 5
    assert ev("0 if a == 0 else 1", a=0) == 0


def test_boolean_logic_short_circuits():
    assert ev("a and b", a=False, b=1) is False
    assert ev("a or b", a=0, b=7) == 7


def test_math_and_builtins():
    assert ev("math.ceil(x / 0.4)", x=1.0) == 3
    assert ev("max(1, min(x, 5))", x=9) == 5


def test_extruder_helpers_read_the_single_stack():
    assert ev("extruderValue(0, 'layer_height') * 2", layer_height=0.2) == pytest.approx(0.4)
    assert ev("resolveOrValue('speed_print')", speed_print=60) == 60


def test_missing_value_is_unevaluable_not_a_crash():
    with pytest.raises(Unevaluable):
        ev("missing + 1")


@pytest.mark.parametrize(
    "expr",
    ["__import__('os').system('x')", "().__class__", "open('f')", "lambda: 1", "[x for x in y]"],
)
def test_anything_outside_the_whitelist_is_refused(expr):
    with pytest.raises(Unevaluable):
        ev(expr, y=[1])
