#!/usr/bin/env python3
"""Persistent NeMo/Invariant policy process for the public voice pilot."""
import hashlib
import importlib
import importlib.metadata
import json
import math
import struct
import sys
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
    def __init__(self, mode, configured_tools, all_tools, config):
        required = {key: config.get(key) for key in EXPECTED_CONFIG}
        if required != EXPECTED_CONFIG:
            raise ValueError("framework comparator requires the frozen public-voice configuration")
        self.mode = mode
        self.configured_tools = _tool_names(configured_tools)
        self.all_tools = _tool_names(all_tools)
        if not self.configured_tools <= self.all_tools:
            raise ValueError("governed tools must be present in the nominal tool set")
        factory = _nemo_decider if mode == "nemo" else _invariant_decider
        self.decide = factory(self.configured_tools)
        self.active = False
        self.counter = 0
        self.rising_run = None
        self.last_accepted = None
        self.last_contribution = None
        self.audit = []

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
        action = self.decide(command["name"], self.active)
        record = {"op": "call", "tool": command["name"], "timestamp_ms": timestamp,
                  "governed": self.active, "action": action}
        self.audit.append(record)
        return {"ok": True, "action": action, "record": record, "state": self.state()}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in {"nemo", "invariant"}:
        raise SystemExit("usage: framework_sidecar.py nemo|invariant")
    mode = sys.argv[1]
    session = None
    for line in sys.stdin:
        try:
            command = json.loads(line)
            if not isinstance(command, dict) or not isinstance(command.get("op"), str):
                raise ValueError("command must be an object with an op")
            if command["op"] == "init":
                if session is not None or set(command) != {
                        "op", "governed_tools", "all_tools", "config"}:
                    raise ValueError("invalid or repeated init")
                session = Session(mode, command["governed_tools"], command["all_tools"],
                                  command["config"])
                distribution = "nemoguardrails" if mode == "nemo" else "invariant-ai"
                module = "nemoguardrails" if mode == "nemo" else "invariant"
                source_root, source_hash = _source_fingerprint(module)
                response = {"ok": True, "state": session.state(), "framework": {
                    "mode": mode, "distribution": distribution,
                    "version": importlib.metadata.version(distribution),
                    "python": sys.version, "executable": sys.executable,
                    "executable_sha256": hashlib.sha256(
                        Path(sys.executable).read_bytes()).hexdigest(),
                    "module_root": source_root, "module_source_sha256": source_hash,
                    "policy_source_sha256": hashlib.sha256(
                        (REPRO / "comparator_adapters.py").read_bytes()).hexdigest(),
                    "config": EXPECTED_CONFIG,
                    "temporal_state": "host-authored; framework DSL owns candidate-call decision",
                }}
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
