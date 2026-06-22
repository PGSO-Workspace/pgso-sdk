"""
Shared rank-based statistics for the Phase 2 benchmark (ported from the
Phase-1-validated implementations: average-rank Spearman, one-sided permutation
test, bootstrap CI, plus a paired-bootstrap delta-rho for candidate-vs-baseline).

Run directly (`python eval_stats.py`) for a self-check on known values.
"""
import numpy as np


def avg_rank(a):
    a = np.asarray(a, dtype=float)
    n = len(a)
    order = np.argsort(a, kind="mergesort")
    sorted_a = a[order]
    ranks = np.empty(n, dtype=float)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    return ranks


def pearson(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    xm, ym = x - x.mean(), y - y.mean()
    denom = np.sqrt((xm * xm).sum() * (ym * ym).sum())
    return float((xm * ym).sum() / denom) if denom > 0 else float("nan")


def spearman(x, y):
    return pearson(avg_rank(x), avg_rank(y))


def permutation_p(x, y, n_perm, seed):
    """One-sided (positive) permutation p for Spearman rho."""
    rng = np.random.default_rng(seed)
    rx, ry = avg_rank(x), avg_rank(y)
    obs = pearson(rx, ry)
    k = sum(1 for _ in range(n_perm) if pearson(rx, rng.permutation(ry)) >= obs)
    return (k + 1) / (n_perm + 1), k


def bootstrap_ci(x, y, n_boot, seed):
    rng = np.random.default_rng(seed)
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    rhos = np.empty(n_boot)
    for b in range(n_boot):
        idx = rng.integers(0, n, n)
        rhos[b] = spearman(x[idx], y[idx])
    lo, hi = np.percentile(rhos, [2.5, 97.5])
    return float(lo), float(hi)


def paired_bootstrap_delta(pred_a, pred_b, human, n_boot, seed):
    """95% CI on rho(pred_a, human) - rho(pred_b, human), paired on the same resamples."""
    rng = np.random.default_rng(seed)
    pa, pb, h = np.asarray(pred_a, float), np.asarray(pred_b, float), np.asarray(human, float)
    n = len(h)
    d = np.empty(n_boot)
    for b in range(n_boot):
        idx = rng.integers(0, n, n)
        d[b] = spearman(pa[idx], h[idx]) - spearman(pb[idx], h[idx])
    lo, hi = np.percentile(d, [2.5, 97.5])
    return float(lo), float(hi)


if __name__ == "__main__":
    # Self-check on known values.
    assert abs(spearman([1, 2, 3, 4], [1, 2, 3, 4]) - 1.0) < 1e-9, "monotonic up"
    assert abs(spearman([1, 2, 3, 4], [4, 3, 2, 1]) + 1.0) < 1e-9, "monotonic down"
    # ties handled (average rank): [1,1,2,2] vs [1,2,3,4] -> rho = 4/sqrt(20) = 0.894427
    r = spearman([1, 1, 2, 2], [1, 2, 3, 4])
    assert abs(r - 0.8944272) < 1e-5, f"tie case got {r}"
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    y = x + rng.normal(size=200)  # strong positive
    p, k = permutation_p(x, y, 2000, 42)
    lo, hi = bootstrap_ci(x, y, 1000, 42)
    assert p < 0.01 and lo > 0, f"expected significant positive, got p={p} ci=[{lo},{hi}]"
    print(f"eval_stats self-check OK | tie-rho={r:.5f} perm_p={p:.4f} ci=[{lo:.3f},{hi:.3f}]")
