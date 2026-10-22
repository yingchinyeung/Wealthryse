"""Arithmetic the analyst may ask for, done by code rather than by the model.

The model writes an expression such as ``(1 + 0.0512) * (1 + 0.031) - 1``.
Every number in it must be a figure some earlier tool returned (or a small
fixed constant such as 100 or 252), so a calculation can only rearrange
validated data. The result gets its own citation id and then counts as a
validated figure for the answer, its tables and its charts.
"""
from __future__ import annotations

import ast
import math
import statistics
from typing import Any, Mapping

from .validator import known_numbers, number_is_known

NAME = "calculate"
MAX_EXPRESSION_CHARS = 2000
CONSTANTS = frozenset({0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0,
                       12.0, 52.0, 100.0, 252.0, 365.0, 365.25, 1000.0, 10000.0})


def _mean(*values: float) -> float:
    return statistics.fmean(_flat(values))


def _flat(values: Any) -> list[float]:
    out: list[float] = []
    for v in values:
        out.extend(_flat(v) if isinstance(v, (list, tuple)) else [v])
    return out


FUNCTIONS = {
    "abs": abs,
    "min": lambda *v: min(_flat(v)),
    "max": lambda *v: max(_flat(v)),
    "sum": lambda *v: math.fsum(_flat(v)),
    "mean": _mean,
    "stdev": lambda *v: statistics.stdev(_flat(v)),
    "sqrt": math.sqrt,
    "ln": math.log,
    "exp": math.exp,
    "product": lambda *v: math.prod(_flat(v)),
}
SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "What the result is, e.g. 'combined return of AAPL and MSFT weights'"},
        "expression": {"type": "string", "description": (
            "Arithmetic using + - * / ** ( ), lists [a, b], and " + ", ".join(sorted(FUNCTIONS))
            + ". Use figures exactly as tools returned them; returns and weights are fractions.")},
    },
    "required": ["label", "expression"],
    "additionalProperties": False,
}
DESCRIPTION = ("Work out a figure the tools do not give directly (sums, differences, ratios, weighted or "
               "compounded returns, averages). Only figures already returned by tools may be used.")


class CalculationError(ValueError):
    pass


def _evaluate(node: ast.AST, literals: list[float]) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, literals)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        literals.append(float(node.value))
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _evaluate(node.operand, literals)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
        left, right = _evaluate(node.left, literals), _evaluate(node.right, literals)
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or (left == 0 and right < 0)):
            raise CalculationError("That power is out of range.")
        return {ast.Add: lambda: left + right, ast.Sub: lambda: left - right, ast.Mult: lambda: left * right,
                ast.Div: lambda: left / right, ast.Pow: lambda: left ** right}[type(node.op)]()
    if isinstance(node, (ast.List, ast.Tuple)):
        if len(node.elts) > 500:
            raise CalculationError("Lists may have at most 500 values.")
        return [_evaluate(e, literals) for e in node.elts]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS
            and not node.keywords):
        return FUNCTIONS[node.func.id](*[_evaluate(a, literals) for a in node.args])
    raise CalculationError("Only numbers, + - * / **, brackets, lists and the listed functions are allowed.")


def calculate(arguments: Mapping[str, Any], results: Mapping[str, Any]) -> dict[str, Any]:
    """Return a tool-style result, or raise CalculationError with a message for the model."""
    expression = str(arguments.get("expression") or "").strip()
    label = str(arguments.get("label") or "").strip()[:200]
    if not expression or len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculationError(f"The expression must be 1 to {MAX_EXPRESSION_CHARS} characters.")
    try:
        tree = ast.parse(expression.replace("−", "-"), mode="eval")
    except SyntaxError as error:
        raise CalculationError("The expression could not be read.") from error
    literals: list[float] = []
    try:
        value = _evaluate(tree, literals)
    except (ArithmeticError, ValueError, TypeError, statistics.StatisticsError) as error:
        if isinstance(error, CalculationError):
            raise
        raise CalculationError("The expression has no valid result.") from error
    if isinstance(value, list) or not math.isfinite(value):
        raise CalculationError("The expression must give a single finite number.")
    known = known_numbers(results)
    unknown = [n for n in literals if n not in CONSTANTS and not number_is_known(n, known)]
    if unknown:
        shown = ", ".join(f"{n:g}" for n in unknown[:10])
        raise CalculationError(f"These numbers do not come from any tool result: {shown}. Use figures exactly as returned.")
    return {"tool": NAME, "account_id": None, "data": {"label": label, "expression": expression, "value": value},
            "citations": [{"source": "calculation"}]}
