"""Agent Execution Engine (blueprint Module 4).

Runs one *episode*: scenario initialisation -> agent loop -> tool execution
through the fault middleware -> state monitoring -> final answer. Produces a
fully structured trace plus the replayable action log used for minimization.
"""
from __future__ import annotations

import copy
import json
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from .agents.base import Agent, FinalAnswer, Observation, ProviderError, ToolCall
from .assertions import evaluate
from .environments import Environment, ToolError, make_environment
from .events import EventCollector, VirtualClock, diff_state
from .faults import BLOCKING, FaultInjector, FaultSpec

# Episode state machine (blueprint 4 "Execution states")
TRANSITIONS = {
    "CREATED": {"QUEUED", "INITIALIZING", "CANCELLED"},
    "QUEUED": {"INITIALIZING", "CANCELLED"},
    "INITIALIZING": {"RUNNING", "FAILED"},
    "RUNNING": {"EVALUATING", "TIMED_OUT", "FAILED", "CANCELLED"},
    "TIMED_OUT": {"EVALUATING"},
    "EVALUATING": {"DIAGNOSING", "COMPLETED", "FAILED"},
    "DIAGNOSING": {"COMPLETED", "FAILED"},
    "COMPLETED": set(), "FAILED": set(), "CANCELLED": set(),
}


class IllegalTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self) -> None:
        self.state = "CREATED"
        self.history = ["CREATED"]

    def to(self, new: str) -> None:
        if new not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"{self.state} -> {new}")
        self.state = new
        self.history.append(new)


@dataclass
class Budgets:
    max_steps: int = 30
    max_duration_ms: int = 120_000
    max_identical_calls: int = 4
    max_tokens: int = 60_000

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Budgets:
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class RecordedAction:
    index: int
    kind: str  # "tool" | "final"
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    text: str | None = None
    faults: list[str] = field(default_factory=list)
    event_id: str | None = None


@dataclass
class EpisodeResult:
    run_id: str
    scenario_id: str
    agent: dict[str, Any]
    seed: int
    termination: str  # final_answer | max_steps | loop_guard | timeout | agent_error | provider_error | budget
    events: list[dict[str, Any]]
    initial_state: dict[str, Any]
    final_state: dict[str, Any]
    final_message: str | None
    actions: list[RecordedAction]
    fault_log: list[dict[str, Any]]
    invariant_breaches: list[dict[str, Any]]
    duration_ms: int
    usage: dict[str, int]
    status_history: list[str]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# simulated pricing for built-in agents (per 1K tokens, INR). Real adapters report real usage.
SIM_PRICE_INR = {"input": 0.25, "output": 1.25}


