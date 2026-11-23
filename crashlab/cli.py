"""crashlab - command-line interface.

    crashlab demo                              # the 10-step story from blueprint section 18
    crashlab run --agents v1 v2 --repeats 5    # run an experiment, store it, print the scorecard
    crashlab run --agents v2 --gate "success>=0.95,security==0,duplicates==0" --fail-on-regression
    crashlab regress --agents v2               # run the saved regression suite
    crashlab validate                          # run the dataset validation pipeline
    crashlab scenarios                         # list scenarios
    crashlab trace <run_id>                    # print a run's timeline
    crashlab serve                             # API + dashboard on http://localhost:8000
    crashlab export-demo                       # write a static, self-contained dashboard
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .scenarios import load_scenarios, split, validate_scenario

TTY = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


RED, GREEN, YELLOW, DIM, BOLD, CYAN, MAG = "31", "32", "33", "2", "1", "36", "35"


def _store(path: str):
    from .store import Store

    return Store(path)


def _select(args) -> list[dict[str, Any]]:
    sc = load_scenarios()
    if getattr(args, "split", None):
        sc = split(sc, args.split)
    if getattr(args, "scenarios", None):
        want = set(args.scenarios)
        sc = [s for s in sc if s["scenario_id"] in want or s["scenario_id"][-4:] in want]
    if getattr(args, "tag", None):
        sc = [s for s in sc if args.tag in s.get("tags", [])]
    return sc


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def cmd_run(args) -> int:
    from .experiment import run_experiment

    scenarios = _select(args)
    if not scenarios:
        print("no scenarios selected", file=sys.stderr)
        return 2
    from .agents import missing_key

    for ref in args.agents:
        if key := missing_key(ref):
            print(c(f"agent '{ref}' needs {key}. Set it first, e.g. in PowerShell:  $env:{key} = \"your-key\"", RED), file=sys.stderr)
            return 2

    total = len(args.agents) * len(scenarios) * args.repeats

    def progress(kind: str, data: dict[str, Any]) -> None:
        if kind == "run.completed" and not args.quiet:
            mark = c("✓", GREEN) if data["passed"] else c("✗", RED)
            sys.stdout.write(f"\r  {data['done']:>4}/{total}  {mark} {data['agent_ref']:<4} {data['scenario_id']} r{data['repeat']}"
                             f"  {c(data['primary_category'] or '', DIM):<40}")
            sys.stdout.flush()

    print(c(f"AGENT CRASH LAB {__version__}", BOLD), f"· {len(args.agents)} agent(s) × {len(scenarios)} scenarios × "
          f"{args.repeats} repeats = {total} episodes")
    res = run_experiment(args.agents, scenarios, repeats=args.repeats, base_seed=args.seed, name=args.name,
                         on_progress=progress)
    print()
    st = _store(args.db)
    st.save_experiment(res)
    for reg in res["regressions"]:
        if args.save_regressions:
            st.save_regression(reg)
    print_scorecards(res)
    if args.out:
        Path(args.out).write_text(json.dumps(res, default=str), encoding="utf-8")
        print(c(f"\nresults written to {args.out}", DIM))
    print(c(f"experiment {res['experiment_id']} stored in {args.db}", DIM))

    code = 0
    if args.gate:
        code = max(code, release_gate(res, args.gate))
    if args.fail_on_regression:
        from .experiment import run_regressions

        regs = st.list_regressions()
        if regs:
            rr = run_regressions(regs, [args.agents[-1]])
            st.save_regression_run(rr)
            bad = [r for r in rr["rows"] if r["results"][args.agents[-1]]["status"] != "pass"]
            print(f"\nregression suite: {len(rr['rows']) - len(bad)}/{len(rr['rows'])} passing on {args.agents[-1]}")
            for r in bad:
                print(c(f"  ✗ {r['name']} ({r['results'][args.agents[-1]]['status']})", RED))
            code = max(code, 1 if bad else 0)
    return code


def print_scorecards(res: dict[str, Any]) -> None:
    labels = res["dimension_labels"]
    for ref, s in res["summaries"].items():
        lo, hi = s["success_ci"]
        print()
        skipped = s.get("inconclusive", 0)
        if not s["episodes"]:
            print(c(f"  {ref}", BOLD), c(f"  no verdict: all {skipped} runs were inconclusive (model provider errors)", YELLOW))
            continue
        if skipped:
            print(c(f"  {skipped} run(s) inconclusive (model provider errors), left out of every number below", YELLOW))
        score = f"{s['reliability_score']:.1f}/100"
        print(c(f"  {s['agent']['name']}@{s['agent']['version']}", BOLD), f"  reliability {c(score, BOLD)}")
        print(f"  success {pct(s['success_rate'])}  [95% CI {pct(lo)} – {pct(hi)}]   "
              f"pass^k {' '.join(f'{x:.2f}' for x in s['pass_hat_k'])}")
        for d, v in s["dimensions"].items():
            bar = "█" * int((v or 0) / 5)
            col = GREEN if (v or 0) >= 90 else YELLOW if (v or 0) >= 70 else RED
            print(f"    {labels[d]:<18} {c(f'{v:>5.1f}' if v is not None else '  n/a', col)} {c(bar, col)}")
        r = s["rates"]
        print(f"    duplicate side effects {pct(r['duplicate_side_effect'])} · attack success {pct(r['security_attack_success'])}"
              f" · false completion {pct(r['false_completion'])} · fault recovery {pct(r['fault_recovery'])}")
        print(f"    latency p95 {s['latency_ms']['p95'] / 1000:.1f}s · est. cost ₹{s['cost_inr']['total']:.2f} ({s['tokens']:,} tokens)")
    if res["comparison"]:
        cmp = res["comparison"]
        d = cmp["success_delta"]
        print(c(f"\n  {cmp['candidate']} vs {cmp['baseline']}", BOLD),
              f" success {d['delta'] * 100:+.1f} pts [95% CI {d['ci_low'] * 100:+.1f}, {d['ci_high'] * 100:+.1f}]"
              f" · McNemar p={cmp['mcnemar_p']:.2g} · fixed {cmp['discordant']['fixed']} / broke {cmp['discordant']['broken']}")
    modes = res.get("failure_modes", [])
    if modes:
        print(c("\n  failure modes", BOLD))
        for m in modes:
            col = RED if m["severity"] == "critical" else YELLOW
            print(f"    {c(m['severity'][:4].upper(), col)} v{m['agent_version']:<6} {m['title'][:70]:<70} ×{m['occurrences']}")


GATE_RE = re.compile(r"^\s*(success|score|security|duplicates|false_completion|policy|pass\^\d+)\s*(>=|<=|==|<|>)\s*([\d.]+)\s*$")


def release_gate(res: dict[str, Any], spec: str) -> int:
    s = res["summaries"][res["config"]["agents"][-1]]
    values = {
        "success": s["success_rate"], "score": s["reliability_score"],
        "security": s["rates"]["security_attack_success"], "duplicates": s["rates"]["duplicate_side_effect"],
        "false_completion": s["rates"]["false_completion"], "policy": s["rates"]["policy_violation"],
    }
    for i, v in enumerate(s["pass_hat_k"], 1):
        values[f"pass^{i}"] = v
    ok = True
    print(c("\n  release gate", BOLD))
    for clause in spec.split(","):
        m = GATE_RE.match(clause)
        if not m:
            print(c(f"    invalid clause {clause!r}", RED))
            return 2
        k, op, th = m.group(1), m.group(2), float(m.group(3))
        v = values[k]
        passed = {">=": v >= th, "<=": v <= th, "==": abs(v - th) < 1e-9, "<": v < th, ">": v > th}[op]
        ok &= passed
        print(f"    {c('PASS', GREEN) if passed else c('BLOCK', RED)}  {k} {op} {th:g}   (observed {v:.3f})")
    return 0 if ok else 1


def cmd_regress(args) -> int:
    from .experiment import run_regressions

    st = _store(args.db)
    regs = st.list_regressions()
    if not regs:
        print("no regression tests saved yet - run `crashlab run --save-regressions` first")
        return 0
    rr = run_regressions(regs, args.agents, repeats=args.repeats)
    st.save_regression_run(rr)
    w = max(len(r["name"]) for r in rr["rows"])
    print(c(f"{'regression':<{w}}  " + "  ".join(f"{a:>8}" for a in args.agents), BOLD))
    for r in rr["rows"]:
        cells = []
        for a in args.agents:
            x = r["results"][a]
            col = GREEN if x["status"] == "pass" else YELLOW if x["status"] == "flaky" else RED
            cells.append(c(f"{x['passed']}/{x['n']} {x['status']:<5}", col))
        print(f"{r['name']:<{w}}  " + "  ".join(cells))
    gate = rr["release_gate"]
    print("\n" + "  ".join(f"{a}: {c('releasable', GREEN) if g else c('blocked', RED)}" for a, g in gate.items()))
    return 0 if gate[args.agents[-1]] else 1


def cmd_validate(args) -> int:
    paths = [Path(p) for p in args.paths] if args.paths else None
    bad = 0
    for s in load_scenarios(paths):
        rep = validate_scenario(s)
        stages = " ".join(c(k, GREEN) if v == "pass" else c(k, RED) for k, v in rep["stages"].items())
        print(f"{'✓' if rep.ok else '✗'} {s['scenario_id']:<16} {stages}")
        for e in rep["errors"]:
            print(c(f"    {e}", RED))
        bad += not rep.ok
    print(f"\n{c('all scenarios valid', GREEN) if not bad else c(f'{bad} invalid', RED)}")
    return 1 if bad else 0


def cmd_scenarios(args) -> int:
    for s in _select(args):
        faults = ",".join(f["type"] for f in s.get("faults", [])) or "—"
        print(f"{s['scenario_id']}  {s['split']:<10} {s['difficulty']:<12} {s['title'][:58]:<58} {c(faults, DIM)}")
    return 0


def print_trace(events: list[dict[str, Any]]) -> None:
    for e in events:
        t = f"{e['t_ms'] / 1000:>7.3f}s"
        et = e["event_type"]
        if et == "tool_call":
            args = ", ".join(f"{k}={v!r}" for k, v in e["tool"].get("arguments", {}).items())
            line = c(f"→ {e['tool']['name']}({args})", CYAN)
        elif et == "tool_result":
            r = e["result"]
            line = (c("  ✓ ok", GREEN) if r["status"] == "ok" else c(f"  ✗ {r['error'].get('code')}: {r['error'].get('message')}", RED))
            if e.get("state_diff"):
                line += c(f"   Δ {len(e['state_diff'])} change(s): " + ", ".join(d["path"] for d in e["state_diff"][:3]), YELLOW)
        elif et == "fault_activated":
            line = c(f"⚡ FAULT {e['metadata']['type']} on {e['metadata']['target']}", MAG)
        elif et == "invariant_breach":
            line = c(f"‼ {e['message']}", RED)
        elif et == "state_mutation":
            line = c(f"⇄ external: {e.get('message')}", YELLOW)
        elif et == "final_answer":
            line = c(f"💬 “{e['message']}”", BOLD)
        elif et == "user_message":
            line = f"👤 “{e['message']}”"
        elif et == "agent_step":
            continue
        else:
            line = c(e.get("message") or et, DIM)
        print(f"  {c(t, DIM)}  {line}")


def cmd_trace(args) -> int:
    run = _store(args.db).get_run(args.run_id)
    if not run:
        print(f"run {args.run_id} not found", file=sys.stderr)
        return 1
    print_trace(run["events"])
    return 0


def cmd_demo(args) -> int:
    """Blueprint section 18, end to end, in the terminal."""
    from .agents import SupportAgentV1, SupportAgentV2
    from .diagnosis import diagnose
    from .evaluators import evaluate_episode
    from .minimizer import minimize
    from .regression import generate_regression
    from .runner import run_episode
    from .scenarios import scenario_by_id

    sc = scenario_by_id(args.scenario)
    step = lambda n, t: print("\n" + c(f"Step {n} — {t}", BOLD))
    print(c("\nLIVE DEMO · Can an AI agent safely refund a customer?", BOLD))
    step(1, "Register the agent");  print("  support-agent@1.0.0 (custom-python) · 13 tools")
    step(2, "Select a scenario");   print(f"  {sc['scenario_id']} · {sc['title']}\n  👤 “{sc['task']['user_message']}”")
    step(3, "Inject a fault")
    for f in sc["faults"]:
        print(f"  ⚡ {f['type']} on {f['target']} (call #{f.get('on_call', 1)}, +{f.get('delay_ms', 0) / 1000:.0f}s)")
    step(4, "Execute the agent")
    ep = run_episode(sc, SupportAgentV1(), args.seed, "demo.v1")
    print_trace(ep.events)
    step(5, "Detect failure")
    card = evaluate_episode(sc, ep)
    for g in card.gating_failures:
        print(c(f"  ✗ [{g['severity']}] {g['name']}", RED) + c(f"  {g['detail']}", DIM))
    fails = diagnose(sc, ep, card)
    f = fails[0]
    step(6, "Diagnose")
    rc = f.root_cause
    print(f"  {c(f.category, RED)} · {f.title}\n  hypothesis: {rc['hypothesis']} (confidence: {rc['confidence']})")
    if rc.get("divergence"):
        print(f"  divergence: {rc['divergence']['description']}")
    print(c("  expected:", DIM), " → ".join(rc["expected"]))
    step(7, "Minimize")
    rp = minimize(sc, ep, f)
    print(f"  {rp.original_steps} steps → {c(str(rp.minimized_steps), GREEN)} steps "
          f"(ddmin history {' → '.join(map(str, rp.history))}, {rp.replays} replays, deterministic={rp.deterministic})")
    for s in rp.steps:
        print(f"    {s['index'] + 1}. {s['tool'] or 'final answer'}({', '.join(f'{k}={v!r}' for k, v in s['args'].items())})"
              + (c(f"  ⚡ {','.join(s['faults'])}", MAG) if s["faults"] else ""))
    step(8, "Generate regression")
    reg = generate_regression(f, sc, rp, activated={x["fault_id"] for x in ep.fault_log})
    print(f"  saved {c(reg['regression']['name'], BOLD)} ({reg['scenario_id']})")
    print(c("  " + json.dumps({"name": reg["regression"]["name"], "faults": reg["faults"],
                                "assertions": reg["expected_state"] + [i for i in reg["invariants"]
                                                         if any(o in i for o in re.findall(r"ORD-\d{4}", " ".join(reg["expected_state"])))]}, indent=2)
            .replace("\n", "\n  "), DIM))
    step(9, "Fix the agent");  print("  support-agent@2.0.0: idempotency keys + post-timeout verification + backoff")
    step(10, "Re-run across repeated trials")
    for name, A in (("v1.0.0", SupportAgentV1), ("v2.0.0", SupportAgentV2)):
        res = [evaluate_episode(reg, run_episode(reg, A(), s, f"demo.{name}.{s}")).passed for s in range(args.repeats)]
        col = GREEN if all(res) else RED
        print(f"  {name}: {c(f'{sum(res)}/{len(res)} passing', col)}  " + "".join(c("●", GREEN) if r else c("●", RED) for r in res))
    print(c("\nDiscover → Reproduce → Diagnose → Fix → Regression-test → Compare  ✓\n", BOLD))
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from server.security import is_loopback

    os.environ["CRASHLAB_DB"] = args.db
    explicit = os.environ.get("CRASHLAB_AUTH")
    if args.no_auth:
        if not is_loopback(args.host):
            print(c("refusing to start: --no-auth is only allowed on a loopback address (127.0.0.1)", RED), file=sys.stderr)
            return 2
        os.environ["CRASHLAB_AUTH"] = "off"
    elif explicit is None:
        os.environ["CRASHLAB_AUTH"] = "required"  # sign-in on by default; --no-auth for a quick local look
    if os.environ["CRASHLAB_AUTH"] == "off" and not is_loopback(args.host):
        print(c("refusing to start: CRASHLAB_AUTH=off on a non-loopback address would expose the API", RED), file=sys.stderr)
        return 2
    os.environ.setdefault("PORT", str(args.port))  # for the first-run setup link
    mode = "local, sign-in off (loopback only)" if os.environ["CRASHLAB_AUTH"] == "off" else "sign-in required"
    print(c(f"AGENT CRASH LAB on http://{args.host}:{args.port}  ·  {mode}", BOLD))
    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=False, proxy_headers=False,
                log_level="warning")
    return 0


def cmd_keys(args) -> int:
    st = _store(args.db)
    if args.action == "create":
        k = st.create_api_key(args.project, args.name, args.scopes.split(","))
        print(c("Store this key now - it is shown once and only its hash is kept:", YELLOW))
        print(k["key"])
        print(c(f"id {k['id']} · project {k['project_id']} · scopes {','.join(k['scopes'])}", DIM))
    elif args.action == "list":
        for k in st.list_api_keys(args.project):
            state = c("revoked", RED) if k["revoked_at"] else c("active", GREEN)
            print(f"{k['id']}  {k['project_id']:<12} {k['name']:<20} {k['scopes']:<18} {state}  last used {k['last_used_at'] or 'never'}")
    elif args.action == "revoke":
        if not args.id or not st.revoke_api_key(args.id):
            print(c("no such active key", RED), file=sys.stderr)
            return 1
        print(f"revoked {args.id}")
    return 0


def cmd_backup(args) -> int:
    from .store import Store

    if args.action == "create":
        out = args.file or f"backups/crashlab-{time.strftime('%Y%m%d-%H%M%S')}.db"
        info = _store(args.db).backup(out)
        print(f"backup written: {info['path']} ({info['bytes']:,} bytes, schema v{info['schema_version']}, integrity ok)")
    else:
        if not args.file:
            print("restore needs a backup file", file=sys.stderr)
            return 2
        info = Store.restore(args.file, args.db)
        print(f"restored {args.db} from {info['restored_from']} (schema v{info['schema_version']})")
        if info["rollback_copy"]:
            print(c(f"previous database kept at {info['rollback_copy']} - restore it the same way to roll back", DIM))
        print(f"schema now v{_store(args.db).schema_version} after migrations")
    return 0


def cmd_export(args) -> int:
    from .export import export_static

    path = export_static(Path(args.out), agents=args.agents, repeats=args.repeats)
    print(f"wrote {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="crashlab", description="AGENT CRASH LAB - reliability testing for AI agents")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--db", default=os.environ.get("CRASHLAB_DB", "crashlab.db"))
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run an experiment")
    r.add_argument("--agents", nargs="+", default=["v1", "v2"])
    r.add_argument("--scenarios", nargs="*")
    r.add_argument("--split", choices=["dev", "validation", "test"])
    r.add_argument("--tag")
    r.add_argument("--repeats", type=int, default=5)
    r.add_argument("--seed", type=int, default=2026)
    r.add_argument("--name")
    r.add_argument("--out")
    r.add_argument("--gate", help='e.g. "success>=0.95,security==0,duplicates==0"')
    r.add_argument("--fail-on-regression", action="store_true")
    r.add_argument("--save-regressions", action="store_true", default=True)
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(fn=cmd_run)

    g = sub.add_parser("regress", help="run the saved regression suite")
    g.add_argument("--agents", nargs="+", default=["v1", "v2"])
    g.add_argument("--repeats", type=int, default=3)
    g.set_defaults(fn=cmd_regress)

    v = sub.add_parser("validate", help="validate scenario files")
    v.add_argument("paths", nargs="*")
    v.set_defaults(fn=cmd_validate)

    s = sub.add_parser("scenarios", help="list scenarios")
    s.add_argument("--split", choices=["dev", "validation", "test"])
    s.add_argument("--tag")
    s.set_defaults(fn=cmd_scenarios)

    t = sub.add_parser("trace", help="print a run's timeline")
    t.add_argument("run_id")
    t.set_defaults(fn=cmd_trace)

    d = sub.add_parser("demo", help="the end-to-end story in your terminal")
    d.add_argument("--scenario", default="ACL-ECOM-0017")
    d.add_argument("--seed", type=int, default=11)
    d.add_argument("--repeats", type=int, default=10)
    d.set_defaults(fn=cmd_demo)

    sv = sub.add_parser("serve", help="start the API and dashboard")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--no-auth", action="store_true", help="disable API keys (loopback only)")
    sv.set_defaults(fn=cmd_serve)

    k = sub.add_parser("keys", help="create, list or revoke API keys")
    k.add_argument("action", choices=["create", "list", "revoke"])
    k.add_argument("id", nargs="?")
    k.add_argument("--name", default="cli")
    k.add_argument("--project", default="default")
    k.add_argument("--scopes", default="read,write", help="comma list of read,write,admin")
    k.set_defaults(fn=cmd_keys)

    b = sub.add_parser("backup", help="back up or restore the database")
    b.add_argument("action", choices=["create", "restore"])
    b.add_argument("file", nargs="?")
    b.set_defaults(fn=cmd_backup)

    e = sub.add_parser("export-demo", help="write a static dashboard with embedded results")
    e.add_argument("--out", default="dist/index.html")
    e.add_argument("--agents", nargs="+", default=["v1", "v2"])
    e.add_argument("--repeats", type=int, default=5)
    e.set_defaults(fn=cmd_export)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
