"""
Phase 1 -- Steps 3-6: correlation, honesty controls, F0 diagnostic, verdict, chart.

Reads predicted.csv and grades eGeMAPS arousal (PRE-REGISTERED primary =
confidence-weighted mean) against human arousal: Spearman rho, a one-sided
permutation test, a bootstrap 95% CI, per-show / per-speaker breakdowns, the
F0-failure diagnostic (abstention decomposed + low-confidence + the energy /
f0_std range-utilization), a scatter chart, and the verdict vs the
pre-registered bands. The bands are PRINTED FIRST; nothing is derived from the
results. Spearman is hand-rolled (no scipy needed) with average-rank ties.
"""
import csv

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config as C


# --- statistics (hand-rolled; rank-based, tie-aware) -------------------------
def avg_rank(a):
    """Average ranks (ties share the mean rank). 0-based ranks."""
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
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    xm = x - x.mean()
    ym = y - y.mean()
    denom = np.sqrt((xm * xm).sum() * (ym * ym).sum())
    return float((xm * ym).sum() / denom) if denom > 0 else float("nan")


def spearman(x, y):
    return pearson(avg_rank(x), avg_rank(y))


def permutation_p(x, y, n_perm, seed):
    """One-sided (positive) permutation p for Spearman rho."""
    rng = np.random.default_rng(seed)
    rx, ry = avg_rank(x), avg_rank(y)
    rho_obs = pearson(rx, ry)
    count = sum(1 for _ in range(n_perm) if pearson(rx, rng.permutation(ry)) >= rho_obs)
    return (count + 1) / (n_perm + 1), count, rho_obs


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


