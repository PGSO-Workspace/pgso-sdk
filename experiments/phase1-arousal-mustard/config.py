"""
PGSO Phase 1 -- Signal Generalization Eval: pre-registration & shared config.

Everything decision-relevant lives here and is PRINTED at the top of every run.
The decision bands are PRE-REGISTERED: changing them after seeing results is
forbidden and defeats the test.
"""
from pathlib import Path

# --- Identity / scope --------------------------------------------------------
EXPERIMENT = "phase1-arousal-mustard"
PRIMARY_AXIS = "arousal"  # the ONLY axis eGeMAPS produces; no valence mapping exists
CORPUS = "MUStARD++ (acted sitcom)"
SCOPE_NOTE = (
    "ACTED-BASELINE / PRECONDITION ONLY. The eGeMAPS signal produces arousal "
    "only (no valence mapping exists in the crate), and no spontaneous corpus "
    "(IEMOCAP improvised / MSP-Podcast) is available locally. This run grades "
    "eGeMAPS arousal vs MUStARD++ human arousal on ACTED speech. It does NOT "
    "answer the acted->spontaneous generalization question; it is the "
    "precondition -- if arousal does not track even here, the spontaneous "
    "question is moot. A pass here is necessary, not sufficient."
)

# --- PRE-REGISTERED decision bands (fixed before any run) --------------------
# Bands are from the task spec; applied here to arousal (the testable axis).
RHO_GENERALIZES = 0.35  # rho >= 0.35 AND perm p < P_THRESHOLD -> GENERALIZES
RHO_WEAK = 0.20         # 0.20 <= rho < 0.35 -> WEAK ; rho < 0.20 -> DOES NOT GENERALIZE
P_THRESHOLD = 0.001     # permutation p threshold for a GENERALIZES verdict

# --- Honesty-control parameters ----------------------------------------------
PERMUTATIONS = 5000        # >= 1000 required; 5000 for a tighter p estimate
BOOTSTRAP_RESAMPLES = 2000  # 95% CI on rho (matches Phase 0)
SEED = 42

# --- Audio / extractor contract ----------------------------------------------
SAMPLE_RATE = 16000     # eGeMAPS construction rate; ffmpeg decodes to this, mono
# The extractor's DEFAULT analysis window (lib.rs EgemapsConfig::for_sample_rate).
# Used only to report how many utterances are long enough to yield >=1 reading.
DEFAULT_WINDOW_S = 0.8

# --- Paths (reuse Phase 0 layout) --------------------------------------------
HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
DATA = PROJECT / "data"
CSV_PATH = DATA / "mustard_repo" / "mustard++_text.csv"
VIDEO_DIR = DATA / "videos" / "final_utterance_videos"
OUT = HERE / "out"
WAV_DIR = OUT / "wav"
MANIFEST = OUT / "manifest.csv"
PREDICTED = OUT / "predicted.csv"
WINDOWS = OUT / "windows.csv"
RESULTS_DIR = OUT / "results"

# --- calibration study (label-free; see calibrate.py) ------------------------
CALIB_REF_PERCENTILE = 95  # energy_ref / f0_std_ref <- this percentile of per-window features
CALIB_MIN_SPK_UTTS = 2     # within-speaker centering needs >= this many utterances/speaker


def verdict_band(rho: float, p: float) -> str:
    """Map (rho, permutation p) to the pre-registered band label."""
    if rho >= RHO_GENERALIZES and p < P_THRESHOLD:
        return "GENERALIZES"
    if rho >= RHO_WEAK:
        return "WEAK (signal present, degraded)"
    return "DOES NOT GENERALIZE"


def print_header() -> None:
    """Print the pre-registered criterion + scope at the top of every run."""
    line = "=" * 74
    print(line)
    print(f"PGSO {EXPERIMENT}  |  primary axis: {PRIMARY_AXIS}  |  corpus: {CORPUS}")
    print(line)
    print("PRE-REGISTERED bands (fixed before any run; post-hoc changes forbidden):")
    print(f"  rho >= {RHO_GENERALIZES:.2f} AND perm p < {P_THRESHOLD:<6} -> GENERALIZES")
    print(f"  {RHO_WEAK:.2f} <= rho < {RHO_GENERALIZES:.2f}                 -> WEAK (degraded)")
    print(f"  rho <  {RHO_WEAK:.2f}                            -> DOES NOT GENERALIZE")
    print(f"  permutations={PERMUTATIONS}  bootstrap={BOOTSTRAP_RESAMPLES}  seed={SEED}")
    print(line)
    print("SCOPE: " + SCOPE_NOTE)
    print(line)
