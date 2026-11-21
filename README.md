# AGENT CRASH LAB

**Find failures before your customers do.**

Agent Crash Lab is a testing, debugging and reliability platform for AI agents that act through tools. It runs an agent inside a simulated business system and changes the world around it while it works: gateways time out after committing, APIs rate-limit, reads go stale, warehouses ship an order mid-cancellation, and tickets carry prompt injections. Then it checks what actually happened in the system of record, not what the agent says happened.

Each failure comes back as:

- a deterministic, replayable trace,
- the violated rule and the evidence events behind it,
- a root-cause hypothesis anchored at the earliest observable divergence,
- a **minimal reproduction** (delta-debugged from, for example, 8 steps down to 2), and
- a **regression test** that re-runs the real agent under the same faults before every release.

> Stress → Detect → Explain → Minimize → Prevent → Compare

This repository is the working vertical slice from the blueprint: **one environment, two agent versions, a fault engine, the full failure-to-regression loop**, plus an SDK, CLI, REST API with live streaming, and a dashboard. It is all tested (116 tests, including security, website and real-browser suites) and runs with no external services.

---

> **New to Python?** Follow the step-by-step Windows guide: [`docs/HOW-TO-RUN.md`](docs/HOW-TO-RUN.md).

## Quick start (60 seconds)

```bash
pip install -e ".[server]"

crashlab demo                       # the whole story in your terminal (blueprint §18)
crashlab run --agents v1 v2         # 200 episodes: 20 scenarios × 5 repeats × 2 versions
crashlab serve                      # site on http://127.0.0.1:8000, dashboard at /app/, API docs at /docs
```

Locally, `crashlab serve` runs without API keys, but only on 127.0.0.1. Bound to any other address, it refuses to start unless keys are required:

```bash
crashlab keys create --name admin --scopes admin     # prints the key once; only its hash is stored
CRASHLAB_AUTH=required crashlab serve --host 0.0.0.0
crashlab backup create                               # online backup + integrity check
```

With Docker (auth is always on): `cp .env.example .env`, run `docker compose run --rm crashlab crashlab keys create --scopes admin`, then `docker compose up --build`.

`crashlab export-demo --out dist/index.html` writes a self-contained HTML report with a full experiment embedded, so you can share results without running a server.

### What `crashlab run` finds

```
support-agent@1.0.0   reliability 83.0/100
success 19.0%  [95% CI 12.5% – 27.8%]   pass^k 0.19 0.14 0.11 0.08 0.05
  duplicate side effects 15.0% · attack success 75.0% · false completion 10.5%

support-agent@2.0.0   reliability 99.8/100
success 99.0%  [95% CI 94.6% – 99.8%]   pass^k 0.99 0.98 0.97 0.96 0.95
  duplicate side effects 0.0% · attack success 0.0% · false completion 0.0%

v2 vs v1  success +80.0 pts [95% CI +72.0, +88.0] · McNemar p=1.7e-24 · fixed 80 / broke 0

failure modes
  CRIT v1.0.0  Duplicate refund after issue_refund timed out after committing   ×15
  CRIT v1.0.0  Agent executed a prompt-injected instruction                     ×10
  HIGH v1.0.0  Agent reported an outcome the system never reached               ×10
  HIGH v1.0.0  Retry loop on get_order without backoff                          ×5
  HIGH v2.0.0  Approval skipped for a high-value refund                         ×1
  …
```

Version 2.0 still fails once in 100 episodes: it skips a supervisor approval about 12% of the time. That flakiness is left in on purpose, because a 99% agent is exactly where repeated-trial metrics (pass^k, Wilson intervals) show what a single demo run hides.

---

## Test your own agent

Your code drives the tools; the platform injects faults, records the trace and evaluates the outcome. The agent can be any framework or a plain function.

```python
from crashlab.sdk import CrashLab, ToolCallFailed

lab = CrashLab()      # CrashLab(base_url=..., api_key=...) uploads every episode to a server

def my_agent(task, tools, context):
    order = tools.get_order(order_id="ORD-1001")
    try:
        tools.issue_refund(order_id="ORD-1001", amount=order["amount"], idempotency_key="rf-1001")
    except ToolCallFailed as e:
        if e.code == "timeout":
            tools.get_refund_status(order_id="ORD-1001")
    return "Your refund has been initiated."

print(lab.test(my_agent, scenarios=["ACL-ECOM-0002"], repeats=5).summary())
```

To test a real LLM, use the built-in tool-calling agent: `ANTHROPIC_API_KEY=… crashlab run --agents v2 llm`.

See `examples/test_my_agent.py`.

## Gate releases in CI

```bash
crashlab run --agents v2 --repeats 5 \
  --gate "pass^5>=0.85,security==0,duplicates==0,false_completion==0" \
  --fail-on-regression          # also re-runs every saved regression test
```

This exits non-zero when any clause fails. `.github/workflows/ci.yml` runs the test suite, validates the dataset, dogfoods the gate and uploads the HTML report as an artifact.

---

## How it works

```
 scenario JSON ─▶ Environment (seeded, resettable) ◀─ tool calls ─┐
                       ▲                                           │
             Fault middleware (timeouts, 503/429, stale,           │
             malformed, injection, concurrent writes)              │
                       │                                   Agent (v1, v2, LLM, SDK, replay)
                 Event collector ── virtual clock ── invariant monitor
                       │
   Evaluators: state · tool-call · policy · security · conversation · recovery
                       │
   Diagnosis (taxonomy + root/consequence folding + earliest divergence)
                       │
   ddmin minimizer (faults pinned to decisions) ─▶ regression generator ─▶ suite + release gate
```

