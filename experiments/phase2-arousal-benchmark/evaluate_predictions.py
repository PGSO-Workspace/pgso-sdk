"""Evaluate recorded predictions without loading audio, models or providers.

This is a development evaluator, not a preregistration or a governance verdict.
CSV columns: corpus, split, scene, speaker, candidate, human_arousal, prediction,
status. status is ok, abstained, or error; missing predictions are never imputed.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform

import numpy as np

from eval_stats import spearman


def load(path):
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"corpus", "split", "scene", "speaker", "candidate",
                    "human_arousal", "prediction", "status"}
        if not required <= set(reader.fieldnames or []):
            raise ValueError("missing prediction columns")
        rows = list(reader)
    if not rows:
        raise ValueError("empty predictions")
    seen, identities, speakers, candidates = set(), {}, {}, {}
    for row in rows:
        if None in row or any(row[key] is None for key in required):
            raise ValueError("malformed prediction row")
        for key in required:
            row[key] = row[key].strip()
        if any(not row[key].strip() for key in required - {"prediction"}):
            raise ValueError("blank required field")
        if row["split"] not in {"train", "development", "test"}:
            raise ValueError("unknown split")
        key = row["corpus"], row["scene"]
        observation = (*key, row["candidate"])
        if observation in seen:
            raise ValueError("duplicate candidate/utterance prediction")
        seen.add(observation)
        human = float(row["human_arousal"])
        if not np.isfinite(human):
            raise ValueError("nonfinite human label")
        identity = row["speaker"], row["split"], human
        if key in identities and identities[key] != identity:
            raise ValueError("candidate label, speaker or split mismatch")
        identities[key] = identity
        speaker = row["corpus"], row["speaker"]
        if speaker in speakers and speakers[speaker] != row["split"]:
            raise ValueError("speaker leakage across splits")
        speakers[speaker] = row["split"]
        candidates.setdefault(row["corpus"], {}).setdefault(row["candidate"], set()).add(key)
        status = row["status"]
        if status not in {"ok", "abstained", "error"}:
            raise ValueError("unknown prediction status")
        if status == "ok":
            if not np.isfinite(float(row["prediction"])):
                raise ValueError("nonfinite prediction marked ok")
        elif row["prediction"].strip():
            raise ValueError("abstention/error must not contain an imputed prediction")
    for registry in candidates.values():
        sets = list(registry.values())
        if any(keys != sets[0] for keys in sets):
            raise ValueError("candidate omitted utterances; record abstention/error explicitly")
    return rows


def estimate(rows, n_boot, seed, other=None):
    """Resample speakers with replacement, retaining all their paired utterances.

    If any replicate is undefined, withhold the interval and report its count;
    silently conditioning the interval on finite replicates changes the estimand.
    """
    groups = {}
    for index, row in enumerate(rows):
        groups.setdefault(row["speaker"], []).append(index)
    result = {"n": len(rows), "speakers": len(groups), "estimate": None,
              "ci95": None, "bootstrap_requested": n_boot, "bootstrap_undefined": 0}
    if len(rows) < 3 or len(groups) < 2:
        result["status"] = "insufficient_data"
        return result
    prediction = np.array([float(row["prediction"]) for row in rows])
    human = np.array([float(row["human_arousal"]) for row in rows])
    baseline = None if other is None else np.array([float(row["prediction"]) for row in other])

    def statistic(indices):
        value = spearman(prediction[indices], human[indices])
        if baseline is not None:
            value -= spearman(baseline[indices], human[indices])
        return value

    value = statistic(np.arange(len(rows)))
    if not np.isfinite(value):
        result["status"] = "undefined_statistic"
        return result
    result["estimate"] = float(value)
    blocks = list(groups.values())
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(n_boot):
        indices = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        value = statistic(indices)
        if np.isfinite(value):
            values.append(value)
        else:
            result["bootstrap_undefined"] += 1
    if result["bootstrap_undefined"]:
        result["status"] = "undefined_bootstrap_replicates"
    else:
        result["ci95"] = [float(v) for v in np.percentile(values, [2.5, 97.5])]
        result["status"] = "ok"
    return result


def evaluate(rows, baseline="egemaps", n_boot=2000, seed=42):
    if n_boot < 2:
        raise ValueError("at least two bootstrap replicates required")
    reports = []
    for corpus, split in sorted({(r["corpus"], r["split"]) for r in rows}):
        subset = [r for r in rows if (r["corpus"], r["split"]) == (corpus, split)]
        registry = {cid: sorted([r for r in subset if r["candidate"] == cid], key=lambda r: r["scene"])
                    for cid in sorted({r["candidate"] for r in subset})}
        if baseline not in registry:
            raise ValueError(f"missing baseline in {corpus}/{split}")
        summary = {"corpus": corpus, "split": split, "candidates": {}, "paired_vs_baseline": {}}
        for cid, observations in registry.items():
            valid = [r for r in observations if r["status"] == "ok"]
            summary["candidates"][cid] = {
                "total": len(observations), "coverage": len(valid) / len(observations),
                "abstained": sum(r["status"] == "abstained" for r in observations),
                "errors": sum(r["status"] == "error" for r in observations),
                "spearman": estimate(valid, n_boot, seed),
            }
            if cid == baseline:
                continue
            pairs = [(a, b) for a, b in zip(observations, registry[baseline])
                     if a["status"] == b["status"] == "ok"]
            a, b = ([p[0] for p in pairs], [p[1] for p in pairs])
            summary["paired_vs_baseline"][cid] = {
                "scope": "common non-abstaining utterances only; not overall utility",
                "scenes": [r["scene"] for r in a],
                "delta_spearman": estimate(a, n_boot, seed, b),
            }
        reports.append(summary)
    return {"schema": "pgso.signal-evaluation.v1", "status": "development",
            "scope": "utterance-level signal association; no governance or streaming claim",
            "resampling_unit": "speaker_within_corpus", "seed": seed,
            "baseline": baseline, "reports": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", default="egemaps")
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    result = evaluate(load(args.predictions), args.baseline, args.bootstrap, args.seed)
    result["input_sha256"] = hashlib.sha256(args.predictions.read_bytes()).hexdigest()
    result["evaluator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["statistics_sha256"] = hashlib.sha256(Path(__file__).with_name("eval_stats.py").read_bytes()).hexdigest()
    result["environment"] = {"python": platform.python_version(), "numpy": np.__version__}
    # Exclusive creation protects earlier results from accidental replacement.
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")


if __name__ == "__main__":
    main()
