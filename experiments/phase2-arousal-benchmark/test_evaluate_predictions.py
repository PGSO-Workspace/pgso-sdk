"""Synthetic software checks; no dataset inference or provider calls."""
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import evaluate_predictions as E


def fixture():
    return [dict(corpus="synthetic", split="test", scene=f"{speaker}-{label}",
                 speaker=speaker, candidate=candidate, human_arousal=str(label),
                 prediction=str(label if candidate != "reversed" else -label), status="ok")
            for speaker in ("a", "b", "c") for label in (1, 2, 3)
            for candidate in ("egemaps", "identical", "reversed")]


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "predictions.csv"

    def write(self, rows):
        with self.path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(fixture()[0]))
            writer.writeheader()
            writer.writerows(rows)
        return self.path

    def test_paired_direction_and_identical_predictions(self):
        rows = E.load(self.write(fixture()))
        result = E.evaluate(rows, n_boot=30)["reports"][0]
        self.assertEqual(result["candidates"]["egemaps"]["spearman"]["speakers"], 3)
        for candidate, expected in (("identical", 0), ("reversed", -2)):
            delta = result["paired_vs_baseline"][candidate]["delta_spearman"]
            self.assertEqual(delta["estimate"], expected)
            self.assertEqual(delta["ci95"], [expected, expected])
        self.assertEqual(E.evaluate(rows, n_boot=30), E.evaluate(list(reversed(rows)), n_boot=30))

    def test_missing_predictions_preserve_denominator_and_pairing(self):
        rows = fixture()
        rows[0].update(status="abstained", prediction="")
        rows[4].update(status="error", prediction="")
        report = E.evaluate(E.load(self.write(rows)), n_boot=30)["reports"][0]
        self.assertEqual(report["candidates"]["egemaps"]["total"], 9)
        self.assertEqual(report["candidates"]["egemaps"]["abstained"], 1)
        self.assertEqual(report["candidates"]["identical"]["errors"], 1)
        self.assertEqual(report["paired_vs_baseline"]["identical"]["delta_spearman"]["n"], 7)

    def test_rejects_silent_data_loss_and_label_mismatch(self):
        cases = [fixture()[:-1], fixture() + [fixture()[0]]]
        for field, value in (("human_arousal", "9"), ("speaker", "other"),
                             ("prediction", "NaN"), ("status", "abstained")):
            rows = fixture()
            rows[1][field] = value
            cases.append(rows)
        for rows in cases:
            with self.subTest(rows=rows[:2]), self.assertRaises(ValueError):
                E.load(self.write(rows))

    def test_rejects_speaker_leakage(self):
        rows = fixture()
        for row in rows:
            if row["scene"] == "a-1":
                row["split"] = "development"
        with self.assertRaisesRegex(ValueError, "speaker leakage"):
            E.load(self.write(rows))

    def test_corpora_are_reported_separately(self):
        rows = fixture()
        other = [dict(row, corpus="other") for row in fixture()]
        for row in other:
            row["human_arousal"] = str(-float(row["human_arousal"]))
        result = E.evaluate(E.load(self.write(rows + other)), n_boot=10)
        self.assertEqual(len(result["reports"]), 2)
        scores = {r["corpus"]: r["candidates"]["egemaps"]["spearman"]["estimate"]
                  for r in result["reports"]}
        self.assertEqual(scores, {"synthetic": 1, "other": -1})

    def test_malformed_rows_and_missing_baseline_fail(self):
        self.path.write_text("corpus,split\nexample,test\n")
        with self.assertRaises(ValueError):
            E.load(self.path)
        self.write(fixture())
        with self.path.open("a") as handle:
            handle.write("short,row\n")
        with self.assertRaises(ValueError):
            E.load(self.path)
        with self.assertRaisesRegex(ValueError, "missing baseline"):
            E.evaluate(E.load(self.write(fixture())), baseline="absent", n_boot=10)

    def test_single_speaker_is_not_treated_as_independent_utterances(self):
        rows = [r for r in fixture() if r["speaker"] == "a"]
        result = E.evaluate(E.load(self.write(rows)), n_boot=10)
        stat = result["reports"][0]["candidates"]["egemaps"]["spearman"]
        self.assertEqual(stat["status"], "insufficient_data")
        self.assertIsNone(stat["ci95"])

    def test_constant_labels_do_not_produce_a_successful_verdict(self):
        rows = fixture()
        for row in rows:
            row["human_arousal"] = "1"
        result = E.evaluate(E.load(self.write(rows)), n_boot=10)
        stat = result["reports"][0]["candidates"]["egemaps"]["spearman"]
        self.assertEqual(stat["status"], "undefined_statistic")
        self.assertIsNone(stat["estimate"])
        json.dumps(result, allow_nan=False)

    def test_undefined_cluster_resamples_are_disclosed(self):
        rows = [dict(speaker=speaker, prediction=str(value), human_arousal=str(value))
                for speaker, value in (("a", 1), ("a", 1), ("b", 2), ("b", 2))]
        result = E.estimate(rows, n_boot=30, seed=42)
        self.assertEqual(result["estimate"], 1)
        self.assertGreater(result["bootstrap_undefined"], 0)
        self.assertIsNone(result["ci95"])

    def test_cli_hashes_input_and_does_not_overwrite_results(self):
        self.write(fixture())
        output = self.path.with_suffix(".json")
        cmd = [sys.executable, str(Path(E.__file__)), str(self.path), "--output", str(output),
               "--bootstrap", "10"]
        subprocess.run(cmd, check=True, capture_output=True)
        original = output.read_bytes()
        result = json.loads(original)
        self.assertEqual(len(result["input_sha256"]), 64)
        self.assertEqual(result["status"], "development")
        self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
        self.assertEqual(original, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
