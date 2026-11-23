"""Environment, fault injection, runner and assertion language."""
import pytest

from crashlab.agents import SupportAgentV1, SupportAgentV2
from crashlab.assertions import AssertionSyntaxError, evaluate
from crashlab.environments import ToolError, make_environment
from crashlab.faults import FaultInjector, FaultSpec
from crashlab.runner import IllegalTransition, StateMachine, run_episode
from crashlab.scenarios import scenario_by_id


def env():
    return make_environment({"environment": "ecommerce", "initial_state": {"fixture": "base_store"}})


def test_refund_is_pending_not_settled():
    e = env()
    e.call("issue_refund", {"order_id": "ORD-1001", "amount": 4999})
    assert e.payment_status("ORD-1001") == "REFUND_PENDING"
    assert e.refund_count("ORD-1001") == 1


def test_idempotency_key_deduplicates_but_blind_retry_does_not():
    e = env()
    e.call("issue_refund", {"order_id": "ORD-1001", "amount": 4999, "idempotency_key": "k"})
    r = e.call("issue_refund", {"order_id": "ORD-1001", "amount": 4999, "idempotency_key": "k"})
    assert r["deduplicated"] and e.refund_count("ORD-1001") == 1
    e.call("issue_refund", {"order_id": "ORD-1001", "amount": 4999})
    assert e.refund_count("ORD-1001") == 2


def test_business_errors_and_bad_arguments():
    e = env()
    with pytest.raises(ToolError) as ex:
        e.call("cancel_order", {"order_id": "ORD-1001"})
    assert ex.value.code == "not_cancellable"
    with pytest.raises(ToolError) as ex:
        e.call("issue_refund", {"order_id": "ORD-1001"})
    assert ex.value.code == "invalid_arguments"


def test_reset_is_deterministic():
    e = env()
    snap = e.snapshot()
    e.call("cancel_order", {"order_id": "ORD-1002"})
    e.reset()
    assert e.snapshot() == snap


def test_fault_injector_on_call_and_max_activations():
    inj = FaultInjector([FaultSpec("F", "transient_error", "issue_refund", on_call=2, max_activations=2)], seed=1)
    fired = [bool(inj.decide("issue_refund", i)) for i in range(5)]
    assert fired == [False, True, True, False, False]


def test_fault_probability_is_seeded():
    spec = [FaultSpec("F", "transient_error", "get_order", on_call=None, max_activations=99, probability=0.5)]
    ia, ib = FaultInjector(spec, 7), FaultInjector(spec, 7)
    a = [bool(ia.decide("get_order", i)) for i in range(20)]
    b = [bool(ib.decide("get_order", i)) for i in range(20)]
    assert a == b and 0 < sum(a) < 20


def test_assertions_are_safe_and_correct():
    q = env().queries()
    assert evaluate("refund_count('ORD-1001') == 0 and order_status('ORD-1002') in ('PROCESSING',)", q).passed
    assert not evaluate("refund_total() > 0", q).passed
    for bad in ["__import__('os').system('x')", "open('f')", "refund_count.__class__", "[x for x in ()]"]:
        with pytest.raises(AssertionSyntaxError):
            evaluate(bad, q)


def test_timeout_after_commit_commits_state_but_reports_timeout():
    ep = run_episode(scenario_by_id("ACL-ECOM-0002"), SupportAgentV1(), 1, "t")
    refund_results = [e for e in ep.events if e["event_type"] == "tool_result" and e["tool"]["name"] == "issue_refund"]
    assert refund_results[0]["result"]["status"] == "error" and refund_results[0]["state_diff"]
    assert ep.final_state["refunds"].__len__() == 2
    assert {b["invariant"] for b in ep.invariant_breaches} >= {"refund_count('ORD-1001') <= 1"}


def test_concurrent_update_and_stale_read():
    ep = run_episode(scenario_by_id("ACL-ECOM-0015"), SupportAgentV2(), 1, "t")
    assert any(e["event_type"] == "state_mutation" and e["actor"] == "external" for e in ep.events)
    assert ep.final_state["orders"]["ORD-1002"]["status"] == "SHIPPED"
    ep = run_episode(scenario_by_id("ACL-ECOM-0003"), SupportAgentV2(), 1, "t")
    stale = [e for e in ep.events if e["event_type"] == "tool_result" and e["tool"]["name"] == "get_refund_status"][0]
    assert stale["result"]["output"]["refunds"] == []  # served from the initial snapshot
    assert len(ep.final_state["refunds"]) == 1      # idempotency key saved the retry


def test_loop_guard_stops_runaway_agent():
    ep = run_episode(scenario_by_id("ACL-ECOM-0006"), SupportAgentV1(), 1, "t")
    assert ep.termination == "loop_guard"


def test_identical_seed_identical_trace():
    sc = scenario_by_id("ACL-ECOM-0017")
    a = run_episode(sc, SupportAgentV1(), 99, "x").events
    b = run_episode(sc, SupportAgentV1(), 99, "x").events
    assert a == b


def test_unauthorized_tool_is_blocked():
    sc = dict(scenario_by_id("ACL-ECOM-0001"))
    sc["tools_allowed"] = [t for t in sc["tools_allowed"] if t != "search_kb"]
    ep = run_episode(sc, SupportAgentV1(), 1, "t")
    assert any(e["event_type"] == "guardrail" for e in ep.events)


def test_state_machine_rejects_illegal_transitions():
    sm = StateMachine()
    sm.to("INITIALIZING")
    with pytest.raises(IllegalTransition):
        sm.to("COMPLETED")


def test_loop_guard_catches_cycles_not_just_repeats():
    # Review: A->B->A->B used to slip past the identical-call guard.
    from crashlab.agents.base import Agent, ToolCall
    from crashlab.runner import run_episode
    from crashlab.scenarios import scenario_by_id

    class PingPong(Agent):
        def reset(self, task, tools, context, rng):
            self.i = 0

        def act(self, observation):
            self.i += 1
            return ToolCall("get_order" if self.i % 2 else "get_refund_status", {"order_id": "ORD-1001"})

    ep = run_episode(scenario_by_id("ACL-ECOM-0001"), PingPong(), 1, "pingpong")
    assert ep.termination == "loop_guard" and len(ep.actions) == 8  # 4 repeats of a 2-call cycle
