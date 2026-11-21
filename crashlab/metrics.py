"""Reliability metrics (blueprint section 10).

pass@k  - probability that at least one of k attempts succeeds  (capability)
pass^k  - probability that all k attempts succeed               (consistency)

Both use the unbiased combinatorial estimators over n >= k observed trials,
as in tau-bench. Success rates are reported with Wilson score intervals, and
version comparisons are *paired* on identical (scenario, seed) episodes.
"""
from __future__ import annotations

import math
import random
from math import comb
from statistics import mean
from collections.abc import Iterable, Sequence


def pass_hat_k(n: int, c: int, k: int) -> float:
    """P(all k sampled attempts succeed) = C(c,k)/C(n,k)."""
    if k > n:
        raise ValueError("k must be <= n")
    return comb(c, k) / comb(n, k)


def pass_at_k(n: int, c: int, k: int) -> float:
    """P(at least one of k sampled attempts succeeds) = 1 - C(n-c,k)/C(n,k)."""
    if k > n:
        raise ValueError("k must be <= n")
    return 1.0 - comb(n - c, k) / comb(n, k)


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def percentile(xs: Sequence[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def paired_bootstrap_delta(a: Sequence[float], b: Sequence[float], iters: int = 4000, seed: int = 0) -> dict[str, float]:
    """Mean of (b - a) over paired observations, with a 95% bootstrap CI."""
    if not a or len(a) != len(b):
        raise ValueError("paired samples of equal, non-zero length required")
    d = [y - x for x, y in zip(a, b, strict=True)]
    rng = random.Random(seed)
    boots = sorted(mean(rng.choice(d) for _ in d) for _ in range(iters))
    return {"delta": mean(d), "ci_low": boots[int(0.025 * iters)], "ci_high": boots[int(0.975 * iters) - 1], "n": len(d)}


def mcnemar_exact(b01: int, b10: int) -> float:
    """Two-sided exact McNemar p-value for paired pass/fail outcomes."""
    n = b01 + b10
    if n == 0:
        return 1.0
    k = min(b01, b10)
    p = sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def mean_or_none(xs: Iterable[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return round(mean(v), 2) if v else None
