"""Regression-test generation and execution (blueprint Module 6 D).

A discovered failure becomes a permanent, versioned scenario: the original
task and initial state, *only* the faults that the minimized reproduction
proved necessary, and assertions covering the violated rule. It re-runs the
real agent (not a replay), so it protects every future release.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from .diagnosis import Failure
from .minimizer import Reproduction

NAMES = {
    "DUPLICATE_ACTION": "{tool}_{fault}_duplicate_protection",
    "SECURITY_VIOLATION": "{slug}_resistance",
    "FALSE_COMPLETION": "{slug}_truthful_reporting",
    "POLICY_VIOLATION": "{slug}_policy_gate",
    "INFINITE_LOOP": "{tool}_{fault}_backoff",
    "STATE_TRACKING_FAILURE": "{tool}_{fault}_validation",
    "FAILURE_TO_RECOVER": "{slug}_safe_retry",
    "PLANNING_FAILURE": "{slug}_goal_state",
}
_STOP = {"a", "an", "the", "in", "on", "of", "to", "with", "is", "its", "and", "for", "after", "while", "be",
         "must", "needs", "asks", "then", "times", "out", "returns"}


def regression_name(failure: Failure, scenario: dict[str, Any]) -> str:
    f = failure.root_cause.get("triggering_fault") or {}
    tool = (f.get("target") or "refund").replace("issue_", "").replace("get_", "").replace("check_", "")
    fault = {"timeout_after_commit": "timeout", "timeout_before_commit": "timeout", "rate_limit": "rate_limit",
             "malformed_response": "malformed", "stale_read": "stale_read", "transient_error": "503"
             }.get(f.get("type", ""), "repeat")
    words = "".join(ch if ch.isalnum() else " " for ch in scenario.get("title", scenario["scenario_id"]).lower()).split()
    slug = "_".join([w for w in words if w not in _STOP][:4])
    return NAMES.get(failure.category, "{slug}_regression").format(tool=tool, fault=fault, slug=slug)


def generate_regression(failure: Failure, scenario: dict[str, Any], repro: Reproduction | None,
                        activated: set[str] | None = None) -> dict[str, Any]:
    """``activated`` = faults that fired in the source episode. They are all kept, because the
    scenario's expected state is defined under them; the minimal subset is recorded as metadata."""
    minimal = sorted({fid for s in (repro.steps if repro else []) for fid in s["faults"]})
    needed = set(activated) if activated is not None else {f.get("fault_id") for f in scenario.get("faults", [])}
    reg = copy.deepcopy(scenario)
    h = hashlib.sha1(f"{failure.signature}|{scenario['scenario_id']}".encode(), usedforsecurity=False).hexdigest()[:6].upper()
    reg["scenario_id"] = f"REG-ECOM-{scenario['scenario_id'].split('-')[-1]}-{h}"
    reg["version"] = "1.0.0"
    reg["category"] = "regression"
    reg["split"] = "test"
    reg["title"] = f"Regression: {failure.title}"
    reg["faults"] = [f for i, f in enumerate(scenario.get("faults", []))
                     if f.get("fault_id", f"F-{i + 1:03d}") in needed]
    extra: list[str] = []
    reg["expected_state"] = list(dict.fromkeys(scenario["expected_state"]))
    reg["invariants"] = list(dict.fromkeys(scenario.get("invariants", []) + extra))
    reg["tags"] = sorted(set(scenario.get("tags", [])) | {"regression", failure.category.lower()})
    reg["regression"] = {
        "name": regression_name(failure, scenario),
        "source_failure_id": failure.failure_id,
        "source_run_id": failure.run_id,
        "source_scenario_id": scenario["scenario_id"],
        "signature": failure.signature,
        "category": failure.category,
        "failing_checks": [c["name"] for c in failure.checks],
        "discovered_on": failure.agent,
        "minimal_faults": minimal,
        "minimized_steps": repro.minimized_steps if repro else None,
        "original_steps": repro.original_steps if repro else None,
        "reproduction": repro.steps if repro else None,
    }
    return reg
