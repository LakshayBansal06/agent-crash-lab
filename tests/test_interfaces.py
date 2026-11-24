"""SDK, CLI, LLM agent loop and HTTP API."""
import json
import time

from crashlab.agents.llm import GeminiToolAgent, LLMToolAgent
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


def test_model_calls_retry_transient_provider_errors():
    import httpx
    import pytest

    from crashlab.agents import ProviderError
    from crashlab.agents.llm import post_with_retry

    req = httpx.Request("POST", "https://api.example")

    def calls(*replies):
        it = iter(replies)

        def post(*a, **k):
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r
        return post

    slept = []
    out = post_with_retry("u", {}, {}, sleep=slept.append, post=calls(
        httpx.Response(503, request=req), httpx.ReadTimeout("slow", request=req),
        httpx.Response(429, headers={"retry-after": "2"}, request=req), httpx.Response(200, json={"ok": True}, request=req)))
    assert out == {"ok": True} and slept == [1.0, 2.0, 2.0]  # backoff, a timeout retried, then Retry-After
    with pytest.raises(ProviderError, match="HTTP 400"):  # a bad request is not retried
        post_with_retry("u", {}, {}, sleep=slept.append, post=calls(httpx.Response(400, request=req)))
    slept.clear()
    with pytest.raises(ProviderError, match="HTTP 429"):  # a daily quota (Retry-After in hours) gives up at once
        post_with_retry("u", {}, {}, sleep=slept.append, post=calls(
            httpx.Response(429, headers={"retry-after": "51184"}, request=req)))
    assert slept == []
    now = [0.0]
    with pytest.raises(ProviderError):  # an outage stops at the time budget, not after minutes of retries
        post_with_retry("u", {}, {}, budget_s=10, clock=lambda: now[0],
                        sleep=lambda t: now.__setitem__(0, now[0] + t),
                        post=calls(*[httpx.Response(503, request=req)] * 5))
    assert now[0] <= 10


def test_provider_errors_are_inconclusive_and_real_usage_is_recorded():
    from crashlab.agents import ProviderError, make_agent
    from crashlab.agents.llm import GeminiToolAgent
    from crashlab.experiment import run_experiment

    reply = {"candidates": [{"content": {"role": "model", "parts": [{"text": "Your refund is on its way."}]}}],
             "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 30}, "modelVersion": "gemini-x-001"}
    sc = scenario_by_id("ACL-ECOM-0001")
    ep = run_episode(sc, GeminiToolAgent(model="fake", transport=lambda m, b: reply), 1, "real")
    assert ep.usage["input_tokens"] == 1200 and ep.usage["output_tokens"] == 30 and ep.usage["llm_calls"] == 1
    step = next(e for e in ep.events if e["event_type"] == "agent_step")
    assert step["metadata"]["model"] == "gemini-x-001"

    def down(model, body):
        raise ProviderError("model provider failed: HTTP 503")

    ep = run_episode(sc, GeminiToolAgent(model="fake", transport=down), 1, "down")
    assert ep.termination == "provider_error"

    import crashlab.experiment as ex
    real_make = ex.make_agent
    ex.make_agent = lambda ref: GeminiToolAgent(model="fake", transport=down) if ref == "gemini" else real_make(ref)
    try:
        res = run_experiment(["v2", "gemini"], [sc], repeats=2, minimize_failures=False)
    finally:
        ex.make_agent = real_make
    g = res["summaries"]["gemini"]
    assert g["inconclusive"] == 2 and g["episodes"] == 0 and not res["failures"]  # never counted as agent failures
    assert res["summaries"]["v2"]["episodes"] == 2 and make_agent("v2")


def test_model_agents_refuse_to_run_without_a_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["--db", str(tmp_path / "k.db"), "run", "--agents", "gemini", "--repeats", "1", "--quiet"]) == 2
    assert "needs GEMINI_API_KEY" in capsys.readouterr().err


