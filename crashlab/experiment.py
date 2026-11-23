"""Experiments: agents x scenarios x repeats, with the full
Stress -> Detect -> Explain -> Minimize -> Prevent -> Compare loop."""
from __future__ import annotations

import hashlib
import time
import uuid
from collections import Counter, defaultdict
from typing import Any
from collections.abc import Callable

from .agents import make_agent
from .diagnosis import Failure, diagnose
from .evaluators import DEFAULT_WEIGHTS, DIMENSION_LABELS, evaluate_episode
from .metrics import mcnemar_exact, mean_or_none, paired_bootstrap_delta, pass_at_k, pass_hat_k, percentile, wilson
from .minimizer import minimize
from .regression import generate_regression
from .runner import EpisodeResult, episode_cost_inr, run_episode

ProgressFn = Callable[[str, dict[str, Any]], None]


def stable_seed(base: int, scenario_id: str, repeat: int) -> int:
    return int(hashlib.sha256(f"{base}|{scenario_id}|{repeat}".encode()).hexdigest()[:8], 16)


def run_experiment(
    agents: list[str],
    scenarios: list[dict[str, Any]],
    *,
    repeats: int = 5,
    base_seed: int = 2026,
    name: str | None = None,
    experiment_id: str | None = None,
    on_progress: ProgressFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
    minimize_failures: bool = True,
) -> dict[str, Any]:
    exp_id = experiment_id or f"exp_{uuid.uuid4().hex[:8]}"
    emit = on_progress or (lambda *_: None)
    started = time.time()
    total = len(agents) * len(scenarios) * repeats
    by_id = {s["scenario_id"]: s for s in scenarios}
    emit("experiment.started", {"experiment_id": exp_id, "total": total})

    runs: list[dict[str, Any]] = []
    episodes: dict[str, EpisodeResult] = {}
    failures: list[Failure] = []
    status = "COMPLETED"
    done = 0
    for ref in agents:
        for s in scenarios:
            for r in range(repeats):
                if should_cancel and should_cancel():
                    status = "CANCELLED"
                    break
                seed = stable_seed(base_seed, s["scenario_id"], r)
                agent = make_agent(ref)
                run_id = f"{exp_id}.{agent.version}.{s['scenario_id'][-4:]}.r{r}"
                emit("run.started", {"run_id": run_id, "agent": ref, "scenario_id": s["scenario_id"], "repeat": r})
                ep = run_episode(s, agent, seed, run_id)
                card = evaluate_episode(s, ep)
                inconclusive = ep.termination == "provider_error"  # the model provider failed: no verdict on the agent
                fl = [] if inconclusive else diagnose(s, ep, card)
                failures += fl
                episodes[run_id] = ep
                run = {
                    "run_id": run_id, "experiment_id": exp_id, "agent_ref": ref, "agent": ep.agent,
                    "scenario_id": s["scenario_id"], "scenario_title": s.get("title"), "category": s["category"],
                    "seed": seed, "repeat": r, "status": "INCONCLUSIVE" if inconclusive else "COMPLETED",
                    "passed": card.passed and not inconclusive, "score": card.score,
                    "dimensions": card.dimensions, "termination": ep.termination, "steps": len(ep.actions),
                    "duration_ms": ep.duration_ms, "usage": ep.usage, "cost_inr": episode_cost_inr(ep.usage),
                    "final_message": ep.final_message, "faults_activated": [f["fault_id"] for f in ep.fault_log],
                    "failure_ids": [f.failure_id for f in fl],
                    "primary_category": fl[0].category if fl else None,
                    "scorecard": card.to_dict(), "events": ep.events,
                    "initial_state": ep.initial_state, "final_state": ep.final_state,
                    "status_history": ep.status_history + (["DIAGNOSING"] if fl else []) + ["COMPLETED"],
                }
                runs.append(run)
                done += 1
                emit("run.completed", {k: run[k] for k in ("run_id", "agent_ref", "scenario_id", "repeat", "passed",
                                                           "score", "primary_category", "steps", "duration_ms", "status")}
                     | {"done": done, "total": total, "events": ep.events[-12:]})
                if fl:
                    emit("run.failed", {"run_id": run_id, "category": fl[0].category, "title": fl[0].title})

    clusters = cluster_failures(failures, runs)
    reproductions: dict[str, dict[str, Any]] = {}
    regressions: list[dict[str, Any]] = []
    if minimize_failures:
        for cl in clusters:
            rep = cl["_rep"]
            emit("diagnosis.minimizing", {"signature": cl["signature"], "failure_id": rep.failure_id})
            repro = minimize(by_id[rep.scenario_id], episodes[rep.run_id], rep)
            reproductions[rep.failure_id] = repro.to_dict()
            cl["reproduction_id"] = rep.failure_id
            cl["_repro"] = repro
        # one regression per (agent version, source scenario), covering every failure mode found there
        names: Counter[str] = Counter()
        for (_ver, sid), group in _group(clusters, lambda c: (c["agent_version"], c["scenario_id"])):
            lead = group[0]  # clusters are severity-sorted
            activated = {f["fault_id"] for c in group for fid in c["failure_ids"][:1]
                         for f in episodes[_run_of(failures, fid)].fault_log}
            reg = generate_regression(lead["_rep"], by_id[sid], lead["_repro"], activated=activated)
            names[reg["regression"]["name"]] += 1
            if names[reg["regression"]["name"]] > 1:
                reg["regression"]["name"] += f"_{sid[-4:]}"
            reg["regression"]["covers"] = [{"signature": c["signature"], "category": c["category"], "title": c["title"]}
                                           for c in group]
            regressions.append(reg)
            for c in group:
                c["regression_id"] = reg["scenario_id"]
    for cl in clusters:
        cl.pop("_rep", None)
        cl.pop("_repro", None)
    modes = failure_modes(clusters)

    scored = [r for r in runs if r["status"] != "INCONCLUSIVE"]  # provider errors never count for or against an agent
    summaries = {ref: {**summarize([r for r in scored if r["agent_ref"] == ref], by_id, repeats),
                       "inconclusive": sum(1 for r in runs if r["agent_ref"] == ref and r["status"] == "INCONCLUSIVE")}
                 for ref in agents}
    comparison = compare(scored, agents[0], agents[1]) if len(agents) >= 2 else None
    result = {
        "experiment_id": exp_id,
        "name": name or f"{' vs '.join(agents)} · {len(scenarios)} scenarios × {repeats}",
        "status": status,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "wall_time_s": round(time.time() - started, 2),
        "config": {"agents": agents, "scenario_ids": [s["scenario_id"] for s in scenarios], "repeats": repeats,
                   "base_seed": base_seed, "weights": DEFAULT_WEIGHTS},
        "dimension_labels": DIMENSION_LABELS,
        "summaries": summaries,
        "comparison": comparison,
        "clusters": clusters,
        "failure_modes": modes,
        "failures": [f.to_dict() for f in failures],
        "reproductions": reproductions,
        "regressions": regressions,
        "runs": runs,
    }
    emit("experiment.completed", {"experiment_id": exp_id, "status": status})
    return result


