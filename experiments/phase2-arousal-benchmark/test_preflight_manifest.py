"""Tests use synthetic bytes only; no corpus, provider or model calls."""
import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from preflight_manifest import FIELDS, ManifestError, preflight


class E0PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a, self.b = self.root / "a.wav", self.root / "b.wav"
        self.a.write_bytes(b"synthetic-a")
        self.b.write_bytes(b"synthetic-b")
        self.rows = [self.record("a", "alice", "development", self.a),
                     self.record("b", "bob", "test", self.b)]

    @staticmethod
    def record(scene, speaker, split, audio):
        return {
            "corpus": "synthetic", "split": split, "scene": scene, "speaker": speaker,
            "wav_path": str(audio), "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            "human_arousal": "0.7", "license_ref": "synthetic-test-only",
            "permission_status": "pending",
        }

    def save(self):
        path = self.root / "manifest.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted(FIELDS))
            writer.writeheader()
            writer.writerows(self.rows)
        return path

    def test_metadata_does_not_certify_research(self):
        report = preflight(self.save())
        self.assertEqual(report["n_observations"], 2)
        self.assertEqual(report["status"], "metadata_only")
        self.assertFalse(report["research_ready"])

    def test_optional_hash_validation(self):
        report = preflight(self.save(), verify_audio=True)
        self.assertEqual(report["status"], "hashes_verified")
        self.assertFalse(report["research_ready"])

    def test_duplicate_identifier(self):
        self.rows[1]["scene"] = "a"
        with self.assertRaisesRegex(ManifestError, "duplicate"):
            preflight(self.save())

    def test_speaker_split_leak(self):
        self.rows[1]["speaker"] = "alice"
        with self.assertRaisesRegex(ManifestError, "speaker leakage"):
            preflight(self.save())

    def test_duplicate_audio_split_leak(self):
        self.rows[1]["audio_sha256"] = self.rows[0]["audio_sha256"]
        with self.assertRaisesRegex(ManifestError, "duplicate audio"):
            preflight(self.save())

    def test_bad_audio_hash(self):
        self.rows[0]["audio_sha256"] = "0" * 64
        with self.assertRaisesRegex(ManifestError, "hash mismatch"):
            preflight(self.save(), verify_audio=True)

    def test_invalid_label(self):
        self.rows[0]["human_arousal"] = "nan"
        with self.assertRaisesRegex(ManifestError, "nonfinite"):
            preflight(self.save())

    def test_missing_audio(self):
        self.a.unlink()
        with self.assertRaisesRegex(ManifestError, "missing audio"):
            preflight(self.save(), verify_audio=True)


if __name__ == "__main__":
    unittest.main()
