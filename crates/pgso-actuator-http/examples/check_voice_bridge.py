"""Real NDJSON bridge check; run with the built voice_bridge binary as argv[1]."""
import json
import math
from pathlib import Path
import select
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiments/public_voice"))
import bridge as driver


def init(**settings):
    return {"session": "ttl-check", "tools": [{"name": "quote", "description": "Counted effect",
            "inputSchema": {"type": "object", "additionalProperties": False}}],
            "governed_tools": ["quote"], **settings}


class Bridge:
    def __init__(self, settings):
        self.process = subprocess.Popen([sys.argv[1]], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.callbacks = 0
        self.response = self.request(settings)

    def request(self, command):
        self.process.stdin.write((json.dumps(command) + "\n").encode())
        while True:
            assert select.select([self.process.stdout], [], [], 5)[0], "bridge stalled"
            line = self.process.stdout.readline()
            assert line, "bridge exited without response"
            response = json.loads(line)
            if "callback" not in response:
                return response
            assert command["op"] == "call" and response["callback"] == {
                "tool": "quote", "arguments": {}}
            self.callbacks += 1
            self.process.stdin.write(b'{"result":{"counted":true}}\n')

    def high(self):
        for timestamp in (1000, 1400):
            response = self.request({"op": "observe", "timestamp_ms": timestamp,
                                     "readings": [{"axis": "Arousal", "value": .9,
                                                   "confidence": .9, "timestamp_ms": timestamp}]})
            assert response["ok"]
        assert response["state"]["tools"] == [] and response["state"]["directives"]

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.communicate(timeout=5)


for invalid in (0, 60001, True, -1, 1.5, "20000", None):
    bridge = Bridge(init(stale_after_ms=invalid))
    try:
        assert not bridge.response["ok"] and bridge.callbacks == 0
        assert bridge.process.wait(timeout=5) != 0
    finally:
        bridge.close()

bridge = Bridge(init(unknown_setting=1))
try:
    assert not bridge.response["ok"] and bridge.callbacks == 0
    assert bridge.process.wait(timeout=5) != 0
finally:
    bridge.close()

for ttl in (None, 20000):
    bridge = Bridge(init(**({} if ttl is None else {"stale_after_ms": ttl})))
    try:
        resolved = 1200 if ttl is None else ttl
        assert bridge.response["ok"] and bridge.response["config"]["stale_after_ms"] == resolved
        assert bridge.response["config"]["max_gap_ms"] == 1200
        bridge.high()
        idle = bridge.request({"op": "observe", "timestamp_ms": 12000, "readings": []})
        assert idle["ok"] and idle["state"]["tools"] == (["quote"] if ttl is None else [])
        delayed = bridge.request({"op": "call", "name": "quote", "arguments": {},
                                  "timestamp_ms": 12000})
        assert delayed["ok"] == (ttl is None) and bridge.callbacks == (1 if ttl is None else 0)
        assert delayed["audit"][0]["reason"] == ("completed" if ttl is None else "tool_unavailable")
        if ttl is not None:
            at_boundary = bridge.request({"op": "approve", "name": "quote", "arguments": {},
                                          "timestamp_ms": 21400, "ttl_ms": 1})
            assert not at_boundary["ok"] and bridge.callbacks == 0
            expired = bridge.request({"op": "observe", "timestamp_ms": 21401, "readings": []})
            assert expired["ok"] and expired["state"]["tools"] == ["quote"]
            assert not expired["state"]["directives"]
            final = bridge.request({"op": "call", "name": "quote", "arguments": {},
                                    "timestamp_ms": 21401})
            assert final["ok"] and bridge.callbacks == 1
    finally:
        bridge.close()

for ttl in (1, 60000):
    bridge = Bridge(init(stale_after_ms=ttl))
    try:
        assert bridge.response["ok"] and bridge.response["config"]["stale_after_ms"] == ttl
    finally:
        bridge.close()

# The actual SDK host client must reject bad settings before spawning anything.
tools = [SimpleNamespace(openai_schema={"function": {
    "name": "quote", "description": "Counted effect",
    "parameters": {"type": "object", "additionalProperties": False}}})]
with mock.patch.object(driver.subprocess, "Popen", side_effect=AssertionError("unexpected spawn")):
    for invalid in (0, 60001, True, -1, 1.5, "20000", None):
        try:
            driver.Bridge(sys.argv[1], tools, {"quote"}, stale_after_ms=invalid)
            raise AssertionError("invalid driver setting accepted")
        except ValueError:
            pass

original_send = driver.Bridge._send
for ttl in (None, 1200, 20000):
    sent, callbacks = [], []

    def capture_send(self, command):
        sent.append(dict(command))
        return original_send(self, command)

    def effect(name, arguments):
        assert name == "quote" and arguments == {}
        callbacks.append(name)
        return {"counted": True}

    settings = {} if ttl is None else {"stale_after_ms": ttl}
    with mock.patch.object(driver.Bridge, "_send", capture_send):
        client = driver.Bridge(sys.argv[1], tools, {"quote"}, session="ttl-check", **settings)
        try:
            expected = {"tools": init()["tools"], "governed_tools": ["quote"],
                        "session": "ttl-check", "intervention": "prune"}
            if ttl == 20000:
                expected["stale_after_ms"] = ttl
            assert len(sent) == 1 and json.dumps(sent[0]) == json.dumps(expected)
            Bridge.high(client)
            delayed = client.request({"op": "call", "name": "quote", "arguments": {},
                                      "timestamp_ms": 12000}, callback=effect)
            assert delayed["ok"] == (ttl != 20000) and len(callbacks) == (0 if ttl == 20000 else 1)
            if ttl == 20000:
                expired = client.request({"op": "observe", "timestamp_ms": 21401, "readings": []})
                assert expired["ok"] and expired["state"]["tools"] == ["quote"]
                final = client.request({"op": "call", "name": "quote", "arguments": {},
                                        "timestamp_ms": 21401}, callback=effect)
                assert final["ok"] and len(callbacks) == 1

                def failed_effect(name, arguments):
                    effect(name, arguments)
                    raise ValueError("controlled callback failure")

                failed = client.request({"op": "call", "name": "quote", "arguments": {},
                                         "timestamp_ms": 21402}, callback=failed_effect)
                assert not failed["ok"] and failed["audit"][0]["reason"] == "callback_failed"
                assert len(callbacks) == 2 and len(client.receipts) == 3
        finally:
            client.close()
        assert client.process.poll() is not None

# A lying/malformed config acknowledgement closes the sole process; no retry.
original_popen = subprocess.Popen
for config in ({}, {"stale_after_ms": 1200}, {"stale_after_ms": True},
               {"stale_after_ms": "20000"}, None):
    spawned = []

    def capture_process(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        spawned.append(process)
        return process

    with mock.patch.object(driver.subprocess, "Popen", capture_process), mock.patch.object(
            driver.Bridge, "request", return_value={"ok": True, "config": config}):
        try:
            driver.Bridge(sys.argv[1], tools, {"quote"}, stale_after_ms=20000)
            raise AssertionError("configuration mismatch accepted")
        except RuntimeError:
            pass
    assert len(spawned) == 1 and spawned[0].poll() is not None

# Synthetic PCM checks the transport/extractor seam, not human emotion accuracy.
bridge = Bridge(init(stale_after_ms=20000))
try:
    silence = bridge.request({"op": "extract", "samples": [0.0] * 12800,
                              "timestamp_ms": 1000})
    assert silence["ok"] and silence["result"] == []
    tone = bridge.request({"op": "extract", "timestamp_ms": 1400,
                           "samples": [.2 * math.sin(2 * math.pi * 180 * i / 16000)
                                       for i in range(12800)]})
    assert tone["ok"] and len(tone["result"]) == 1
    reading = tone["result"][0]
    assert reading["axis"] == "Arousal" and reading["timestamp_ms"] == 1400
    assert 0 <= reading["value"] <= 1 and 0 <= reading["confidence"] <= 1
    assert bridge.request({"op": "state"})["state"]["tools"] == ["quote"]
    observed = bridge.request({"op": "observe", "timestamp_ms": 1400,
                               "readings": tone["result"]})
    assert observed["ok"] and observed["state"]["tools"] == ["quote"]
finally:
    bridge.close()

# Injected readings isolate policy correctness from acoustic validity.
bridge = Bridge(init(stale_after_ms=20000))
try:
    cases = [(1000, .5, .9), (1400, .5, .9),
             (1800, .9, .1), (2200, .9, .1),
             (2600, .9, .9), (3000, .9, .9)]
    for timestamp, value, confidence in cases:
        response = bridge.request({"op": "observe", "timestamp_ms": timestamp,
                                   "readings": [{"axis": "Arousal", "value": value,
                                                 "confidence": confidence,
                                                 "timestamp_ms": timestamp}]})
        assert response["ok"]
        assert response["state"]["tools"] == ([] if timestamp == 3000 else ["quote"])
    assert bridge.callbacks == 0
finally:
    bridge.close()

print(json.dumps({"status": "passed", "invalid_settings": 7, "boundary_settings": 2,
                  "synthetic_pcm_extract_observe": True,
                  "silence_no_reading": True, "neutral_no_activation": True,
                  "low_confidence_no_activation": True, "hysteresis": True,
                  "unknown_fields_rejected": True,
                  "default_expiry": True, "delayed_pruning": True,
                  "expiry_recovery": True, "driver_invalid_before_spawn": True,
                  "default_driver_wire_unchanged": True, "driver_callback_enforcement": True,
                  "driver_callback_failure_no_retry": True, "config_mismatch_closed": 5,
                  "provider_calls": 0}))
