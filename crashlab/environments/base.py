"""Environment contract (blueprint 5, Module 2).

Each environment provides: an initial state, a tool interface, business
rules, deterministic reset, state snapshots and *query functions* used by the
assertion language. Tools mutate the environment's own database; evaluators
later inspect that database independently of what the agent claims.
"""
from __future__ import annotations

import copy
import inspect
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable


class ToolError(Exception):
    """A business-level error returned to the agent (e.g. 'order already shipped')."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    description: str
    mutating: bool = False
    latency_ms: int = 120
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)

    def json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.params,
                "required": self.required,
            },
        }


def tool(description: str, *, mutating: bool = False, latency_ms: int = 120, **params: dict[str, Any]):
    """Decorator registering an environment method as an agent-facing tool."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(fn)
        required = [
            n for n, p in sig.parameters.items() if n != "self" and p.default is inspect.Parameter.empty
        ]
        fn.__tool__ = dict(  # type: ignore[attr-defined]
            description=description, mutating=mutating, latency_ms=latency_ms,
            params=params, required=required,
        )
        return fn

    return deco


def query(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Marks a method as usable inside scenario assertions."""
    fn.__query__ = True  # type: ignore[attr-defined]
    return fn


class Environment:
    name = "base"

    def __init__(self, initial_state: dict[str, Any]) -> None:
        self._initial = copy.deepcopy(initial_state)
        self.state: dict[str, Any] = copy.deepcopy(initial_state)

    # -- lifecycle -----------------------------------------------------------
    def reset(self) -> None:
        self.state = copy.deepcopy(self._initial)

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.state)

    def restore(self, snap: dict[str, Any]) -> None:
        self.state = copy.deepcopy(snap)

    @property
    def initial_state(self) -> dict[str, Any]:
        return copy.deepcopy(self._initial)

    # -- tool registry ---------------------------------------------------------
    def tools(self) -> dict[str, ToolSpec]:
        out: dict[str, ToolSpec] = {}
        for name in dir(type(self)):
            fn = getattr(type(self), name)
            meta = getattr(fn, "__tool__", None)
            if meta:
                out[name] = ToolSpec(name=name, fn=getattr(self, name), **meta)
        return out

    def queries(self) -> dict[str, Callable[..., Any]]:
        return {
            n: getattr(self, n)
            for n in dir(type(self))
            if getattr(getattr(type(self), n), "__query__", False)
        }

    def call(self, name: str, args: dict[str, Any]) -> Any:
        spec = self.tools().get(name)
        if spec is None:
            raise ToolError("unknown_tool", f"Tool '{name}' does not exist")
        try:
            return spec.fn(**args)
        except TypeError as exc:  # bad / missing arguments
            raise ToolError("invalid_arguments", str(exc)) from None

    def call_on(self, state: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
        """Execute a *read* against another state (used for stale-read faults)."""
        live = self.state
        self.state = copy.deepcopy(state)
        try:
            return self.call(name, args)
        finally:
            self.state = live

    # -- helpers ---------------------------------------------------------------
    def apply_mutation(self, path: str, value: Any) -> None:
        """Set a dotted path, e.g. 'orders.ORD-1001.status'. Used by external
        concurrent-update faults."""
        parts = path.split(".")
        node = self.state
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = copy.deepcopy(value)
