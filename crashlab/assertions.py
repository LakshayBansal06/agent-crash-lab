"""A tiny, *safe* assertion language for scenario expectations.

    refund_count('ORD-1001') == 1
    refund_total('ORD-1001') <= order_amount('ORD-1001')
    payment_status('ORD-1001') in ('PAID', 'REFUND_PENDING')
    not escalated() or refund_count() == 0

Only literals, comparisons, boolean logic, simple arithmetic and calls to the
environment's whitelisted ``@query`` functions are allowed. Nothing is passed
to Python's ``eval``; the AST is interpreted directly, so uploaded scenarios
cannot execute code (blueprint 17.4 "malicious uploaded scenarios").
"""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

_CMP = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not,
}
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}


class AssertionSyntaxError(ValueError):
    pass


@dataclass
class AssertionOutcome:
    expression: str
    passed: bool
    observed: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {"expression": self.expression, "passed": self.passed, "observed": self.observed}
        if self.error:
            d["error"] = self.error
        return d


def compile_assertion(expr: str, allowed: set[str]) -> ast.Expression:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise AssertionSyntaxError(f"{expr!r}: {exc.msg}") from None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Compare, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp,
                             ast.Not, ast.USub, ast.Constant, ast.Tuple, ast.List, ast.Load, ast.BinOp,
                             *_CMP.keys(), *_BIN.keys())):
            continue
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in allowed:
                name = getattr(node.func, "id", ast.dump(node.func))
                raise AssertionSyntaxError(f"{expr!r}: function {name!r} is not an allowed query")
            if node.keywords:
                raise AssertionSyntaxError(f"{expr!r}: keyword arguments are not supported")
            continue
        if isinstance(node, ast.Name) and node.id in allowed:
            continue
        raise AssertionSyntaxError(f"{expr!r}: '{type(node).__name__}' is not allowed")
    return tree


def evaluate(expr: str, queries: dict[str, Callable[..., Any]]) -> AssertionOutcome:
    observed: dict[str, Any] = {}

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(ev(e) for e in node.elts)
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return (not v) if isinstance(node.op, ast.Not) else -v
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(ev(v) for v in node.values)
            return any(ev(v) for v in node.values)
        if isinstance(node, ast.BinOp):
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comp in zip(node.ops, node.comparators, strict=True):
                right = ev(comp)
                if not _CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            args = [ev(a) for a in node.args]
            val = queries[node.func.id](*args)  # type: ignore[attr-defined]
            observed[ast.unparse(node)] = val
            return val
        raise AssertionSyntaxError(type(node).__name__)

    try:
        tree = compile_assertion(expr, set(queries))
        return AssertionOutcome(expr, bool(ev(tree)), observed)
    except AssertionSyntaxError:
        raise
    except Exception as exc:  # query raised (e.g. unknown order) -> failed assertion, not a crash
        return AssertionOutcome(expr, False, observed, error=f"{type(exc).__name__}: {exc}")
