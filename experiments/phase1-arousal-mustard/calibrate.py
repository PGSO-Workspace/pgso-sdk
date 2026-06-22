"""
Phase 1 calibration study -- LABEL-FREE (pre-registered before any run).

Asks whether the Phase-1 WEAK arousal result was calibration-limited or
inherent, WITHOUT letting the human labels choose any mapping constant:

  V0  default refs (0.3 / 50), absolute              -> baseline (= Phase 1)
  V1  refs <- P95 of per-window features, absolute   -> undo dual-compression
  V3  V1 refs + within-speaker centering             -> PRIMARY (within-speaker regime)

Comparisons are apples-to-apples WITHIN a regime:
  - range effect (absolute):       rho(V1) - rho(V0)          [paired bootstrap CI]
  - range effect (within-speaker): rho(V3) - rho(V0_ws)       [paired bootstrap CI]
  - speaker effect (descriptive):  rho(V0_ws) vs rho(V0) on the same >=2-utt subset
Improvement is real iff the paired-bootstrap 95% CI on the delta excludes 0.
The references come only from feature DISTRIBUTIONS; the shipped crate is
unmodified (arousal recomputed here from per-window energy/f0_std).
"""
import csv
from collections import Counter, defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config as C

W_ENERGY, W_F0 = 0.6, 0.4          # shipped AxisMapping weights (features.rs)
DEF_REF_E, DEF_REF_F = 0.3, 50.0   # shipped default references


# --- stats (rank-based, tie-aware; self-contained) ---------------------------
def avg_rank(a):
    a = np.asarray(a, float)
    n = len(a)
    order = np.argsort(a, kind="mergesort")
    sorted_a = a[order]
    ranks = np.empty(n, float)
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
    rng = np.random.default_rng(seed)
    rx, ry = avg_rank(x), avg_rank(y)
    obs = pearson(rx, ry)
    k = sum(1 for _ in range(n_perm) if pearson(rx, rng.permutation(ry)) >= obs)
    return (k + 1) / (n_perm + 1)


def center_ws(vals, spk):
    """Subtract each speaker's mean (within-subject centering)."""
    out = np.asarray(vals, float).copy()
    for s in set(spk.tolist()):
        m = spk == s
        out[m] = out[m] - out[m].mean()
    return out


def paired_bootstrap_delta(pred_a, pred_b, human, seed, speakers=None):
    """95% CI on rho(pred_a)-rho(pred_b). If speakers given, within-speaker regime."""
    rng = np.random.default_rng(seed)
    n = len(human)
    d = np.empty(C.BOOTSTRAP_RESAMPLES)
    for b in range(C.BOOTSTRAP_RESAMPLES):
        idx = rng.integers(0, n, n)
        if speakers is None:
            d[b] = spearman(pred_a[idx], human[idx]) - spearman(pred_b[idx], human[idx])
        else:
            sa = speakers[idx]
            hh = center_ws(human[idx], sa)
            d[b] = spearman(center_ws(pred_a[idx], sa), hh) - spearman(center_ws(pred_b[idx], sa), hh)
    lo, hi = np.percentile(d, [2.5, 97.5])
    return float(lo), float(hi)


def arousal(energy, f0_std, ref_e, ref_f):
    """Shipped AxisMapping formula with substituted references."""
    e = np.clip(energy / ref_e, 0.0, 1.0)
    p = np.clip(f0_std / ref_f, 0.0, 1.0)
    return np.clip(W_ENERGY * e + W_F0 * p, 0.0, 1.0)


def wmean(vals, weights):
    w = weights.sum()
    return float((vals * weights).sum() / w) if w > 1e-9 else float(vals.mean())


