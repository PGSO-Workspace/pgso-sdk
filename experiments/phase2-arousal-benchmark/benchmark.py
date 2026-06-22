"""
Phase 2 benchmark orchestrator (corpus-agnostic).

Input: a standard manifest CSV with columns
    scene, wav_path, human_arousal, speaker[, human_valence]
(one spontaneous corpus; the corpus-specific loader that produces this manifest
is written when the audio lands -- IEMOCAP improvised / MSP-Podcast).

For each candidate it produces a NATIVE per-utterance predicted arousal:
  - eGeMAPS: the Phase-1 Rust extractor (800ms window + confidence-weighted mean), reused as-is.
  - wav2small / heavy: the model's own whole-utterance pooling.
Then: Spearman rho vs human arousal (+permutation p, bootstrap CI, Pearson),
pairwise delta-rho vs the eGeMAPS baseline (paired bootstrap CI), a verdict
table, one scatter per candidate, and a root-cause read. No pooling across
corpora; abstentions reported, never imputed.

Usage: python benchmark.py <manifest.csv> [corpus_name]
"""
import csv
import subprocess
import sys
import time
from pathlib import Path

import librosa
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import config as C
import eval_stats as S


def load_manifest(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def predict_egemaps(rows):
    """Run the Phase-1 Rust extractor over the utterances; return {scene: arousal|nan} + diagnostics."""
    C.OUT.mkdir(parents=True, exist_ok=True)
    man = C.OUT / "_egemaps_manifest.csv"
    pred = C.OUT / "_egemaps_predicted.csv"
    with open(man, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scene", "speaker", "show", "sarcasm", "human_valence", "human_arousal", "wav_path"])
        for r in rows:
            w.writerow([r["scene"], r.get("speaker", "?"), "corpus", "0",
                        r.get("human_valence", "0"), r["human_arousal"], r["wav_path"]])
    t0 = time.perf_counter()
    res = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--manifest-path", str(C.PHASE1_EXTRACT_BIN_MANIFEST),
         "--", str(man), str(pred)],
        capture_output=True, text=True, check=False,
    )
    dt = time.perf_counter() - t0
    if res.returncode != 0:
        raise RuntimeError(f"eGeMAPS bin failed: {res.stderr.strip()[-300:]}")
    out, n_abst, n_short, n_lowvoice = {}, 0, 0, 0
    with open(pred, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = row["pred_arousal_wmean"]
            out[row["scene"]] = float(v) if v != "" else float("nan")
            if v == "":
                n_abst += 1
                if row.get("too_short") == "true":
                    n_short += 1
                else:
                    n_lowvoice += 1
    diag = {"latency_s_total": dt, "abstain": n_abst, "too_short": n_short, "low_voicing": n_lowvoice}
    return out, diag


def predict_learned(rows, module):
    """Whole-utterance arousal (idx 0 of A/D/V) for a learned candidate."""
    out, infer_t = {}, []
    if module == "wav2small":
        import wav2small_model as m
        model = m.load("cpu")
        predict = lambda s: m.predict_adv(model, s)[0]
    else:
        import audeering_model as m
        proc, model = m.load("cpu")
        predict = lambda s: m.predict_adv(proc, model, s)[0]
    for r in rows:
        try:
            sig, _ = librosa.load(r["wav_path"], sr=C.SAMPLE_RATE, mono=True)
            if sig.size < int(0.1 * C.SAMPLE_RATE):
                out[r["scene"]] = float("nan")
                continue
            t0 = time.perf_counter()
            out[r["scene"]] = float(predict(sig))
            infer_t.append(time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001 - record failure, never impute
            print(f"   [{module} fail] {r['scene']}: {type(e).__name__}", file=sys.stderr)
            out[r["scene"]] = float("nan")
    return out, {"latency_ms_mean": (np.mean(infer_t) * 1000) if infer_t else float("nan")}


def aligned(scenes, pred, human):
    p = np.array([pred[s] for s in scenes], float)
    h = np.array([human[s] for s in scenes], float)
    m = np.isfinite(p) & np.isfinite(h)
    return p[m], h[m], m


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python benchmark.py <manifest.csv> [corpus_name]", file=sys.stderr)
        return 2
    manifest_path = sys.argv[1]
    corpus = sys.argv[2] if len(sys.argv) > 2 else "corpus"

    C.print_header()
    rows = load_manifest(manifest_path)
    scenes = [r["scene"] for r in rows]
    human = {r["scene"]: float(r["human_arousal"]) for r in rows}
    ha = np.array([human[s] for s in scenes], float)
    print(f"[{corpus}] N={len(rows)} utterances")
    print(f"[label] human arousal: min={ha.min():.2f} max={ha.max():.2f} mean={ha.mean():.2f} "
          f"sd={ha.std():.2f}  (range restriction caps achievable rho -- cf. Phase 1)")

    # --- predictions per candidate ---
    preds, meta = {}, {}
    print("\n[egemaps] running Phase-1 Rust extractor ...")
    preds["egemaps"], meta["egemaps"] = predict_egemaps(rows)
    for cid in ("wav2small", "wav2vec2_large_msp"):
        mod = "wav2small" if cid == "wav2small" else "heavy"
        print(f"[{cid}] running ({mod}) ...")
        preds[cid], meta[cid] = predict_learned(rows, mod)

    # --- per-candidate stats ---
    reg = {c["id"]: c for c in C.CANDIDATES}
    stats = {}
    for cid in reg:
        if cid not in preds:
            continue
        p, h, _ = aligned(scenes, preds[cid], human)
        if len(p) < 5 or np.std(p) < 1e-9:
            stats[cid] = {"n": len(p), "rho": float("nan"), "ci": (float("nan"), float("nan")),
                          "p": float("nan"), "pearson": float("nan"), "band": "n/a"}
            continue
        rho = S.spearman(p, h)
        ci = S.bootstrap_ci(p, h, C.BOOTSTRAP_RESAMPLES, C.SEED)
        pp, _ = S.permutation_p(p, h, C.PERMUTATIONS, C.SEED)
        stats[cid] = {"n": len(p), "rho": rho, "ci": ci, "p": pp,
                      "pearson": S.pearson(p, h), "band": C.verdict_band(rho, pp)}

    # --- verdict table ---
    print("\n" + "=" * 92)
    print(f"VERDICT TABLE -- {corpus} (arousal)")
    print("=" * 92)
    print(f"{'candidate':<30}{'N':>5}{'rho':>8}{'95% CI':>18}{'p':>9}{'band':>14}{'commercial':>14}")
    print("-" * 92)
    for cid, c in reg.items():
        st = stats.get(cid)
        if not st:
            print(f"{c['label']:<30}  (did not run)")
            continue
        ci = f"[{st['ci'][0]:+.2f},{st['ci'][1]:+.2f}]"
        comm = "YES" if c["commercial_ok"] else "NO"
        rho = st["rho"]
        print(f"{c['label']:<30}{st['n']:>5}{rho:>+8.3f}{ci:>18}{st['p']:>9.4f}{st['band']:>14}{comm:>14}")
    print("-" * 92)
    print("sizes/latency:  " + " | ".join(
        f"{c['id']}={c['params']}" + (f",{meta[c['id']].get('latency_ms_mean'):.0f}ms/utt"
        if c['id'] in meta and 'latency_ms_mean' in meta[c['id']] and np.isfinite(meta[c['id']]['latency_ms_mean']) else "")
        for c in C.CANDIDATES if c['id'] in stats))
    eg = meta.get("egemaps", {})
    print(f"eGeMAPS extraction diagnostic: abstain={eg.get('abstain')} "
          f"(too_short={eg.get('too_short')}, low_voicing={eg.get('low_voicing')})")

    # --- pairwise delta vs baseline ---
    print("\n=== PAIRWISE delta-rho vs eGeMAPS baseline (paired bootstrap; CI excludes 0 => REAL) ===")
    base = preds.get("egemaps", {})
    for cid in ("wav2small", "wav2vec2_large_msp"):
        if cid not in preds:
            continue
        pa = np.array([preds[cid][s] for s in scenes], float)
        pb = np.array([base[s] for s in scenes], float)
        h = ha.copy()
        m = np.isfinite(pa) & np.isfinite(pb) & np.isfinite(h)
        if m.sum() < 5:
            print(f"  {cid}: insufficient paired N")
            continue
        d_rho = S.spearman(pa[m], h[m]) - S.spearman(pb[m], h[m])
        lo, hi = S.paired_bootstrap_delta(pa[m], pb[m], h[m], C.BOOTSTRAP_RESAMPLES, C.SEED)
        real = "REAL" if (lo > 0 or hi < 0) else "within noise"
        print(f"  {reg[cid]['label']:<30} N={int(m.sum())}  delta-rho={d_rho:+.3f}  "
              f"95% CI [{lo:+.3f},{hi:+.3f}]  -> {real}")

    # --- scatters ---
    C.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(C.SEED)
    for cid in reg:
        st = stats.get(cid)
        if not st or not np.isfinite(st["rho"]):
            continue
        p, h, _ = aligned(scenes, preds[cid], human)
        jitter = rng.uniform(-0.15, 0.15, size=len(h))
        fig, ax = plt.subplots(figsize=(6.6, 4.8))
        ax.scatter(h + jitter, p, s=14, alpha=0.5)
        ax.set_xlabel(f"human arousal ({corpus})")
        ax.set_ylabel(f"predicted arousal ({cid})")
        ax.set_title(f"{reg[cid]['label']} -- rho={st['rho']:+.3f} "
                     f"[{st['ci'][0]:+.2f},{st['ci'][1]:+.2f}], p={st['p']:.4f}, N={st['n']}  [{st['band']}]")
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        fig.savefig(C.RESULTS_DIR / f"scatter_{corpus}_{cid}.png", dpi=130)
        plt.close(fig)
    print(f"\n[charts] {C.RESULTS_DIR}/scatter_{corpus}_*.png")

    # --- root-cause read ---
    print("\n=== ROOT-CAUSE READ ===")
    ran = {k: v for k, v in stats.items() if np.isfinite(v.get("rho", float("nan")))}
    if ran:
        best = max(ran, key=lambda k: ran[k]["rho"])
        print(f"  best candidate: {reg[best]['label']} (rho={ran[best]['rho']:+.3f}, {ran[best]['band']})")
        if all(v["band"] in ("WEAK", "DOES NOT TRACK") for v in ran.values()):
            print(f"  Even the strongest signal stays <= WEAK on {corpus}: attribute to task difficulty "
                  f"(spontaneous arousal ceiling) + label range restriction (sd={ha.std():.2f}).")
        print(f"  vs Phase-1 acted baseline rho={C.PHASE1_BASELINE_RHO}: see pairwise CIs above for whether "
              f"a learned signal beats the heuristic for real.")
        print("  NB: both learned candidates are CC BY-NC-SA -- a STRONG learned result is an academic")
        print("      ceiling, not a shippable signal, absent a commercial license or an own-trained head.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
