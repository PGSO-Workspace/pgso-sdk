#!/usr/bin/env python3
"""Replay raw lifecycle episodes through independent comparator policies.

NeMo and Invariant make the final tool decision in their policy DSL. This
adapter implements temporal state in host code; it makes no claim about all
vendor-native features. No PGSO output or expected label is accepted.
"""
import json
import math
import struct
import sys

def _f32(value):
    return struct.unpack("!f", struct.pack("!f", value))[0]


BASELINE = _f32(0.5)
DEVIATION = _f32(0.3)
CONFIDENCE = _f32(0.5)
THRESHOLD = _f32(BASELINE + DEVIATION)
MAX_GAP_MS = 1200
HORIZON = 3
U64_MAX = 2**64 - 1
MODES = {"nemo", "invariant", "threshold", "voice_agnostic"}


def _unit(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite in [0, 1]")
    return _f32(value)


def _time(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= U64_MAX:
        raise ValueError(f"{name} must be an unsigned 64-bit integer")
    return value


def validate(raw, index):
    if not isinstance(raw, dict) or set(raw) != {"id", "events"} or not isinstance(raw["id"], str) or not raw["id"].strip():
        raise ValueError(f"episode {index}: expected exactly a non-empty string id and events")
    if not isinstance(raw["events"], list) or not raw["events"]:
        raise ValueError(f"episode {index}: events must be a non-empty array")
    events = []
    schemas = {
        "observation": {"kind", "value", "confidence", "timestamp_ms"},
        "expire": {"kind", "cutoff_ms"},
        "call": {"kind", "tool", "timestamp_ms"},
        "silence": {"kind"},
    }
    for position, event in enumerate(raw["events"]):
        if not isinstance(event, dict) or event.get("kind") not in schemas or set(event) != schemas.get(event.get("kind")):
            raise ValueError(f"episode {index} event {position}: invalid event fields")
        event = dict(event)
        if event["kind"] == "observation":
            event["value"] = _unit(event["value"], "value")
            event["confidence"] = _unit(event["confidence"], "confidence")
            event["timestamp_ms"] = _time(event["timestamp_ms"], "timestamp_ms")
        elif event["kind"] == "expire":
            event["cutoff_ms"] = _time(event["cutoff_ms"], "cutoff_ms")
        elif event["kind"] == "call":
            if event["tool"] not in {"quote", "human"}:
                raise ValueError(f"episode {index} event {position}: tool must be quote or human")
            event["timestamp_ms"] = _time(event["timestamp_ms"], "timestamp_ms")
        events.append(event)
    return raw["id"], events


def _nemo_decider():
    from nemoguardrails import LLMRails, RailsConfig
    config = RailsConfig.from_content(colang_content='''
define bot allow tool
  "ALLOW"
define bot block tool
  "BLOCK"
define flow enforce lifecycle
  if $tool == "quote" and $governed == 1
    bot block tool
    stop
  else
    bot allow tool
    stop
''', yaml_content='''
models: []
rails:
  input:
    flows:
      - enforce lifecycle
''')
    rails = LLMRails(config=config)

    def decide(tool, governed):
        reply = rails.generate(messages=[
            {"role": "context", "content": {"tool": tool, "governed": int(governed)}},
            {"role": "user", "content": tool},
        ])["content"]
        if reply not in {"ALLOW", "BLOCK"}:
            raise RuntimeError(f"unexpected NeMo decision {reply!r}")
        return reply.lower()
    return decide


def _invariant_decider():
    from invariant.analyzer import LocalPolicy
    policy = LocalPolicy.from_string('''
raise "quote forbidden by host-computed lifecycle" if:
  (call: ToolCall)
  call is tool:quote
  call.function.arguments.governed == 1
''')

    def decide(tool, governed):
        trace = [{"role": "assistant", "content": "", "tool_calls": [{
            "id": "candidate", "type": "function",
            "function": {"name": tool, "arguments": {"governed": int(governed)}},
        }]}]
        return "block" if policy.analyze(trace).errors else "allow"
    return decide


def evaluate(validated, mode, decide):
    ident, events = validated
    governed = False
    counter = 0
    rising_run = None
    last_accepted = None
    last_contribution = None
    callback_count = 0
    outputs = []

    def callback(tool):
        nonlocal callback_count
        callback_count += 1
        return {"tool": tool, "ordinal": callback_count}

    for event in events:
        kind = event["kind"]
        if kind == "observation":
            timestamp = event["timestamp_ms"]
            if event["confidence"] < CONFIDENCE or (last_accepted is not None and timestamp < last_accepted):
                continue
            if last_accepted is not None and timestamp - last_accepted > MAX_GAP_MS:
                counter = 0
                rising_run = None
            deviation = abs(_f32(event["value"] - BASELINE))
            rising = event["value"] > BASELINE
            if mode == "threshold":
                governed = event["value"] >= THRESHOLD
                counter = int(governed)
                if governed:
                    last_contribution = timestamp
            elif mode != "voice_agnostic":
                if deviation < DEVIATION:
                    counter, rising_run, governed = 0, None, False
                else:
                    if counter and rising_run != rising:
                        counter = 0
                    rising_run = rising
                    counter += 1
                    # Pending readings hold policy. At H, the Rising-only rule
                    # contributes governance; a falling trigger contributes none.
                    if counter >= HORIZON:
                        governed = rising
                        last_contribution = timestamp if rising else last_contribution
            last_accepted = timestamp
        elif kind == "expire":
            if last_contribution is not None and last_contribution < event["cutoff_ms"]:
                governed = False
        elif kind == "call":
            action = "allow" if mode == "voice_agnostic" else decide(event["tool"], governed)
            before = callback_count
            receipt = None
            if action == "allow":
                receipt = callback(event["tool"])
            outputs.append({"timestamp_ms": event["timestamp_ms"], "tool": event["tool"], "action": action,
                            "callback_delta": callback_count - before, "receipt": receipt})
    return {"id": ident, "outputs": outputs, "callback_count": callback_count,
            "state": {"governed": governed, "counter": counter, "last_accepted_ms": last_accepted,
                      "last_contribution_ms": last_contribution},
            "integration": "host-computed lifecycle; framework policy owns tool decision" if mode in {"nemo", "invariant"} else "engineering ablation"}


def validate_payload(payload):
    if not isinstance(payload, list) or not payload:
        raise ValueError("input must be a non-empty JSON array")
    validated = [validate(item, i) for i, item in enumerate(payload)]
    ids = [item[0] for item in validated]
    if len(ids) != len(set(ids)):
        raise ValueError("episode ids must be unique")
    return validated


def main():
    if len(sys.argv) not in {2, 3} or sys.argv[1] not in MODES:
        raise SystemExit("usage: comparator_adapters.py nemo|invariant|threshold|voice_agnostic [episodes.json]")
    mode = sys.argv[1]
    source = open(sys.argv[2], encoding="utf-8") if len(sys.argv) == 3 else sys.stdin
    with source:
        payload = json.load(source)
    validated = validate_payload(payload)
    decide = _nemo_decider() if mode == "nemo" else _invariant_decider() if mode == "invariant" else lambda tool, state: "block" if tool == "quote" and state else "allow"
    json.dump([evaluate(item, mode, decide) for item in validated], sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