| Module | File | What it does |
|---|---|---|
| Environment | `crashlab/environments/ecommerce.py` | Customers, orders, payment gateway (async settlement, optional idempotency), shipping, KB, tickets, approvals. 13 tools; `@query` functions for assertions. |
| Fault engine | `crashlab/faults.py` | 8 fault types, targeted by tool and call number, probability sampled from a seeded RNG, and every activation logged. A *pinned* mode attaches faults to specific decisions for replay. |
| Execution | `crashlab/runner.py` | Episode state machine, step/time/token budgets, loop guard, permission guardrail, virtual clock, structural state diffs, live invariant monitor. |
| Assertions | `crashlab/assertions.py` | Safe AST-interpreted DSL, e.g. `refund_count('ORD-1001') <= 1`. It never calls `eval`, so uploaded scenarios can't run code. |
| Evaluators | `crashlab/evaluators.py` | Six independent evaluators with severities. An episode passes only if no critical or high check fails. |
| Diagnosis | `crashlab/diagnosis.py` | 11-category taxonomy. Unmet goals, recovery checks and missing replies are folded into the root failure that explains them, so one bug gives one finding. |
| Minimizer | `crashlab/minimizer.py` | Zeller's ddmin over the recorded action log. A candidate counts only if it reproduces the same category, root check and observed detail, and the result is checked for determinism. |
| Regressions | `crashlab/regression.py` | Turns a failure into a versioned scenario with the activated faults and assertions, named like `refund_timeout_duplicate_protection`. |
| Metrics | `crashlab/metrics.py` | Unbiased pass@k / pass^k, Wilson intervals, paired bootstrap deltas, exact McNemar test. |
| Interfaces | `sdk.py`, `cli.py`, `server/app.py`, `web/` | SDK, CLI, FastAPI (202 + SSE progress), and a dependency-free dashboard. |

### The dataset

There are 20 original scenarios in `crashlab/data/scenarios/`, validated against `scenario.schema.json` and split into dev, validation and test. They cover duplicate side effects, network and API failures, rate-limit loops, malformed and stale data, ticket and RAG prompt injection, cross-customer access, approval gates, identity checks, a shipping race condition and step-ordering bugs. `crashlab validate` runs the validation pipeline: schema, tools, assertions, invariants at t=0, and deterministic replay.

### Metrics, precisely

| Metric | Definition |
|---|---|
| Task success | Episodes with no failed critical or high check ÷ episodes (with a Wilson 95% CI) |
| pass^k | Mean over scenarios of C(c,k)/C(n,k): every one of k attempts succeeds |
| pass@k | Mean over scenarios of 1 − C(n−c,k)/C(n,k): at least one attempt succeeds |
| Duplicate side-effect rate | Episodes where a write committed twice on the same target ÷ episodes |
| Attack success rate | Security-category episodes with a failed security check ÷ those episodes |
| False completion rate | Replies containing a claim the final state contradicts ÷ replies |
| Reliability score | Weighted mean of the six dimension scores (weights configurable per scenario) |

Latency is measured on the deterministic virtual clock. Cost uses simulated token pricing for the built-in agents; real adapters report real usage.

---

## Public website

`crashlab serve` also serves a public site: a landing page with real benchmark results and an FAQ, plus privacy policy, contact form and thank-you page. Each page has its own title, description, canonical URL and share-preview image, and the server provides `sitemap.xml` and `robots.txt`. The dashboard (`/app/`) and API are never indexed.

Optional features switch on from `.env` and stay off when blank, so no placeholder IDs ship:
- **Analytics:** Google Analytics 4 (`CRASHLAB_GA_ID`), loaded only after cookie consent.
- **Spam protection:** Cloudflare Turnstile on the contact form (`TURNSTILE_*`).
- **HTTPS:** a Caddy proxy (`docker compose --profile https up`) with `CRASHLAB_HSTS=1`.

Testimonials show only once `web/site/testimonials.json` holds real quotes with `"consent": true`.

## Interface

Both the website and the dashboard have:
- light and dark themes (following the OS until you choose, with no flash on load),
- search with `/` or Ctrl+K,
- copy buttons on every code block,
- a back-to-top button and scroll progress bar,
- skip links,
- confirmation dialogs before costly or irreversible actions,
- skeleton loading states,
- a print stylesheet that turns any page into a clean report.

The contact form validates inline as you type, and still validates on the server without JavaScript. It records UTM campaign tags with each lead.

## Security & operations

Every `/api/v1` route needs a scoped API key. Data is isolated per project (another project's objects return 404). The API also has:
- rate limits and body-size caps,
- a strict CSP and security headers,
- CORS that's closed by default,
- JSON logs with request ids,
- Prometheus `/metrics`,
- versioned migrations and verified backup/restore.

[`docs/SECURITY.md`](docs/SECURITY.md) maps the common failure modes of quickly-built apps to the code and the test that proves each one is handled, and lists what is still out of scope.

## Honest scope

**Built and tested:** everything above, end to end.

**Next, in blueprint order:**
- Postgres, Redis/Celery workers and object storage: the store already mirrors the §8.3 tables, so this is a driver swap.
- Container or gVisor isolation for customer code.
- Adapters for the OpenAI Agents SDK, LangGraph and CrewAI.
- More environments (support desk, travel, workspace).
- Human user accounts/SSO, TLS at a proxy, multi-node rate limiting, and billing.
- An LLM conversation judge calibrated on labelled examples.

The root-cause engine reports evidence-linked hypotheses with a confidence level. It does not claim to know why a model made a decision.

## Development

```bash
pip install -e ".[dev]"
pytest -q                 # 116 tests: engine, evaluators, ddmin, SDK, CLI, API, security, website, browser
                          # (browser tests need: pip install playwright && playwright install chromium)
ruff check crashlab server tests
```

See `docs/ARCHITECTURE.md` for design decisions.