# --- IO ----------------------------------------------------------------------
def load_predicted():
    with open(C.PREDICTED, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> int:
    C.print_header()
    rows = load_predicted()
    n_total = len(rows)
    abst = [r for r in rows if r["pred_arousal_wmean"] == ""]
    too_short = sum(1 for r in abst if r["too_short"] == "true")
    low_voicing = len(abst) - too_short
    pred = [r for r in rows if r["pred_arousal_wmean"] != ""]

    def col(name, cast=float):
        return np.array([cast(r[name]) for r in pred])

    human = col("human_arousal")
    wmean = col("pred_arousal_wmean")
    pmean = col("pred_arousal_mean")
    pmed = col("pred_arousal_median")
    conf = col("mean_confidence")
    energy = col("mean_energy")
    f0std = col("mean_f0_std")
    shows = [r["show"] for r in pred]
    speakers = [r["speaker"] for r in pred]
    n = len(pred)

    print(f"\n[N] total={n_total}  abstained={len(abst)} "
          f"(too_short={too_short}, low_voicing={low_voicing})  N_correlated={n}")
    print(f"[range] human arousal: min={human.min():.0f} max={human.max():.0f} "
          f"mean={human.mean():.2f} sd={human.std():.2f}  (restricted range attenuates rho)")
    print(f"[range] predicted (wmean): min={wmean.min():.3f} max={wmean.max():.3f} "
          f"mean={wmean.mean():.3f} sd={wmean.std():.3f}")
    print("[note] z-scoring both channels does not change Spearman/Pearson "
          "(rank/affine invariant); applied only for the scatter. (Phase-0 offset precaution.)")

    # --- correlations: primary + sensitivity ---
    rho_w = spearman(wmean, human)
    rho_m = spearman(pmean, human)
    rho_md = spearman(pmed, human)
    r_pearson = pearson(wmean, human)
    p_w, k, _ = permutation_p(wmean, human, C.PERMUTATIONS, C.SEED)
    lo, hi = bootstrap_ci(wmean, human, C.BOOTSTRAP_RESAMPLES, C.SEED)

    print("\n=== CORRELATION (predicted arousal vs human arousal) ===")
    print(f"  PRIMARY  Spearman rho (conf-weighted mean) = {rho_w:+.3f}  "
          f"95% CI [{lo:+.3f}, {hi:+.3f}]")
    print(f"           permutation p (1-sided, {C.PERMUTATIONS} shuffles) = {p_w:.4f}  "
          f"({k}/{C.PERMUTATIONS} >= observed)")
    print(f"           Pearson r (secondary) = {r_pearson:+.3f}")
    print(f"  SENSITIVITY  rho(plain mean) = {rho_m:+.3f}   rho(median) = {rho_md:+.3f}")

    # --- F0-failure / extraction diagnostic ---
    print("\n=== F0 / EXTRACTION DIAGNOSTIC (extraction-failure vs signal-reality) ===")
    print(f"  utterance abstention      = {len(abst)}/{n_total} ({100*len(abst)/n_total:.1f}%)"
          f"  [too_short={too_short}, low_voicing(VAD)={low_voicing}]")
    lowconf = int((conf < 0.30).sum())
    print(f"  low-confidence readings   = {lowconf}/{n} ({100*lowconf/n:.1f}%) have mean_confidence < 0.30")
    print(f"  confidence percentiles    = p10={np.percentile(conf,10):.2f} "
          f"p50={np.percentile(conf,50):.2f} p90={np.percentile(conf,90):.2f}")
    e_ratio = np.minimum(energy / 0.3, 1.0)
    f_ratio = np.minimum(f0std / 50.0, 1.0)
    print(f"  energy-term utilization   = mean {e_ratio.mean():.2f} of range; "
          f"saturated(energy>=0.3) {100*(energy>=0.3).mean():.1f}%  -> loudness cue mostly muted")
    print(f"  f0_std-term utilization   = mean {f_ratio.mean():.2f} of range; "
          f"saturated(f0_std>=50) {100*(f0std>=50).mean():.1f}%  -> pitch cue often clipped")

    # --- per-show breakdown ---
    print("\n=== PER-SHOW (heterogeneity) ===")
    shows_arr = np.array(shows)
    for s in sorted(set(shows), key=lambda s: -(shows_arr == s).sum()):
        m = shows_arr == s
        ns = int(m.sum())
        tag = "" if ns >= 15 else "  (small N)"
        rs = spearman(wmean[m], human[m]) if ns >= 2 else float("nan")
        print(f"  {s:16s} N={ns:3d}  rho={rs:+.3f}{tag}")

    # --- per-speaker breakdown (N >= 15) ---
    print("\n=== PER-SPEAKER (N >= 15) ===")
    spk_arr = np.array(speakers)
    big = [(s, int((spk_arr == s).sum())) for s in set(speakers) if (spk_arr == s).sum() >= 15]
    for s, ns in sorted(big, key=lambda t: -t[1]):
        m = spk_arr == s
        print(f"  {s:16s} N={ns:3d}  rho={spearman(wmean[m], human[m]):+.3f}")
    covered = sum(ns for _, ns in big)
    print(f"  ({len(big)} speakers with N>=15 cover {covered}/{n}; "
          f"remaining {n-covered} across smaller-N speakers)")

    # --- verdict ---
    band = C.verdict_band(rho_w, p_w)
    print("\n" + "=" * 74)
    print(f"VERDICT (arousal, ACTED baseline):  rho={rho_w:+.3f}  p={p_w:.4f}  ->  {band}")
    print("  Bands: GENERALIZES (rho>=0.35 & p<0.001) | WEAK [0.20,0.35) | "
          "DOES NOT GENERALIZE (<0.20)")
    print("  SCOPE: acted precondition only -- NOT the acted->spontaneous test "
          "(no spontaneous corpus available; signal has no valence axis).")
    print("=" * 74)

    # --- chart ---
    C.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(C.SEED)
    jitter = rng.uniform(-0.18, 0.18, size=n)
    fig, ax = plt.subplots(figsize=(7.5, 5.2))
    for s in sorted(set(shows), key=lambda s: -(shows_arr == s).sum()):
        m = shows_arr == s
        ax.scatter(human[m] + jitter[m], wmean[m], s=16, alpha=0.5, label=f"{s} (n={int(m.sum())})")
    ax.set_xlabel("human arousal (1-9 Likert; x-jittered)")
    ax.set_ylabel("predicted arousal (eGeMAPS, conf-weighted mean)")
    ax.set_title("PGSO Phase 1 -- MUStARD++ (ACTED) arousal\n"
                 f"Spearman rho={rho_w:+.3f} [95% CI {lo:+.3f},{hi:+.3f}], "
                 f"perm p={p_w:.4f}, N={n}  ->  {band}")
    ax.legend(fontsize=8, title="show")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    png = C.RESULTS_DIR / "scatter_arousal_wmean.png"
    fig.savefig(png, dpi=130)
    print(f"\n[chart] {png}")

    # --- machine-readable record ---
    with open(C.RESULTS_DIR / "verdict.txt", "w", encoding="utf-8") as f:
        f.write(f"axis=arousal corpus=MUStARD++(acted) N={n}\n")
        f.write(f"rho_wmean={rho_w:.4f} ci95=[{lo:.4f},{hi:.4f}] perm_p={p_w:.4f} k={k}/{C.PERMUTATIONS}\n")
        f.write(f"rho_mean={rho_m:.4f} rho_median={rho_md:.4f} pearson={r_pearson:.4f}\n")
        f.write(f"abstained={len(abst)} too_short={too_short} low_voicing={low_voicing}\n")
        f.write(f"verdict={band}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
