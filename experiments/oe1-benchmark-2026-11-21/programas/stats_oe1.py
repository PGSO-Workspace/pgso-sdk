# -*- coding: utf-8 -*-
"""Estadísticos auxiliares con remuestreo por hablante (2000 réplicas, semilla 42), según el manifiesto."""
import numpy as np
from scipy.stats import rankdata, spearmanr

N_BOOT = 2000
SEED = 42


def rho(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return np.nan
    return float(spearmanr(x, y).correlation)


def auc(score, label):
    s, l = np.asarray(score, float), np.asarray(label, int)
    n1, n0 = l.sum(), (1 - l).sum()
    if n1 == 0 or n0 == 0:
        return np.nan
    r = rankdata(s)
    return float((r[l == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def bal_acc(score, label, thr):
    s, l = np.asarray(score, float), np.asarray(label, int)
    p = (s >= thr).astype(int)
    tpr = (p[l == 1] == 1).mean() if (l == 1).any() else np.nan
    tnr = (p[l == 0] == 0).mean() if (l == 0).any() else np.nan
    return float((tpr + tnr) / 2)


def best_threshold(score, label):
    """Umbral que maximiza la exactitud balanceada en los datos dados (solo origen)."""
    s = np.asarray(score, float)
    cand = np.unique(s)
    if cand.size > 2000:
        cand = np.quantile(s, np.linspace(0, 1, 2001))
    vals = [bal_acc(s, label, t) for t in cand]
    return float(cand[int(np.nanargmax(vals))])


def centered(values, groups):
    v, g = np.asarray(values, float), np.asarray(groups)
    out = v.copy()
    for k in np.unique(g):
        m = g == k
        out[m] = v[m] - v[m].mean()
    return out


def cluster_boot(stat, groups, n_boot=N_BOOT, seed=SEED):
    """stat(idx) -> float. Devuelve (estimado, [li, ls], réplicas indefinidas)."""
    g = np.asarray(groups)
    keys = np.unique(g)
    blocks = [np.flatnonzero(g == k) for k in keys]
    est = stat(np.arange(len(g)))
    rng = np.random.default_rng(seed)
    vals, undef = [], 0
    for _ in range(n_boot):
        idx = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        v = stat(idx)
        if np.isfinite(v):
            vals.append(v)
        else:
            undef += 1
    ci = [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))] if vals and not undef else None
    return {'estimate': None if not np.isfinite(est) else float(est), 'ci95': ci,
            'speakers': int(len(keys)), 'n': int(len(g)), 'bootstrap_undefined': int(undef)}
