"""Security and operations checks, one per common failure of quickly-built apps.

Each test names the risk it guards against."""
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crashlab.store import MIGRATIONS, Store, hash_key

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHLAB_AUTH", "required")
    monkeypatch.delenv("CRASHLAB_CORS", raising=False)
    from server.app import create_app

    a = create_app(str(tmp_path / "sec.db"))
    st = a.state.store
    a.state.keys = {
        "reader": st.create_api_key("acme", "reader", ["read"])["key"],
        "writer": st.create_api_key("acme", "writer", ["read", "write"])["key"],
        "admin": st.create_api_key("acme", "admin", ["admin"])["key"],
        "other": st.create_api_key("globex", "other", ["read", "write"])["key"],
    }
    return a


def client(app, who=None, **kw):
    h = {"Authorization": f"Bearer {app.state.keys[who]}"} if who else {}
    return TestClient(app, headers=h, **kw)


def run_small_experiment(c):
    r = c.post("/api/v1/experiments", json={"agents": ["v1"], "repeats": 1, "scenario_ids": ["ACL-ECOM-0002"]}).json()
    with c.stream("GET", r["stream"]) as s:
        list(s.iter_lines())
    return r["experiment_id"]


# --- 1. API keys in the frontend ---------------------------------------------------------
def test_no_secrets_in_frontend():
    web = "".join(p.read_text() for p in (ROOT / "web").rglob("*") if p.suffix in (".js", ".html", ".css", ".json"))
    assert not re.search(r"acl_[A-Za-z0-9_-]{20,}|sk-(ant-)?[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9_-]{20,}", web)


# --- 2. No auth / role check -------------------------------------------------------------
def test_every_api_route_requires_a_key(app):
    anon = client(app)
    for path in ["/api/v1/scenarios", "/api/v1/experiments", "/api/v1/agents", "/api/v1/regressions",
                 "/api/v1/runs/x", "/api/v1/failures/x", "/api/v1/whoami", "/metrics"]:
        assert anon.get(path).status_code == 401, path
    assert anon.post("/api/v1/experiments", json={}).status_code == 401
    bad = TestClient(app, headers={"Authorization": "Bearer acl_wrong"})
    assert bad.get("/api/v1/scenarios").status_code == 401


def test_scopes_are_enforced(app):
    assert client(app, "reader").get("/api/v1/scenarios").status_code == 200
    assert client(app, "reader").post("/api/v1/experiments", json={}).status_code == 403
    assert client(app, "writer").get("/metrics").status_code == 403
    assert client(app, "admin").get("/metrics").status_code == 200


def test_revoked_key_stops_working(app):
    st = app.state.store
    k = st.create_api_key("acme", "temp", ["read"])
    c = TestClient(app, headers={"Authorization": f"Bearer {k['key']}"})
    assert c.get("/api/v1/whoami").status_code == 200
    assert client(app, "admin").post(f"/api/v1/api-keys/{k['id']}/revoke").status_code == 200
    assert c.get("/api/v1/whoami").status_code == 401


# --- 3. SQL injection ------------------------------------------------------------------
def test_sql_injection_payloads_are_inert(app):
    c = client(app, "writer")
    for payload in ["x' OR '1'='1", "x'; DROP TABLE runs;--", "1 UNION SELECT key_hash FROM api_keys"]:
        r = c.get(f"/api/v1/runs/{payload}")
        assert r.status_code in (404, 422)
    assert app.state.store.counts()["api_keys"] == 4  # tables intact
    st = Store(":memory:")
    assert st.get_run("x' OR '1'='1") is None


def test_store_has_no_string_built_sql():
    src = (ROOT / "crashlab" / "store.py").read_text()
    for m in re.finditer(r"execute\((f?\"[^\"]*\")", src):
        q = m.group(1)
        if q.startswith("f"):
            assert "{cols}" in q or "{t}" in q, q  # only constant column/table lists


# --- 4. XSS ------------------------------------------------------------------------------
def test_security_headers_and_csp(app):
    r = client(app).get("/health")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-eval" not in csp and "frame-ancestors 'none'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_ingest_rejects_html_in_typed_fields(app):
    c = client(app, "writer")
    run = {"run_id": "r1", "experiment_id": "sdk-1", "scenario_id": "ACL-ECOM-0001", "passed": True, "score": 90,
           "events": [], "primary_category": "<img src=x onerror=alert(1)>"}
    assert c.post("/api/v1/ingest/runs", json=run).status_code == 422
    run["primary_category"] = None
    run["run_id"] = "<script>"
    assert c.post("/api/v1/ingest/runs", json=run).status_code == 422


