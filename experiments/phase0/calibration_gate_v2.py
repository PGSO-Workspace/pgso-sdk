"""
PGSO Phase 0 — Calibration Gate v2
====================================
Changes vs v1:
  - Text branch: GoEmotions (RoBERTa) -> emotion logits -> VAD centroids
  - Divergence: signed directional (V_audio - V_text, A_audio - A_text)
    plus Euclidean for comparison

GO/NO-GO:
  AUC >= 0.70  -> GO
  0.60 <= AUC < 0.70 -> GRAY ZONE
  AUC < 0.60  -> NO-GO
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
from transformers import (
    Wav2Vec2Processor,
    Wav2Vec2PreTrainedModel,
    Wav2Vec2Model,
    pipeline as hf_pipeline,
)
from sklearn.metrics import roc_auc_score, roc_curve
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT = Path(__file__).resolve().parent.parent.parent
DATA = PROJECT / "data"
CSV_PATH = DATA / "mustard_repo" / "mustard++_text.csv"
VIDEO_DIR = DATA / "videos" / "final_utterance_videos"
OUTPUT_DIR = Path(__file__).resolve().parent / "results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

import imageio_ffmpeg
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# ---------------------------------------------------------------------------
# GoEmotions -> VAD centroid mapping
# ---------------------------------------------------------------------------
# 28 GoEmotions labels mapped to (Valence, Arousal) centroids in [0, 1].
# Sources: Russell circumplex, Warriner et al. 2013, NRC VAD Lexicon.
EMOTION_VAD = {
    "admiration":     (0.80, 0.55),
    "amusement":      (0.85, 0.70),
    "anger":          (0.15, 0.80),
    "annoyance":      (0.25, 0.65),
    "approval":       (0.75, 0.45),
    "caring":         (0.75, 0.40),
    "confusion":      (0.40, 0.55),
    "curiosity":      (0.60, 0.55),
    "desire":         (0.70, 0.65),
    "disappointment": (0.20, 0.40),
    "disapproval":    (0.25, 0.50),
    "disgust":        (0.15, 0.60),
    "embarrassment":  (0.25, 0.55),
    "excitement":     (0.85, 0.80),
    "fear":           (0.15, 0.80),
    "gratitude":      (0.80, 0.45),
    "grief":          (0.10, 0.45),
    "joy":            (0.90, 0.75),
    "love":           (0.90, 0.55),
    "nervousness":    (0.30, 0.70),
    "optimism":       (0.80, 0.55),
    "pride":          (0.80, 0.55),
    "realization":    (0.55, 0.50),
    "relief":         (0.70, 0.30),
    "remorse":        (0.20, 0.45),
    "sadness":        (0.15, 0.30),
    "surprise":       (0.55, 0.75),
    "neutral":        (0.50, 0.35),
}


# ===================================================================
# 1. PARSE ANNOTATIONS
# ===================================================================
def load_annotations() -> pd.DataFrame:
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
    print(f"[annotations] {len(df)} instances "
          f"(sarc={df['sarcasm'].sum()}, non={(df['sarcasm']==0).sum()})")
    return df


# ===================================================================
# 2. AUDIO EXTRACTION
# ===================================================================
def extract_audio(video_path: str, sr: int = 16000) -> np.ndarray:
    cmd = [
        FFMPEG, "-i", video_path,
        "-f", "wav", "-acodec", "pcm_s16le",
        "-ar", str(sr), "-ac", "1",
        "-loglevel", "error", "-"
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()}")
    audio, _ = sf.read(io.BytesIO(result.stdout))
    return audio.astype(np.float32)


# ===================================================================
# 3. AUDIO BRANCH — wav2vec2
# ===================================================================
class RegressionHead(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.dense = nn.Linear(config.hidden_size, config.hidden_size)
        self.dropout = nn.Dropout(config.final_dropout)
        self.out_proj = nn.Linear(config.hidden_size, config.num_labels)

    def forward(self, features, **kwargs):
        x = self.dropout(features)
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
    print("[audio model] Loading wav2vec2 emotion model...")
    processor = Wav2Vec2Processor.from_pretrained(MODEL_NAME)
    model = EmotionModel.from_pretrained(MODEL_NAME)
    model.eval()
    print("[audio model] Ready.")
    return processor, model


def predict_vad_audio(audio, processor, model):
    inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
    with torch.no_grad():
        _, logits = model(inputs.input_values)
    vals = logits.squeeze().numpy()
    arousal, _dominance, valence = float(vals[0]), float(vals[1]), float(vals[2])
    return valence, arousal


# ===================================================================
# 4. TEXT BRANCH — GoEmotions -> VAD centroids
# ===================================================================
def load_text_model():
    print("[text model] Loading GoEmotions (RoBERTa)...")
    pipe = hf_pipeline(
        "text-classification",
        model="SamLowe/roberta-base-go_emotions",
        top_k=None,
        device=-1,
    )
    print("[text model] Ready.")
    return pipe


def predict_vad_text(sentence: str, pipe) -> tuple[float, float]:
    """Return (valence, arousal) as probability-weighted VAD centroid."""
    results = pipe(sentence)[0]  # list of {label, score}
    v_sum = 0.0
    a_sum = 0.0
    w_sum = 0.0
    for item in results:
        label = item["label"]
        score = item["score"]
        if label in EMOTION_VAD:
            v_c, a_c = EMOTION_VAD[label]
            v_sum += score * v_c
            a_sum += score * a_c
            w_sum += score
    if w_sum == 0:
        return 0.5, 0.5
    return v_sum / w_sum, a_sum / w_sum


# ===================================================================
# 5. DIVERGENCE METRICS
# ===================================================================
def compute_divergences(v_audio, a_audio, v_text, a_text):
    return {
        "div_euclidean": float(np.sqrt((v_audio - v_text)**2 + (a_audio - a_text)**2)),
        "div_v_signed": float(v_audio - v_text),
        "div_a_signed": float(a_audio - a_text),
        "div_v_abs": float(abs(v_audio - v_text)),
        "div_a_abs": float(abs(a_audio - a_text)),
    }


# ===================================================================
# 6. MAIN PIPELINE
# ===================================================================
def run_pipeline():
    df = load_annotations()
    processor, audio_model = load_audio_model()
    text_pipe = load_text_model()

    results = []
    n = len(df)

    for i, row in df.iterrows():
        scene = row["scene"]
        pct = (len(results) + 1) / n * 100
        sys.stdout.write(f"\r[pipeline] {len(results)+1}/{n} ({pct:.0f}%) - {scene}    ")
        sys.stdout.flush()

        try:
            audio = extract_audio(row["video_path"])
            v_audio, a_audio = predict_vad_audio(audio, processor, audio_model)
            v_text, a_text = predict_vad_text(row["sentence"], text_pipe)
            divs = compute_divergences(v_audio, a_audio, v_text, a_text)

            results.append({
                "scene": scene,
                "sarcasm": row["sarcasm"],
                "implicit_emotion": row["implicit_emotion"],
                "explicit_emotion": row["explicit_emotion"],
                "sentence": row["sentence"],
                "v_audio": v_audio,
                "a_audio": a_audio,
                "v_text": v_text,
                "a_text": a_text,
                **divs,
            })
        except Exception as e:
            print(f"\n[WARN] Skipped {scene}: {e}")

    print(f"\n[pipeline] Done. {len(results)}/{n} succeeded.")
    return pd.DataFrame(results)


# ===================================================================
# 7. ANALYSIS
# ===================================================================
def analyze(df: pd.DataFrame):
    y = df["sarcasm"].values

    # --- AUC for every metric ---
    metrics = [
        ("div_euclidean", "Euclidean ||(V,A)_a - (V,A)_t||"),
        ("div_v_signed",  "Signed V_audio - V_text"),
        ("div_a_signed",  "Signed A_audio - A_text"),
        ("div_v_abs",     "|V_audio - V_text|"),
        ("div_a_abs",     "|A_audio - A_text|"),
        ("v_audio",       "V_audio alone"),
        ("a_audio",       "A_audio alone"),
        ("v_text",        "V_text alone"),
        ("a_text",        "A_text alone"),
    ]

    print("\n" + "=" * 70)
    print("  PGSO PHASE 0 v2 - ALL DIVERGENCE METRICS")
    print("=" * 70)

    best_auc = 0
    best_name = ""
    best_col = ""

    for col, name in metrics:
        vals = df[col].values
        auc_pos = roc_auc_score(y, vals)
        auc_neg = roc_auc_score(y, -vals)
        auc = max(auc_pos, auc_neg)
        direction = "+" if auc_pos >= auc_neg else "-"

        # Bootstrap CI
        rng = np.random.default_rng(42)
        boots = []
        sign = 1.0 if auc_pos >= auc_neg else -1.0
        for _ in range(2000):
            idx = rng.choice(len(y), size=len(y), replace=True)
            if len(np.unique(y[idx])) < 2:
                continue
            boots.append(roc_auc_score(y[idx], sign * vals[idx]))
        ci_lo = np.percentile(boots, 2.5)
        ci_hi = np.percentile(boots, 97.5)

        flag = ""
        if auc >= 0.70:
            flag = " << GO"
        elif auc >= 0.60:
            flag = " ~ GRAY"

        print(f"  {name:40s}  AUC={auc:.4f} [{ci_lo:.3f},{ci_hi:.3f}] ({direction}){flag}")

        if auc > best_auc:
            best_auc = auc
            best_name = name
            best_col = col

    # --- Gate decision on best metric ---
    if best_auc >= 0.70:
        gate = "GO"
    elif best_auc >= 0.60:
        gate = "GRAY ZONE"
    else:
        gate = "NO-GO"

    print("\n" + "-" * 70)
    print(f"  Best metric: {best_name}")
    print(f"  Best AUC:    {best_auc:.4f}")
    print(f"  GATE:        >>> {gate} <<<")
    print("-" * 70)

    # --- Per-axis descriptive stats ---
    print("\n[stats] Per-axis means by class:")
    for col in ["v_audio", "a_audio", "v_text", "a_text"]:
        s1 = df.loc[y == 1, col]
        s0 = df.loc[y == 0, col]
        print(f"  {col:10s}  sarc={s1.mean():.4f}({s1.std():.3f})  "
              f"non={s0.mean():.4f}({s0.std():.3f})  D={s1.mean()-s0.mean():+.4f}")

    print("\n[stats] Divergence means by class:")
    for col in ["div_euclidean", "div_v_signed", "div_a_signed"]:
        s1 = df.loc[y == 1, col]
        s0 = df.loc[y == 0, col]
        pooled = np.sqrt((s1.std()**2 + s0.std()**2) / 2)
        d = (s1.mean() - s0.mean()) / pooled if pooled > 0 else 0
        print(f"  {col:18s}  sarc={s1.mean():.4f}({s1.std():.3f})  "
              f"non={s0.mean():.4f}({s0.std():.3f})  D={s1.mean()-s0.mean():+.4f}  d={d:.3f}")

    # --- Plots ---
    # Use best metric for ROC + distribution, plus a comparison panel
    best_sign = 1.0
    auc_pos = roc_auc_score(y, df[best_col].values)
    auc_neg = roc_auc_score(y, -df[best_col].values)
    if auc_neg > auc_pos:
        best_sign = -1.0

    scores_best = best_sign * df[best_col].values
    fpr, tpr, _ = roc_curve(y, scores_best)

    fig, axes = plt.subplots(2, 2, figsize=(14, 11))

    # ROC - best metric
    ax = axes[0, 0]
    ax.plot(fpr, tpr, color="steelblue", lw=2,
            label=f"{best_name}\nAUC = {best_auc:.3f}")
    ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.5)
    ax.set_xlabel("FPR")
    ax.set_ylabel("TPR")
    ax.set_title("ROC - Best Divergence Metric")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(alpha=0.3)

    # Distribution - best metric
    ax = axes[0, 1]
    ax.hist(df.loc[y == 0, best_col], bins=30, alpha=0.6,
            label="non-sarcastic", color="steelblue", density=True)
    ax.hist(df.loc[y == 1, best_col], bins=30, alpha=0.6,
            label="sarcastic", color="coral", density=True)
    ax.set_xlabel(best_name)
    ax.set_ylabel("Density")
    ax.set_title(f"Distribution - {best_name}")
    ax.legend()
    ax.grid(alpha=0.3)

    # V_text distribution (v1 vs v2 comparison insight)
    ax = axes[1, 0]
    ax.hist(df.loc[y == 0, "v_text"], bins=30, alpha=0.6,
            label="non-sarcastic", color="steelblue", density=True)
    ax.hist(df.loc[y == 1, "v_text"], bins=30, alpha=0.6,
            label="sarcastic", color="coral", density=True)
    v_text_auc = max(roc_auc_score(y, df["v_text"]), roc_auc_score(y, -df["v_text"]))
    ax.set_title(f"V_text GoEmotions (AUC={v_text_auc:.3f})")
    ax.set_xlabel("Valence (text)")
    ax.legend()
    ax.grid(alpha=0.3)

    # Scatter: (V,A) audio vs text colored by class
    ax = axes[1, 1]
    for label, color, name in [(0, "steelblue", "non-sarc"), (1, "coral", "sarcastic")]:
        mask = y == label
        ax.scatter(
            df.loc[mask, "v_text"], df.loc[mask, "v_audio"],
            alpha=0.4, color=color, label=name, s=25
        )
    ax.plot([0, 1], [0, 1], "k--", alpha=0.3, lw=1)
    ax.set_xlabel("V_text (GoEmotions)")
    ax.set_ylabel("V_audio (wav2vec2)")
    ax.set_title("V_audio vs V_text - divergence = distance from diagonal")
    ax.legend()
    ax.grid(alpha=0.3)

    plt.suptitle("PGSO Phase 0 v2 - GoEmotions + Directional Divergence", fontsize=13)
    plt.tight_layout()
    fig_path = OUTPUT_DIR / "phase0_calibration_gate_v2.png"
    fig.savefig(fig_path, dpi=150, bbox_inches="tight")
    print(f"\n[plot] Saved to {fig_path}")

    csv_path = OUTPUT_DIR / "phase0_results_v2.csv"
    df.to_csv(csv_path, index=False)
    print(f"[data] Saved to {csv_path}")

    return best_auc, gate


# ===================================================================
if __name__ == "__main__":
    df = run_pipeline()
    # Save raw results immediately in case analyze fails
    df.to_csv(OUTPUT_DIR / "phase0_results_v2.csv", index=False)
    print(f"[data] Raw results saved to {OUTPUT_DIR / 'phase0_results_v2.csv'}")
    analyze(df)
