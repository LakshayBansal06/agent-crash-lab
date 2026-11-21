"""Python SDK (blueprint Module 1 A).

Test *your* agent - whatever framework it uses - against Crash Lab
scenarios. Your code drives the tools; the platform injects faults, records
the trace and evaluates the outcome.

    from crashlab.sdk import CrashLab, ToolCallFailed

    lab = CrashLab()                                   # local, in-process

    def my_agent(task, tools, context):
        order = tools.get_order(order_id="ORD-1001")
        try:
            tools.issue_refund(order_id="ORD-1001", amount=order["amount"], idempotency_key="rf-1001")
        except ToolCallFailed as e:
            if e.code == "timeout":
                ...
        return "Your refund has been initiated."

    report = lab.test(my_agent, scenarios=["ACL-ECOM-0002"], repeats=5)
    print(report.summary())

Or the context-manager form, for agents that run their own loop:

    with lab.episode("ACL-ECOM-0002", seed=7) as ep:
        ep.tools.get_order(order_id="ORD-1001")
        ep.finish("...")
    print(ep.report["passed"])

Set ``base_url`` + ``api_key`` to upload every episode to a Crash Lab server.
"""
from __future__ import annotations

import queue
import threading
from typing import Any
from collections.abc import Callable

from .agents.base import Agent, FinalAnswer, ToolCall
from .diagnosis import diagnose
from .evaluators import evaluate_episode
from .experiment import stable_seed
from .runner import episode_cost_inr, run_episode
from .scenarios import load_scenarios, scenario_by_id

_FINISH = object()


class ToolCallFailed(Exception):
    def __init__(self, tool: str, error: dict[str, Any]) -> None:
        super().__init__(f"{tool}: {error.get('message')}")
        self.tool, self.error, self.code = tool, error, error.get("code")


class _BridgeAgent(Agent):
    """Agent whose decisions come from user code running on another thread."""

    framework = "sdk"

    def __init__(self, name: str, version: str) -> None:
        self.name, self.version = name, version
        self.actions: queue.Queue = queue.Queue()
        self.observations: queue.Queue = queue.Queue()
        self.started = threading.Event()
        self.done = threading.Event()

    def reset(self, task, tools, context, rng):
        self.task, self.tool_schemas, self.context = task, tools, context
        self.started.set()

    def act(self, observation):
        if observation is not None:
            self.observations.put(observation)
        item = self.actions.get()
        return item


class _Tools:
    def __init__(self, bridge: _BridgeAgent, names: list[str]) -> None:
        self._bridge, self._names = bridge, set(names)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)

        def call(**kwargs: Any) -> Any:
            if self._bridge.done.is_set():
                raise ToolCallFailed(name, {"code": "episode_terminated", "message": "episode already ended"})
            self._bridge.actions.put(ToolCall(name, kwargs))
            obs = self._bridge.observations.get(timeout=30)
            if obs is None:
                raise ToolCallFailed(name, {"code": "episode_terminated",
                                            "message": "episode stopped by a budget or loop guard"})
            if not obs.ok:
                raise ToolCallFailed(name, obs.error or {})
            return obs.result

        return call

    def __dir__(self):
        return sorted(self._names)


