"""
PGSO Phase 0 — Instrument Calibration Gate
===========================================
Question: Does the VAD-extraction pipeline separate congruent from
incongruent speech above its own measurement noise?

GO/NO-GO:
  AUC >= 0.70  -> GO
  0.60 <= AUC < 0.70 -> GRAY ZONE
  AUC < 0.60  -> NO-GO

Branches:
  Audio -> wav2vec2-large-robust-12-ft-emotion-msp-dim -> (V,A)_audio
  Text  -> NRC VAD Lexicon v2.1 (word-level avg)       -> (V,A)_text

Divergence = ||(V,A)_audio - (V,A)_text||
Label      = sarcasm (proxy for say-hear incongruence)
"""

import csv
import io
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import torch
import torch.nn as nn
from transformers import Wav2Vec2Processor, Wav2Vec2PreTrainedModel, Wav2Vec2Model
from sklearn.metrics import roc_auc_score, roc_curve
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT = Path(__file__).resolve().parent.parent.parent
DATA = PROJECT / "data"
CSV_PATH = DATA / "mustard_repo" / "mustard++_text.csv"
VIDEO_DIR = DATA / "videos" / "final_utterance_videos"
LEXICON_PATH = (
    DATA / "nrc-vad" / "NRC-VAD-Lexicon-v2.1" / "NRC-VAD-Lexicon-v2.1.txt"
)
OUTPUT_DIR = Path(__file__).resolve().parent / "results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ffmpeg binary bundled by imageio-ffmpeg
import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


# ===================================================================
# 1. PARSE ANNOTATIONS
# ===================================================================
def load_annotations() -> pd.DataFrame:
    """Load MUStARD++ target-utterance rows with sarcasm labels."""
    rows = []
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row.get("KEY", "")
            if not key.endswith("_u"):
                continue
            scene = row["SCENE"]
            video_file = VIDEO_DIR / f"{scene}_u.mp4"
            if not video_file.exists():
                continue
            rows.append({
                "scene": scene,
                "sentence": row["SENTENCE"],
                "sarcasm": int(row["Sarcasm"]),
                "implicit_emotion": row["Implicit_Emotion"],
                "explicit_emotion": row["Explicit_Emotion"],
                "valence_human": float(row["Valence"]),
                "arousal_human": float(row["Arousal"]),
                "video_path": str(video_file),
            })
    df = pd.DataFrame(rows)
    print(f"[annotations] {len(df)} instances loaded "
          f"(sarcastic={df['sarcasm'].sum()}, "
          f"non-sarcastic={(df['sarcasm'] == 0).sum()})")
    return df


# ===================================================================
# 2. AUDIO EXTRACTION (mp4 -> 16 kHz mono numpy)
# ===================================================================
def extract_audio(video_path: str, sr: int = 16000) -> np.ndarray:
    """Extract mono audio at target sample rate from mp4 via ffmpeg."""
    cmd = [
        FFMPEG, "-i", video_path,
        "-f", "wav", "-acodec", "pcm_s16le",
        "-ar", str(sr), "-ac", "1",
        "-loglevel", "error", "-"
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed on {video_path}: {result.stderr.decode()}"
        )
    audio, _ = sf.read(io.BytesIO(result.stdout))
    return audio.astype(np.float32)


