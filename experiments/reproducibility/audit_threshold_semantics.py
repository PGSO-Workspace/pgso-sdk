#!/usr/bin/env python3
"""Characterize historical T comparator without importing or running the pilot.

This deliberately checks what the pinned implementation does, not what an ideal
baseline should do. A changed behavior requires a new label and review; never
rewrite archived results. No model, network, audio corpus or payment is used.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import unittest


def load_function(path: Path):
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(path))
    functions = [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "_instantaneous_blocks"]
    constants = [n for n in module.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "MIN_CONFIDENCE" for t in n.targets)]
    if len(functions) != 1 or len(constants) != 1:
        raise ValueError("expected exactly one comparator and confidence constant")
    confidence = ast.literal_eval(constants[0].value)
    if confidence != 0.5:
        raise ValueError("confidence configuration changed: review fixture")
    scope = {"MIN_CONFIDENCE": confidence}
    isolated = ast.Module(body=functions, type_ignores=[])
    exec(compile(ast.fix_missing_locations(isolated), str(path), "exec"), scope)
    return scope["_instantaneous_blocks"], hashlib.sha256(source.encode()).hexdigest()


def cases(function):
    def reading(value, confidence=0.9):
        return dict(axis="arousal", value=value, confidence=confidence)
    return {
        "high_then_nominal": function([reading(0.95), reading(0.5)]),
        "missing_confidence": function([dict(axis="arousal", value=0.95)]),
        "nonfinite_value": function([reading(float("inf"))]),
        "nominal_only": function([reading(0.5)]),
        "low_confidence_only": function([reading(0.95, 0.1)]),
        "empty": function([]),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2] / "experiments/public_voice/pilot.py")
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite evidence")
    function, digest = load_function(args.source)
    observations = cases(function)
    expected = dict(high_then_nominal=True, missing_confidence=True, nonfinite_value=True, nominal_only=False, low_confidence_only=False, empty=False)
    suite = unittest.TestSuite()
    for name, value in expected.items():
        def check(n=name, v=value):
            if observations[n] is not v:
                raise AssertionError(f"historical behavior changed for {n}; review the baseline definition")
        suite.addTest(unittest.FunctionTestCase(check, description=name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=args.source.parent, capture_output=True, text=True, check=False)
    payload = {
        "schema": "pgso.audit.threshold-characterization.v1",
        "status": "passed" if result.wasSuccessful() else "failed",
        "source_sha256": digest,
        "checkout_commit": git.stdout.strip() if git.returncode == 0 else None,
        "source_filename": args.source.name,
        "checks": result.testsRun,
        "observations": observations,
        "scope": "Characterization of the historical any-window comparator. Not a task-utility experiment or SDK-safety proof.",
        "remedy": "Preserve T_any as historical. Specify a separately named current/last-accepted-window comparator with input validation, precision and expiry semantics before new comparisons."
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
