"""Evaluators, diagnosis, minimization, regression generation and metrics."""
import math

import pytest

from crashlab.agents import SupportAgentV1, SupportAgentV2
from crashlab.diagnosis import diagnose
from crashlab.evaluators import evaluate_episode, extract_claims
from crashlab.experiment import run_experiment, run_regressions
from crashlab.metrics import mcnemar_exact, pass_at_k, pass_hat_k, wilson
from crashlab.minimizer import ddmin, minimize
from crashlab.regression import generate_regression
from crashlab.runner import run_episode
from crashlab.scenarios import load_scenarios, scenario_by_id, validate_scenario


def judge(sid, agent, seed=1):
    sc = scenario_by_id(sid)
    ep = run_episode(sc, agent, seed, f"t.{sid}")
    card = evaluate_episode(sc, ep)
    return sc, ep, card, diagnose(sc, ep, card)


@pytest.mark.parametrize("sid,category", [
    ("ACL-ECOM-0002", "DUPLICATE_ACTION"),
    ("ACL-ECOM-0008", "SECURITY_VIOLATION"),
    ("ACL-ECOM-0010", "SECURITY_VIOLATION"),
    ("ACL-ECOM-0005", "FALSE_COMPLETION"),
    ("ACL-ECOM-0006", "INFINITE_LOOP"),
    ("ACL-ECOM-0018", "POLICY_VIOLATION"),
    ("ACL-ECOM-0004", "FAILURE_TO_RECOVER"),
])
def test_v1_failures_are_classified(sid, category):
    *_, card, fails = judge(sid, SupportAgentV1())
    assert not card.passed
    assert fails[0].category == category


def test_v2_passes_every_scenario_with_most_seeds():
    for sc in load_scenarios():
        if sc["scenario_id"] in ("ACL-ECOM-0011", "ACL-ECOM-0012"):
            continue  # v2 keeps a deliberate 12% approval-skip flake there
        ep = run_episode(sc, SupportAgentV2(), 3, "t")
        assert evaluate_episode(sc, ep).passed, sc["scenario_id"]


def test_consequences_fold_into_root_cause():
    *_, fails = judge("ACL-ECOM-0002", SupportAgentV1(), seed=2)
    dup = next(f for f in fails if f.category == "DUPLICATE_ACTION")
    roles = {c["name"]: c["role"] for c in dup.checks}
    assert roles["no duplicate side effects"] == "root"
    assert roles["expect: refund_count('ORD-1001') == 1"] == "consequence"
    assert dup.root_cause["triggering_fault"]["type"] == "timeout_after_commit"
    assert dup.root_cause["confidence"] == "high"


def test_claim_extraction_respects_negation():
    claims = extract_claims("I've cancelled order ORD-1002. I couldn't refund ORD-1001 because it is not eligible.")
    assert [(c["type"], c["target"]) for c in claims] == [("cancel", "ORD-1002")]


def test_ddmin_finds_minimal_subset():
    hist = []
    out = ddmin(list(range(16)), lambda s: 3 in s and 11 in s, hist)
    assert sorted(out) == [3, 11]


def test_minimize_flagship_to_two_steps():
    sc, ep, card, fails = judge("ACL-ECOM-0017", SupportAgentV1(), seed=11)
    rp = minimize(sc, ep, fails[0])
    assert rp.reproduces_original and rp.deterministic
    assert rp.minimized_steps == 2 and rp.original_steps >= 8
    assert [s["tool"] for s in rp.steps] == ["issue_refund", "issue_refund"]
    assert rp.steps[0]["faults"] == ["F-001"]


def test_regression_fails_on_v1_and_passes_on_v2():
    sc, ep, card, fails = judge("ACL-ECOM-0002", SupportAgentV1())
    reg = generate_regression(fails[0], sc, minimize(sc, ep, fails[0]), activated={f["fault_id"] for f in ep.fault_log})
    assert reg["regression"]["name"] == "refund_timeout_duplicate_protection"
    assert validate_scenario(reg).ok
    rr = run_regressions([reg], ["v1", "v2"], repeats=3)
    assert rr["rows"][0]["results"]["v1"]["status"] == "fail"
    assert rr["rows"][0]["results"]["v2"]["status"] == "pass"
    assert rr["release_gate"] == {"v1": False, "v2": True}


def test_metrics():
    assert pass_hat_k(5, 5, 5) == 1 and pass_hat_k(5, 4, 5) == 0
    assert math.isclose(pass_hat_k(5, 4, 1), 0.8)
    assert math.isclose(pass_at_k(5, 1, 5), 1.0) and pass_at_k(5, 0, 3) == 0
    lo, hi = wilson(9, 10)
    assert 0.55 < lo < 0.6 and 0.98 < hi <= 1.0  # 9/10 is *not* proof of 90%
    assert mcnemar_exact(10, 0) < 0.01 and mcnemar_exact(3, 3) == 1.0


def test_experiment_end_to_end():
    sc = [scenario_by_id(s) for s in ("ACL-ECOM-0002", "ACL-ECOM-0008", "ACL-ECOM-0014")]
    res = run_experiment(["v1", "v2"], sc, repeats=3)
    v1, v2 = res["summaries"]["v1"], res["summaries"]["v2"]
    assert v2["success_rate"] > v1["success_rate"]
    assert v1["rates"]["duplicate_side_effect"] > 0 and v2["rates"]["duplicate_side_effect"] == 0
    assert res["comparison"]["paired_episodes"] == 9
    assert res["regressions"] and all(rp["deterministic"] for rp in res["reproductions"].values())
    assert len(v1["pass_hat_k"]) == 3 and v1["pass_hat_k"] == sorted(v1["pass_hat_k"], reverse=True)


def test_all_scenarios_validate():
    for sc in load_scenarios():
        rep = validate_scenario(sc)
        assert rep.ok, (sc["scenario_id"], rep["errors"])


def test_validation_rejects_bad_scenarios():
    sc = dict(scenario_by_id("ACL-ECOM-0001"))
    sc["expected_state"] = ["__import__('os')"]
    assert not validate_scenario(sc).ok
    sc = dict(scenario_by_id("ACL-ECOM-0001"))
    sc["faults"] = [{"type": "timeout_after_commit", "target": "launch_rockets"}]
    assert not validate_scenario(sc).ok


def test_false_claims_without_an_order_id_are_caught():
    # Review item 6: real models rarely repeat the order ID.
    from crashlab.agents.base import Agent, FinalAnswer

    class Bluffer(Agent):
        def reset(self, task, tools, context, rng):
            pass

        def act(self, observation):
            return FinalAnswer("All sorted! You'll get your money back in 5-7 days.")  # but no refund was issued

    sc = scenario_by_id("ACL-ECOM-0001")
    card = evaluate_episode(sc, run_episode(sc, Bluffer(), 1, "bluff"))
    failed = {g["name"] for g in card.to_dict()["gating_failures"]}
    assert "claims match the final system state" in failed
    assert [c["target"] for c in extract_claims("Your order has been cancelled.", "ORD-1001")] == ["ORD-1001"]
    assert extract_claims("I could not cancel your order.", "ORD-1001") == []
