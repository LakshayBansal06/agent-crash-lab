"""Accounts: sign-up, sign-in, admin console, first-admin setup, password reset, Google sign-in."""
import re
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"origin": "http://testserver"}
PW = "a long enough password"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHLAB_AUTH", "required")
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SMTP_HOST", "SMTP_FROM", "CRASHLAB_ADMIN_EMAILS",
              "CRASHLAB_PUBLIC_URL", "RENDER_EXTERNAL_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRASHLAB_RATE_AUTH_PER_MIN", "1000")
    from server.app import create_app

    return create_app(str(tmp_path / "auth.db"))


def client(app):
    return TestClient(app, headers=ORIGIN, follow_redirects=False)


def signup(c, email="ann@example.com", name="Ann", password=PW):
    return c.post("/signup", data={"name": name, "email": email, "password": password})


def test_signup_signs_in_and_gives_a_private_project(app):
    a, b = client(app), client(app)
    r = signup(a)
    assert r.status_code == 303 and r.headers["location"].startswith("/app/")
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    me = a.get("/api/v1/whoami").json()
    assert me["user"] == {"name": "Ann", "email": "ann@example.com", "role": "user"} and "admin" not in me["scopes"]
    exp = a.post("/api/v1/experiments", json={"agents": ["v2"], "repeats": 1, "scenario_ids": ["ACL-ECOM-0001"]}).json()
    signup(b, email="bob@example.com", name="Bob")
    assert b.get(f"/api/v1/experiments/{exp['experiment_id']}").status_code == 404  # another user's data doesn't exist for you
    assert client(app).get("/api/v1/whoami").status_code == 401


def test_signup_validation_and_duplicates(app):
    c = client(app)
    r = c.post("/signup", data={"name": "", "email": "nope", "password": "short"})
    assert r.status_code == 422 and r.text.count('aria-invalid="true"') == 3
    assert signup(c).status_code == 303
    r = signup(client(app))
    assert r.status_code == 422 and "create an account with this email" in r.text and "already exists" not in r.text


def test_login_wrong_password_logout(app):
    signup(client(app))
    c = client(app)
    r = c.post("/login", data={"email": "ann@example.com", "password": "wrong password!"})
    assert r.status_code == 401 and "match an account" in r.text and "set-cookie" not in r.headers
    r = c.post("/login", data={"email": "ANN@example.com", "password": PW, "next": "/account"})
    assert r.status_code == 303 and r.headers["location"] == "/account"
    assert c.get("/account").status_code == 200
    assert c.post("/logout").headers["location"].startswith("/login")
    assert c.get("/api/v1/whoami").status_code == 401  # the session is gone server-side too


def test_open_redirect_is_blocked(app):
    signup(client(app))
    for evil in ("https://evil.com", "//evil.com", "/\\evil.com"):
        r = client(app).post("/login", data={"email": "ann@example.com", "password": PW, "next": evil})
        assert r.headers["location"] == "/app/"


