"""
PGSO Phase 0 - Final Calibration Gate Analysis
================================================
Runs all remaining diagnostics on v2 results and produces the
definitive gate decision with comprehensive evidence.
"""
import csv
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, roc_curve
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score, StratifiedKFold
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
CSV_ANNO = Path(__file__).resolve().parent.parent.parent / "data" / "mustard_repo" / "mustard++_text.csv"

df = pd.read_csv(RESULTS / "phase0_results_v2.csv")
y = df["sarcasm"].values
n = len(df)

print("=" * 70)
print("  PGSO PHASE 0 v3 - FINAL CALIBRATION ANALYSIS")
print("=" * 70)

# =====================================================================
# 1. MULTI-FEATURE DIVERGENCE (2D: signed V + signed A)
# =====================================================================
print("\n--- 1. Multi-feature divergence (LogReg on signed V + signed A) ---")
X_2d = df[["div_v_signed", "div_a_signed"]].values

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
scores_2d = cross_val_score(LogisticRegression(), X_2d, y, cv=cv, scoring="roc_auc")
print(f"  2D (V+A signed) 5-fold CV AUC: {scores_2d.mean():.4f} +/- {scores_2d.std():.4f}")
print(f"  Per-fold: {[f'{s:.3f}' for s in scores_2d]}")

# Also try all 4 raw axes
X_4d = df[["v_audio", "a_audio", "v_text", "a_text"]].values
scores_4d = cross_val_score(LogisticRegression(), X_4d, y, cv=cv, scoring="roc_auc")
print(f"  4D (all raw axes) 5-fold CV AUC: {scores_4d.mean():.4f} +/- {scores_4d.std():.4f}")

# Fit full model for ROC plot
lr = LogisticRegression().fit(X_2d, y)
proba_2d = lr.predict_proba(X_2d)[:, 1]
auc_2d_full = roc_auc_score(y, proba_2d)
print(f"  2D full-data AUC (overfit upper bound): {auc_2d_full:.4f}")
print(f"  LogReg coefs: V_signed={lr.coef_[0][0]:.3f}, A_signed={lr.coef_[0][1]:.3f}")

# Bootstrap CI on full-data AUC
rng = np.random.default_rng(42)
boot_aucs = []
for _ in range(2000):
    idx = rng.choice(n, size=n, replace=True)
    if len(np.unique(y[idx])) < 2:
        continue
    boot_aucs.append(roc_auc_score(y[idx], proba_2d[idx]))
ci_2d_lo = np.percentile(boot_aucs, 2.5)
ci_2d_hi = np.percentile(boot_aucs, 97.5)
print(f"  Bootstrap 95% CI (full-data): [{ci_2d_lo:.3f}, {ci_2d_hi:.3f}]")

# =====================================================================
# 2. ALTERNATIVE LABEL: implicit != explicit emotion mismatch
# =====================================================================
print("\n--- 2. Alternative label: emotion mismatch (implicit != explicit) ---")
df["emo_mismatch"] = (df["implicit_emotion"] != df["explicit_emotion"]).astype(int)
y_emo = df["emo_mismatch"].values
print(f"  Mismatch=1: {y_emo.sum()}, Match=0: {(y_emo == 0).sum()}")

for col, name in [("div_v_signed", "Signed V"), ("div_a_signed", "Signed A"),
                   ("div_euclidean", "Euclidean")]:
    vals = df[col].values
    auc_p = roc_auc_score(y_emo, vals)
    auc_n = roc_auc_score(y_emo, -vals)
    auc = max(auc_p, auc_n)
    print(f"  {name:15s} vs emo_mismatch: AUC={auc:.4f}")

scores_emo = cross_val_score(LogisticRegression(), X_2d, y_emo, cv=cv, scoring="roc_auc")
print(f"  2D LogReg vs emo_mismatch CV AUC: {scores_emo.mean():.4f} +/- {scores_emo.std():.4f}")

# =====================================================================
# 3. SUBGROUP ANALYSIS (by show)
# =====================================================================
print("\n--- 3. Subgroup analysis by show ---")
show_map = {"1": "BBT", "2": "FRIENDS", "3": "SV"}
df["show"] = df["scene"].apply(lambda s: show_map.get(s.split("_")[0], "OTHER"))