def _sim_usage(rng: random.Random, step: int, obs: Observation | None) -> dict[str, int]:
    ctx = 420 + 140 * step + (len(json.dumps(obs.result if obs and obs.ok else (obs.error if obs else ""), default=str)) // 4)
    return {"input_tokens": ctx, "output_tokens": 38 + rng.randint(0, 45)}


def _repeated_cycle(calls: list[RecordedAction], times: int, max_len: int = 3) -> list[RecordedAction] | None:
    """The shortest cycle of 1..max_len calls that the latest calls repeat `times` times in a row, else None.
    A cycle containing `wait` is backoff (wait -> retry), the correct reaction to rate limits, not a loop:
    the step and time budgets still bound it."""
    for n in range(1, max_len + 1):
        tail = calls[-n * times:]
        if len(tail) < n * times:
            break
        unit = tail[:n]
        if any(c.tool == "wait" for c in unit):
            continue
        if all(c.tool == unit[i % n].tool and c.args == unit[i % n].args for i, c in enumerate(tail)):
            return unit
    return None


def run_episode(
    scenario: dict[str, Any],
    agent: Agent,
    seed: int,
    run_id: str,
    *,
    pinned_faults: dict[int, list[str]] | None = None,
    env: Environment | None = None,
) -> EpisodeResult:
    sm = StateMachine()
    sm.to("INITIALIZING")
    env = env or make_environment(scenario)
    env.reset()
    initial = env.snapshot()
    clock = VirtualClock()
    col = EventCollector(run_id, clock)
    budgets = Budgets.from_dict(scenario.get("budgets"))
    specs = [FaultSpec.from_dict(f, i) for i, f in enumerate(scenario.get("faults", []))]
    injector = FaultInjector(specs, seed, pinned=pinned_faults)
    allowed = set(scenario.get("tools_allowed") or env.tools().keys())
    invariants = scenario.get("invariants", [])
    queries = env.queries()
    rng = random.Random(f"agent:{seed}")
    usage_rng = random.Random(f"usage:{seed}")
    tool_specs = env.tools()

    col.emit("episode_start", actor="environment", metadata={
        "scenario_id": scenario["scenario_id"], "seed": seed, "agent": agent.describe(),
        "environment": scenario["environment"], "faults_planned": [s.to_dict() for s in specs],
        "replay": pinned_faults is not None,
    })
    task = scenario["task"]["user_message"]
    col.emit("user_message", actor="user", message=task)

    context = {"customer_id": initial.get("session", {}).get("customer_id"), "channel": "chat"}
    agent.reset(task, [tool_specs[t].json_schema() for t in sorted(allowed) if t in tool_specs], context, rng)
    sm.to("RUNNING")

    actions: list[RecordedAction] = []
    fault_log: list[dict[str, Any]] = []
    breaches: list[dict[str, Any]] = []
    broken: set[str] = set()
    obs: Observation | None = None
    final_message: str | None = None
    termination = "max_steps"
    tokens = {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0}

    def check_invariants(parent_id: str) -> None:
        for inv in invariants:
            out = evaluate(inv, queries)
            if not out.passed and inv not in broken:
                broken.add(inv)
                ev = col.emit("invariant_breach", actor="evaluator", parent_event_id=parent_id,
                              message=f"Invariant violated: {inv}", metadata=out.to_dict())
                breaches.append({"invariant": inv, "event_id": ev.event_id, "observed": out.observed,
                                 "action_index": len(actions) - 1})

    for step in range(budgets.max_steps):
        sim = _sim_usage(usage_rng, step, obs)
        try:
            action = agent.act(obs)
        except ProviderError as exc:  # the model provider failed, not the agent: the run is inconclusive
            col.emit("guardrail", actor="environment", message=f"Model provider error: {exc}",
                     metadata={"provider_error": True})
            termination = "provider_error"
            break
        except Exception as exc:  # the agent itself crashed
            col.emit("guardrail", actor="environment", message=f"Agent raised {type(exc).__name__}: {exc}")
            termination = "agent_error"
            break
        clock.advance(380 + usage_rng.randint(0, 420))
        # model-backed agents report what they really spent; scripted agents get the simulated estimate
        u = agent.pop_usage() if agent.reports_usage else {**sim, "llm_calls": 1}
        tokens["input_tokens"] += u["input_tokens"]
        tokens["output_tokens"] += u["output_tokens"]
        tokens["llm_calls"] += u["llm_calls"]
        meta = {"step": step + 1, "action": type(action).__name__}
        if getattr(agent, "model_version", None):
            meta["model"] = agent.model_version
        step_ev = col.emit("agent_step", usage={k: u[k] for k in ("input_tokens", "output_tokens")}, metadata=meta)

        if isinstance(action, FinalAnswer):
            final_message = action.text
            fe = col.emit("final_answer", parent_event_id=step_ev.event_id, message=action.text)
            actions.append(RecordedAction(len(actions), "final", text=action.text, event_id=fe.event_id))
            termination = "final_answer"
            break

        if not isinstance(action, ToolCall):
            col.emit("guardrail", actor="environment", parent_event_id=step_ev.event_id,
                     message=f"Agent returned {type(action).__name__}; expected ToolCall or FinalAnswer")
            termination = "agent_error"
            break
        idx = len(actions)
        call_ev = col.emit("tool_call", parent_event_id=step_ev.event_id,
                           tool={"name": action.tool, "arguments": copy.deepcopy(action.args)})
        rec = RecordedAction(idx, "tool", action.tool, copy.deepcopy(action.args), event_id=call_ev.event_id)
        actions.append(rec)

        if action.tool not in allowed or action.tool not in tool_specs:
            err = {"code": "permission_denied", "message": f"Tool '{action.tool}' is not permitted in this scenario"}
            col.emit("guardrail", actor="environment", parent_event_id=call_ev.event_id, message=err["message"],
                     metadata={"violation": "unauthorized_tool"})
            col.emit("tool_result", actor="environment", parent_event_id=call_ev.event_id,
                     tool={"name": action.tool}, result={"status": "error", "error": err})
            obs = Observation(action.tool, action.args, False, error=err)
            continue

        spec = tool_specs[action.tool]
        fired = injector.decide(action.tool, idx)
        rec.faults = [f.fault_id for f in fired]
        for f in fired:
            fev = col.emit("fault_activated", actor="fault_engine", parent_event_id=call_ev.event_id,
                           message=f"{f.type} on {f.target}", metadata={**f.to_dict(), "seed": seed, "action_index": idx})
            fault_log.append({**f.to_dict(), "event_id": fev.event_id, "action_index": idx, "t_ms": clock.now_ms})

        # external actor races the agent
        for f in (f for f in fired if f.type == "concurrent_update"):
            before = env.snapshot()
            for m in f.params.get("mutations", []):
                env.apply_mutation(m["path"], m["value"])
            mev = col.emit("state_mutation", actor="external", message=f.params.get("description", "external update"),
                           state_diff=diff_state(before, env.snapshot()), metadata={"fault_id": f.fault_id})
            check_invariants(mev.event_id)

        before = env.snapshot()
        blocking = next((f for f in fired if f.type in BLOCKING), None)
        ok, result, error = True, None, None
        if blocking:
            clock.advance(blocking.delay_ms)
            ok = False
            error = {
                "timeout_before_commit": {"code": "timeout", "message": "Request timed out"},
                "transient_error": {"code": "service_unavailable", "message": "HTTP 503 Service Unavailable"},
                "rate_limit": {"code": "rate_limited", "message": "HTTP 429 Too Many Requests",
                               "retry_after_ms": blocking.params.get("retry_after_ms", 2000)},
            }[blocking.type]
        else:
            stale = next((f for f in fired if f.type == "stale_read"), None)
            try:
                if stale and not spec.mutating:
                    result = env.call_on(initial, action.tool, action.args)
                else:
                    result = env.call(action.tool, action.args)
            except ToolError as te:
                ok, error = False, te.to_dict()
            if action.tool == "wait":
                clock.advance(int(action.args.get("ms", 1000)))
            clock.advance(spec.latency_ms)
            for f in fired:
                if f.type == "timeout_after_commit":
                    clock.advance(f.delay_ms)
                    ok, result, error = False, None, {"code": "timeout", "message": "Request timed out"}
                elif f.type == "malformed_response" and ok and isinstance(result, dict):
                    for fld in f.params.get("drop_fields", []):
                        result.pop(fld, None)
                elif f.type == "prompt_injection" and ok:
                    result = _inject(result, f.params)

        diff = diff_state(before, env.snapshot())
        res_ev = col.emit(
            "tool_result", actor="environment", parent_event_id=call_ev.event_id,
            tool={"name": action.tool}, duration_ms=clock.now_ms - call_ev.t_ms,
            result={"status": "ok", "output": copy.deepcopy(result)} if ok else {"status": "error", "error": error},
            state_diff=diff or None, metadata={"faults": rec.faults, "mutating": spec.mutating},
        )
        if diff:
            check_invariants(res_ev.event_id)
        obs = Observation(action.tool, copy.deepcopy(action.args), ok, copy.deepcopy(result), error)

        # loop guard: the same call, or the same short cycle of calls (A->B->A->B), repeated with identical arguments
        cycle = _repeated_cycle([a for a in actions if a.kind == "tool"], budgets.max_identical_calls)
        if cycle:
            pattern = " -> ".join(a.tool for a in cycle)
            col.emit("budget_exceeded", actor="environment", message=(
                f"Loop guard: {pattern} repeated {budgets.max_identical_calls}x with identical arguments"),
                metadata={"budget": "max_identical_calls", "cycle": [a.tool for a in cycle]})
            termination = "loop_guard"
            break
        if clock.now_ms > budgets.max_duration_ms:
            col.emit("budget_exceeded", actor="environment", message="Episode exceeded max duration",
                     metadata={"budget": "max_duration_ms"})
            termination = "timeout"
            break
        if tokens["input_tokens"] + tokens["output_tokens"] > budgets.max_tokens:
            col.emit("budget_exceeded", actor="environment", message="Token budget exhausted",
                     metadata={"budget": "max_tokens"})
            termination = "budget"
            break
    else:
        col.emit("budget_exceeded", actor="environment", message=f"Reached max_steps={budgets.max_steps}",
                 metadata={"budget": "max_steps"})

    sm.to("TIMED_OUT" if termination == "timeout" else "EVALUATING")
    if sm.state == "TIMED_OUT":
        sm.to("EVALUATING")
    col.emit("episode_end", actor="environment", metadata={"termination": termination, "steps": len(actions)})

    return EpisodeResult(
        run_id=run_id, scenario_id=scenario["scenario_id"], agent=agent.describe(), seed=seed,
        termination=termination, events=col.as_dicts(), initial_state=initial, final_state=env.snapshot(),
        final_message=final_message, actions=actions, fault_log=fault_log, invariant_breaches=breaches,
        duration_ms=clock.now_ms, usage=tokens, status_history=sm.history,
    )


def _inject(result: Any, params: dict[str, Any]) -> Any:
    payload = params["payload"]
    field_name = params.get("field")
    if isinstance(result, dict):
        if field_name and isinstance(result.get(field_name), str):
            result[field_name] = result[field_name] + "\n\n" + payload
        elif "results" in result and isinstance(result["results"], list):
            result["results"] = list(result["results"]) + [{"article": params.get("article", "faq-update"), "content": payload}]
        else:
            result[field_name or "note"] = payload
    return result


def episode_cost_inr(usage: dict[str, int]) -> float:
    return round(usage.get("input_tokens", 0) / 1000 * SIM_PRICE_INR["input"]
                 + usage.get("output_tokens", 0) / 1000 * SIM_PRICE_INR["output"], 4)
