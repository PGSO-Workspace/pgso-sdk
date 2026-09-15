#!/usr/bin/env python3
"""Persistent external-framework policy process for the public voice pilot."""
import hashlib
import importlib
import importlib.metadata
import json
import math
import struct
import subprocess
import sys
import tomllib
from contextlib import redirect_stdout
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPRO = HERE.parent / "reproducibility"
sys.path.insert(0, str(REPRO))
from comparator_adapters import (  # noqa: E402
    _configured_tools,
    _invariant_decider,
    _nemo_decider,
)


EXPECTED_CONFIG = {
    "population_prior": 0.5,
    "ema_alpha": 0.0,
    "confidence_threshold": 0.5,
    "deviation_threshold": 0.2,
    "hysteresis_window": 2,
    "max_gap_ms": 1200,
    "stale_after_ms": 1200,
    "direction": "Rising",
}


def _f32(value):
    return struct.unpack("!f", struct.pack("!f", value))[0]


def _tool_names(value):
    if not isinstance(value, list) or len(value) != len(set(value)):
        raise ValueError("governed_tools must be a list of unique tool names")
    return _configured_tools(value)


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return _f32(value)


def _timestamp(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _source_fingerprint(module_name):
    root = Path(importlib.import_module(module_name).__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return str(root), digest.hexdigest()


class Session:
    def __init__(self, mode, configured_tools, all_tools, config,
                 agentspec_checkout=None, agentspec_revision=None):
        required = {key: config.get(key) for key in EXPECTED_CONFIG}
        if required != EXPECTED_CONFIG:
            raise ValueError("framework comparator requires the frozen public-voice configuration")
        self.mode = mode
        self.configured_tools = _tool_names(configured_tools)
        self.all_tools = _tool_names(all_tools)
        if not self.configured_tools <= self.all_tools:
            raise ValueError("governed tools must be present in the nominal tool set")
        self.rules = None
        self.agentspec = None
        if mode == "agentspec":
            self.agentspec = self._load_agentspec(
                agentspec_checkout, agentspec_revision)
        else:
            factory = _nemo_decider if mode == "nemo" else _invariant_decider
            self.decide = factory(self.configured_tools)
        self.active = False
        self.counter = 0
        self.rising_run = None
        self.last_accepted = None
        self.last_contribution = None
        self.audit = []

    def _load_agentspec(self, checkout_value, revision):
        checkout = Path(checkout_value or "").resolve()
        if not checkout.is_dir() or not (checkout / "src/spec_lang/AgentSpec.g4").is_file():
            raise ValueError("AgentSpec checkout is missing required source files")
        if not isinstance(revision, str) or len(revision) != 40 or any(
                character not in "0123456789abcdef" for character in revision):
            raise ValueError("AgentSpec revision must be a lowercase 40-character commit")
        actual = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=checkout, check=True,
            text=True, capture_output=True).stdout.strip()
        if actual != revision:
            raise ValueError(f"AgentSpec revision must be {revision}, found {actual}")
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout,
            check=True, text=True, capture_output=True).stdout.strip()
        if dirty:
            raise ValueError("AgentSpec tracked files differ from the pinned revision")
        source = checkout / "src"
        sys.path.insert(0, str(source))
        with redirect_stdout(sys.stderr):
            from agent import Action
            from enforcement import EnforceResult
            from interpreter import RuleInterpreter
            from rule import Rule
            from state import RuleState
            rules = {}
            texts = {}
            for tool in sorted(self.configured_tools):
                text = (f"rule @pgso_{tool}\ntrigger {tool}\ncheck true\n"
                        "enforce skip\nend\n")
                rules[tool] = Rule.from_text(text)
                texts[tool] = text
        self.rules = rules
        digest = hashlib.sha256()
        files = []
        for path in sorted(source.rglob("*.py")) + [source / "spec_lang/AgentSpec.g4"]:
            relative = str(path.relative_to(checkout))
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
            files.append(relative)
        dependencies = {}
        for distribution in (
                "antlr4-python3-runtime", "langchain", "langchain-core",
                "langchain-community", "langchain-experimental", "langchain-openai",
                "langchain-text-splitters", "openai", "pydantic"):
            dependencies[distribution] = importlib.metadata.version(distribution)
        rule_text = "".join(texts[tool] for tool in sorted(texts))
        project = tomllib.loads((checkout / "pyproject.toml").read_text())["project"]
        if project.get("name") != "agentspec" or not isinstance(project.get("version"), str):
            raise ValueError("AgentSpec checkout has unexpected package metadata")
        return {
            "checkout": str(checkout), "revision": revision,
            "git_tree": subprocess.run(
                ["git", "rev-parse", "HEAD^{tree}"], cwd=checkout, check=True,
                text=True, capture_output=True).stdout.strip(),
            "source_sha256": digest.hexdigest(), "source_files": files,
            "dependencies": dependencies, "rules": texts,
            "rules_sha256": hashlib.sha256(rule_text.encode()).hexdigest(),
            "project_version": project["version"],
            "license_file_present": any((checkout / name).is_file() for name in (
                "LICENSE", "LICENSE.md", "COPYING", "NOTICE")),
            "classes": {"Action": Action, "EnforceResult": EnforceResult,
                        "RuleInterpreter": RuleInterpreter, "RuleState": RuleState},
        }

    def _agentspec_decide(self, name):
        _tool_names([name])
        if not self.active:
            return "allow", "CONTINUE"
        classes = self.agentspec["classes"]
        matched = None
        with redirect_stdout(sys.stderr):
            for rule in self.rules.values():
                if rule.triggered(name, ""):
                    matched = rule
                    break
            if matched is None:
                return "allow", "CONTINUE"
            action = classes["Action"](name=name, input="", action=None)
            state = classes["RuleState"](action=action, intermediate_steps=[])
            result, replacement = classes["RuleInterpreter"](
                matched, state).verify_and_enforce(action)
        if result != classes["EnforceResult"].SKIP or not replacement.is_skip():
            raise RuntimeError("AgentSpec exact-tool rule did not return native SKIP")
        return "block", result.name

    def state(self):
        return {
            "tools": sorted(self.all_tools - self.configured_tools if self.active else self.all_tools),
            "directives": (["Ask the user for clarification before continuing."]
                           if self.active else []),
            "active": self.active,
            "counter": self.counter,
            "last_accepted_ms": self.last_accepted,
            "last_contribution_ms": self.last_contribution,
        }

    def observe(self, command):
        timestamp = _timestamp(command.get("timestamp_ms"), "timestamp_ms")
        readings = command.get("readings")
        if not isinstance(readings, list):
            raise ValueError("readings must be a list")
        cutoff = max(0, timestamp - EXPECTED_CONFIG["stale_after_ms"])
        if self.last_contribution is not None and self.last_contribution < cutoff:
            self.active = False
        for reading in readings:
            if not isinstance(reading, dict) or set(reading) != {
                    "axis", "value", "confidence", "timestamp_ms"}:
                raise ValueError("reading has invalid fields")
            reading_time = _timestamp(reading["timestamp_ms"], "reading timestamp_ms")
            value = _number(reading["value"], "value")
            confidence = _number(reading["confidence"], "confidence")
            if not 0 <= value <= 1 or not 0 <= confidence <= 1:
                raise ValueError("value and confidence must be in [0, 1]")
            if str(reading["axis"]).lower() != "arousal":
                continue
            if confidence < EXPECTED_CONFIG["confidence_threshold"] or (
                    self.last_accepted is not None and reading_time < self.last_accepted):
                continue
            if (self.last_accepted is not None and
                    reading_time - self.last_accepted > EXPECTED_CONFIG["max_gap_ms"]):
                self.counter = 0
                self.rising_run = None
            prior = _f32(EXPECTED_CONFIG["population_prior"])
            deviation = abs(_f32(value - prior))
            rising = value > prior
            if deviation < _f32(EXPECTED_CONFIG["deviation_threshold"]):
                self.counter, self.rising_run, self.active = 0, None, False
            else:
                if self.counter and self.rising_run != rising:
                    self.counter = 0
                self.rising_run = rising
                self.counter += 1
                if self.counter >= EXPECTED_CONFIG["hysteresis_window"]:
                    self.active = rising
                    if rising:
                        self.last_contribution = reading_time
            self.last_accepted = reading_time
        record = {"op": "observe", "timestamp_ms": timestamp,
                  "readings": readings, "state": self.state()}
        self.audit.append(record)
        return {"ok": True, "record": record, "state": self.state()}

    def call(self, command):
        if set(command) != {"op", "name", "timestamp_ms"}:
            raise ValueError("call has invalid fields")
        timestamp = _timestamp(command["timestamp_ms"], "timestamp_ms")
        if self.mode == "agentspec":
            action, native_decision = self._agentspec_decide(command["name"])
        else:
            action = self.decide(command["name"], self.active)
            native_decision = action.upper()
        record = {"op": "call", "tool": command["name"], "timestamp_ms": timestamp,
                  "governed": self.active, "action": action,
                  "native_decision": native_decision}
        self.audit.append(record)
        return {"ok": True, "action": action, "record": record, "state": self.state()}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"nemo", "invariant", "agentspec"}:
        raise SystemExit("usage: framework_sidecar.py nemo|invariant|agentspec")
    mode = sys.argv[1]
    session = None
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if not isinstance(command, dict) or not isinstance(command.get("op"), str):
                raise ValueError("command must be an object with an op")
            if command["op"] == "init":
                expected = {"op", "governed_tools", "all_tools", "config"}
                if mode == "agentspec":
                    expected |= {"agentspec_checkout", "agentspec_revision"}
                if session is not None or set(command) != expected:
                    raise ValueError("invalid or repeated init")
                session = Session(mode, command["governed_tools"], command["all_tools"],
                                  command["config"], command.get("agentspec_checkout"),
                                  command.get("agentspec_revision"))
                distribution = ("nemoguardrails" if mode == "nemo" else
                                "invariant-ai" if mode == "invariant" else "agentspec")
                if mode == "agentspec":
                    source_root = session.agentspec["checkout"]
                    source_hash = session.agentspec["source_sha256"]
                    version = session.agentspec["project_version"]
                else:
                    module = "nemoguardrails" if mode == "nemo" else "invariant"
                    source_root, source_hash = _source_fingerprint(module)
                    version = importlib.metadata.version(distribution)
                response = {"ok": True, "state": session.state(), "framework": {
                    "mode": mode, "distribution": distribution,
                    "version": version,
                    "python": sys.version, "executable": sys.executable,
                    "executable_sha256": hashlib.sha256(
                        Path(sys.executable).read_bytes()).hexdigest(),
                    "module_root": source_root, "module_source_sha256": source_hash,
                    "policy_source_sha256": (
                        session.agentspec["rules_sha256"] if mode == "agentspec" else
                        hashlib.sha256((REPRO / "comparator_adapters.py").read_bytes()).hexdigest()),
                    "config": EXPECTED_CONFIG,
                    "temporal_state": "host-authored; framework DSL owns candidate-call decision",
                }}
                if mode == "agentspec":
                    response["framework"]["external_runtime"] = {
                        key: value for key, value in session.agentspec.items()
                        if key != "classes"
                    }
            elif session is None:
                raise ValueError("init required")
            elif command["op"] == "observe":
                if set(command) != {"op", "timestamp_ms", "readings"}:
                    raise ValueError("observe has invalid fields")
                response = session.observe(command)
            elif command["op"] == "call":
                response = session.call(command)
            else:
                raise ValueError("unsupported op")
        except Exception as error:
            print(json.dumps({"ok": False, "error": str(error)}, separators=(",", ":")),
                  flush=True)
            continue
        print(json.dumps(response, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