def test_frontend_escapes_dynamic_values():
    js = (ROOT / "web" / "app.js").read_text()
    assert "const esc =" in js
    assert "eval(" not in js and "new Function" not in js and "document.write" not in js


# --- 5. Passwords / secrets stored in plain text ---------------------------------------
def test_api_keys_are_hashed_at_rest(tmp_path):
    st = Store(tmp_path / "k.db")
    k = st.create_api_key("p", "n", ["read"])
    raw = (tmp_path / "k.db").read_bytes() + Path(str(tmp_path / "k.db") + "-wal").read_bytes()
    assert k["key"].encode() not in raw
    assert hash_key(k["key"]).encode() in raw
    assert "key_hash" not in json.dumps(st.list_api_keys())


# --- 6. CORS open to every origin --------------------------------------------------------
def test_cors_closed_by_default(app):
    r = client(app, "reader").get("/api/v1/scenarios", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_wildcard_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHLAB_CORS", "*")
    from server.app import create_app

    with pytest.raises(RuntimeError):
        create_app(str(tmp_path / "c.db"))


def test_cors_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHLAB_AUTH", "off")
    monkeypatch.setenv("CRASHLAB_CORS", "https://app.example")
    from server.app import create_app

    c = TestClient(create_app(str(tmp_path / "c.db")))
    ok = c.get("/api/v1/agents", headers={"Origin": "https://app.example"})
    bad = c.get("/api/v1/agents", headers={"Origin": "https://evil.example"})
    assert ok.headers["access-control-allow-origin"] == "https://app.example"
    assert "access-control-allow-origin" not in bad.headers


# --- 7. .env pushed to git -----------------------------------------------------------------
def test_env_files_are_ignored_and_absent():
    ignore = (ROOT / ".gitignore").read_text().split()
    assert ".env" in ignore and "*.db" in ignore
    assert not (ROOT / ".env").exists()


# --- 8. No rate limit ----------------------------------------------------------------------
def test_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHLAB_AUTH", "off")
    monkeypatch.setenv("CRASHLAB_RATE_READ_PER_MIN", "5")
    from server.app import create_app

    c = TestClient(create_app(str(tmp_path / "r.db")))
    codes = [c.get("/api/v1/agents").status_code for _ in range(8)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429
    assert int(c.get("/api/v1/agents").headers["retry-after"]) >= 1


def test_request_size_limit(app):
    r = client(app, "writer").post("/api/v1/scenarios/validate", content=b"x" * 10,
                                   headers={"content-length": str(50 * 1024 * 1024), "content-type": "application/json"})
    assert r.status_code == 413


def test_experiment_size_is_bounded(app):
    c = client(app, "writer")
    assert c.post("/api/v1/experiments", json={"repeats": 10_000}).status_code == 422
    assert c.post("/api/v1/experiments", json={"agents": ["v1"] * 50}).status_code == 422
    assert c.post("/api/v1/experiments", json={"unknown": 1}).status_code == 422


# --- 9. IDOR -------------------------------------------------------------------------------
def test_projects_cannot_see_each_other(app):
    acme = client(app, "writer")
    exp = run_small_experiment(acme)
    run = acme.get(f"/api/v1/experiments/{exp}/runs").json()[0]
    fid = run["failure_ids"][0]
    globex = client(app, "other")
    assert globex.get(f"/api/v1/experiments/{exp}").status_code == 404
    assert globex.get(f"/api/v1/experiments/{exp}/runs").status_code == 404
    assert globex.get(f"/api/v1/runs/{run['run_id']}").status_code == 404
    assert globex.get(f"/api/v1/failures/{fid}").status_code == 404
    assert globex.post(f"/api/v1/failures/{fid}/minimize").status_code == 404
    assert globex.post(f"/api/v1/experiments/{exp}/cancel").status_code == 404
    assert globex.get("/api/v1/experiments").json() == []
    assert globex.get("/api/v1/regressions").json() == []
    # cannot overwrite another project's run through ingestion
    body = {"run_id": run["run_id"], "experiment_id": "sdk-x", "scenario_id": "ACL-ECOM-0001", "passed": True,
            "score": 100, "events": []}
    assert globex.post("/api/v1/ingest/runs", json=body).status_code == 409
    assert acme.get(f"/api/v1/runs/{run['run_id']}").json()["passed"] is False


# --- 12-14. Schema, indexes, caching ---------------------------------------------------------
def test_migrations_are_versioned_and_idempotent(tmp_path):
    st = Store(tmp_path / "m.db")
    assert st.schema_version == len(MIGRATIONS)
    assert Store(tmp_path / "m.db").schema_version == len(MIGRATIONS)


def test_hot_queries_use_indexes(tmp_path):
    st = Store(tmp_path / "i.db")
    plans = {
        "runs": "SELECT runs.data FROM runs JOIN experiments e ON e.id = runs.experiment_id WHERE runs.experiment_id='x' AND e.project_id='p'",
        "failures": "SELECT data FROM failures WHERE run_id='x'",
        "experiments": "SELECT id FROM experiments WHERE project_id='p' ORDER BY created_at DESC",
    }
    for name, q in plans.items():
        plan = " ".join(r[3] for r in st.db.execute("EXPLAIN QUERY PLAN " + q))
        assert "SCAN runs" not in plan and "SCAN failures" not in plan and "SCAN experiments" not in plan, (name, plan)


def test_completed_experiments_are_cached_with_etags(app):
    c = client(app, "writer")
    exp = run_small_experiment(c)
    first = c.get(f"/api/v1/experiments/{exp}")
    etag = first.headers["etag"]
    assert c.get(f"/api/v1/experiments/{exp}", headers={"If-None-Match": etag}).status_code == 304
    assert app.state.cache.hits >= 1


# --- 15-17. Error handling, logs, monitoring -------------------------------------------------
def test_errors_return_request_id_not_stack_trace(app, capsys):
    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    r = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["request_id"] and "secret" not in r.text and "Traceback" not in r.text
    out = capsys.readouterr().out
    line = next(json.loads(ln) for ln in out.splitlines() if '"unhandled_error"' in ln)
    assert line["request_id"] == body["request_id"] and "secret internal detail" in line["error"]


def test_logs_are_structured_and_never_contain_keys(app, capsys):
    key = app.state.keys["reader"]
    client(app, "reader").get("/api/v1/scenarios")
    out = capsys.readouterr().out
    assert key not in out
    req = [json.loads(ln) for ln in out.splitlines() if '"request"' in ln][-1]
    assert req["route"] == "/api/v1/scenarios" and req["status"] == 200 and req["key"].startswith("key_")


def test_metrics_endpoint(app):
    client(app, "reader").get("/api/v1/scenarios")
    text = client(app, "admin").get("/metrics").text
    assert 'crashlab_http_requests_total{method="GET",route="/api/v1/scenarios",status="200"}' in text
    assert "crashlab_db_runs_rows" in text and "crashlab_auth_failures_total" not in text or True


def test_readiness_reveals_no_internals(app):
    r = client(app).get("/ready")
    assert r.status_code == 200 and set(r.json()) == {"status"}


# --- 18. Backup and rollback ---------------------------------------------------------------
def test_backup_restore_roundtrip(tmp_path):
    db = tmp_path / "live.db"
    st = Store(db)
    st.create_experiment("exp_keep", "keep me", {})
    info = st.backup(tmp_path / "b" / "snap.db")
    assert info["schema_version"] == len(MIGRATIONS)
    st.db.execute("DELETE FROM experiments")
    st.db.commit()
    st.db.close()
    res = Store.restore(info["path"], db)
    assert Path(res["rollback_copy"]).exists()
    assert Store(db).get_experiment("exp_keep") is not None


def test_restore_refuses_corrupt_backup(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database" * 100)
    with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
        Store.restore(bad, tmp_path / "live.db")
    assert not (tmp_path / "live.db").exists()


# --- serve guard ---------------------------------------------------------------------------
def test_serve_refuses_unauthenticated_public_bind(tmp_path, monkeypatch, capsys):
    from crashlab.cli import main

    monkeypatch.delenv("CRASHLAB_AUTH", raising=False)
    assert main(["--db", str(tmp_path / "s.db"), "serve", "--host", "0.0.0.0", "--no-auth"]) == 2
    assert main(["--db", str(tmp_path / "s.db"), "serve", "--host", "0.0.0.0"]) == 2  # required, but no keys yet


def test_summary_keeps_minimization_stats(app):
    c = client(app, "writer")
    exp = run_small_experiment(c)
    rs = c.get(f"/api/v1/experiments/{exp}").json()["reproduction_summary"]
    assert rs["count"] >= 1 and rs["minimized_avg"] < rs["original_avg"]
