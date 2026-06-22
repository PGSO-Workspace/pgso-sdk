"""
PGSO Phase 2 -- Arousal-signal BENCHMARK: pre-registration & shared config.

Measures how well several prosodic arousal signals track HUMAN arousal on the
SAME spontaneous corpus, same axis, same pre-registered bands. The bands are
fixed BEFORE any run and printed at the top of every run; no post-hoc changes,
no tuning to cross a band, no pooling corpora.

STATUS: pre-staged. Awaiting a spontaneous corpus on disk (IEMOCAP improvised /
MSP-Podcast). The candidate models + harness are validated on synthetic audio;
the corpus loader + verdict run when audio lands.
"""
from pathlib import Path

EXPERIMENT = "phase2-arousal-benchmark"
PRIMARY_AXIS = "arousal"

# --- PRE-REGISTERED decision bands (fixed before any run) --------------------
# Spearman rho of PREDICTED arousal vs HUMAN arousal, per candidate, per corpus.
RHO_STRONG = 0.50    # rho >= 0.50 AND perm p < P_THRESHOLD -> STRONG (governance-grade)
RHO_MODERATE = 0.35  # 0.35 <= rho < 0.50 -> MODERATE (usable, document limits)
RHO_WEAK = 0.20      # 0.20 <= rho < 0.35 -> WEAK (no better than Phase-1 heuristic band)
P_THRESHOLD = 0.001  # < 0.20 -> DOES NOT TRACK

# --- honesty-control parameters ----------------------------------------------
PERMUTATIONS = 5000
BOOTSTRAP_RESAMPLES = 2000
SEED = 42

# --- reference points --------------------------------------------------------
PHASE1_BASELINE_RHO = 0.206  # Phase-1 eGeMAPS arousal vs human arousal (ACTED). The bar to beat.

# --- audio / windowing (reuse Phase 1 for equal footing) ---------------------
SAMPLE_RATE = 16000
WINDOW_S = 0.8   # 12800 samples @ 16 kHz (Phase-1 analysis window)
HOP_S = 0.4      # 6400 samples @ 16 kHz
# Every candidate predicts arousal on the SAME voiced windows and is aggregated
# by confidence-weighted mean using the SAME eGeMAPS voicing confidence, so any
# rho difference is due to per-window prediction quality, not aggregation.

# --- candidate registry (the benchmark axis) ---------------------------------
# commercial_ok drives the "shippable?" column: a STRONG-but-NC model is an
# academic ceiling, not a wireable SDK signal.
CANDIDATES = [
    {
        "id": "egemaps",
        "label": "eGeMAPS 2-feature (Phase 1)",
        "kind": "dsp",
        "source": "crates/pgso-signal-egemaps (Rust, reused as-is)",
        "params": "~2 features",
        "license": "project-owned",
        "commercial_ok": True,
        "mode": "deterministic",
    },
    {
        "id": "wav2small",
        "label": "Wav2Small2.0 A/D/V",
        "kind": "learned",
        "source": "audeering/wav2small",
        "params": "17K params / 68 KB",
        "license": "CC BY-NC-SA 4.0",
        "commercial_ok": False,
        "mode": "zero-shot",
    },
    {
        "id": "wav2vec2_large_msp",
        "label": "wav2vec2-large-robust msp-dim A/D/V",
        "kind": "learned",
        "source": "audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim",
        "params": "~165M params / ~1.3 GB",
        "license": "CC BY-NC-SA 4.0",
        "commercial_ok": False,
        "mode": "zero-shot",
    },
]

# --- paths -------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
PHASE1_EXTRACT_BIN_MANIFEST = PROJECT / "experiments" / "phase1-arousal-mustard" / "extract" / "Cargo.toml"
OUT = HERE / "out"
RESULTS_DIR = OUT / "results"


def verdict_band(rho: float, p: float) -> str:
    if rho >= RHO_STRONG and p < P_THRESHOLD:
        return "STRONG"
    if rho >= RHO_MODERATE:
        return "MODERATE"
    if rho >= RHO_WEAK:
        return "WEAK"
    return "DOES NOT TRACK"


def print_header() -> None:
    line = "=" * 76
    print(line)
    print(f"PGSO {EXPERIMENT}  |  axis: {PRIMARY_AXIS}  |  BENCHMARK (per-candidate, per-corpus)")
    print(line)
    print("PRE-REGISTERED bands (fixed before any run; never changed post-hoc):")
    print(f"  rho >= {RHO_STRONG:.2f} AND perm p < {P_THRESHOLD} -> STRONG (governance-grade)")
    print(f"  {RHO_MODERATE:.2f} <= rho < {RHO_STRONG:.2f}             -> MODERATE (usable, document limits)")
    print(f"  {RHO_WEAK:.2f} <= rho < {RHO_MODERATE:.2f}             -> WEAK (= Phase-1 heuristic band)")
    print(f"  rho <  {RHO_WEAK:.2f}                       -> DOES NOT TRACK")
    print(f"  Phase-1 eGeMAPS baseline to beat: rho = {PHASE1_BASELINE_RHO} (ACTED)")
    print(f"  permutations={PERMUTATIONS}  bootstrap={BOOTSTRAP_RESAMPLES}  seed={SEED}")
    print("  RULES: no pooling, no tuning, no cherry-picking a corpus; report all candidates that ran.")
    print(line)
