"""CRASHLAB_AUTH=public: no sign-in anywhere, a private workspace per browser, and never any admin power."""
import time

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"origin": "http://testserver"}
API = "/api/v1"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHLAB_AUTH", "public")
    for k in ("GOOGLE_CLIENT_ID", "CRASHLAB_ADMIN_EMAILS", "CRASHLAB_PUBLIC_URL"):
        monkeypatch.delenv(k, raising=False)
    from server.app import create_app

    return create_app(str(tmp_path / "public.db"))


def browser(app):
    c = TestClient(app, headers=ORIGIN, follow_redirects=False)
    c.get("/")  # the first page view hands out the visitor cookie
    return c


def test_no_sign_in_pages(app):
    c = browser(app)
    for path in ("/login", "/signup", "/setup?token=x", "/admin", "/admin/login", "/account", "/forgot", "/logout"):
        r = c.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/app/", path
    home = c.get("/").text
    assert 'href="/signup"' not in home and 'href="/login"' not in home and "Open the dashboard" in home
    assert all(i["url"] not in ("/signup", "/login") for i in c.get("/search-index.json").json())


def test_every_browser_gets_its_own_workspace(app):
    a, b = browser(app), browser(app)
    me = a.get(f"{API}/whoami").json()
    assert me["key_id"].startswith("visitor:") and me["scopes"] == ["read", "write"]
    assert me["project_id"] != b.get(f"{API}/whoami").json()["project_id"]
    exp = a.post(f"{API}/experiments", json={"agents": ["v2"], "scenario_ids": ["ACL-ECOM-0001"], "repeats": 1})
    assert exp.status_code == 202
    exp_id = exp.json()["experiment_id"]
    for _ in range(100):
        if a.get(f"{API}/experiments/{exp_id}").json()["status"] == "COMPLETED":
            break
        time.sleep(0.05)
    assert [e["experiment_id"] for e in a.get(f"{API}/experiments").json()] == [exp_id]
    assert b.get(f"{API}/experiments").json() == []  # someone else's browser sees nothing of it
    assert b.post(f"{API}/experiments/{exp_id}/delete").status_code == 404


def test_visitors_never_get_admin_powers(app):
    c = browser(app)
    assert c.get(f"{API}/contact-messages").status_code == 403  # contact-form messages stay private
    r = c.post(f"{API}/experiments", json={"agents": ["gemini"], "scenario_ids": ["ACL-ECOM-0001"], "repeats": 1})
    assert r.status_code in (403, 422)  # real-model runs spend API credit: never for visitors
    assert c.post(f"{API}/api-keys", json={"name": "x", "scopes": ["read"]}).status_code == 403
    assert c.post(f"{API}/experiments", json={"agents": ["v1", "v2"], "repeats": 50}).status_code == 422  # fair-use cap


def test_writes_from_other_sites_are_blocked(app):
    c = browser(app)
    evil = TestClient(app, headers={"origin": "https://evil.example"}, cookies=dict(c.cookies))
    assert evil.post(f"{API}/experiments", json={"agents": ["v2"], "repeats": 1}).status_code == 403


def test_a_forged_cookie_is_not_a_workspace(app):
    c = TestClient(app, headers=ORIGIN, cookies={"acl_visitor": "../../etc"}, follow_redirects=False)
    assert c.get(f"{API}/experiments").status_code == 401