def _group(items, key):
    out: dict[Any, list[Any]] = {}
    for it in items:
        out.setdefault(key(it), []).append(it)
    return list(out.items())


def _run_of(failures: list[Failure], failure_id: str) -> str:
    return next(f.run_id for f in failures if f.failure_id == failure_id)


def failure_modes(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll per-scenario clusters up into distinct failure *modes* (same category + title)."""
    modes = []
    for (ver, cat, title), group in _group(clusters, lambda c: (c["agent_version"], c["category"], c["title"])):
        modes.append({"agent_version": ver, "category": cat, "title": title, "severity": group[0]["severity"],
                      "occurrences": sum(c["occurrences"] for c in group),
                      "scenarios": sorted({c["scenario_id"] for c in group}),
                      "clusters": [c["signature"] for c in group]})
    sev = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    modes.sort(key=lambda m: (m["agent_version"], sev[m["severity"]], -m["occurrences"]))
    return modes


def cluster_failures(failures: list[Failure], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Failure]] = defaultdict(list)
    for f in failures:
        groups[(f.agent["version"], f.signature)].append(f)
    per_scenario_runs = Counter((r["agent"]["version"], r["scenario_id"]) for r in runs)
    out = []
    for (ver, sig), fs in groups.items():
        rep = sorted(fs, key=lambda f: (not f.primary, f.seed))[0]
        n = per_scenario_runs[(ver, rep.scenario_id)]
        out.append({
            "signature": sig, "category": rep.category, "severity": rep.severity, "title": rep.title,
            "scenario_id": rep.scenario_id, "agent_version": ver, "occurrences": len(fs), "runs_in_scenario": n,
            "rate": round(len(fs) / n, 3) if n else None, "failure_ids": [f.failure_id for f in fs],
            "representative_failure_id": rep.failure_id, "_rep": rep,
        })
    sev = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    out.sort(key=lambda c: (sev[c["severity"]], -c["occurrences"]))
    return out


def summarize(runs: list[dict[str, Any]], by_id: dict[str, dict[str, Any]], repeats: int) -> dict[str, Any]:
    n = len(runs)
    c = sum(r["passed"] for r in runs)
    lo, hi = wilson(c, n)
    per_s: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        per_s[r["scenario_id"]].append(r)
    per_scenario = {}
    for sid, rs in per_s.items():
        ns, cs = len(rs), sum(x["passed"] for x in rs)
        per_scenario[sid] = {
            "n": ns, "c": cs, "rate": cs / ns, "ci": wilson(cs, ns),
            "pass_hat_k": [pass_hat_k(ns, cs, k) for k in range(1, ns + 1)],
            "pass_at_k": [pass_at_k(ns, cs, k) for k in range(1, ns + 1)],
            "mean_score": mean_or_none(x["score"] for x in rs),
            "categories": dict(Counter(x["primary_category"] for x in rs if x["primary_category"])),
        }
    kmax = min(x["n"] for x in per_scenario.values()) if per_scenario else 0
    phk = [round(sum(v["pass_hat_k"][k] for v in per_scenario.values()) / len(per_scenario), 4) for k in range(kmax)]
    pak = [round(sum(v["pass_at_k"][k] for v in per_scenario.values()) / len(per_scenario), 4) for k in range(kmax)]

    def failed_check(r: dict[str, Any], name: str | None = None, evaluator: str | None = None) -> bool:
        return any((name is None or g["name"] == name) and (evaluator is None or g["evaluator"] == evaluator)
                   for g in r["scorecard"]["gating_failures"])

    sec_runs = [r for r in runs if by_id[r["scenario_id"]]["category"] == "prompt_injection_security"]
    faulted = [r for r in runs if r["faults_activated"]]
    replied = [r for r in runs if r["final_message"]]
    lat = [r["duration_ms"] for r in runs]
    cost = sum(r["cost_inr"] for r in runs)
    failures = Counter(cat for r in runs for cat in _cats(r))
    return {
        "agent": runs[0]["agent"] if runs else None,
        "episodes": n, "successes": c, "success_rate": c / n if n else 0, "success_ci": [lo, hi],
        "reliability_score": mean_or_none(r["score"] for r in runs),
        "pass_hat_k": phk, "pass_at_k": pak,
        "strict_consistency": sum(1 for v in per_scenario.values() if v["c"] == v["n"]) / len(per_scenario) if per_scenario else 0,
        "dimensions": {d: mean_or_none(r["dimensions"].get(d) for r in runs) for d in DEFAULT_WEIGHTS},
        "rates": {
            "policy_violation": sum(failed_check(r, evaluator="policy") for r in runs) / n if n else 0,
            "security_attack_success": (sum(failed_check(r, evaluator="security") for r in sec_runs) / len(sec_runs)) if sec_runs else 0,
            "fault_recovery": (sum(not failed_check(r, evaluator="recovery") for r in faulted) / len(faulted)) if faulted else None,
            "duplicate_side_effect": sum(failed_check(r, "no duplicate side effects") for r in runs) / n if n else 0,
            "false_completion": (sum(failed_check(r, "claims match the final system state") for r in replied) / len(replied)) if replied else 0,
        },
        "latency_ms": {"mean": round(sum(lat) / n) if n else 0, "p50": round(percentile(lat, .5)) if lat else 0,
                       "p95": round(percentile(lat, .95)) if lat else 0},
        "cost_inr": {"total": round(cost, 2), "per_episode": round(cost / n, 3) if n else 0,
                     "per_success": round(cost / c, 3) if c else None},
        "tokens": sum(r["usage"]["input_tokens"] + r["usage"]["output_tokens"] for r in runs),
        "failure_distribution": dict(failures.most_common()),
        "per_scenario": per_scenario,
    }


def _cats(run: dict[str, Any]) -> list[str]:
    return [run["primary_category"]] if run["primary_category"] else []


def compare(runs: list[dict[str, Any]], base: str, cand: str) -> dict[str, Any]:
    a = {(r["scenario_id"], r["repeat"]): r for r in runs if r["agent_ref"] == base}
    b = {(r["scenario_id"], r["repeat"]): r for r in runs if r["agent_ref"] == cand}
    keys = sorted(set(a) & set(b))
    pa = [float(a[k]["passed"]) for k in keys]
    pb = [float(b[k]["passed"]) for k in keys]
    b01 = sum(1 for x, y in zip(pa, pb, strict=True) if x == 0 and y == 1)
    b10 = sum(1 for x, y in zip(pa, pb, strict=True) if x == 1 and y == 0)
    per_s = defaultdict(lambda: [0, 0, 0])
    for k in keys:
        per_s[k[0]][0] += a[k]["passed"]
        per_s[k[0]][1] += b[k]["passed"]
        per_s[k[0]][2] += 1
    fixed = [s for s, (x, y, n) in per_s.items() if y > x]
    regressed = [s for s, (x, y, n) in per_s.items() if y < x]
    return {
        "baseline": base, "candidate": cand, "paired_episodes": len(keys),
        "success_delta": paired_bootstrap_delta(pa, pb) if keys else None,
        "score_delta": paired_bootstrap_delta([a[k]["score"] for k in keys], [b[k]["score"] for k in keys]) if keys else None,
        "discordant": {"fixed": b01, "broken": b10}, "mcnemar_p": mcnemar_exact(b01, b10),
        "scenarios_improved": sorted(fixed), "scenarios_regressed": sorted(regressed),
        "per_scenario": {s: {"baseline": x / n, "candidate": y / n} for s, (x, y, n) in sorted(per_s.items())},
    }


def run_regressions(regressions: list[dict[str, Any]], agents: list[str], repeats: int = 3,
                    base_seed: int = 4242) -> dict[str, Any]:
    rows = []
    for reg in regressions:
        row = {"scenario_id": reg["scenario_id"], "name": reg["regression"]["name"],
               "category": reg["regression"]["category"], "source": reg["regression"]["source_scenario_id"],
               "discovered_on": reg["regression"]["discovered_on"]["version"], "results": {}}
        for ref in agents:
            passes, n, cats = 0, 0, Counter()
            for r in range(repeats):
                seed = stable_seed(base_seed, reg["scenario_id"], r)
                ep = run_episode(reg, make_agent(ref), seed, f"reg.{reg['scenario_id']}.{ref}.r{r}")
                if ep.termination == "provider_error":
                    continue  # inconclusive: the model provider failed, not the agent
                n += 1
                card = evaluate_episode(reg, ep)
                passes += card.passed
                if not card.passed:
                    cats.update(f.category for f in diagnose(reg, ep, card)[:1])
            status = ("inconclusive" if n == 0 else "pass" if passes == n else "flaky" if passes else "fail")
            row["results"][ref] = {"passed": passes, "n": n, "inconclusive": repeats - n, "status": status,
                                   "categories": dict(cats)}
        rows.append(row)
    # an inconclusive test can't prove the fix holds, so it blocks the release gate like a failure would
    gate = {ref: all(row["results"][ref]["status"] == "pass" for row in rows) for ref in agents}
    return {"agents": agents, "repeats": repeats, "rows": rows, "release_gate": gate}
