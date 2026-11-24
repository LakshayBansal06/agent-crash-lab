"""FastAPI backend (blueprint section 8).

A modular monolith with a background worker pool. Experiments never run in
the HTTP request: ``POST /experiments`` returns 202 with an id, and progress
streams over Server-Sent Events.

Security model
--------------
* Every ``/api/v1`` route requires an API key with the right scope
  (read / write / admin) unless ``CRASHLAB_AUTH=off``, which ``crashlab serve``
  only allows on a loopback address.
* Keys belong to a project; every query is scoped to it. Another project's
  objects return 404, the same as objects that don't exist (no IDOR, no
  enumeration).
* Rate limits per key or IP, a request-size cap, strict security headers and
  CSP, and CORS only for explicitly listed origins.
* Errors return a request id, never a stack trace; the trace goes to the
  structured log under that id.

Run:  crashlab serve   ·   docs at http://localhost:8000/docs
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import queue
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Path as PathParam, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from crashlab import __version__
from crashlab.agents import LLM_REFS, REGISTRY, missing_key
from crashlab.diagnosis import Failure
from crashlab.environments import make_environment
from crashlab.experiment import assemble_ingested, run_experiment, run_regressions
from crashlab.minimizer import Reproduction, minimize
from crashlab.regression import generate_regression
from crashlab.runner import EpisodeResult, RecordedAction
from crashlab.scenarios import load_scenarios, scenario_by_id, validate_scenario
from crashlab.store import Principal, Store

from .observability import Metrics, log
from .auth import mount_auth, print_setup_link
from .site import mount_site
from .security import (DOCS_CSP, ID_PATTERN, MAX_BODY_BYTES, SECURITY_HEADERS, auth_mode, classify, client_id, client_ip, cors_origins,
                       rate_limiter, requires)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
API = "/api/v1"
MODEL_MAX_REPEATS = 3
MAX_ACTIVE_KEYS = 10
MAX_INGESTED_RUNS = 500


def ID() -> Any:  # noqa: N802 - a fresh Path() per route; FastAPI binds the parameter name to the instance
    return PathParam(pattern=ID_PATTERN)


class Job:
    def __init__(self, exp_id: str, project_id: str) -> None:
        self.exp_id, self.project_id = exp_id, project_id
        self.events: list[dict[str, Any]] = []
        self.subscribers: list[queue.Queue] = []
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.done = False

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        msg = {"type": kind, "data": data}
        with self.lock:
            self.events.append(msg)
            for q in self.subscribers:
                q.put(msg)


class LRU:
    """Cache for immutable reads (completed experiments, runs). Keys include the project."""

    def __init__(self, size: int = 256) -> None:
        self.size, self.data, self.lock = size, OrderedDict(), threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: tuple) -> Any:
        with self.lock:
            if key in self.data:
                self.hits += 1
                self.data.move_to_end(key)
                return self.data[key]
            self.misses += 1
            return None

    def put(self, key: tuple, value: Any) -> None:
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)

    def drop(self, prefix: tuple) -> None:
        with self.lock:
            for k in [k for k in self.data if k[: len(prefix)] == prefix]:
                del self.data[k]


# ------------------------------------------------------------------- models
class ExperimentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agents: list[str] = Field(default_factory=lambda: ["v1", "v2"], min_length=1, max_length=4)
    scenario_ids: list[str] | None = Field(None, max_length=500)
    repeats: int = Field(5, ge=1, le=50)
    base_seed: int = Field(2026, ge=0, le=2**31)
    name: str | None = Field(None, max_length=120)


class RegressionRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agents: list[str] = Field(default_factory=lambda: ["v1", "v2"], min_length=1, max_length=4)
    repeats: int = Field(3, ge=1, le=20)


class IngestRun(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str = Field(pattern=ID_PATTERN)
    experiment_id: str = Field(pattern=ID_PATTERN)
    scenario_id: str = Field(pattern=ID_PATTERN)
    passed: bool
    score: float = Field(ge=0, le=100)
    events: list[dict[str, Any]] = Field(max_length=5000)
    # fields the dashboard renders are typed, so nothing but the expected shape is stored
    agent_ref: str | None = Field(None, pattern=r"^[A-Za-z0-9@._\-]{1,64}$")
    agent: dict[str, str] = Field(default_factory=lambda: {"name": "sdk-agent", "version": "dev"})
    seed: int | None = None
    repeat: int = Field(0, ge=0, le=10_000)
    steps: int = Field(0, ge=0, le=100_000)
    duration_ms: int = Field(0, ge=0)
    cost_inr: float = Field(0, ge=0)
    scenario_title: str | None = Field(None, max_length=200)
    category: str | None = Field(None, pattern=r"^[a-z_]{1,64}$")
    primary_category: str | None = Field(None, pattern=r"^[A-Z_]{1,64}$")
    final_message: str | None = Field(None, max_length=20_000)
    faults_activated: list[str] = Field(default_factory=list, max_length=200)
    failure_ids: list[str] = Field(default_factory=list, max_length=200)


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=3)


# ---------------------------------------------------------------------- app
def create_app(db_path: str | None = None) -> FastAPI:
    mode = auth_mode()  # validates the env var at startup
    app = FastAPI(title="AGENT CRASH LAB API", version=__version__,
                  description="Autonomous testing, debugging and reliability platform for AI agents.")
    store = Store(db_path or os.environ.get("CRASHLAB_DB", "crashlab.db"))
    app.state.store = store
    app.state.metrics = metrics = Metrics()
    app.state.jobs = jobs = {}
    app.state.cache = cache = LRU()
    app.state.limiter = limiter = rate_limiter()
    pool = ThreadPoolExecutor(max_workers=int(os.environ.get("CRASHLAB_WORKERS", "2")))
    model_per_run = int(os.environ.get("CRASHLAB_MODEL_EPISODES_PER_RUN", "60"))
    model_per_day = int(os.environ.get("CRASHLAB_MODEL_EPISODES_PER_DAY", "200"))

    def guard_model_spend(p: Principal, agents: list[str], scenarios: int, repeats: int) -> None:
        """Claude and Gemini agents spend the server owner's API credit: admins only, small runs, a daily budget."""
        model = [a for a in agents if a in LLM_REFS]
        if not model:
            return
        if not p.can("admin"):
            raise HTTPException(403, "real-model agents (Claude, Gemini) spend this server's API credit, so only admins can run them")
        if repeats > MODEL_MAX_REPEATS:
            raise HTTPException(422, f"real-model agents run at most {MODEL_MAX_REPEATS} repeats per scenario")
        episodes = len(model) * scenarios * repeats
        if episodes > model_per_run:
            raise HTTPException(422, f"{episodes} real-model episodes requested; the limit per run is {model_per_run}")
        if store.reserve_model_episodes(p.project_id, episodes, model_per_day) is None:
            raise HTTPException(429, f"today's real-model budget ({model_per_day} episodes) is used up; it resets at 00:00 UTC")
    max_jobs = int(os.environ.get("CRASHLAB_MAX_RUNNING", "4"))
    stale = store.fail_unfinished()  # a deploy or crash killed their workers; never leave them RUNNING forever
    log("startup", version=__version__, auth=mode, schema_version=store.schema_version, db=os.path.basename(store.path),
        interrupted_experiments=stale)
    reg_state: dict[str, dict[str, Any]] = {}  # project -> {"running": bool, "error": str | None}

    def prune_jobs(keep: int = 20) -> None:
        """Finished jobs only serve late stream subscribers; keep the newest few so memory stays bounded."""
        done = [k for k, j in jobs.items() if j.done]
        for k in done[:-keep]:
            jobs.pop(k, None)

    # compress JSON and static assets; SSE is excluded by Starlette (text/event-stream is not buffered)
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    origins = cors_origins()
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["Authorization", "Content-Type"], allow_credentials=False)

    @app.middleware("http")
    async def edge(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        start = time.perf_counter()
        path = request.url.path
        status = 500
        try:
            if request.method in ("POST", "PUT", "PATCH"):
                cl = request.headers.get("content-length")
                if cl is None and request.headers.get("transfer-encoding"):
                    status = 411
                    return _error(411, "Content-Length required", rid)
                if cl and (not cl.isdigit() or int(cl) > MAX_BODY_BYTES):
                    status = 413
                    return _error(413, f"request body exceeds {MAX_BODY_BYTES} bytes", rid)
            if path.startswith(API):
                wait = limiter.check(client_id(request), classify(request.method, path))
                if wait:
                    metrics.inc("rate_limited_total")
                    status = 429
                    return _error(429, "rate limit exceeded", rid, {"Retry-After": str(int(wait) + 1)})
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            ms = round((time.perf_counter() - start) * 1000, 1)
            route = request.scope.get("route")
            tmpl = getattr(route, "path", "unmatched")
            metrics.inc("http_requests_total", method=request.method, route=tmpl, status=str(status))
            metrics.inc("http_request_seconds_sum", ms / 1000, route=tmpl)
            p = getattr(request.state, "principal", None)
            log("request", request_id=rid, method=request.method, route=tmpl, status=status, ms=ms,
                key=getattr(p, "key_id", None), client=client_ip(request))

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
            response.headers["Content-Security-Policy"] = DOCS_CSP  # Swagger UI loads from its CDN
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        response.headers["X-Request-ID"] = getattr(request.state, "request_id", "")
        path = request.url.path
        if path.startswith(("/app", API, "/docs", "/redoc", "/openapi.json", "/metrics", "/static/index")):
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")  # private surfaces never get indexed
        if os.environ.get("CRASHLAB_HSTS") == "1":
            # only enable behind HTTPS (e.g. the Caddy proxy in docker-compose.yml)
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith(API):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    def _error(status: int, detail: str, rid: str, hdrs: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse({"detail": detail, "request_id": rid}, status_code=status,
                            headers={**(hdrs or {}), **SECURITY_HEADERS, "X-Request-ID": rid})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        wants_html = "text/html" in request.headers.get("accept", "") and not request.url.path.startswith(API)
        if exc.status_code == 404 and wants_html:
            return app.state.render_404(request)
        return _error(exc.status_code, str(exc.detail), getattr(request.state, "request_id", ""), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errs = [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()][:20]
        rid = getattr(request.state, "request_id", "")
        return JSONResponse({"detail": "invalid request", "errors": errs, "request_id": rid}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "")
        log("unhandled_error", logging.ERROR, exc_info=True, request_id=rid, path=request.url.path)
        metrics.inc("errors_total")
        return _error(500, "internal error; quote the request id when reporting it", rid)

    def cached_json(request: Request, key: tuple, producer, cacheable) -> Response:
        hit = cache.get(key)
        if hit is None:
            obj = producer()
            if obj is None:
                raise HTTPException(404, "not found")
            body = json.dumps(obj, default=str).encode()
            hit = (body, '"' + hashlib.sha256(body).hexdigest()[:20] + '"')
            if cacheable(obj):
                cache.put(key, hit)
        body, etag = hit
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(body, media_type="application/json", headers={"ETag": etag, "Cache-Control": "private, no-cache"})

    # ---------------------------------------------------------------- health
    @app.get("/health")
    @app.get(f"{API}/health")
    def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        try:
            store.db.execute("SELECT 1").fetchone()
            ok = True
        except Exception:  # noqa: BLE001
            ok = False
        return JSONResponse({"status": "ready" if ok else "unavailable"}, status_code=200 if ok else 503)

    @app.get("/metrics", response_class=PlainTextResponse)
    def prometheus(p: Principal = requires("admin")) -> str:
        running = sum(1 for j in jobs.values() if not j.done)
        rows = store.counts()
        return metrics.render({"experiments_running": running, "cache_hits": cache.hits, "cache_misses": cache.misses,
                               **{f"db_{k}_rows": v for k, v in rows.items()}})

    @app.get(f"{API}/whoami")
    def whoami(p: Principal = requires("read")) -> dict[str, Any]:
        out = {"key_id": p.key_id, "project_id": p.project_id, "scopes": sorted(p.scopes), "auth": auth_mode(),
               "sdk_wheel": next((f"/sdk/{w.name}" for w in sorted((WEB_DIR / "sdk").glob("*.whl"))), None)}
        if p.key_id.startswith("user:") and (u := store.get_user(p.key_id[5:])):
            out["user"] = {k: u[k] for k in ("name", "email", "role")}
        return out

    # ------------------------------------------------------------- api keys
    # Every signed-in user manages keys for their own workspace (that is how their SDK, CLI and CI connect).
    # Keys themselves can't mint keys, so a leaked key can't multiply; only admins can grant the admin scope.
    def key_manager(p: Principal) -> None:
        if not (p.key_id.startswith("user:") or p.can("admin")):
            raise HTTPException(403, "API keys are managed from a signed-in session (or with an admin key)")

    @app.get(f"{API}/api-keys")
    def list_keys(p: Principal = requires("write")) -> list[dict[str, Any]]:
        key_manager(p)
        return store.list_api_keys(p.project_id)

    @app.post(f"{API}/api-keys", status_code=201)
    def create_key(body: KeyIn, p: Principal = requires("write")) -> dict[str, Any]:
        key_manager(p)
        if "admin" in body.scopes and not p.can("admin"):
            raise HTTPException(403, "only admins can create admin keys")
        if sum(1 for k in store.list_api_keys(p.project_id) if not k["revoked_at"]) >= MAX_ACTIVE_KEYS:
            raise HTTPException(409, f"a workspace can have at most {MAX_ACTIVE_KEYS} active keys; revoke one first")
        try:
            k = store.create_api_key(p.project_id, body.name, body.scopes)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        log("api_key_created", key=k["id"], project=p.project_id, by=p.key_id, scopes=k["scopes"])
        return k  # the only time the plaintext key is ever returned

    @app.post(f"{API}/api-keys/{{key_id}}/revoke")
    def revoke_key(key_id: str = ID(), p: Principal = requires("write")) -> dict[str, Any]:
        key_manager(p)
        if not store.revoke_api_key(key_id, p.project_id):
            raise HTTPException(404, "not found")
        log("api_key_revoked", key=key_id, by=p.key_id)
        return {"id": key_id, "revoked": True}

    # ------------------------------------------------- agents & datasets
    @app.get(f"{API}/agents")
    def agents(p: Principal = requires("read")) -> list[dict[str, Any]]:
        seen, out = set(), []
        for cls in REGISTRY.values():
            if cls in seen:
                continue
            seen.add(cls)
            a = cls()
            out.append({"ref": "v1" if a.version.startswith("1") else "v2", "id": f"{a.name}@{a.version}",
                        **a.describe(), "doc": (cls.__doc__ or "").strip().split("\n")[0]})
        out.append({"ref": "llm", "id": "llm-agent", "name": "llm-agent",
                    "version": os.environ.get("CRASHLAB_MODEL", "claude-sonnet-5-5"),
                    "framework": "anthropic-messages", "available": bool(os.environ.get("ANTHROPIC_API_KEY"))})
        out.append({"ref": "gemini", "id": "gemini-agent", "name": "gemini-agent",
                    "version": os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
                    "framework": "gemini-generate-content", "available": bool(os.environ.get("GEMINI_API_KEY"))})
        return out

    @app.get(f"{API}/scenarios")
    def scenarios(p: Principal = requires("read")) -> list[dict[str, Any]]:
        return load_scenarios()

    @app.get(f"{API}/scenarios/{{scenario_id}}")
    def scenario(scenario_id: str = ID(), p: Principal = requires("read")) -> dict[str, Any]:
        try:
            return scenario_by_id(scenario_id)
        except KeyError:
            raise HTTPException(404, "not found") from None

    @app.post(f"{API}/scenarios/validate")
    def validate(body: dict[str, Any], p: Principal = requires("read")) -> dict[str, Any]:
        rep = validate_scenario(body)
        return {"ok": rep.ok, **rep}

    @app.get(f"{API}/environments/{{name}}/tools")
    def tools(name: str = PathParam(pattern=r"^[a-z_]{1,32}$"), p: Principal = requires("read")) -> list[dict[str, Any]]:
        try:
            env = make_environment({"environment": name, "initial_state": {"fixture": "base_store"}})
        except KeyError:
            raise HTTPException(404, "not found") from None
        return [{**t.json_schema(), "mutating": t.mutating, "latency_ms": t.latency_ms} for t in env.tools().values()]

    # --------------------------------------------------------- experiments
    @app.post(f"{API}/experiments", status_code=202)
    def create_experiment(body: ExperimentIn, p: Principal = requires("write")) -> dict[str, Any]:
        for a in body.agents:
            if a not in REGISTRY and a not in LLM_REFS:
                raise HTTPException(422, f"unknown agent {a!r}")
            if key := missing_key(a):
                raise HTTPException(422, f"agent {a!r} needs {key} set on the server")
        if sum(1 for j in jobs.values() if not j.done) >= max_jobs:
            raise HTTPException(429, "too many experiments running; try again shortly", headers={"Retry-After": "10"})
        sc = load_scenarios()
        if body.scenario_ids:
            wanted = set(body.scenario_ids)
            sc = [s for s in sc if s["scenario_id"] in wanted]
            if not sc:
                raise HTTPException(422, "no matching scenarios")
        guard_model_spend(p, body.agents, len(sc), body.repeats)
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        name = body.name or f"{' vs '.join(body.agents)} · {len(sc)} scenarios × {body.repeats}"
        store.create_experiment(exp_id, name, body.model_dump(), project_id=p.project_id)
        prune_jobs()
        job = jobs[exp_id] = Job(exp_id, p.project_id)
        metrics.inc("experiments_started_total")
        log("experiment_queued", experiment=exp_id, project=p.project_id, key=p.key_id,
            episodes=len(body.agents) * len(sc) * body.repeats)

        def work() -> None:
            store.set_status(exp_id, "RUNNING")
            t0 = time.perf_counter()
            try:
                def progress(kind: str, data: dict[str, Any]) -> None:
                    if kind == "run.completed":
                        metrics.inc("episodes_total", passed=str(data["passed"]).lower())
                    job.publish(kind, data)

                res = run_experiment(body.agents, sc, repeats=body.repeats, base_seed=body.base_seed, name=name,
                                     experiment_id=exp_id, on_progress=progress, should_cancel=job.cancel.is_set)
                store.save_experiment(res, project_id=p.project_id)
                for reg in res["regressions"]:
                    store.save_regression(reg, project_id=p.project_id)
                metrics.inc("failures_detected_total", len(res["failures"]))
                log("experiment_finished", experiment=exp_id, status=res["status"],
                    seconds=round(time.perf_counter() - t0, 2), failures=len(res["failures"]))
            except Exception:  # noqa: BLE001 - the worker must never die silently
                store.set_status(exp_id, "FAILED")
                metrics.inc("experiments_failed_total")
                log("experiment_failed", logging.ERROR, exc_info=True, experiment=exp_id)
                job.publish("experiment.failed", {"error": "experiment failed; see server logs", "experiment_id": exp_id})
            finally:
                job.done = True
                job.publish("stream.end", {})

        pool.submit(work)
        return {"experiment_id": exp_id, "status": "QUEUED", "episodes": len(body.agents) * len(sc) * body.repeats,
                "stream": f"{API}/experiments/{exp_id}/stream"}

    @app.get(f"{API}/experiments")
    def list_experiments(p: Principal = requires("read")) -> list[dict[str, Any]]:
        return store.list_experiments(p.project_id)

    @app.get(f"{API}/experiments/{{exp_id}}")
    def get_experiment(request: Request, exp_id: str = ID(), p: Principal = requires("read")) -> Response:
        job = jobs.get(exp_id)
        if job and not job.done and job.project_id == p.project_id:
            e = store.get_experiment(exp_id, p.project_id)
            done = [m for m in job.events if m["type"] == "run.completed"]
            e["progress"] = {"done": len(done), "total": done[-1]["data"]["total"] if done else None}
            return JSONResponse(e)
        return cached_json(request, (p.project_id, "exp", exp_id), lambda: store.get_experiment(exp_id, p.project_id),
                           lambda e: e["status"] in ("COMPLETED", "CANCELLED", "FAILED"))

    @app.post(f"{API}/experiments/{{exp_id}}/cancel")
    def cancel_experiment(exp_id: str = ID(), p: Principal = requires("write")) -> dict[str, Any]:
        job = jobs.get(exp_id)
        if not job or job.project_id != p.project_id:
            raise HTTPException(404, "not found")
        if job.done:
            raise HTTPException(409, "experiment is not running")
        job.cancel.set()
        log("experiment_cancel_requested", experiment=exp_id, key=p.key_id)
        return {"experiment_id": exp_id, "status": "CANCELLING"}

    @app.get(f"{API}/experiments/{{exp_id}}/runs")
    def experiment_runs(exp_id: str = ID(), p: Principal = requires("read")) -> list[dict[str, Any]]:
        if store.get_experiment(exp_id, p.project_id) is None:
            raise HTTPException(404, "not found")
        return store.list_runs(exp_id, p.project_id)

    @app.get(f"{API}/experiments/{{exp_id}}/stream")
    async def stream(exp_id: str = ID(), p: Principal = requires("read")) -> StreamingResponse:
        job = jobs.get(exp_id)
        if not job or job.project_id != p.project_id:
            raise HTTPException(404, "not found")
        q: queue.Queue = queue.Queue()
        with job.lock:
            backlog = list(job.events)
            job.subscribers.append(q)

        async def gen():
            try:
                for m in backlog:
                    yield f"event: {m['type']}\ndata: {json.dumps(m['data'], default=str)}\n\n"
                    if m["type"] == "stream.end":
                        return
                while True:
                    try:
                        m = await asyncio.to_thread(q.get, True, 15)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    yield f"event: {m['type']}\ndata: {json.dumps(m['data'], default=str)}\n\n"
                    if m["type"] == "stream.end":
                        return
            finally:
                with job.lock:
                    if q in job.subscribers:
                        job.subscribers.remove(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # ---------------------------------------------------------------- runs
    def _run(run_id: str, project: str) -> dict[str, Any]:
        r = store.get_run(run_id, project)
        if not r:
            raise HTTPException(404, "not found")
        return r

    @app.get(f"{API}/runs/{{run_id}}")
    def get_run(request: Request, run_id: str = ID(), p: Principal = requires("read")) -> Response:
        def produce():
            r = store.get_run(run_id, p.project_id)
            return r | {"failures": store.failures_for_run(run_id, p.project_id)} if r else None
        return cached_json(request, (p.project_id, "run", run_id), produce, lambda r: True)

    @app.get(f"{API}/runs/{{run_id}}/trace")
    def get_trace(run_id: str = ID(), p: Principal = requires("read")) -> list[dict[str, Any]]:
        return _run(run_id, p.project_id)["events"]

    @app.get(f"{API}/runs/{{run_id}}/failures")
    def get_run_failures(run_id: str = ID(), p: Principal = requires("read")) -> list[dict[str, Any]]:
        _run(run_id, p.project_id)
        return store.failures_for_run(run_id, p.project_id)

    # ------------------------------------------------------------ failures
    def _failure(fid: str, project: str) -> dict[str, Any]:
        f = store.get_failure(fid, project)
        if not f:
            raise HTTPException(404, "not found")
        return f

    @app.get(f"{API}/failures/{{failure_id}}")
    def get_failure(failure_id: str = ID(), p: Principal = requires("read")) -> dict[str, Any]:
        return _failure(failure_id, p.project_id)

    @app.post(f"{API}/failures/{{failure_id}}/minimize")
    def minimize_failure(failure_id: str = ID(), p: Principal = requires("write")) -> dict[str, Any]:
        f = _failure(failure_id, p.project_id)
        run = _run(f["run_id"], p.project_id)
        repro = minimize(scenario_by_id(f["scenario_id"]), _episode_from_run(run), _failure_obj(f)).to_dict()
        store.save_reproduction(f["experiment_id"], repro)
        return repro

    @app.post(f"{API}/failures/{{failure_id}}/regression", status_code=201)
    def save_regression(failure_id: str = ID(), p: Principal = requires("write")) -> dict[str, Any]:
        f = _failure(failure_id, p.project_id)
        run = _run(f["run_id"], p.project_id)
        sc = scenario_by_id(f["scenario_id"])
        rp = f.get("reproduction")
        repro = Reproduction(**rp) if rp else minimize(sc, _episode_from_run(run), _failure_obj(f))
        reg = generate_regression(_failure_obj(f), sc, repro, activated=set(run["faults_activated"]))
        store.save_regression(reg, project_id=p.project_id)
        return reg

    # --------------------------------------------------------- regressions
    @app.get(f"{API}/regressions")
    def list_regressions(p: Principal = requires("read")) -> list[dict[str, Any]]:
        return store.list_regressions(p.project_id)

    @app.post(f"{API}/regression-suites/default/run", status_code=202)
    def run_regression_suite(body: RegressionRunIn, p: Principal = requires("write")) -> dict[str, Any]:
        """Re-runs every saved regression test in the background (real-model agents can take minutes).
        Poll GET .../status until running is false, then read GET .../latest."""
        regs = store.list_regressions(p.project_id)
        if not regs:
            raise HTTPException(409, "regression suite is empty")
        for a in body.agents:
            if a not in REGISTRY and a not in LLM_REFS:
                raise HTTPException(422, f"unknown agent {a!r}")
            if key := missing_key(a):
                raise HTTPException(422, f"agent {a!r} needs {key} set on the server")
        state = reg_state.setdefault(p.project_id, {"running": False, "error": None})
        if state["running"]:
            raise HTTPException(409, "the regression suite is already running")
        guard_model_spend(p, body.agents, len(regs), body.repeats)
        state.update(running=True, error=None)

        def work() -> None:
            try:
                res = run_regressions(regs, body.agents, repeats=body.repeats)
                store.save_regression_run(res, p.project_id)
            except Exception:  # noqa: BLE001 - reported through /status, logged with a trace
                log("regression_run_failed", logging.ERROR, exc_info=True, project=p.project_id)
                state["error"] = "the regression run failed; see the server log"
            finally:
                state["running"] = False

        pool.submit(work)
        return {"status": "RUNNING", "tests": len(regs), "status_url": f"{API}/regression-suites/default/status"}

    @app.get(f"{API}/regression-suites/default/status")
    def regression_status(p: Principal = requires("read")) -> dict[str, Any]:
        return reg_state.get(p.project_id, {"running": False, "error": None})

    @app.get(f"{API}/regression-suites/default/latest")
    def latest_regression_run(p: Principal = requires("read")) -> Any:
        # "No suite has run yet" is a normal state on a fresh install, not an error:
        # answer 204 so browsers don't log a failed request on every dashboard load.
        r = store.latest_regression_run(p.project_id)
        if not r:
            return Response(status_code=204)
        return r

    # ------------------------------------------------------- contact messages
    @app.get(f"{API}/contact-messages")
    def contact_messages(p: Principal = requires("admin")) -> list[dict[str, Any]]:
        return store.list_contact_messages()

    @app.post(f"{API}/contact-messages/{{msg_id}}/delete")
    def delete_contact_message(msg_id: int, p: Principal = requires("admin")) -> dict[str, Any]:
        if not store.delete_contact_message(msg_id):
            raise HTTPException(404, "not found")
        log("contact_message_deleted", id=msg_id, by=p.key_id)
        return {"id": msg_id, "deleted": True}

    # ----------------------------------------------------------- ingestion
    @app.post(f"{API}/ingest/runs", status_code=201)
    def ingest_run(run: IngestRun, p: Principal = requires("write")) -> dict[str, Any]:
        """SDK upload endpoint. The project always comes from the key, never the payload."""
        if len(store.experiment_runs_full(run.experiment_id, p.project_id)) >= MAX_INGESTED_RUNS:
            raise HTTPException(413, f"an SDK experiment holds at most {MAX_INGESTED_RUNS} runs; start a new one")
        try:
            store.save_external_run(run.model_dump(), project_id=p.project_id)
        except PermissionError:
            raise HTTPException(409, "id already in use") from None
        # rebuild the experiment summary so the dashboard shows SDK runs exactly like built-in ones
        exp = store.get_experiment(run.experiment_id, p.project_id)
        result = assemble_ingested(run.experiment_id, exp["name"], exp["created_at"],
                                   store.experiment_runs_full(run.experiment_id, p.project_id),
                                   {s["scenario_id"]: s for s in load_scenarios()})
        if result["runs"]:
            store.save_experiment(result, project_id=p.project_id)
        cache.drop((p.project_id, "run", run.run_id))
        cache.drop((p.project_id, "exp", run.experiment_id))
        return {"run_id": run.run_id, "stored": True}

    # ----------------------------------------------------------------- web
    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
        if (WEB_DIR / "sdk").is_dir():  # the SDK as a pip-installable wheel, built into the Docker image
            app.mount("/sdk", StaticFiles(directory=WEB_DIR / "sdk"), name="sdk")

        @app.get("/app/", include_in_schema=False)
        def dashboard() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml",
                                headers={"Cache-Control": "public, max-age=86400"})

    mount_site(app)
    mount_auth(app)
    return app


def _episode_from_run(run: dict[str, Any]) -> EpisodeResult:
    actions, idx = [], 0
    for e in run["events"]:
        if e["event_type"] == "tool_call":
            actions.append(RecordedAction(idx, "tool", e["tool"]["name"], e["tool"].get("arguments", {}),
                                          event_id=e["event_id"]))
            idx += 1
        elif e["event_type"] == "fault_activated" and actions:
            actions[-1].faults.append(e["metadata"]["fault_id"])
        elif e["event_type"] == "final_answer":
            actions.append(RecordedAction(idx, "final", text=e.get("message"), event_id=e["event_id"]))
            idx += 1
    breaches = [{"invariant": e["metadata"]["expression"], "event_id": e["event_id"]}
                for e in run["events"] if e["event_type"] == "invariant_breach"]
    faults = [{**e["metadata"], "event_id": e["event_id"]} for e in run["events"] if e["event_type"] == "fault_activated"]
    return EpisodeResult(run["run_id"], run["scenario_id"], run["agent"], run["seed"], run["termination"], run["events"],
                         run["initial_state"], run["final_state"], run["final_message"], actions, faults, breaches,
                         run["duration_ms"], run["usage"], run.get("status_history", []))


def _failure_obj(f: dict[str, Any]) -> Failure:
    fields = Failure.__dataclass_fields__
    return Failure(**{k: v for k, v in f.items() if k in fields})


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """Lazy ``server.app:app`` for uvicorn, so importing this module has no side effects."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
            print_setup_link(_app)
        return _app
    raise AttributeError(name)
