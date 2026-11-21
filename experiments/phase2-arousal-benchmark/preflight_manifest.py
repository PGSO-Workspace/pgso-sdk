"""Structural preflight for candidate E0 speech-corpus manifests.
Does not infer licensing, consent, acoustic validity, or research approval.
"""
import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

FIELDS = {"corpus", "split", "scene", "speaker", "wav_path", "audio_sha256", "human_arousal", "license_ref", "permission_status"}
SPLITS = {"train", "development", "test"}
PERMISSIONS = {"documented", "pending", "unknown"}
SHA_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


class ManifestError(ValueError):
    """Structural E0 manifest error."""


def preflight(path: Path, *, verify_audio: bool = False) -> dict:
    """Check manifest integrity; never mark research ready from these checks alone."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not FIELDS.issubset(set(reader.fieldnames or [])):
            raise ManifestError("missing columns: " + ", ".join(sorted(FIELDS - set(reader.fieldnames or []))))
        rows = list(reader)
    if not rows:
        raise ManifestError("empty manifest")
    identities, splits, audio_splits = set(), {}, {}
    counts, speakers, permissions = Counter(), defaultdict(set), Counter()
    for line, record in enumerate(rows, start=2):
        if None in record:
            raise ManifestError(f"line {line}: malformed CSV")
        row = {k: (record.get(k) or "").strip() for k in FIELDS}
        if any(not value for value in row.values()):
            raise ManifestError(f"line {line}: blank required field")
        if row["split"] not in SPLITS or row["permission_status"] not in PERMISSIONS:
            raise ManifestError(f"line {line}: invalid split or permission status")
        if not SHA_RE.fullmatch(row["audio_sha256"]):
            raise ManifestError(f"line {line}: invalid audio SHA256")
        try:
            label = float(row["human_arousal"])
        except ValueError as exc:
            raise ManifestError(f"line {line}: invalid arousal label") from exc
        if not math.isfinite(label):
            raise ManifestError(f"line {line}: nonfinite arousal label")
        identity = row["corpus"], row["scene"]
        if identity in identities:
            raise ManifestError(f"line {line}: duplicate corpus/scene")
        identities.add(identity)
        spk = row["corpus"], row["speaker"]
        if spk in splits and splits[spk] != row["split"]:
            raise ManifestError(f"line {line}: speaker leakage across splits")
        splits[spk] = row["split"]
        digest = row["audio_sha256"].lower()
        if digest in audio_splits and audio_splits[digest] != row["split"]:
            raise ManifestError(f"line {line}: duplicate audio across splits")
        audio_splits[digest] = row["split"]
        if verify_audio:
            audio = Path(row["wav_path"])
            if not audio.is_absolute():
                audio = path.parent / audio
            if not audio.is_file():
                raise ManifestError(f"line {line}: missing audio")
            if hashlib.sha256(audio.read_bytes()).hexdigest() != digest:
                raise ManifestError(f"line {line}: audio hash mismatch")
        counts[(row["corpus"], row["split"])] += 1
        speakers[row["corpus"]].add(row["speaker"])
        permissions[row["permission_status"]] += 1
    return {
        "schema": "pgso.e0-manifest-preflight.v1",
        "status": "hashes_verified" if verify_audio else "metadata_only",
        "research_ready": False,
        "research_ready_reason": "Permission, annotation validity, model overlap and approved protocol require independent checks",
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "n_observations": len(rows),
        "by_corpus_split": [{"corpus": c, "split": s, "observations": n} for (c, s), n in sorted(counts.items())],
        "speakers_per_corpus": {c: len(v) for c, v in sorted(speakers.items())},
        "permission_statuses_self_reported": dict(sorted(permissions.items())),
        "audio_hashes_verified": verify_audio,
        "limitations": ["No audio decoding", "License claims not checked", "Training overlap not checked", "No experiment executed"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-audio", action="store_true")
    args = parser.parse_args()
    report = preflight(args.manifest, verify_audio=args.verify_audio)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


if __name__ == "__main__":
    main()
