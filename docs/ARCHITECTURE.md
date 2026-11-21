# Architecture & design decisions

## Principles

1. **Ground truth over narrative.** Evaluators read the environment's database and the action trace. The agent's final message is treated as a set of *claims* to verify, never as evidence.
2. **Determinism first.** Every source of randomness is seeded: fault probability (`faults:{seed}`), agent sampling (`agent:{seed}`), simulated usage, and virtual-clock latencies. The same seed produces a byte-identical trace, and `crashlab validate` checks this for every scenario.
3. **One bug, one finding.** Raw failed checks are grouped. Root categories (security, duplicate, false completion, policy, loop, state tracking, tool and argument errors) become failures. Consequence checks (unmet expectations, recovery checks, a missing reply) attach to the root failure that touches the same resources.
4. **Severity gates, scores inform.** A transparent six-dimension score is reported, but pass/fail depends only on critical and high checks. A double refund cannot be averaged away.

## Fault timing under minimization

Faults are normally triggered by call number ("the 1st `issue_refund`"). If ddmin removed an earlier call, a counter-based fault would land on a different decision and change the failure. During recording, the runner stores which faults fired on which action. Replays run the injector in **pinned mode**, so faults stay attached to the same decision regardless of what is removed around them. This is the precondition blueprint §6.C warns about.

A reduced candidate is accepted only if diagnosis reproduces the same category, the same root check, and the same observed detail (with event ids normalised). That rules out trivial reductions such as "the empty trace also fails to reach the goal".

## Regression generation

A regression re-runs the **real agent**, not the replay. It keeps every fault that fired in the source episode, because the scenario's expected state is defined under those conditions. The minimal fault subset found by ddmin is recorded as metadata. There is one regression per (agent version, source scenario), and it lists every failure mode it guards against.

## Production path (blueprint §4, §8, §12)

| Slice today | Production |
|---|---|
| SQLite JSON blobs (`store.py`) | PostgreSQL with the same tables; traces and states in S3 with pointer rows |
| `ThreadPoolExecutor` + in-memory job bus | Redis queue + Celery workers (execution, evaluation, diagnosis, report) |
| SSE from the job bus | SSE/WebSocket fan-out via Redis pub/sub |
| Single tenant, optional bearer key | Organisations, projects, hashed API keys, row-level security |
| In-process agents | Isolated containers per episode (no host mounts, CPU/memory/network limits, gVisor for untrusted code) |

The API surface (`/api/v1/...`), event schema, scenario schema and metric definitions are the stable contracts. Everything behind them can be replaced piece by piece.