# ===================================================================
# 3. AUDIO BRANCH — wav2vec2 emotion model
# ===================================================================
# Custom model class from the HuggingFace model card:
# https://huggingface.co/audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim
class RegressionHead(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.dropout = nn.Dropout(config.final_dropout)
        self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

    def forward(self, features, **kwargs):
        x = features
        x = self.dropout(x)
        x = self.dense(x)
        x = torch.tanh(x)
        x = self.dropout(x)
        x = self.out_proj(x)
        return x


class EmotionModel(Wav2Vec2PreTrainedModel):
    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.wav2vec2 = Wav2Vec2Model(config)
        self.classifier = RegressionHead(config)
        self.init_weights()

    def forward(self, input_values):
        outputs = self.wav2vec2(input_values)
        hidden_states = outputs[0]
        hidden_states = torch.mean(hidden_states, dim=1)
        logits = self.classifier(hidden_states)
        return hidden_states, logits


MODEL_NAME = "audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim"


def load_audio_model():
    """Load wav2vec2 emotion model + processor."""
    print("[audio model] Loading wav2vec2 emotion model (first run downloads ~1.2 GB)...")
    processor = Wav2Vec2Processor.from_pretrained(MODEL_NAME)
    model = EmotionModel.from_pretrained(MODEL_NAME)
    model.eval()
    print("[audio model] Ready.")
    return processor, model


def predict_vad_audio(
    audio: np.ndarray,
    processor: Wav2Vec2Processor,
    model: EmotionModel,
) -> tuple[float, float]:
    """Return (valence, arousal) in [0,1] from audio waveform.

    Model outputs [arousal, dominance, valence] — we reorder.
    """
    inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
    with torch.no_grad():
        _, logits = model(inputs.input_values)
    # logits shape: (1, 3) -> [arousal, dominance, valence]
    vals = logits.squeeze().numpy()
    arousal, _dominance, valence = float(vals[0]), float(vals[1]), float(vals[2])
    return valence, arousal


# ===================================================================
# 4. TEXT BRANCH — NRC VAD Lexicon v2.1
# ===================================================================
def load_lexicon() -> dict[str, tuple[float, float, float]]:
    """Load NRC VAD v2.1 lexicon. Scale [-1,1] -> [0,1]."""
    lexicon = {}
    with open(LEXICON_PATH, "r", encoding="utf-8") as f:
        header = f.readline()  # skip header
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) != 4:
                continue
            word = parts[0].lower()
            v = (float(parts[1]) + 1.0) / 2.0  # [-1,1] -> [0,1]
            a = (float(parts[2]) + 1.0) / 2.0
            d = (float(parts[3]) + 1.0) / 2.0
            lexicon[word] = (v, a, d)
    print(f"[lexicon] {len(lexicon)} terms loaded.")
    return lexicon


def predict_vad_text(
    sentence: str,
    lexicon: dict[str, tuple[float, float, float]],
) -> tuple[float, float, int]:
    """Return (valence, arousal, n_matched) from text via lexicon lookup.

    Simple bag-of-words: average VAD over matched words.
    """
    import re
    words = re.findall(r"[a-z']+", sentence.lower())
    vals = [lexicon[w] for w in words if w in lexicon]
    if not vals:
        return 0.5, 0.5, 0  # neutral fallback, 0 matched
    v_avg = np.mean([v[0] for v in vals])
    a_avg = np.mean([v[1] for v in vals])
    return float(v_avg), float(a_avg), len(vals)


# ===================================================================
# 5. DIVERGENCE
# ===================================================================
def divergence(v_audio, a_audio, v_text, a_text) -> float:
    """Euclidean distance in (V, A) space."""
    return float(np.sqrt((v_audio - v_text) ** 2 + (a_audio - a_text) ** 2))


# ===================================================================
# 6. MAIN PIPELINE
# ===================================================================
def run_pipeline():
    # --- Load resources ---
    df = load_annotations()
    processor, model = load_audio_model()
    lexicon = load_lexicon()

    results = []
    n = len(df)

    for i, row in df.iterrows():
        scene = row["scene"]
        pct = (len(results) + 1) / n * 100
        sys.stdout.write(f"\r[pipeline] {len(results)+1}/{n} ({pct:.0f}%) — {scene}")
        sys.stdout.flush()

        try:
            # Audio branch
            audio = extract_audio(row["video_path"])
            v_audio, a_audio = predict_vad_audio(audio, processor, model)

            # Text branch
            v_text, a_text, n_matched = predict_vad_text(row["sentence"], lexicon)

            # Divergence
            div = divergence(v_audio, a_audio, v_text, a_text)

            results.append({
                "scene": scene,
                "sarcasm": row["sarcasm"],
                "implicit_emotion": row["implicit_emotion"],
                "explicit_emotion": row["explicit_emotion"],
                "v_audio": v_audio,
                "a_audio": a_audio,
                "v_text": v_text,
                "a_text": a_text,
                "n_matched_words": n_matched,
                "divergence": div,
            })
        except Exception as e:
            print(f"\n[WARN] Skipped {scene}: {e}")

    print(f"\n[pipeline] Done. {len(results)}/{n} succeeded.")
    return pd.DataFrame(results)


