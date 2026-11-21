"""Common event schema.

Every framework, adapter and built-in agent is normalised into this one
structure (blueprint section 8.4). Events carry *observable* behaviour only:
inputs, outputs, tool decisions, faults and state changes. No hidden
reasoning is required or recorded.
"""
from __future__ import annotations

import copy
import itertools
from dataclasses import asdict, dataclass, field
from typing import Any

EVENT_TYPES = (
    "episode_start",
    "user_message",
    "agent_step",
    "tool_call",
    "tool_result",
    "fault_activated",
    "state_mutation",
    "invariant_breach",
    "guardrail",
    "final_answer",
    "budget_exceeded",
    "episode_end",
)


class VirtualClock:
    """Deterministic clock. Tool latencies and injected delays advance it, so a
    trace replays with identical timestamps every time."""

    def __init__(self) -> None:
        self.now_ms = 0

    def advance(self, ms: int) -> int:
        self.now_ms += max(0, int(ms))
        return self.now_ms


@dataclass
class Event:
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    t_ms: int
    parent_event_id: str | None = None
    actor: str = "agent"  # agent | environment | fault_engine | external | evaluator | user
    tool: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    duration_ms: int = 0
    message: str | None = None
    state_diff: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in (None, {}, [])} | {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "t_ms": self.t_ms,
        }


class EventCollector:
    def __init__(self, run_id: str, clock: VirtualClock) -> None:
        self.run_id = run_id
        self.clock = clock
        self.events: list[Event] = []
        self._seq = itertools.count(1)

    def emit(self, event_type: str, **kw: Any) -> Event:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}")
        seq = next(self._seq)
        ev = Event(
            event_id=f"{self.run_id}:e{seq:03d}",
            run_id=self.run_id,
            sequence=seq,
            event_type=event_type,
            t_ms=self.clock.now_ms,
            **kw,
        )
        self.events.append(ev)
        return ev

    def as_dicts(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.events]


def diff_state(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    """Structural diff used to show *exactly* what an action changed."""
    out: list[dict[str, Any]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for k in sorted(set(before) | set(after), key=str):
            p = f"{path}.{k}" if path else str(k)
            if k not in before:
                out.append({"path": p, "op": "add", "after": copy.deepcopy(after[k])})
            elif k not in after:
                out.append({"path": p, "op": "remove", "before": copy.deepcopy(before[k])})
            else:
                out.extend(diff_state(before[k], after[k], p))
    elif isinstance(before, list) and isinstance(after, list):
        n = max(len(before), len(after))
        for i in range(n):
            p = f"{path}[{i}]"
            if i >= len(before):
                out.append({"path": p, "op": "add", "after": copy.deepcopy(after[i])})
            elif i >= len(after):
                out.append({"path": p, "op": "remove", "before": copy.deepcopy(before[i])})
            else:
                out.extend(diff_state(before[i], after[i], p))
    elif before != after:
        out.append({"path": path, "op": "replace", "before": before, "after": after})
    return out
