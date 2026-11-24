"""Second review, problem 1 and 3: one free account must not be able to fill the disk, hold every worker,
or corrupt its own results by listing an agent twice."""
import time

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"origin": "http://testserver"}
PW = "a long enough password"
API = "/api/v1"


@pytest.fixture()
def make(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHLAB_AUTH", "required")
    monkeypatch.setenv("CRASHLAB_RATE_AUTH_PER_MIN", "1000")
    monkeypatch.setenv("CRASHLAB_RATE_EXPENSIVE_PER_MIN", "1000")
    monkeypatch.setenv("CRASHLAB_RATE_WRITE_PER_MIN", "1000")
    for k in ("GOOGLE_CLIENT_ID", "CRASHLAB_ADMIN_EMAILS", "TURNSTILE_SECRET_KEY", "TURNSTILE_SITE_KEY"):
        monkeypatch.delenv(k, raising=False)

    def _make(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        from server.app import create_app

        app = create_app(str(tmp_path / f"fair{len(list(tmp_path.iterdir()))}.db"))
        c = TestClient(app, headers=ORIGIN, follow_redirects=False)
        c.post("/signup", data={"name": "Free User", "email": "free@example.com", "password": PW})
        return app, c
    return _make


def wait_done(c, exp_id):
    for _ in range(200):
        e = c.get(f"{API}/experiments/{exp_id}").json()
        if e["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return e
        time.sleep(0.05)
    raise AssertionError("experiment never finished")


def start(c, **body):
    return c.post(f"{API}/experiments", json={"agents": ["v2"], "scenario_ids": ["ACL-ECOM-0001"], "repeats": 1, **body})


def test_listing_an_agent_twice_is_rejected(make):
    _, c = make()
    r = c.post(f"{API}/experiments", json={"agents": ["v2", "v2"], "repeats": 1})
    assert r.status_code == 422 and "only once" in r.text
    assert c.post(f"{API}/regression-suites/default/run", json={"agents": ["v1", "v1"]}).status_code == 422


def test_free_accounts_have_an_episode_cap_per_run(make):
    _, c = make()
    r = c.post(f"{API}/experiments", json={"agents": ["v1", "v2"], "repeats": 50})  # 2 × 30 × 50 = 3,000
    assert r.status_code == 422 and "at most 600" in r.json()["detail"]
    assert c.get(f"{API}/experiments").json() == []  # nothing was created


def test_one_running_experiment_per_workspace(make):
    from server.app import Job

    app, c = make()
    me = c.get(f"{API}/whoami").json()["project_id"]
    app.state.jobs["exp_busy"] = Job("exp_busy", me)  # an experiment still running in this workspace
    r = start(c)
    assert r.status_code == 429 and "already has an experiment running" in r.json()["detail"]
    other = TestClient(app, headers=ORIGIN, follow_redirects=False)
    other.post("/signup", data={"name": "Other", "email": "other@example.com", "password": PW})
    assert start(other).status_code == 202  # someone else's run doesn't lock this workspace out


def test_workspace_quota_removes_the_oldest_experiments(make):
    _, c = make(CRASHLAB_PROJECT_MAX_RUNS=4)
    first = start(c, repeats=2).json()["experiment_id"]
    wait_done(c, first)
    second = start(c, repeats=2).json()["experiment_id"]
    wait_done(c, second)
    r = start(c, repeats=2)  # 2 + 2 + 2 > 4: the oldest goes
    assert r.status_code == 202 and r.json()["removed_to_make_room"] == [first]
    wait_done(c, r.json()["experiment_id"])
    assert c.get(f"{API}/experiments/{first}").status_code == 404
    assert start(c, repeats=5).status_code == 413  # 5 runs can never fit a 4-run workspace


def test_experiments_can_be_deleted(make):
    _, c = make()
    exp = start(c).json()["experiment_id"]
    wait_done(c, exp)
    assert c.post(f"{API}/experiments/{exp}/delete").json() == {"experiment_id": exp, "deleted": True}
    assert c.get(f"{API}/experiments/{exp}").status_code == 404
    assert c.post(f"{API}/experiments/{exp}/delete").status_code == 404


def test_someone_elses_experiment_cannot_be_deleted(make):
    app, c = make()
    exp = start(c).json()["experiment_id"]
    wait_done(c, exp)
    other = TestClient(app, headers=ORIGIN, follow_redirects=False)
    other.post("/signup", data={"name": "Other", "email": "other@example.com", "password": PW})
    assert other.post(f"{API}/experiments/{exp}/delete").status_code == 404
    assert c.get(f"{API}/experiments/{exp}").status_code == 200


def test_a_full_disk_refuses_new_runs_and_uploads(make):
    _, c = make(CRASHLAB_DB_MAX_MB="0.01")
    r = start(c)
    assert r.status_code == 507 and "storage is full" in r.json()["detail"]


def test_released_model_budget_can_be_used_again(tmp_path):
    from crashlab.store import Store

    st = Store(tmp_path / "b.db")
    assert st.reserve_model_episodes("p", 60, 100) == 40
    day = time.strftime("%Y-%m-%d", time.gmtime())
    st.release_model_episodes("p", 45, day)  # a cancelled run used only 15 of its 60
    assert st.reserve_model_episodes("p", 85, 100) == 0
