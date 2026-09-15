"""Guard against vacuous case sets and Git normalization of recorded evidence."""
import hashlib
import json
from pathlib import Path
import unittest

from run import EXECUTION_CASES, named_cases


class PackageIntegrity(unittest.TestCase):
    def test_required_cases_cannot_pass_when_empty_or_duplicated(self):
        with self.assertRaises(ValueError):
            named_cases([], EXECUTION_CASES)
        cases = [{"case": name, "pass": True} for name in sorted(EXECUTION_CASES)]
        cases[-1] = cases[0]
        with self.assertRaises(ValueError):
            named_cases(cases, EXECUTION_CASES)

    def test_committed_reference_bytes_match_manifest(self):
        reference = Path(__file__).parent / "reference-results/54c1c7d"
        manifest = json.loads((reference / "manifest.json").read_text())
        self.assertEqual(manifest["git_status"], "")
        self.assertEqual(json.loads((reference / "results.json").read_text())["status"], "passed")
        outputs = manifest["output_sha256"]
        self.assertEqual(set(outputs), {p.name for p in reference.iterdir()} - {"manifest.json"})
        for name, expected in outputs.items():
            self.assertEqual(hashlib.sha256((reference / name).read_bytes()).hexdigest(), expected, name)


if __name__ == "__main__":
    unittest.main()