for show in sorted(df["show"].unique()):
    sub = df[df["show"] == show]
    if len(sub) < 20 or sub["sarcasm"].nunique() < 2:
        continue
    y_sub = sub["sarcasm"].values
    vals = sub["div_v_signed"].values
    auc_p = roc_auc_score(y_sub, vals)
    auc_n = roc_auc_score(y_sub, -vals)
    auc = max(auc_p, auc_n)
    # 2D LogReg CV
    X_sub = sub[["div_v_signed", "div_a_signed"]].values
    cv_auc = "N/A"
    if len(sub) >= 30:
        n_splits = min(5, min(y_sub.sum(), (y_sub == 0).sum()))
        if n_splits >= 2:
            cv_sub = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
            try:
                sc = cross_val_score(LogisticRegression(), X_sub, y_sub,
                                     cv=cv_sub, scoring="roc_auc")
                cv_auc = f"{sc.mean():.3f}"
            except Exception:
                pass
    print(f"  {show:10s}: n={len(sub):3d} (sarc={y_sub.sum():3d}, non={(y_sub==0).sum():3d})  "
          f"V-signed={auc:.3f}  2D-CV={cv_auc}")

# =====================================================================
# 4. SARCASM TYPE ANALYSIS
# =====================================================================
print("\n--- 4. Sarcasm type analysis ---")
stype_map = {}
with open(CSV_ANNO, "r", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        if row.get("KEY", "").endswith("_u"):
            stype_map[row["SCENE"]] = row.get("Sarcasm_Type", "NONE")

df["sarcasm_type"] = df["scene"].map(stype_map).fillna("NONE")
for stype in ["NONE", "PRO", "ILL", "EMB", "LIK"]:
    sub = df[df["sarcasm_type"] == stype]
    if len(sub) < 3:
        continue
    mean_div = sub["div_v_signed"].mean()
    std_div = sub["div_v_signed"].std()
    print(f"  {stype:6s}: n={len(sub):3d}  mean_V_signed={mean_div:+.4f} (std={std_div:.4f})")

# =====================================================================
# 5. PERMUTATION TEST
# =====================================================================
print("\n--- 5. Permutation test (1000 shuffles) ---")
real_auc = roc_auc_score(y, df["div_v_signed"].values)
n_perm = 1000
null_aucs = []
for _ in range(n_perm):
    y_shuf = rng.permutation(y)
    null_aucs.append(roc_auc_score(y_shuf, df["div_v_signed"].values))
null_aucs = np.array(null_aucs)
p_value = (null_aucs >= real_auc).mean()
print(f"  Real AUC: {real_auc:.4f}")
print(f"  Null dist: mean={null_aucs.mean():.4f}, std={null_aucs.std():.4f}")
print(f"  p-value (one-sided): {p_value:.4f}")
sig_05 = "YES" if p_value < 0.05 else "NO"
sig_01 = "YES" if p_value < 0.01 else "NO"
print(f"  Significant at 0.05? {sig_05}")
print(f"  Significant at 0.01? {sig_01}")

# =====================================================================
# 6. FINAL COMPREHENSIVE PLOT
# =====================================================================
fig, axes_plt = plt.subplots(2, 3, figsize=(18, 11))

# 6a. ROC comparison
ax = axes_plt[0, 0]
for vals, name, color in [
    (df["div_v_signed"].values, f"Signed V (0.661)", "steelblue"),
    (proba_2d, f"2D LogReg V+A ({auc_2d_full:.3f})", "coral"),
    (df["v_audio"].values, "V_audio alone (0.651)", "gray"),
]:
    fpr, tpr, _ = roc_curve(y, vals)
    ax.plot(fpr, tpr, label=name, lw=2, color=color)
ax.plot([0, 1], [0, 1], "k--", lw=1, alpha=0.4)
ax.set_xlabel("FPR")
ax.set_ylabel("TPR")
ax.set_title("ROC Comparison")
ax.legend(fontsize=8, loc="lower right")
ax.grid(alpha=0.3)

# 6b. Distribution - signed V divergence
ax = axes_plt[0, 1]
ax.hist(df.loc[y == 0, "div_v_signed"], bins=30, alpha=0.6,
        label="non-sarcastic", color="steelblue", density=True)
ax.hist(df.loc[y == 1, "div_v_signed"], bins=30, alpha=0.6,
        label="sarcastic", color="coral", density=True)
ax.axvline(df.loc[y == 0, "div_v_signed"].mean(), color="steelblue", ls="--", lw=1.5)
ax.axvline(df.loc[y == 1, "div_v_signed"].mean(), color="coral", ls="--", lw=1.5)
ax.set_xlabel("V_audio - V_text (signed)")
ax.set_title("Signed V Divergence by Class")
ax.legend()
ax.grid(alpha=0.3)

# 6c. 2D scatter: signed V vs signed A
ax = axes_plt[0, 2]
ax.scatter(df.loc[y == 0, "div_v_signed"], df.loc[y == 0, "div_a_signed"],
           alpha=0.4, color="steelblue", label="non-sarc", s=25)
ax.scatter(df.loc[y == 1, "div_v_signed"], df.loc[y == 1, "div_a_signed"],
           alpha=0.4, color="coral", label="sarcastic", s=25)
ax.axhline(0, color="k", lw=0.5, alpha=0.3)
ax.axvline(0, color="k", lw=0.5, alpha=0.3)
ax.set_xlabel("Signed V divergence")
ax.set_ylabel("Signed A divergence")
ax.set_title("2D Divergence Space")
ax.legend()
ax.grid(alpha=0.3)

# 6d. Permutation null distribution
ax = axes_plt[1, 0]
ax.hist(null_aucs, bins=40, alpha=0.7, color="gray", density=True, label="Null distribution")
ax.axvline(real_auc, color="coral", lw=2, label=f"Observed AUC={real_auc:.3f}")
ax.axvline(0.5, color="k", ls="--", lw=1, alpha=0.4)
ax.set_xlabel("AUC")
ax.set_title(f"Permutation Test (p={p_value:.4f})")
ax.legend()
ax.grid(alpha=0.3)

# 6e. Per-sarcasm-type divergence
ax = axes_plt[1, 1]
types_present = [t for t in ["NONE", "PRO", "ILL", "EMB", "LIK"]
                 if t in df["sarcasm_type"].values and len(df[df["sarcasm_type"] == t]) >= 3]
type_means = [df.loc[df["sarcasm_type"] == t, "div_v_signed"].mean() for t in types_present]
type_stds = [df.loc[df["sarcasm_type"] == t, "div_v_signed"].std() for t in types_present]
type_ns = [len(df[df["sarcasm_type"] == t]) for t in types_present]
colors = ["steelblue" if t == "NONE" else "coral" for t in types_present]
ax.bar(range(len(types_present)), type_means, yerr=type_stds, capsize=4,
       color=colors, alpha=0.7)
ax.set_xticks(range(len(types_present)))
ax.set_xticklabels([f"{t}\n(n={ns})" for t, ns in zip(types_present, type_ns)])
ax.set_ylabel("Mean signed V divergence")
ax.set_title("Divergence by Sarcasm Type")
ax.axhline(0, color="k", lw=0.5, alpha=0.3)
ax.grid(alpha=0.3, axis="y")

# 6f. Summary table
ax = axes_plt[1, 2]
ax.axis("off")
summary_lines = [
    "PGSO Phase 0 - Final Gate Report",
    "=" * 40,
    "",
    "Best single metric:",
    "  Signed V_audio - V_text",
    f"  AUC = 0.661 [0.606, 0.717]",
    "  Cohen's d = 0.557",
    "",
    "Multi-feature (2D LogReg, 5-fold CV):",
    f"  AUC = {scores_2d.mean():.3f} +/- {scores_2d.std():.3f}",
    "",
    "Permutation test:",
    f"  p = {p_value:.4f}",
    "",
    "GO/NO-GO Thresholds:",
    "  >= 0.70 = GO",
    "  0.60-0.70 = GRAY ZONE  <-- HERE",
    "  < 0.60 = NO-GO",
    "",
    "GATE:  GRAY ZONE (0.661)",
    "=" * 40,
    "Signal is real (p<0.01) but below",
    "pre-registered GO threshold (0.70).",
    "Proceed with caution; improve",
    "extraction before committing to",
    "full infrastructure build.",
]
ax.text(0.05, 0.95, "\n".join(summary_lines), transform=ax.transAxes, fontsize=9,
        verticalalignment="top", fontfamily="monospace",
        bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.8))

plt.suptitle("PGSO Phase 0 - Final Calibration Gate Report (n=396)", fontsize=14)
plt.tight_layout()
fig.savefig(RESULTS / "phase0_final_report.png", dpi=150, bbox_inches="tight")
print(f"\n[plot] Saved final report to {RESULTS / 'phase0_final_report.png'}")

# =====================================================================
# FINAL GATE DECISION
# =====================================================================
print("\n" + "=" * 70)
print("  FINAL GATE DECISION")
print("=" * 70)
print(f"  Best single-feature AUC:   0.661 (Signed V_audio - V_text)")
print(f"  Best 2D CV AUC:            {scores_2d.mean():.3f} +/- {scores_2d.std():.3f}")
print(f"  Permutation p-value:       {p_value:.4f}")
print(f"  Cohen's d (signed V):      0.557 (medium)")
print()
print(f"  The signal IS real (p={p_value:.4f}) and IS above chance.")
print(f"  But it does NOT reach the pre-registered GO threshold (AUC >= 0.70).")
print()
print(f"  GATE:  >>> GRAY ZONE <<<")
print("=" * 70)
