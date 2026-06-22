"""
Pre-stage validation (NO corpus): confirm each benchmark candidate loads and
runs on synthetic audio, and report params + per-window latency + commercial
flag. This validates the pipeline and the "lightweight" claim before a corpus
arrives. It produces NO verdict and uses NO real data.

Synthetic clips span calm -> excited (rising energy + pitch variation); a sane
arousal model should rank excited > calm, but this is only a smoke signal, not
an accuracy claim.
"""
import csv
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf

import config as C

WIN = int(C.WINDOW_S * C.SAMPLE_RATE)


def synth(kind: str, dur: float = 3.0, sr: int = C.SAMPLE_RATE) -> np.ndarray:
    t = np.arange(int(dur * sr)) / sr
    f0, amp, vib = {"calm": (110, 0.05, 2), "neutral": (160, 0.15, 10), "excited": (240, 0.33, 45)}[kind]
    inst = f0 + vib * np.sin(2 * np.pi * 4 * t)
    phase = 2 * np.pi * np.cumsum(inst) / sr
    sig = amp * np.sin(phase) + 0.3 * amp * np.sin(2 * phase)
    return sig.astype(np.float32)


CLIPS = {k: synth(k) for k in ("calm", "neutral", "excited")}


def n_params(model) -> int:
    return sum(p.numel() for p in model.parameters())


def time_window(fn, n: int = 8) -> float:
    """Mean seconds to run fn() over one 0.8 s window."""
    fn()  # warmup
    start = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - start) / n


def validate_wav2small() -> dict:
    import wav2small_model as w2s
    t0 = time.perf_counter()
    model = w2s.load("cpu")
    load_s = time.perf_counter() - t0
    adv = {k: w2s.predict_adv(model, clip)[0] for k, clip in CLIPS.items()}  # arousal = idx 0
    win = CLIPS["excited"][:WIN]
    lat = time_window(lambda: w2s.predict_adv(model, win))
    return {"loaded": True, "params": n_params(model), "load_s": load_s,
            "lat_ms": lat * 1000, "arousal": adv}


def validate_heavy() -> dict:
    import audeering_model as au
    t0 = time.perf_counter()
    processor, model = au.load("cpu")
    load_s = time.perf_counter() - t0
    adv = {k: au.predict_adv(processor, model, clip)[0] for k, clip in CLIPS.items()}
    win = CLIPS["excited"][:WIN]
    lat = time_window(lambda: au.predict_adv(processor, model, win), n=3)
    return {"loaded": True, "params": n_params(model), "load_s": load_s,
            "lat_ms": lat * 1000, "arousal": adv}


def validate_egemaps() -> dict:
    """Confirm the Phase-1 Rust extractor runs on synthetic audio (reused as-is)."""
    synth_dir = C.OUT / "synth"
    synth_dir.mkdir(parents=True, exist_ok=True)
    manifest = C.OUT / "synth_manifest.csv"
    pred = C.OUT / "synth_predicted.csv"
    with open(manifest, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["scene", "speaker", "show", "sarcasm", "human_valence", "human_arousal", "wav_path"])
        for k, clip in CLIPS.items():
            wav = synth_dir / f"{k}.wav"
            sf.write(wav, clip, C.SAMPLE_RATE, subtype="PCM_16")
            w.writerow([k, "synthetic", "synth", "0", "0", "0", str(wav)])
    t0 = time.perf_counter()
    res = subprocess.run(
        ["cargo", "run", "--release", "--quiet", "--manifest-path", str(C.PHASE1_EXTRACT_BIN_MANIFEST),
         "--", str(manifest), str(pred)],
        capture_output=True, text=True, check=False,
    )
    run_s = time.perf_counter() - t0
    if res.returncode != 0:
        return {"loaded": False, "err": res.stderr.strip()[-300:]}
    arousal = {}
    with open(pred, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = row["pred_arousal_wmean"]
            arousal[row["scene"]] = float(v) if v != "" else float("nan")
    return {"loaded": True, "params": "~2 features", "load_s": run_s, "lat_ms": None, "arousal": arousal}


def main() -> int:
    C.print_header()
    C.OUT.mkdir(parents=True, exist_ok=True)
    print("PRE-STAGE VALIDATION on synthetic audio (no corpus, no verdict).\n")

    runners = {"egemaps": validate_egemaps, "wav2small": validate_wav2small,
               "wav2vec2_large_msp": validate_heavy}
    results = {}
    for cand in C.CANDIDATES:
        cid = cand["id"]
        print(f"[{cid}] loading + running ...")
        try:
            results[cid] = runners[cid]()
        except Exception as e:  # noqa: BLE001 - record which candidates ran, never fabricate
            results[cid] = {"loaded": False, "err": f"{type(e).__name__}: {e}"}
            print(f"   FAILED: {results[cid]['err']}")
        else:
            r = results[cid]
            if r["loaded"]:
                a = r["arousal"]
                print(f"   ok | arousal calm={a.get('calm', float('nan')):.3f} "
                      f"neutral={a.get('neutral', float('nan')):.3f} excited={a.get('excited', float('nan')):.3f}")

    print("\n" + "=" * 76)
    print("CANDIDATE READINESS TABLE")
    print("=" * 76)
    hdr = f"{'candidate':<34}{'ran':<5}{'params':<20}{'lat/0.8s-win':<14}{'commercial'}"
    print(hdr)
    print("-" * 76)
    for cand in C.CANDIDATES:
        r = results.get(cand["id"], {})
        ran = "yes" if r.get("loaded") else "NO"
        params = str(r.get("params", cand["params"]))
        lat = r.get("lat_ms")
        lat_s = f"{lat:.1f} ms" if isinstance(lat, (int, float)) else "n/a"
        comm = "YES" if cand["commercial_ok"] else f"NO ({cand['license']})"
        print(f"{cand['label']:<34}{ran:<5}{params:<20}{lat_s:<14}{comm}")
        if not r.get("loaded") and "err" in r:
            print(f"    -> {r['err']}")
    print("=" * 76)
    print("Arousal monotonicity smoke-check (calm < excited expected):")
    for cid, r in results.items():
        if r.get("loaded") and isinstance(r.get("arousal"), dict):
            a = r["arousal"]
            if "calm" in a and "excited" in a:
                ok = a["excited"] > a["calm"]
                print(f"  {cid:<22} calm={a['calm']:.3f} -> excited={a['excited']:.3f}  {'OK' if ok else 'INVERTED'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
