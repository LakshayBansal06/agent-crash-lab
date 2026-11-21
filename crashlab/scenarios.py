"""Scenario dataset: loading, validation pipeline and splits (blueprint 6.2-6.4).

Validation stages, mirroring the authoring pipeline in the blueprint:
  1. JSON-schema validation
  2. tool / environment validation   (tools and fault targets exist)
  3. assertion validation            (expressions compile against env queries)
  4. initial-state validation        (fixture builds; invariants hold at t=0)
  5. deterministic replay            (same seed -> byte-identical trace)
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any
from collections.abc import Iterable

import jsonschema

from .assertions import AssertionSyntaxError, compile_assertion, evaluate
from .environments import make_environment

DATA_DIR = Path(__file__).parent / "data"
SCENARIO_DIR = DATA_DIR / "scenarios"


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    return json.loads((DATA_DIR / "schemas" / "scenario.schema.json").read_text())


def load_scenarios(paths: Iterable[Path] | None = None) -> list[dict[str, Any]]:
    files = sorted(paths) if paths else sorted(SCENARIO_DIR.glob("*.json"))
    return [json.loads(Path(p).read_text()) for p in files]


def scenario_by_id(sid: str) -> dict[str, Any]:
    for s in load_scenarios():
        if s["scenario_id"] == sid:
            return s
    raise KeyError(sid)


def fingerprint(scenario: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(scenario, sort_keys=True).encode()).hexdigest()[:12]


class ValidationReport(dict):
    @property
    def ok(self) -> bool:
        return not self["errors"]


def validate_scenario(scenario: dict[str, Any], *, replay: bool = True) -> ValidationReport:
    errors: list[str] = []
    stages: dict[str, str] = {}

    try:
        jsonschema.validate(scenario, schema())
        stages["schema"] = "pass"
    except jsonschema.ValidationError as exc:
        stages["schema"] = "fail"
        errors.append(f"schema: {exc.message} at /{'/'.join(map(str, exc.absolute_path))}")
        return ValidationReport(scenario_id=scenario.get("scenario_id"), stages=stages, errors=errors)

    try:
        env = make_environment(scenario)
        stages["initial_state"] = "pass"
    except Exception as exc:
        stages["initial_state"] = "fail"
        errors.append(f"initial_state: {exc}")
        return ValidationReport(scenario_id=scenario["scenario_id"], stages=stages, errors=errors)

    tools = env.tools()
    bad = [t for t in scenario["tools_allowed"] if t not in tools]
    bad += [f"fault target {f['target']}" for f in scenario.get("faults", []) if f["target"] not in tools]
    stages["tools"] = "fail" if bad else "pass"
    errors += [f"tools: unknown {b}" for b in bad]

    q = env.queries()
    for expr in scenario["expected_state"] + scenario["invariants"]:
        try:
            compile_assertion(expr, set(q))
        except AssertionSyntaxError as exc:
            errors.append(f"assertion: {exc}")
    stages["assertions"] = "fail" if any(e.startswith("assertion") for e in errors) else "pass"

    if stages["assertions"] == "pass":
        broken = [i for i in scenario["invariants"] if not evaluate(i, q).passed]
        if broken:
            errors.append(f"invariants violated at t=0: {broken}")
        stages["invariants_t0"] = "fail" if broken else "pass"

    if replay and not errors:
        from .agents import SupportAgentV2
        from .runner import run_episode

        a = run_episode(scenario, SupportAgentV2(), 7, "validate")
        b = run_episode(scenario, SupportAgentV2(), 7, "validate")
        same = json.dumps(a.events, sort_keys=True, default=str) == json.dumps(b.events, sort_keys=True, default=str)
        stages["deterministic_replay"] = "pass" if same else "fail"
        if not same:
            errors.append("replay: identical seeds produced different traces")

    return ValidationReport(scenario_id=scenario["scenario_id"], stages=stages, errors=errors,
                            fingerprint=fingerprint(scenario))


def split(scenarios: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [s for s in scenarios if s.get("split", "dev") == name]