class Episode:
    def __init__(self, lab: CrashLab, scenario: dict[str, Any], seed: int, agent_name: str, agent_version: str,
                 run_id: str) -> None:
        self.lab, self.scenario, self.seed, self.run_id = lab, scenario, seed, run_id
        self.bridge = _BridgeAgent(agent_name, agent_version)
        self.report: dict[str, Any] | None = None
        self._result: dict[str, Any] = {}
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self._result["ep"] = run_episode(self.scenario, self.bridge, self.seed, self.run_id)
        except Exception as exc:  # pragma: no cover
            self._result["error"] = exc
        finally:
            self.bridge.done.set()
            self.bridge.observations.put(None)

    def __enter__(self) -> Episode:
        self._thread.start()
        self.bridge.started.wait(5)
        self.task = self.bridge.task
        self.context = self.bridge.context
        self.tools = _Tools(self.bridge, [t["name"] for t in self.bridge.tool_schemas])
        self.tool_schemas = self.bridge.tool_schemas
        return self

    def finish(self, message: str) -> None:
        if not self.bridge.done.is_set():
            self.bridge.actions.put(FinalAnswer(message))

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._thread.is_alive() and "ep" not in self._result:
            self.bridge.actions.put(FinalAnswer("" if exc else "(no final message)"))
        self._thread.join(30)
        if "error" in self._result:
            raise self._result["error"]
        ep = self._result["ep"]
        card = evaluate_episode(self.scenario, ep)
        fails = diagnose(self.scenario, ep, card)
        self.report = {
            "run_id": ep.run_id, "experiment_id": self.lab.experiment_id, "agent_ref": f"{ep.agent['name']}@{ep.agent['version']}",
            "agent": ep.agent, "scenario_id": self.scenario["scenario_id"], "scenario_title": self.scenario.get("title"),
            "category": self.scenario["category"], "seed": self.seed, "repeat": 0, "passed": card.passed,
            "score": card.score, "dimensions": card.dimensions, "termination": ep.termination, "steps": len(ep.actions),
            "duration_ms": ep.duration_ms, "usage": ep.usage, "cost_inr": episode_cost_inr(ep.usage),
            "final_message": ep.final_message, "faults_activated": [f["fault_id"] for f in ep.fault_log],
            "failure_ids": [f.failure_id for f in fails], "primary_category": fails[0].category if fails else None,
            "scorecard": card.to_dict(), "events": ep.events, "initial_state": ep.initial_state,
            "final_state": ep.final_state, "failures": [f.to_dict() for f in fails],
        }
        self.lab._upload(self.report)


class Report:
    def __init__(self, runs: list[dict[str, Any]]) -> None:
        self.runs = runs

    @property
    def success_rate(self) -> float:
        return sum(r["passed"] for r in self.runs) / len(self.runs) if self.runs else 0.0

    @property
    def failures(self) -> list[dict[str, Any]]:
        return [f for r in self.runs for f in r["failures"]]

    def summary(self) -> str:
        lines = [f"{len(self.runs)} episodes · success {self.success_rate:.0%}"]
        for f in self.failures:
            lines.append(f"  ✗ [{f['severity']}] {f['scenario_id']} {f['title']} - {f['root_cause']['hypothesis']}")
        return "\n".join(lines)


class CrashLab:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, project_id: str = "default",
                 experiment_id: str = "sdk-local") -> None:
        self.base_url, self.api_key, self.project_id, self.experiment_id = base_url, api_key, project_id, experiment_id

    def scenarios(self) -> list[dict[str, Any]]:
        return load_scenarios()

    def episode(self, scenario: str | dict[str, Any], seed: int = 0, agent_name: str = "my-agent",
                agent_version: str = "dev", run_id: str | None = None) -> Episode:
        sc = scenario_by_id(scenario) if isinstance(scenario, str) else scenario
        return Episode(self, sc, seed, agent_name, agent_version,
                       run_id or f"{self.experiment_id}.{agent_version}.{sc['scenario_id'][-4:]}.s{seed}")

    def test(self, agent_fn: Callable[[str, Any, dict[str, Any]], str], scenarios: list[str] | None = None,
             repeats: int = 3, agent_name: str = "my-agent", agent_version: str = "dev", base_seed: int = 2026) -> Report:
        ids = scenarios or [s["scenario_id"] for s in load_scenarios()]
        runs = []
        for sid in ids:
            for r in range(repeats):
                seed = stable_seed(base_seed, sid, r)
                with self.episode(sid, seed, agent_name, agent_version,
                                  run_id=f"{self.experiment_id}.{agent_version}.{sid[-4:]}.r{r}") as ep:
                    try:
                        msg = agent_fn(ep.task, ep.tools, ep.context)
                    except ToolCallFailed as exc:
                        msg = f"I ran into a problem: {exc}"
                    ep.finish(msg or "")
                ep.report["repeat"] = r
                runs.append(ep.report)
        return Report(runs)

    def _upload(self, report: dict[str, Any]) -> None:
        if not self.base_url:
            return
        import httpx

        httpx.post(f"{self.base_url.rstrip('/')}/api/v1/ingest/runs", json=report, timeout=30,
                   headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}).raise_for_status()