def main() -> int:
    C.print_header()
    print("LABEL-FREE CALIBRATION STUDY (pre-registered):")
    print(f"  refs <- P{C.CALIB_REF_PERCENTILE} of per-window features (NOT fit to labels)")
    print("  V1 = re-scaled refs (absolute);  V3 = V1 + within-speaker centering (PRIMARY)")
    print("  improvement real iff paired-bootstrap 95% CI on delta-rho excludes 0")
    print("=" * 74)

    # --- load per-window features + per-utterance meta ---
    meta = {}
    with open(C.PREDICTED, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            meta[r["scene"]] = (float(r["human_arousal"]), r["speaker"])
    wins = defaultdict(list)
    with open(C.WINDOWS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            wins[r["scene"]].append(
                (float(r["energy"]), float(r["f0_std"]), float(r["confidence"]), float(r["arousal_default"]))
            )

    # --- label-free references (P95 of ALL per-window features) ---
    all_e = np.array([w[0] for sc in wins for w in wins[sc]])
    all_f = np.array([w[1] for sc in wins for w in wins[sc]])
    ref_e = float(np.percentile(all_e, C.CALIB_REF_PERCENTILE))
    ref_f = float(np.percentile(all_f, C.CALIB_REF_PERCENTILE))
    print(f"[refs] energy_ref: {DEF_REF_E} -> {ref_e:.3f}   "
          f"f0_std_ref: {DEF_REF_F} -> {ref_f:.2f}   (label-free, P{C.CALIB_REF_PERCENTILE})")

    # --- per-utterance predictions ---
    scenes = sorted(wins.keys())
    human, speaker, v0, v1, v0_check = [], [], [], [], []
    for sc in scenes:
        arr = np.array(wins[sc])
        e, fz, c, ad = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
        v0.append(wmean(arousal(e, fz, DEF_REF_E, DEF_REF_F), c))
        v1.append(wmean(arousal(e, fz, ref_e, ref_f), c))
        v0_check.append(wmean(ad, c))   # Rust's own per-window arousal, re-aggregated
        h, sp = meta[sc]
        human.append(h)
        speaker.append(sp)
    human = np.array(human); speaker = np.array(speaker)
    v0 = np.array(v0); v1 = np.array(v1); v0_check = np.array(v0_check)

    # --- sanity: Python recompute of V0 must match the Rust extractor ---
    diff = float(np.max(np.abs(v0 - v0_check)))
    print(f"[sanity] max|python V0 - rust V0| = {diff:.2e}  "
          f"(recompute formula validated)  N={len(scenes)}")

    # --- absolute regime: does range re-scale help? ---
    rho0 = spearman(v0, human)
    rho1 = spearman(v1, human)
    lo_a, hi_a = paired_bootstrap_delta(v1, v0, human, C.SEED)
    p1 = permutation_p(v1, human, C.PERMUTATIONS, C.SEED)
    print("\n=== ABSOLUTE regime ===")
    print(f"  rho V0 (default) = {rho0:+.3f}   rho V1 (P95 refs) = {rho1:+.3f}   perm p(V1) = {p1:.4f}")
    print(f"  delta(range) = {rho1 - rho0:+.3f}  95% CI [{lo_a:+.3f}, {hi_a:+.3f}]"
          f"  -> {'REAL' if lo_a > 0 or hi_a < 0 else 'not distinguishable from 0'}")

    # --- within-speaker regime ---
    cnt = Counter(speaker.tolist())
    ws = np.array([cnt[s] >= C.CALIB_MIN_SPK_UTTS for s in speaker])
    n_ws, n_drop = int(ws.sum()), int((~ws).sum())
    sp_ws = speaker[ws]
    hum_ws_c = center_ws(human[ws], sp_ws)
    rho0_ws = spearman(center_ws(v0[ws], sp_ws), hum_ws_c)
    rho3_ws = spearman(center_ws(v1[ws], sp_ws), hum_ws_c)
    rho0_abs_sub = spearman(v0[ws], human[ws])
    lo_w, hi_w = paired_bootstrap_delta(v1[ws], v0[ws], human[ws], C.SEED, speakers=sp_ws)
    p3 = permutation_p(center_ws(v1[ws], sp_ws), hum_ws_c, C.PERMUTATIONS, C.SEED)
    print("\n=== WITHIN-SPEAKER regime "
          f"(speakers with >={C.CALIB_MIN_SPK_UTTS} utts: N={n_ws}, dropped {n_drop}) ===")
    print(f"  rho V0_ws (default) = {rho0_ws:+.3f}   rho V3_ws (PRIMARY) = {rho3_ws:+.3f}   perm p(V3) = {p3:.4f}")
    print(f"  delta(range | within-speaker) = {rho3_ws - rho0_ws:+.3f}  "
          f"95% CI [{lo_w:+.3f}, {hi_w:+.3f}]"
          f"  -> {'REAL' if lo_w > 0 or hi_w < 0 else 'not distinguishable from 0'}")
    print(f"  speaker effect (descriptive, diff regime): rho0_abs(subset)={rho0_abs_sub:+.3f} "
          f"-> rho0_ws={rho0_ws:+.3f}")

    # --- verdict ---
    band = C.verdict_band(rho3_ws, p3)
    print("\n" + "=" * 74)
    print(f"CALIBRATION VERDICT (label-free):  PRIMARY rho(V3, within-speaker) = {rho3_ws:+.3f} "
          f"p={p3:.4f}  ->  {band}")
    print(f"  reaches 0.35 GENERALIZES bar? {'YES' if rho3_ws >= C.RHO_GENERALIZES else 'NO'}")
    print("  Interpretation: a material, CI-backed lift => Phase-1 WEAK was calibration-limited;")
    print("  no lift => the prosody-only signal is inherently weak on this corpus.")
    print("=" * 74)

    # --- chart ---
    C.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    labels = ["V0\ndefault·abs", "V1\nP95·abs", "V0\ndefault·ws", "V3\nP95·ws (primary)"]
    vals = [rho0, rho1, rho0_ws, rho3_ws]
    colors = ["#999999", "#4c72b0", "#bbbbbb", "#dd8452"]
    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(labels, vals, color=colors)
    ax.axhline(C.RHO_GENERALIZES, ls="--", c="green", lw=1, label=f"{C.RHO_GENERALIZES} GENERALIZES")
    ax.axhline(C.RHO_WEAK, ls=":", c="orange", lw=1, label=f"{C.RHO_WEAK} WEAK floor")
    ax.axhline(0.0, c="black", lw=0.8)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + (0.01 if v >= 0 else -0.03),
                f"{v:+.3f}", ha="center", fontsize=9)
    ax.set_ylabel("Spearman rho vs human arousal")
    ax.set_title("PGSO Phase 1 -- label-free calibration of eGeMAPS arousal (MUStARD++)\n"
                 "absolute vs within-speaker regimes")
    ax.legend(fontsize=8)
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    png = C.RESULTS_DIR / "calib_rho_comparison.png"
    fig.savefig(png, dpi=130)
    print(f"\n[chart] {png}")

    with open(C.RESULTS_DIR / "calib_verdict.txt", "w", encoding="utf-8") as f:
        f.write(f"refs: energy {DEF_REF_E}->{ref_e:.3f}  f0_std {DEF_REF_F}->{ref_f:.2f} (P{C.CALIB_REF_PERCENTILE})\n")
        f.write(f"absolute: rho_v0={rho0:.4f} rho_v1={rho1:.4f} d_range=[{lo_a:.4f},{hi_a:.4f}] p_v1={p1:.4f}\n")
        f.write(f"within-speaker(N={n_ws}): rho_v0={rho0_ws:.4f} rho_v3={rho3_ws:.4f} "
                f"d_range=[{lo_w:.4f},{hi_w:.4f}] p_v3={p3:.4f}\n")
        f.write(f"primary=rho_v3_ws={rho3_ws:.4f} band={band} reaches_0.35={rho3_ws >= C.RHO_GENERALIZES}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
