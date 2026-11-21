"""Automatic failure minimization (blueprint Module 6 C).

Delta debugging (Zeller & Hildebrandt's ``ddmin``) over the recorded action
sequence of a failed episode. Each candidate subsequence is *replayed*
against a freshly reset environment with the original seed. Faults are
pinned to the action that originally triggered them, so removing earlier
steps cannot shift a timeout onto a different call - the precondition the
blueprint warns about. A candidate is kept only if it reproduces the same
failure signature (category + failing check).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any
from collections.abc import Callable

from .agents import ReplayAgent
from .diagnosis import Failure, diagnose, normalise_detail
from .evaluators import evaluate_episode
from .runner import EpisodeResult, RecordedAction, run_episode


@dataclass
class Reproduction:
    failure_id: str
    signature: str
    category: str
    original_steps: int
    minimized_steps: int
    steps: list[dict[str, Any]]
    history: list[int]
    replays: int
    deterministic: bool
    reproduces_original: bool
    minimized_events: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def replay(scenario: dict[str, Any], actions: list[RecordedAction], seed: int, agent: dict[str, Any],
           run_id: str = "replay") -> EpisodeResult:
    pinned = {i: list(a.faults) for i, a in enumerate(actions) if a.faults}
    return run_episode(scenario, ReplayAgent(actions, agent), seed, run_id, pinned_faults=pinned)


def _reproduces(target: Failure) -> Callable[[list[Failure]], bool]:
    """Same failure == same category, same root check, same observed detail."""
    root = next(c for c in target.checks if c.get("role", "root") == "root")
    key = (root["name"], normalise_detail(root.get("detail", "")))

    def ok(fs: list[Failure]) -> bool:
        return any(f.category == target.category and any(
            (c["name"], normalise_detail(c.get("detail", ""))) == key for c in f.checks) for f in fs)

    return ok


def ddmin(items: list[Any], test: Callable[[list[Any]], bool], history: list[int]) -> list[Any]:
    n = 2
    while len(items) >= 2:
        chunk = math.ceil(len(items) / n)
        subsets = [items[i:i + chunk] for i in range(0, len(items), chunk)]
        reduced = False
        for s in subsets:
            if test(s):
                items, n, reduced = s, 2, True
                break
        if not reduced:
            for i in range(len(subsets)):
                comp = [x for j, s in enumerate(subsets) if j != i for x in s]
                if comp and test(comp):
                    items, n, reduced = comp, max(n - 1, 2), True
                    break
        if reduced:
            history.append(len(items))
            continue
        if n >= len(items):
            break
        n = min(len(items), 2 * n)
    if len(items) == 1 and test([]):
        items = []
    return items


def minimize(scenario: dict[str, Any], ep: EpisodeResult, failure: Failure) -> Reproduction:
    matches = _reproduces(failure)
    replays = 0
    cache: dict[tuple[int, ...], bool] = {}

    def test(subset: list[RecordedAction]) -> bool:
        nonlocal replays
        key = tuple(a.index for a in subset)
        if key not in cache:
            replays += 1
            r = replay(scenario, subset, ep.seed, ep.agent)
            cache[key] = matches(diagnose(scenario, r, evaluate_episode(scenario, r)))
        return cache[key]

    full_ok = test(list(ep.actions))
    history = [len(ep.actions)]
    minimal = ddmin(list(ep.actions), test, history) if full_ok else list(ep.actions)

    final = replay(scenario, minimal, ep.seed, ep.agent, run_id=f"{ep.run_id}:min")
    det = all(test(minimal) for _ in range(2)) and _same(final, replay(scenario, minimal, ep.seed, ep.agent,
                                                                     run_id=f"{ep.run_id}:min"))
    steps = [{"index": i, "original_index": a.index, "kind": a.kind, "tool": a.tool, "args": a.args,
              "text": a.text, "faults": a.faults} for i, a in enumerate(minimal)]
    return Reproduction(
        failure_id=failure.failure_id, signature=failure.signature, category=failure.category,
        original_steps=len(ep.actions), minimized_steps=len(minimal), steps=steps, history=history,
        replays=replays, deterministic=det, reproduces_original=full_ok, minimized_events=final.events,
    )


def _same(a: EpisodeResult, b: EpisodeResult) -> bool:
    strip = lambda evs: [{k: v for k, v in e.items() if k not in ("event_id", "run_id", "parent_event_id")} for e in evs]
    return strip(a.events) == strip(b.events)