# ===================================================================
# 7. ANALYSIS & GATE DECISION
# ===================================================================
def analyze(df: pd.DataFrame):
    """Compute AUC, plot ROC, print gate decision."""

    # --- Filter out zero-match text instances ---
    df_valid = df[df["n_matched_words"] > 0].copy()
    n_dropped = len(df) - len(df_valid)
    if n_dropped > 0:
        print(f"[analysis] Dropped {n_dropped} instances with 0 lexicon matches.")
    print(f"[analysis] Analyzing {len(df_valid)} instances.")

    y_true = df_valid["sarcasm"].values
    scores = df_valid["divergence"].values

    # --- AUC ---
    auc = roc_auc_score(y_true, scores)
    fpr, tpr, thresholds = roc_curve(y_true, scores)

    # --- Bootstrap 95% CI ---
    rng = np.random.default_rng(42)
    n_boot = 2000
    aucs_boot = []
    for _ in range(n_boot):
        idx = rng.choice(len(y_true), size=len(y_true), replace=True)
        if len(np.unique(y_true[idx])) < 2:
            continue
        aucs_boot.append(roc_auc_score(y_true[idx], scores[idx]))
    ci_lo = np.percentile(aucs_boot, 2.5)
    ci_hi = np.percentile(aucs_boot, 97.5)

    # --- Gate decision ---
    if auc >= 0.70:
        gate = "GO"
    elif auc >= 0.60:
        gate = "GRAY ZONE"
    else:
        gate = "NO-GO"

    print("\n" + "=" * 60)
    print("  PGSO PHASE 0 — CALIBRATION GATE RESULT")
    print("=" * 60)
    print(f"  AUC:        {auc:.4f}")
    print(f"  95% CI:     [{ci_lo:.4f}, {ci_hi:.4f}]")
    print(f"  n:          {len(df_valid)}")
    print(f"  sarcastic:  {y_true.sum()}")
    print(f"  non-sarc:   {(y_true == 0).sum()}")
    print(f"  GATE:       >>> {gate} <<<")
    print("=" * 60)

    # --- Descriptive stats ---
    print("\n[stats] Divergence by class:")
    for label, name in [(1, "sarcastic"), (0, "non-sarcastic")]:
        vals = scores[y_true == label]
        print(f"  {name:15s}: mean={vals.mean():.4f}  std={vals.std():.4f}  "
              f"median={np.median(vals):.4f}")

    # --- Effect size (Cohen's d) ---
    s1 = scores[y_true == 1]
    s0 = scores[y_true == 0]
    pooled_std = np.sqrt((s1.std()**2 + s0.std()**2) / 2)
    cohens_d = (s1.mean() - s0.mean()) / pooled_std if pooled_std > 0 else 0
    print(f"  Cohen's d:  {cohens_d:.4f}")

    # --- Plot ---
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # ROC curve
    ax = axes[0]
    ax.plot(fpr, tpr, color="steelblue", lw=2,
            label=f"AUC = {auc:.3f} [{ci_lo:.3f}, {ci_hi:.3f}]")
    ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.5)
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("ROC — Divergence as Sarcasm Discriminator")
    ax.legend(loc="lower right")
    ax.grid(alpha=0.3)

    # Divergence distribution
    ax = axes[1]
    ax.hist(scores[y_true == 0], bins=30, alpha=0.6, label="non-sarcastic",
            color="steelblue", density=True)
    ax.hist(scores[y_true == 1], bins=30, alpha=0.6, label="sarcastic",
            color="coral", density=True)
    ax.set_xlabel("Divergence ||(V,A)_audio − (V,A)_text||")
    ax.set_ylabel("Density")
    ax.set_title("Divergence Distribution by Class")
    ax.legend()
    ax.grid(alpha=0.3)

    plt.tight_layout()
    fig_path = OUTPUT_DIR / "phase0_calibration_gate.png"
    fig.savefig(fig_path, dpi=150)
    print(f"\n[plot] Saved to {fig_path}")

    # --- Save raw results ---
    csv_path = OUTPUT_DIR / "phase0_results.csv"
    df_valid.to_csv(csv_path, index=False)
    print(f"[data] Saved to {csv_path}")

    return auc, gate


# ===================================================================
if __name__ == "__main__":
    df = run_pipeline()
    analyze(df)
