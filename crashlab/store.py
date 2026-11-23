"""Persistence.

SQLite for the vertical slice; the tables mirror the production PostgreSQL
schema in blueprint 8.3. Security-relevant properties:

* Every query is parameterised. No SQL is ever built from user input.
* Every read and write is scoped to a ``project_id``. Rows belonging to
  another project are indistinguishable from rows that do not exist (the
  API returns 404), which is what prevents IDOR.
* API keys are stored only as SHA-256 hashes of 256-bit random tokens; the
  plaintext is shown once at creation and never persisted or logged.
* Schema changes go through numbered migrations (``PRAGMA user_version``),
  and ``backup()`` / ``restore()`` use SQLite's online backup API with an
  integrity check, keeping the previous database for rollback.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PROJECT = "default"
SCOPES = ("read", "write", "admin")

MIGRATIONS: list[str] = [
    # 1 - core tables (blueprint 8.3)
    """
    CREATE TABLE experiments (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT 'default', name TEXT, status TEXT NOT NULL,
      created_at TEXT NOT NULL, config TEXT NOT NULL, summary TEXT
    );
    CREATE TABLE runs (
      id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
      agent_ref TEXT, scenario_id TEXT, repeat INTEGER, seed INTEGER, passed INTEGER, score REAL,
      primary_category TEXT, data TEXT NOT NULL
    );
    CREATE TABLE failures (
      id TEXT PRIMARY KEY, experiment_id TEXT REFERENCES experiments(id) ON DELETE CASCADE, run_id TEXT,
      category TEXT, severity TEXT, signature TEXT, data TEXT NOT NULL
    );
    CREATE TABLE reproductions (
      failure_id TEXT PRIMARY KEY, experiment_id TEXT REFERENCES experiments(id) ON DELETE CASCADE, data TEXT NOT NULL
    );
    CREATE TABLE regressions (
      id TEXT NOT NULL, project_id TEXT NOT NULL DEFAULT 'default', name TEXT, source_failure_id TEXT,
      created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, data TEXT NOT NULL, PRIMARY KEY (project_id, id)
    );
    CREATE TABLE regression_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL DEFAULT 'default', created_at TEXT NOT NULL,
      data TEXT NOT NULL
    );
    """,
    # 2 - indexes for every foreign key and hot filter (no full scans on list pages)
    """
    CREATE INDEX runs_by_experiment ON runs(experiment_id);
    CREATE INDEX runs_by_scenario ON runs(experiment_id, scenario_id);
    CREATE INDEX failures_by_run ON failures(run_id);
    CREATE INDEX failures_by_experiment ON failures(experiment_id);
    CREATE INDEX experiments_by_project ON experiments(project_id, created_at);
    CREATE INDEX regression_runs_by_project ON regression_runs(project_id, id);
    """,
    # 3 - hashed, scoped, revocable API keys
    """
    CREATE TABLE api_keys (
      id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
      scopes TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT
    );
    CREATE INDEX api_keys_by_project ON api_keys(project_id);
    """,
    # 4 - public contact form (personal data: retention-limited, IP stored only as a hash)
    """
    CREATE TABLE contact_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, name TEXT NOT NULL, email TEXT NOT NULL,
      message TEXT NOT NULL, ip_hash TEXT
    );
    CREATE INDEX contact_messages_by_time ON contact_messages(created_at);
    """,
    # 5 - campaign attribution (UTM tags) for contact-form leads
    """
    ALTER TABLE contact_messages ADD COLUMN source TEXT;
    """,
    # 6 - human accounts: password (scrypt) and/or Google sign-in, server-side sessions, reset links
    """
    CREATE TABLE users (
      id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL, password_hash TEXT,
      google_sub TEXT UNIQUE, role TEXT NOT NULL DEFAULT 'user', project_id TEXT NOT NULL, created_at TEXT NOT NULL,
      last_login_at TEXT, disabled_at TEXT
    );
    CREATE TABLE sessions (
      token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL, expires_at REAL NOT NULL
    );
    CREATE INDEX sessions_by_user ON sessions(user_id);
    CREATE TABLE password_resets (
      token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      expires_at REAL NOT NULL, used_at TEXT
    );
    """,
    # 7 - real-model spend: episodes reserved per project per UTC day (Claude/Gemini cost real money)
    """
    CREATE TABLE model_usage (
      project_id TEXT NOT NULL, day TEXT NOT NULL, episodes INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (project_id, day)
    );
    """,
]
SESSION_DAYS = 14
RESET_MINUTES = 60
ROLES = ("user", "admin")
CONTACT_RETENTION_DAYS = 180


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def hash_password(password: str, *, n: int = 2**14) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=8, p=1, dklen=32)
    return f"scrypt${n}$8$1${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    try:
        _, n, r, p, salt, want = (stored or "").split("$")
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except ValueError:
        # unknown email or Google-only account: spend the same time, so response time reveals nothing
        hashlib.scrypt(password.encode(), salt=b"x" * 16, n=2**14, r=8, p=1, dklen=32)
        return False
    return hmac.compare_digest(dk.hex(), want)


def hash_key(token: str) -> str:
    # Keys are 256-bit random tokens, so a fast hash is appropriate (no dictionary to attack);
    # user-chosen passwords would need a slow KDF such as scrypt/argon2 instead.
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    key_id: str
    project_id: str
    scopes: frozenset[str]

    def can(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes


class Store:
    def __init__(self, path: str | Path = "crashlab.db") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    # ------------------------------------------------------------ migrations --
    @property
    def schema_version(self) -> int:
        return int(self.db.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        with self._lock:
            current = self.schema_version
            for version, sql in enumerate(MIGRATIONS[current:], start=current + 1):
                # executescript commits; run each migration as one transaction
                self.db.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {int(version)};\nCOMMIT;")
            return self.schema_version

    # ----------------------------------------------------------- backup/restore --
    def backup(self, dest: str | Path) -> dict[str, Any]:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(dest))
            try:
                self.db.backup(target)
                ok = target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                version = target.execute("PRAGMA user_version").fetchone()[0]
            finally:
                target.close()
        if not ok:
            dest.unlink(missing_ok=True)
            raise RuntimeError("backup failed integrity check")
        return {"path": str(dest), "bytes": dest.stat().st_size, "schema_version": version, "created_at": _now()}

    @staticmethod
    def restore(src: str | Path, db_path: str | Path) -> dict[str, Any]:
        """Replace ``db_path`` with ``src`` after verifying it. The previous database is kept
        as ``<db>.pre-restore`` so the restore itself can be rolled back."""
        src, db_path = Path(src), Path(db_path)
        check = sqlite3.connect(str(src))
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"{src} failed integrity check; nothing restored")
            version = check.execute("PRAGMA user_version").fetchone()[0]
        finally:
            check.close()
        if version > len(MIGRATIONS):
            raise RuntimeError(f"{src} has schema v{version}, newer than this build (v{len(MIGRATIONS)})")
        rollback = db_path.with_suffix(db_path.suffix + ".pre-restore")
        if db_path.exists():
            shutil.copy2(db_path, rollback)
        for ext in ("-wal", "-shm"):
            Path(str(db_path) + ext).unlink(missing_ok=True)
        shutil.copy2(src, db_path)
        return {"restored_from": str(src), "schema_version": version,
                "rollback_copy": str(rollback) if rollback.exists() else None}

    # --------------------------------------------------------------- api keys --
    def create_api_key(self, project_id: str, name: str, scopes: list[str]) -> dict[str, Any]:
        bad = [s for s in scopes if s not in SCOPES]
        if bad or not scopes:
            raise ValueError(f"invalid scopes {bad or scopes}; choose from {SCOPES}")
        token = "acl_" + secrets.token_urlsafe(32)
        key_id = "key_" + secrets.token_hex(6)
        with self._lock, self.db:
            self.db.execute("INSERT INTO api_keys(id, project_id, name, key_hash, scopes, created_at) VALUES (?,?,?,?,?,?)",
                            (key_id, project_id, name, hash_key(token), ",".join(sorted(set(scopes))), _now()))
        return {"id": key_id, "project_id": project_id, "name": name, "scopes": sorted(set(scopes)), "key": token}

    def authenticate(self, token: str | None) -> Principal | None:
        if not token or not token.startswith("acl_") or len(token) > 200:
            return None
        h = hash_key(token)
        row = self.db.execute("SELECT id, project_id, scopes, key_hash FROM api_keys WHERE key_hash=? AND revoked_at IS NULL",
                              (h,)).fetchone()
        if row is None or not hmac.compare_digest(row["key_hash"], h):
            return None
        with self._lock, self.db:
            self.db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (_now(), row["id"]))
        return Principal(row["id"], row["project_id"], frozenset(row["scopes"].split(",")))

    def revoke_api_key(self, key_id: str, project_id: str | None = None) -> bool:
        with self._lock, self.db:
            if project_id is None:
                cur = self.db.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND revoked_at IS NULL", (_now(), key_id))
            else:
                cur = self.db.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND project_id=? AND revoked_at IS NULL",
                                      (_now(), key_id, project_id))
            return cur.rowcount == 1

    def list_api_keys(self, project_id: str | None = None) -> list[dict[str, Any]]:
        cols = "id, project_id, name, scopes, created_at, last_used_at, revoked_at"  # never the hash
        rows = (self.db.execute(f"SELECT {cols} FROM api_keys ORDER BY created_at") if project_id is None else  # noqa: S608
                self.db.execute(f"SELECT {cols} FROM api_keys WHERE project_id=? ORDER BY created_at", (project_id,)))  # noqa: S608
        return [dict(r) for r in rows]

    def has_api_keys(self) -> bool:
        return self.db.execute("SELECT 1 FROM api_keys WHERE revoked_at IS NULL LIMIT 1").fetchone() is not None

    # ---------------------------------------------------------------- users --
    USER_COLS = ("id, email, name, role, project_id, created_at, last_login_at, disabled_at, "
                 "google_sub IS NOT NULL AS google, password_hash IS NOT NULL AS has_password")

    def create_user(self, email: str, name: str, *, password: str | None = None, google_sub: str | None = None,
                    role: str = "user") -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        uid = "usr_" + secrets.token_hex(8)
        with self._lock, self.db:
            self.db.execute("INSERT INTO users(id, email, name, password_hash, google_sub, role, project_id, created_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (uid, email.strip(), name.strip(), hash_password(password) if password else None,
                             google_sub, role, "p_" + uid[4:], _now()))
        return self.get_user(uid)

    def get_user(self, uid: str) -> dict[str, Any] | None:
        cols = self.USER_COLS
        row = self.db.execute(f"SELECT {cols} FROM users WHERE id=?", (uid,)).fetchone()  # noqa: S608
        return dict(row) if row else None

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT id FROM users WHERE email=?", (email.strip(),)).fetchone()
        return self.get_user(row["id"]) if row else None

    def user_by_google(self, sub: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT id FROM users WHERE google_sub=?", (sub,)).fetchone()
        return self.get_user(row["id"]) if row else None

    def link_google(self, uid: str, sub: str) -> None:
        """Attach a Google identity to an existing account. Password sign-up never proved who owns the
        email, so a password set before this link could belong to an impostor who signed up first:
        drop it and sign every device out. The verified owner can add a new password on /account."""
        with self._lock, self.db:
            self.db.execute("UPDATE users SET google_sub=?, password_hash=NULL WHERE id=?", (sub, uid))
            self.db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))

    def verify_login(self, email: str, password: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT id, password_hash FROM users WHERE email=?", (email.strip(),)).fetchone()
        ok = check_password(password, row["password_hash"] if row else None)
        return self.get_user(row["id"]) if ok and row else None

    def set_password(self, uid: str, password: str) -> None:
        with self._lock, self.db:
            self.db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), uid))
            self.db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))  # a new password signs out every device

    def update_user(self, uid: str, *, role: str | None = None, disabled: bool | None = None) -> None:
        if role is not None and role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        with self._lock, self.db:
            if role is not None:
                self.db.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
            if disabled is not None:
                self.db.execute("UPDATE users SET disabled_at=? WHERE id=?", (_now() if disabled else None, uid))
                if disabled:
                    self.db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))

    def list_users(self, limit: int = 500) -> list[dict[str, Any]]:
        cols = self.USER_COLS
        return [dict(r) for r in self.db.execute(f"SELECT {cols} FROM users ORDER BY created_at LIMIT ?", (limit,))]  # noqa: S608

    def count_admins(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled_at IS NULL").fetchone()[0]

    def reserve_model_episodes(self, project_id: str, episodes: int, daily_limit: int) -> int | None:
        """Book `episodes` real-model episodes against today's budget. Returns what is left after booking,
        or None (and books nothing) when the booking would cross `daily_limit`."""
        day = time.strftime("%Y-%m-%d", time.gmtime())
        with self._lock, self.db:
            row = self.db.execute("SELECT episodes FROM model_usage WHERE project_id=? AND day=?", (project_id, day)).fetchone()
            used = row["episodes"] if row else 0
            if used + episodes > daily_limit:
                return None
            self.db.execute("INSERT INTO model_usage(project_id, day, episodes) VALUES (?,?,?) "
                            "ON CONFLICT(project_id, day) DO UPDATE SET episodes = episodes + excluded.episodes",
                            (project_id, day, episodes))
        return daily_limit - used - episodes

    def create_session(self, uid: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
            self.db.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                            (hash_key(token), uid, _now(), time.time() + SESSION_DAYS * 86400))
            self.db.execute("UPDATE users SET last_login_at=? WHERE id=?", (_now(), uid))
        return token

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token or len(token) > 200:
            return None
        row = self.db.execute("SELECT user_id FROM sessions WHERE token_hash=? AND expires_at > ?",
                              (hash_key(token), time.time())).fetchone()
        user = self.get_user(row["user_id"]) if row else None
        return user if user and not user["disabled_at"] else None

    def end_session(self, token: str | None) -> None:
        if token:
            with self._lock, self.db:
                self.db.execute("DELETE FROM sessions WHERE token_hash=?", (hash_key(token),))

    def create_reset(self, uid: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock, self.db:
            self.db.execute("INSERT INTO password_resets(token_hash, user_id, expires_at) VALUES (?,?,?)",
                            (hash_key(token), uid, time.time() + RESET_MINUTES * 60))
        return token

    def reset_user(self, token: str, *, consume: bool = False) -> dict[str, Any] | None:
        """The account a valid, unused, unexpired reset link belongs to; consume=True spends the link."""
        h = hash_key(token or "")
        row = self.db.execute("SELECT user_id FROM password_resets WHERE token_hash=? AND used_at IS NULL AND expires_at > ?",
                              (h, time.time())).fetchone()
        if row and consume:
            with self._lock, self.db:
                self.db.execute("UPDATE password_resets SET used_at=? WHERE token_hash=?", (_now(), h))
        return self.get_user(row["user_id"]) if row else None

    # ---------------------------------------------------------- experiments --
    def create_experiment(self, exp_id: str, name: str, config: dict[str, Any], project_id: str = DEFAULT_PROJECT) -> None:
        with self._lock, self.db:
            self.db.execute("INSERT INTO experiments(id, project_id, name, status, created_at, config) VALUES (?,?,?,?,?,?)",
                            (exp_id, project_id, name, "QUEUED", _now(), json.dumps(config)))

    def fail_unfinished(self) -> int:
        """Experiments left QUEUED or RUNNING by a restart (their worker died with the process) become FAILED."""
        with self._lock, self.db:
            return self.db.execute("UPDATE experiments SET status='FAILED' WHERE status IN ('QUEUED','RUNNING')").rowcount

    def set_status(self, exp_id: str, status: str) -> None:
        with self._lock, self.db:
            self.db.execute("UPDATE experiments SET status=? WHERE id=?", (status, exp_id))

    def save_experiment(self, result: dict[str, Any], project_id: str = DEFAULT_PROJECT) -> None:
        summary = {k: v for k, v in result.items() if k not in ("runs", "failures", "reproductions")}
        summary["runs_index"] = [_run_light(r) for r in result["runs"]]
        reps = list(result.get("reproductions", {}).values())
        summary["reproduction_summary"] = {
            "count": len(reps),
            "original_avg": round(sum(r["original_steps"] for r in reps) / len(reps), 1) if reps else None,
            "minimized_avg": round(sum(r["minimized_steps"] for r in reps) / len(reps), 1) if reps else None,
        }
        with self._lock, self.db:
            owner = self.db.execute("SELECT project_id FROM experiments WHERE id=?", (result["experiment_id"],)).fetchone()
            if owner and owner["project_id"] != project_id:
                raise PermissionError("experiment belongs to another project")
            self.db.execute(
                "INSERT INTO experiments(id, project_id, name, status, created_at, config, summary) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, status=excluded.status, summary=excluded.summary",
                (result["experiment_id"], project_id, result["name"], result["status"], result["created_at"],
                 json.dumps(result["config"]), json.dumps(summary)))
            self.db.executemany(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(r["run_id"], result["experiment_id"], r["agent_ref"], r["scenario_id"], r["repeat"], r["seed"],
                  int(r["passed"]), r["score"], r["primary_category"], json.dumps(r)) for r in result["runs"]])
            self.db.executemany(
                "INSERT OR REPLACE INTO failures VALUES (?,?,?,?,?,?,?)",
                [(f["failure_id"], result["experiment_id"], f["run_id"], f["category"], f["severity"], f["signature"],
                  json.dumps(f)) for f in result["failures"]])
            self.db.executemany(
                "INSERT OR REPLACE INTO reproductions VALUES (?,?,?)",
                [(fid, result["experiment_id"], json.dumps(rp)) for fid, rp in result["reproductions"].items()])

    def list_experiments(self, project_id: str = DEFAULT_PROJECT, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id, name, status, created_at, config, summary FROM experiments "
                               "WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, int(limit))).fetchall()
        out = []
        for r in rows:
            s = json.loads(r["summary"]) if r["summary"] else {}
            out.append({"experiment_id": r["id"], "name": r["name"], "status": r["status"], "created_at": r["created_at"],
                        "config": json.loads(r["config"]),
                        "headline": {k: {"success_rate": v["success_rate"], "reliability_score": v["reliability_score"],
                                         "episodes": v["episodes"]} for k, v in s.get("summaries", {}).items()}})
        return out

    def get_experiment(self, exp_id: str, project_id: str = DEFAULT_PROJECT) -> dict[str, Any] | None:
        r = self.db.execute("SELECT * FROM experiments WHERE id=? AND project_id=?", (exp_id, project_id)).fetchone()
        if not r:
            return None
        base = {"experiment_id": r["id"], "name": r["name"], "status": r["status"], "created_at": r["created_at"],
                "config": json.loads(r["config"])}
        return base | (json.loads(r["summary"]) if r["summary"] else {}) | {"status": r["status"]}

    # ----------------------------------------------------------------- runs --
    def get_run(self, run_id: str, project_id: str = DEFAULT_PROJECT) -> dict[str, Any] | None:
        r = self.db.execute("SELECT runs.data FROM runs JOIN experiments e ON e.id = runs.experiment_id "
                            "WHERE runs.id=? AND e.project_id=?", (run_id, project_id)).fetchone()
        return json.loads(r["data"]) if r else None

    def list_runs(self, exp_id: str, project_id: str = DEFAULT_PROJECT) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT runs.data FROM runs JOIN experiments e ON e.id = runs.experiment_id "
                               "WHERE runs.experiment_id=? AND e.project_id=? ORDER BY runs.rowid", (exp_id, project_id))
        return [_run_light(json.loads(r["data"])) for r in rows]

    def save_external_run(self, run: dict[str, Any], project_id: str = DEFAULT_PROJECT) -> None:
        exp = run["experiment_id"]
        with self._lock, self.db:
            owner = self.db.execute("SELECT project_id FROM experiments WHERE id=?", (exp,)).fetchone()
            if owner and owner["project_id"] != project_id:
                raise PermissionError("experiment id is taken by another project")
            clash = self.db.execute("SELECT e.project_id FROM runs JOIN experiments e ON e.id=runs.experiment_id "
                                    "WHERE runs.id=?", (run["run_id"],)).fetchone()
            if clash and clash["project_id"] != project_id:
                raise PermissionError("run id is taken by another project")
            self.db.execute("INSERT OR IGNORE INTO experiments(id, project_id, name, status, created_at, config) "
                            "VALUES (?,?,?,?,?,?)", (exp, project_id, f"SDK ingest · {exp}", "COMPLETED", _now(),
                                                     json.dumps({"source": "sdk"})))
            self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (run["run_id"], exp, run.get("agent_ref"), run["scenario_id"], run.get("repeat", 0),
                             run.get("seed"), int(bool(run["passed"])), run["score"], run.get("primary_category"),
                             json.dumps(run)))

    # ------------------------------------------------------------- failures --
    def get_failure(self, failure_id: str, project_id: str = DEFAULT_PROJECT) -> dict[str, Any] | None:
        r = self.db.execute("SELECT f.data, f.experiment_id FROM failures f JOIN experiments e ON e.id = f.experiment_id "
                            "WHERE f.id=? AND e.project_id=?", (failure_id, project_id)).fetchone()
        if not r:
            return None
        f = json.loads(r["data"])
        rp = self.db.execute("SELECT data FROM reproductions WHERE failure_id=?", (failure_id,)).fetchone()
        f["reproduction"] = json.loads(rp["data"]) if rp else None
        f["experiment_id"] = r["experiment_id"]
        return f

    def failures_for_run(self, run_id: str, project_id: str = DEFAULT_PROJECT) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT f.data FROM failures f JOIN experiments e ON e.id = f.experiment_id "
                               "WHERE f.run_id=? AND e.project_id=?", (run_id, project_id))
        return [json.loads(r["data"]) for r in rows]

    def save_reproduction(self, exp_id: str, repro: dict[str, Any]) -> None:
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO reproductions VALUES (?,?,?)",
                            (repro["failure_id"], exp_id, json.dumps(repro)))

    # ---------------------------------------------------------- regressions --
    def save_regression(self, reg: dict[str, Any], project_id: str = DEFAULT_PROJECT) -> None:
        with self._lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO regressions(id, project_id, name, source_failure_id, created_at, data) "
                            "VALUES (?,?,?,?,?,?)", (reg["scenario_id"], project_id, reg["regression"]["name"],
                                                     reg["regression"]["source_failure_id"], _now(), json.dumps(reg)))

    def list_regressions(self, project_id: str = DEFAULT_PROJECT, active_only: bool = True) -> list[dict[str, Any]]:
        if active_only:
            rows = self.db.execute("SELECT data FROM regressions WHERE project_id=? AND active=1 ORDER BY created_at, id",
                                   (project_id,))
        else:
            rows = self.db.execute("SELECT data FROM regressions WHERE project_id=? ORDER BY created_at, id", (project_id,))
        return [json.loads(r["data"]) for r in rows]

    def save_regression_run(self, result: dict[str, Any], project_id: str = DEFAULT_PROJECT) -> int:
        with self._lock, self.db:
            cur = self.db.execute("INSERT INTO regression_runs(project_id, created_at, data) VALUES (?,?,?)",
                                  (project_id, _now(), json.dumps(result)))
            return int(cur.lastrowid)

    def latest_regression_run(self, project_id: str = DEFAULT_PROJECT) -> dict[str, Any] | None:
        r = self.db.execute("SELECT id, created_at, data FROM regression_runs WHERE project_id=? ORDER BY id DESC LIMIT 1",
                            (project_id,)).fetchone()
        return {"id": r["id"], "created_at": r["created_at"], **json.loads(r["data"])} if r else None

    # ------------------------------------------------------------ contact form --
    def save_contact_message(self, name: str, email: str, message: str, ip_hash: str | None,
                             source: dict[str, str] | None = None) -> int:
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - CONTACT_RETENTION_DAYS * 86400))
        with self._lock, self.db:
            self.db.execute("DELETE FROM contact_messages WHERE created_at < ?", (cutoff,))  # retention promise
            cur = self.db.execute("INSERT INTO contact_messages(created_at, name, email, message, ip_hash, source) "
                                  "VALUES (?,?,?,?,?,?)",
                                  (_now(), name, email, message, ip_hash, json.dumps(source) if source else None))
            return int(cur.lastrowid)

    def list_contact_messages(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id, created_at, name, email, message, source FROM contact_messages "
                               "ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(r) | {"source": json.loads(r["source"]) if r["source"] else None} for r in rows]

    def delete_contact_message(self, msg_id: int) -> bool:
        with self._lock, self.db:
            return self.db.execute("DELETE FROM contact_messages WHERE id=?", (int(msg_id),)).rowcount == 1

    def counts(self) -> dict[str, int]:
        tables = ("experiments", "runs", "failures", "regressions", "api_keys", "contact_messages")
        # table names come from the constant tuple above, never from input
        return {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608


def _run_light(r: dict[str, Any]) -> dict[str, Any]:
    keep = ("run_id", "agent_ref", "agent", "scenario_id", "scenario_title", "category", "seed", "repeat", "passed",
            "score", "dimensions", "termination", "steps", "duration_ms", "cost_inr", "faults_activated", "failure_ids",
            "primary_category")
    return {k: r.get(k) for k in keep}
