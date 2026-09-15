#!/usr/bin/env python3
"""Build and evaluate the checked-out SDK; Python standard library only."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from http_transport_check import evaluate as evaluate_http

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
EXECUTION_CASES = {
    "allowed_call", "pruned_quote", "protected_human_step_up_missing",
    "granted_token", "token_replay", "wrong_arguments",
    "wrong_arguments_consumes_token", "policy_transition_sequence_revokes_token",
    "expired_token", "session_isolation", "session_isolation_preserves_token",
    "callback_failure", "callback_failure_consumes_token",
}
HTTP_CASES = {
    "no_bearer", "wrong_bearer", "browser_origin", "authorized_tools", "context",
    "call_valid", "call_invalid_arguments", "call_wrong_session", "call_unknown_tool",
    "mcp_initialize", "mcp_tools_list", "mcp_tools_call", "mcp_invalid_tool_arguments",
    "mcp_unknown_method",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(args, timeout=60):
    return subprocess.check_output(args, cwd=ROOT, text=True, timeout=timeout)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = {ROOT / "Cargo.toml", ROOT / "Cargo.lock"}
    paths.update((ROOT / "crates").rglob("*.rs"))
    paths.update((ROOT / "crates").rglob("Cargo.toml"))
    paths.update(HERE.glob("*.py"))
    paths.add(HERE / "governance-scenarios.json")
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}


def save(out, name, value):
    (out / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def named_cases(cases, expected):
    require(len(cases) == len(expected), "Missing or duplicated cases")
    require({c["case"] for c in cases} == expected, "Unexpected case identifiers")
    require(all(c["pass"] is True for c in cases), "A required case failed")


def governance(binary, out):
    spec = json.loads((HERE / "governance-scenarios.json").read_text())
    rows = []
    for scenario in spec["scenarios"]:
        segments = list(scenario["segments"])
        if "ramp" in scenario:
            ramp = scenario["ramp"]
            segments += [[1, ramp["from"] + (ramp["to"] - ramp["from"]) * i /
                          (ramp["count"] - 1), 0.9, None, 400]
                         for i in range(ramp["count"])]
        segments += scenario.get("tail", [])
        timestamp, step = 0, 0
        for count, value, confidence, expected, cadence in segments:
            require(count > 0 and expected in (0, 1, None), "Invalid scenario segment")
            for _ in range(count):
                timestamp += cadence
                rows.append([scenario["name"], step, timestamp, value, confidence, expected])
                step += 1
    require(rows and any(r[5] is not None for r in rows), "No labeled contract observations")
    inputs = out / "inputs.csv"
    with inputs.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["scenario", "step", "timestamp_ms", "value", "confidence", "expected"])
        writer.writerows(rows)
    trace_text = command([str(binary), str(inputs)])
    require(trace_text == command([str(binary), str(inputs)]), "Fresh-process traces differ")
    (out / "trace.csv").write_text(trace_text)
    trace = list(csv.DictReader(io.StringIO(trace_text)))
    require(len(trace) == len(rows), "Trace length mismatch")
    seen, summary = set(), {}
    for row, actual in zip(rows, trace):
        name, step, _, _, _, expected = row
        key = (name, step)
        require(key not in seen, "Duplicate input identity")
        seen.add(key)
        require((actual["scenario"], int(actual["step"])) == key, "Trace identity mismatch")
        governed = int(actual["governed"])
        require(governed in (0, 1), "Nonbinary governance state")
        require(int(actual["protected_present"]) == 1, "Protected tool absent")
        require(int(actual["directive_count"]) == governed, "Fixture directive state mismatch")
        entry = summary.setdefault(name, dict(steps=0, labeled_steps=0, mismatches=0,
                                              restricted_steps=0, transitions=0, previous=0))
        entry["steps"] += 1
        entry["restricted_steps"] += governed
        entry["transitions"] += governed != entry["previous"]
        entry["previous"] = governed
        require(int(actual["transitions"]) == entry["transitions"], "Audit transition mismatch")
        if expected is not None:
            entry["labeled_steps"] += 1
            entry["mismatches"] += governed != expected
    for entry in summary.values():
        del entry["previous"]
    save(out, "governance.json", summary)
    require(all(s["mismatches"] == 0 for s in summary.values()), "Contract mismatch")
    return dict(steps=len(rows), labeled_steps=sum(s["labeled_steps"] for s in summary.values()),
                scenarios=len(summary), mismatches=0, fresh_process_replays=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New directory; existing directories are refused")
    parser.add_argument("--profile", choices=("dev", "release"), default="release")
    parser.add_argument("--offline", action="store_true", help="Use already cached Cargo dependencies")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = (args.output or ROOT / "target/reproducibility" / stamp).resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = dict(git_commit=command(["git", "rev-parse", "HEAD"]).strip(),
                    git_status=command(["git", "status", "--porcelain"]),
                    rustc=command(["rustc", "-vV"]), cargo=command(["cargo", "--version"]),
                    python=sys.version, platform=platform.platform(), profile=args.profile,
                    source_sha256=source_hashes(),
                    compiler_environment={k: os.environ[k] for k in
                        ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_TARGET", "CARGO_TARGET_DIR")
                        if k in os.environ})
    build = ["cargo", "build", "--locked", "--profile", args.profile,
             "-p", "pgso-core", "-p", "pgso-actuator-http",
             "--example", "validation_trace", "--example", "execution_validation", "--example", "server",
             "--message-format=json"]
    if args.offline:
        build.append("--offline")
    manifest["build_command"] = build
    results = {"status": "failed", "scope": "Finite engineering suites; no comparative utility or population safety inference"}
    try:
        artifacts = [json.loads(line) for line in command(build, timeout=600).splitlines()]
        binaries = {a["target"]["name"]: Path(a["executable"]) for a in artifacts
                    if a.get("reason") == "compiler-artifact" and a.get("executable")}
        require(set(binaries) == {"validation_trace", "execution_validation", "server"}, "Missing build artifacts")
        manifest["binary_sha256"] = {name: sha(path) for name, path in binaries.items()}
        results["governance"] = governance(binaries["validation_trace"], out)
        raw = command([str(binaries["execution_validation"])])
        cases = json.loads(raw)
        named_cases(cases, EXECUTION_CASES)
        require(all(c["actual"] == c["expected"] for c in cases), "Execution outcome mismatch")
        require(cases == json.loads(command([str(binaries["execution_validation"])])), "Execution replay mismatch")
        save(out, "execution.json", cases)
        results["execution"] = dict(cases=len(cases), passed=len(cases), fresh_process_replays=2)
        # Keep server ownership in this process so exceptions run its finally cleanup.
        http = evaluate_http(binaries["server"])
        named_cases(http, HTTP_CASES)
        save(out, "http.json", http)
        results["http"] = dict(cases=len(http), passed=len(http))
        require(source_hashes() == manifest["source_sha256"], "Sources changed during evaluation")
        results["status"] = "passed"
    finally:
        save(out, "results.json", results)
        manifest["output_sha256"] = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}
        save(out, "manifest.json", manifest)
    print(json.dumps(dict(output=str(out), **results), indent=2))


if __name__ == "__main__":
    main()