def test_gemini_agent_loop_with_fake_transport():
    sig = {"thoughtSignature": "opaque"}
    script = iter([
        {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "get_order", "args": {"order_id": "ORD-1001"}}, **sig},
            {"functionCall": {"name": "check_refund_eligibility", "args": {"order_id": "ORD-1001"}}}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {
            "name": "issue_refund", "args": {"order_id": "ORD-1001", "amount": 4999, "idempotency_key": "x"}}}]}}]},
        {"candidates": [{"content": {"role": "model", "parts": [
            {"text": "thinking...", "thought": True}, {"text": "Your refund for ORD-1001 has been initiated."}]}}]},
    ])
    sent = []

    def transport(model, body):
        sent.append(json.loads(json.dumps(body)))
        return next(script)

    sc = scenario_by_id("ACL-ECOM-0001")
    ep = run_episode(sc, GeminiToolAgent(model="fake", transport=transport), 1, "gemini")
    assert evaluate_episode(sc, ep).passed
    assert ep.final_message == "Your refund for ORD-1001 has been initiated."  # thought parts are not the reply
    assert sent[1]["contents"][1]["parts"][0] == {"functionCall": {"name": "get_order", "args": {"order_id": "ORD-1001"}}, **sig}
    assert [list(p) for p in sent[1]["contents"][-1]["parts"]] == [["functionResponse"], ["functionResponse"]]
    decls = sent[0]["tools"][0]["functionDeclarations"]
    assert all("parameters" not in d or d["parameters"]["properties"] for d in decls)


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
    assert len(c.get("/api/v1/scenarios").json()) == 30
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
    started = c.post("/api/v1/regression-suites/default/run", json={"agents": ["v2"]})
    assert started.status_code == 202  # runs in the background, never inside the web request
    for _ in range(200):
        if not c.get("/api/v1/regression-suites/default/status").json()["running"]:
            break
        time.sleep(0.05)
    rr = c.get("/api/v1/regression-suites/default/latest").json()
    assert rr["release_gate"]["v2"] is True
    assert c.post("/api/v1/experiments", json={"agents": ["nope"]}).status_code == 422
    assert c.get("/api/v1/runs/missing").status_code == 404


def test_restart_fails_experiments_its_workers_were_running(tmp_path, monkeypatch):
    # Review item 7: a deploy mid-run must not leave an experiment RUNNING forever.
    monkeypatch.setenv("CRASHLAB_AUTH", "off")
    from crashlab.store import Store
    from server.app import create_app

    db = str(tmp_path / "restart.db")
    st = Store(db)
    st.create_experiment("exp_interrupted", "x", {"agents": ["v2"]}, owner="dead-process")
    st.set_status("exp_interrupted", "RUNNING")
    st.db.execute("UPDATE experiments SET heartbeat='2020-01-01T00:00:00Z' WHERE id='exp_interrupted'")  # worker gone
    # Second review: another live server process sharing the database must keep its running experiment.
    st.create_experiment("exp_other_process", "y", {"agents": ["v2"]}, owner="live-process")
    st.set_status("exp_other_process", "RUNNING")
    st.db.commit()
    app = create_app(db)
    status = dict(app.state.store.db.execute("SELECT id, status FROM experiments").fetchall())
    assert status == {"exp_interrupted": "FAILED", "exp_other_process": "RUNNING"}


def test_sdk_uploads_show_up_as_a_full_experiment(tmp_path, monkeypatch):
    # A user's own agent, tested with the SDK, must appear in their dashboard like a built-in experiment.
    monkeypatch.setenv("CRASHLAB_AUTH", "required")
    from fastapi.testclient import TestClient

    from server.app import create_app

    app = create_app(str(tmp_path / "ingest.db"))
    key = app.state.store.create_api_key("p_me", "sdk", ["read", "write"])["key"]
    c = TestClient(app, headers={"Authorization": f"Bearer {key}"})

    class Uploader(CrashLab):
        def _upload(self, report):
            r = c.post("/api/v1/ingest/runs", json=report)
            assert r.status_code == 201, r.text

    def blind(task, tools, ctx):
        for _ in range(2):
            try:
                tools.issue_refund(order_id="ORD-1001", amount=4999)
                break
            except ToolCallFailed:
                pass
        return "Refund for ORD-1001 processed."

    lab = Uploader()
    lab.test(blind, ["ACL-ECOM-0002"], repeats=2)
    exp = c.get(f"/api/v1/experiments/{lab.experiment_id}").json()
    s = exp["summaries"]["my-agent@dev"]
    assert s["episodes"] == 2 and exp["config"]["repeats"] == 2 and exp["failure_modes"]
    assert any(m["category"] == "DUPLICATE_ACTION" for m in exp["failure_modes"])
    assert lab.experiment_id.startswith("sdk-") and Uploader().experiment_id != lab.experiment_id  # no collisions
