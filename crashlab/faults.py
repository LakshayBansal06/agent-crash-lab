"""Fault Injection Engine (blueprint Module 3).

Faults are declared in scenario JSON and applied by middleware that sits
between the agent and the environment's tools. Every activation is logged with
its virtual timestamp, activation condition and the episode seed so the run
can be replayed exactly.

Supported fault types
---------------------
timeout_after_commit   tool executes, agent receives a timeout
timeout_before_commit  tool does not execute, agent receives a timeout
transient_error        HTTP 503 - tool does not execute
rate_limit             HTTP 429 with retry_after_ms - tool does not execute
malformed_response     tool executes, listed fields are dropped from the reply
stale_read             read is served from the episode's *initial* snapshot
prompt_injection       tool executes, attacker text is appended to a field
concurrent_update      an external actor mutates state before the call
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

FAULT_TYPES = {
    "timeout_after_commit": "Network",
    "timeout_before_commit": "Network",
    "transient_error": "Network",
    "rate_limit": "API",
    "malformed_response": "API",
    "stale_read": "State",
    "concurrent_update": "State",
    "prompt_injection": "Security",
}

#: faults that stop the real tool from running
BLOCKING = {"timeout_before_commit", "transient_error", "rate_limit"}


@dataclass
class FaultSpec:
    fault_id: str
    type: str
    target: str
    on_call: int | None = 1        # nth call of the target tool (1-based); None = every call
    max_activations: int = 1
    probability: float = 1.0
    delay_ms: int = 0
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any], idx: int = 0) -> FaultSpec:
        if d["type"] not in FAULT_TYPES:
            raise ValueError(f"unknown fault type {d['type']!r}")
        return cls(
            fault_id=d.get("fault_id", f"F-{idx + 1:03d}"),
            type=d["type"],
            target=d["target"],
            on_call=d.get("on_call", 1),
            max_activations=d.get("max_activations", 1 if d.get("on_call", 1) is not None else 99),
            probability=float(d.get("probability", 1.0)),
            delay_ms=int(d.get("delay_ms", _default_delay(d["type"]))),
            params=d.get("params", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id, "type": self.type, "target": self.target,
            "on_call": self.on_call, "max_activations": self.max_activations,
            "probability": self.probability, "delay_ms": self.delay_ms, "params": self.params,
        }


def _default_delay(t: str) -> int:
    return {"timeout_after_commit": 12000, "timeout_before_commit": 12000,
            "transient_error": 250, "rate_limit": 50}.get(t, 0)


class FaultInjector:
    """Decides which faults fire on a given tool call.

    Two modes:
    * *live*   - decisions come from the specs (call counters + seeded RNG)
    * *pinned* - decisions come from a map ``action_index -> [fault_id]``
      recorded during a live run. Replay and minimization use this so that a
      fault stays attached to the *same decision* even after other steps are
      removed (blueprint 5.6.C: "preserve the relevant fault timing").
    """

    def __init__(self, specs: list[FaultSpec], seed: int, pinned: dict[int, list[str]] | None = None) -> None:
        self.specs = {s.fault_id: s for s in specs}
        self.rng = random.Random(f"faults:{seed}")
        self.pinned = pinned
        self.call_counts: dict[str, int] = {}
        self.activations: dict[str, int] = {}

    def decide(self, tool: str, action_index: int) -> list[FaultSpec]:
        self.call_counts[tool] = self.call_counts.get(tool, 0) + 1
        n = self.call_counts[tool]
        if self.pinned is not None:
            return [self.specs[f] for f in self.pinned.get(action_index, []) if f in self.specs]
        fired: list[FaultSpec] = []
        for spec in self.specs.values():
            if spec.target != tool:
                continue
            if self.activations.get(spec.fault_id, 0) >= spec.max_activations:
                continue
            if spec.on_call is not None and n != spec.on_call and spec.max_activations == 1:
                continue
            if spec.on_call is not None and n < spec.on_call:
                continue
            # probability is sampled for every eligible call -> reproducible via seed
            if self.rng.random() >= spec.probability:
                continue
            self.activations[spec.fault_id] = self.activations.get(spec.fault_id, 0) + 1
            fired.append(spec)
        return fired
