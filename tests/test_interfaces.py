"""SDK, CLI, LLM agent loop and HTTP API."""
import json

from crashlab.agents.llm import LLMToolAgent
from crashlab.cli import main
from crashlab.evaluators import evaluate_episode
from crashlab.runner import run_episode
from crashlab.scenarios import scenario_by_id
from crashlab.sdk import CrashLab, ToolCallFailed


def safe_agent(task, tools, ctx):
    o = tools.get_order(order_id="ORD-1001")
    tools.check_refund_eligibility(order_id="ORD-1001")
    try:
        tools.issue_refund(order_id="ORD-1001", amount=o["amount"], idempotency_key="k")
    except ToolCallFailed as e:
        assert e.code == "timeout"
        if not tools.get_refund_status(order_id="ORD-1001")["refunds"]:
            tools.issue_refund(order_id="ORD-1001", amount=o["amount"], idempotency_key="k")
    return "Your refund for ORD-1001 has been initiated."


def test_sdk_runs_user_agent():
    rep = CrashLab().test(safe_agent, ["ACL-ECOM-0002", "ACL-ECOM-0001"], repeats=2)
    assert rep.success_rate == 1.0


def test_sdk_detects_bugs_in_user_agent():
    def blind(task, tools, ctx):
        for _ in range(2):
            try:
                tools.issue_refund(order_id="ORD-1001", amount=4999)
                break
            except ToolCallFailed:
                pass
        return "Refund for ORD-1001 processed."

    rep = CrashLab().test(blind, ["ACL-ECOM-0002"], repeats=1)
    assert rep.failures[0]["category"] == "DUPLICATE_ACTION"


def test_llm_agent_loop_with_fake_transport():
    script = iter([
        {"content": [{"type": "tool_use", "id": "a", "name": "get_order", "input": {"order_id": "ORD-1001"}},
                     {"type": "tool_use", "id": "b", "name": "check_refund_eligibility", "input": {"order_id": "ORD-1001"}}]},
        {"content": [{"type": "tool_use", "id": "c", "name": "issue_refund",
                      "input": {"order_id": "ORD-1001", "amount": 4999, "idempotency_key": "x"}}]},
        {"content": [{"type": "text", "text": "Your refund for ORD-1001 has been initiated."}]},
    ])
    sent = []

    def transport(body):
        sent.append(json.loads(json.dumps(body)))
        return next(script)

    sc = scenario_by_id("ACL-ECOM-0001")
    ep = run_episode(sc, LLMToolAgent(model="fake", transport=transport), 1, "llm")
    assert evaluate_episode(sc, ep).passed
    assert [b["type"] for b in sent[1]["messages"][-1]["content"]] == ["tool_result", "tool_result"]
    assert any(t["name"] == "issue_refund" for t in sent[0]["tools"])


def test_cli_run_gate_and_regress(tmp_path, capsys):
    db = str(tmp_path / "c.db")
    code = main(["--db", db, "run", "--agents", "v2", "--scenarios", "0001", "0002", "--repeats", "2", "--quiet",
                 "--gate", "success>=0.9,duplicates==0"])
    assert code == 0
    code = main(["--db", db, "run", "--agents", "v1", "--scenarios", "0002", "--repeats", "2", "--quiet",
                 "--gate", "duplicates==0"])
    assert code == 1
    assert main(["--db", db, "regress", "--agents", "v1", "v2"]) == 0  # v2 releasable
    assert main(["validate"]) == 0
    assert "release gate" in capsys.readouterr().out


def test_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHLAB_AUTH", "required")
    from fastapi.testclient import TestClient

    from server.app import create_app

    app = create_app(str(tmp_path / "api.db"))
    key = app.state.store.create_api_key("default", "t", ["read", "write"])["key"]
    c = TestClient(app, headers={"Authorization": f"Bearer {key}"})
    assert c.get("/health").json()["status"] == "ok"
    assert len(c.get("/api/v1/scenarios").json()) == 20
    # Fresh install: "no regression run yet" is an empty answer, not a 404 the browser logs as an error.
    assert c.get("/api/v1/regression-suites/default/latest").status_code == 204
    r = c.post("/api/v1/experiments", json={"agents": ["v1", "v2"], "repeats": 2,
                                            "scenario_ids": ["ACL-ECOM-0002"]}).json()
    with c.stream("GET", r["stream"]) as s:
        kinds = [ln for ln in s.iter_lines() if ln.startswith("event:")]
    assert kinds[-1] == "event: stream.end" and "event: run.completed" in kinds
    exp = c.get(f"/api/v1/experiments/{r['experiment_id']}").json()
    assert exp["status"] == "COMPLETED"
    runs = c.get(f"/api/v1/experiments/{r['experiment_id']}/runs").json()
    failed = next(x for x in runs if x["failure_ids"])
    assert c.get(f"/api/v1/runs/{failed['run_id']}/trace").status_code == 200
    fid = failed["failure_ids"][0]
    assert c.post(f"/api/v1/failures/{fid}/minimize").json()["minimized_steps"] == 2
    assert c.post(f"/api/v1/failures/{fid}/regression").status_code == 201
    rr = c.post("/api/v1/regression-suites/default/run", json={"agents": ["v2"]}).json()
    assert rr["release_gate"]["v2"] is True
    assert c.post("/api/v1/experiments", json={"agents": ["nope"]}).status_code == 422
    assert c.get("/api/v1/runs/missing").status_code == 404