def test_cross_site_posts_are_rejected(app):
    c = TestClient(app, headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert signup(c).status_code == 403
    good = client(app)
    signup(good)
    cookie = good.cookies.get("acl_session")
    evil = TestClient(app, headers={"origin": "https://evil.example"}, cookies={"acl_session": cookie})
    r = evil.post("/api/v1/experiments", json={"agents": ["v2"], "repeats": 1})
    assert r.status_code == 403 and "cross-site" in r.json()["detail"]


def test_first_admin_setup_link_works_once(app):
    token = app.state.setup_token
    c = client(app)
    assert "setup link" in c.get("/login").text
    assert c.get("/setup?token=wrong").status_code == 303
    r = c.post("/setup", data={"token": token, "name": "Root", "email": "root@example.com", "password": PW})
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert c.get("/admin").status_code == 200
    assert c.get(f"/setup?token={token}").status_code == 303  # an admin exists now: the link is dead
    assert client(app).post("/setup", data={"token": token, "name": "X", "email": "x@example.com",
                                            "password": PW}).headers["location"] == "/admin/login"


def make_admin(app):
    c = client(app)
    c.post("/setup", data={"token": app.state.setup_token, "name": "Root", "email": "root@example.com", "password": PW})
    return c


def test_admin_login_and_console_actions(app):
    make_admin(app)
    user = client(app)
    signup(user)
    assert "admin access" in user.post("/admin/login", data={"email": "ann@example.com", "password": PW}).text
    assert user.get("/admin").status_code == 403
    admin = client(app)
    r = admin.post("/admin/login", data={"email": "root@example.com", "password": PW})
    assert r.headers["location"] == "/admin"
    page = admin.get("/admin").text
    assert "ann@example.com" in page and "Platform overview" in page
    uid = app.state.store.user_by_email("ann@example.com")["id"]
    assert "is now an admin" in admin.post(f"/admin/users/{uid}", data={"action": "role-admin"}).text
    assert user.get("/admin").status_code == 200
    admin.post(f"/admin/users/{uid}", data={"action": "disable"})
    assert user.get("/api/v1/whoami").status_code == 401  # disabled = signed out everywhere
    assert client(app).post("/login", data={"email": "ann@example.com", "password": PW}).status_code == 403
    link = re.search(r"(/reset\?token=[\w-]+)", admin.post(f"/admin/users/{uid}", data={"action": "reset-link"}).text)
    assert link


def test_password_reset_flow(app, monkeypatch):
    sent = []
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.com")
    monkeypatch.setattr("server.auth.send_reset_email", lambda to, link: sent.append((to, link)))
    from server.app import create_app

    app = create_app(app.state.store.path)
    signup(client(app))
    c = client(app)
    unknown = c.post("/forgot", data={"email": "ghost@example.com"}).text
    known = c.post("/forgot", data={"email": "ann@example.com"}).text
    assert "Check your email" in unknown and "Check your email" in known and len(sent) == 1  # no account enumeration
    path = urlparse(sent[0][1])
    token = parse_qs(path.query)["token"][0]
    assert c.post("/reset", data={"token": token, "password": "short"}).status_code == 422
    assert c.post("/reset", data={"token": token, "password": "brand new password"}).headers["location"] == "/login?m=reset"
    assert c.get(f"/reset?token={token}").status_code == 410  # single use
    assert client(app).post("/login", data={"email": "ann@example.com", "password": "brand new password"}).status_code == 303


def test_google_sign_in(app, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CRASHLAB_ADMIN_EMAILS", "boss@example.com")
    calls = []

    def fake(method, url, **kw):
        calls.append((method, url, kw))
        if "token" in url:
            assert kw["data"]["code"] == "good-code" and kw["data"]["code_verifier"]
            return {"access_token": "at"}
        return {"sub": "g-123", "email": "boss@example.com", "email_verified": True, "name": "The Boss"}

    monkeypatch.setattr("server.auth.google_http", fake)
    from server.app import create_app

    app = create_app(app.state.store.path)
    c = client(app)
    assert "Continue with Google" in c.get("/login").text
    r = c.get("/auth/google?next=/admin")
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == ["http://testserver/auth/google/callback"]
    r = c.get(f"/auth/google/callback?code=good-code&state={q['state'][0]}")
    assert r.headers["location"] == "/admin"
    assert c.get("/api/v1/whoami").json()["user"]["role"] == "admin"  # listed in CRASHLAB_ADMIN_EMAILS
    forged = client(app)
    forged.get("/auth/google")
    assert forged.get("/auth/google/callback?code=good-code&state=forged").headers["location"] == "/login?e=google"
    assert client(app).get("/auth/google/callback?code=good-code&state=x").headers["location"] == "/login?e=google"


def test_google_unverified_email_is_refused(app, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setattr("server.auth.google_http", lambda m, u, **kw: {"access_token": "at"} if "token" in u else
                        {"sub": "g-9", "email": "ann@example.com", "email_verified": False})
    from server.app import create_app

    app = create_app(app.state.store.path)
    signup(client(app))
    c = client(app)
    state = parse_qs(urlparse(c.get("/auth/google").headers["location"]).query)["state"][0]
    assert c.get(f"/auth/google/callback?code=x&state={state}").headers["location"] == "/login?e=google"
    assert app.state.store.user_by_email("ann@example.com")["google"] == 0  # not linked to an unverified address


def test_passwords_are_scrypt_hashed(app):
    signup(client(app))
    row = app.state.store.db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert row.startswith("scrypt$") and PW not in row


def test_google_link_revokes_an_impostors_password(app, monkeypatch):
    # Review item 1: someone signs up with the admin's email first; the real owner later uses Google.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CRASHLAB_ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setattr("server.auth.google_http", lambda m, u, **kw: {"access_token": "at"} if "token" in u else
                        {"sub": "g-boss", "email": "boss@example.com", "email_verified": True, "name": "Boss"})
    from server.app import create_app

    app = create_app(app.state.store.path)
    impostor = client(app)
    signup(impostor, email="boss@example.com", name="Impostor", password="impostor password 1")
    owner = client(app)
    state = parse_qs(urlparse(owner.get("/auth/google").headers["location"]).query)["state"][0]
    owner.get(f"/auth/google/callback?code=x&state={state}")
    assert owner.get("/api/v1/whoami").json()["user"]["role"] == "admin"
    assert impostor.get("/api/v1/whoami").status_code == 401  # the impostor's session is gone
    r = client(app).post("/login", data={"email": "boss@example.com", "password": "impostor password 1"})
    assert r.status_code == 401  # and their password no longer works


def test_only_admins_spend_real_model_credit(app, monkeypatch):
    # Review item 2: a free sign-up must not be able to burn the server's Gemini/Claude key.
    monkeypatch.setenv("GEMINI_API_KEY", "server-key")
    user = client(app)
    signup(user)
    r = user.post("/api/v1/experiments", json={"agents": ["gemini"], "repeats": 1, "scenario_ids": ["ACL-ECOM-0001"]})
    assert r.status_code == 403 and "only admins" in r.json()["detail"]
    admin = make_admin(app)
    too_many = admin.post("/api/v1/experiments", json={"agents": ["gemini"], "repeats": 5})
    assert too_many.status_code == 422 and "at most 3 repeats" in too_many.json()["detail"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-key")
    too_big = admin.post("/api/v1/experiments", json={"agents": ["gemini", "llm"], "repeats": 3})  # 2 x 20 x 3 = 120
    assert too_big.status_code == 422 and "limit per run is 60" in too_big.json()["detail"]
    store = app.state.store
    assert store.reserve_model_episodes("p_x", 200, 200) == 0 and store.reserve_model_episodes("p_x", 1, 200) is None
    # built-in agents stay free for everyone
    assert user.post("/api/v1/experiments", json={"agents": ["v2"], "repeats": 1,
                                                   "scenario_ids": ["ACL-ECOM-0001"]}).status_code == 202
