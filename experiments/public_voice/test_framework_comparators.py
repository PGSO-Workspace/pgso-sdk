#!/usr/bin/env python3
"""Offline real-framework and tau-sandbox tests for opt-in N/I arms."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, "/tmp/pgso-tau-bench/src")

from bridge import Bridge, FrameworkPolicy, GovernedEnvironment  # noqa: E402
from pilot import GOVERNED_TOOLS, RUNTIME_CONFIG  # noqa: E402


class FrameworkComparatorTests(unittest.TestCase):
    def agentspec_policy(self, governed_tools, all_tools, directory):
        python = os.environ.get("PGSO_AGENTSPEC_PYTHON")
        checkout = os.environ.get("PGSO_AGENTSPEC_CHECKOUT")
        if not python or not checkout or not Path(python).is_file():
            self.skipTest("missing reconstructed AgentSpec interpreter/checkout")
        return FrameworkPolicy(
            "agentspec", python, governed_tools, all_tools, RUNTIME_CONFIG,
            Path(directory) / "agentspec.stderr.log",
            agentspec_checkout=checkout,
            agentspec_revision="e6fa3902e2cfb9681f454b355691b771f70543f8",
        )

    def test_agentspec_native_skip_expiry_and_real_tau_effect(self):
        from tau2.domains.telecom.environment import get_environment, get_tasks
        binary = HERE.parent.parent / "target/debug/examples/voice_bridge"
        with tempfile.TemporaryDirectory() as directory:
            environment = get_environment()
            initial = get_tasks()[0].initial_state
            environment.set_state(initial.initialization_data,
                                  initial.initialization_actions,
                                  initial.message_history or [])
            tools = environment.get_tools()
            policy = self.agentspec_policy(
                GOVERNED_TOOLS, {tool.name for tool in tools}, directory)
            bridge = Bridge(binary, tools, set(), session="test-agentspec")
            governor = GovernedEnvironment(environment, bridge, policy)
            self.addCleanup(governor.close)
            high = lambda timestamp: {"timestamp_ms": timestamp, "readings": [{
                "axis": "Arousal", "value": .95, "confidence": .9,
                "timestamp_ms": timestamp}]}
            governor.observe(high(400))
            governor.observe(high(800))
            args = {"customer_id": "C1001", "line_id": "L1002"}
            before = environment.get_db_hash()
            with self.assertRaises(ValueError):
                environment.make_tool_call(
                    "disable_roaming", requestor="assistant", **args)
            self.assertEqual(environment.get_db_hash(), before)
            self.assertEqual(policy.audit[-1]["native_decision"], "SKIP")
            environment.make_tool_call(
                "transfer_to_human_agents", requestor="assistant", summary="test")
            self.assertIsNone(policy.audit[-1]["native_decision"])
            self.assertEqual(policy.audit[-1]["decision_origin"],
                             "host_no_matching_rule")
            governor.observe({"timestamp_ms": 2001, "readings": []})
            environment.make_tool_call(
                "disable_roaming", requestor="assistant", **args)
            self.assertIsNone(policy.audit[-1]["native_decision"])
            self.assertEqual(policy.audit[-1]["decision_origin"],
                             "host_no_active_rule")
            external = policy.framework["external_runtime"]
            self.assertEqual(external["revision"],
                             "e6fa3902e2cfb9681f454b355691b771f70543f8")
            self.assertEqual(len(external["rules"]), len(GOVERNED_TOOLS))

    def test_agentspec_rejects_wrong_checkout_revision(self):
        python = os.environ.get("PGSO_AGENTSPEC_PYTHON")
        checkout = os.environ.get("PGSO_AGENTSPEC_CHECKOUT")
        if not python or not checkout:
            self.skipTest("missing reconstructed AgentSpec interpreter/checkout")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RuntimeError):
            FrameworkPolicy(
                "agentspec", python, GOVERNED_TOOLS, GOVERNED_TOOLS,
                RUNTIME_CONFIG, Path(directory) / "agentspec.stderr.log",
                agentspec_checkout=checkout, agentspec_revision="0" * 40,
            )

    def test_agentspec_rejects_untracked_source_shadow(self):
        python = os.environ.get("PGSO_AGENTSPEC_PYTHON")
        checkout_value = os.environ.get("PGSO_AGENTSPEC_CHECKOUT")
        if not python or not checkout_value:
            self.skipTest("missing reconstructed AgentSpec interpreter/checkout")
        checkout = Path(checkout_value)
        probe = checkout / "src/pgso_untracked_probe.py"
        probe.write_text("raise RuntimeError('must never load')\n")
        try:
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(RuntimeError):
                FrameworkPolicy(
                    "agentspec", python, GOVERNED_TOOLS, GOVERNED_TOOLS,
                    RUNTIME_CONFIG, Path(directory) / "agentspec.stderr.log",
                    agentspec_checkout=checkout,
                    agentspec_revision="e6fa3902e2cfb9681f454b355691b771f70543f8",
                )
        finally:
            probe.unlink()

    def test_partial_sidecar_line_respects_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = FrameworkPolicy.__new__(FrameworkPolicy)
            policy.mode = "nemo"
            policy.timeout = .1
            policy.state = {}
            policy.audit = []
            policy._stdout_buffer = b""
            policy._stderr = (Path(directory) / "partial.stderr").open("w")
            policy.process = subprocess.Popen(
                [sys.executable, "-u", "-c",
                 "import sys,time; sys.stdout.write('{'); sys.stdout.flush(); time.sleep(10)"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=policy._stderr,
            )
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                policy.request({"op": "test"})
            self.assertLess(time.monotonic() - started, 1)
            self.assertIsNotNone(policy.process.poll())

    def frameworks(self):
        configured = {
            "nemo": os.environ.get("PGSO_NEMO_PYTHON"),
            "invariant": os.environ.get("PGSO_INVARIANT_PYTHON"),
        }
        missing = [name for name, path in configured.items()
                   if not path or not Path(path).is_file()]
        if missing:
            self.skipTest(f"missing pinned framework interpreters: {', '.join(missing)}")
        return configured.items()

    def policy(self, mode, python, governed_tools, all_tools, directory):
        return FrameworkPolicy(mode, python, governed_tools, all_tools, RUNTIME_CONFIG,
                               Path(directory) / f"{mode}.stderr.log")

    def test_lifecycle_abstention_expiry_handoff_and_empty_set(self):
        all_tools = GOVERNED_TOOLS | {"get_customer_by_phone", "transfer_to_human_agents"}
        for mode, python in self.frameworks():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                policy = self.policy(mode, python, GOVERNED_TOOLS, all_tools, directory)
                self.addCleanup(policy.close)
                high = lambda timestamp, confidence=.9: {
                    "timestamp_ms": timestamp,
                    "readings": [{"axis": "Arousal", "value": .95,
                                  "confidence": confidence, "timestamp_ms": timestamp}],
                }
                policy.observe(high(400))
                self.assertEqual(policy.decide("enable_roaming", 400), "allow")
                policy.observe(high(800))
                self.assertEqual(policy.decide("enable_roaming", 800), "block")
                self.assertEqual(policy.decide("transfer_to_human_agents", 800), "allow")
                policy.observe(high(1200, confidence=.1))
                self.assertEqual(policy.decide("enable_roaming", 1200), "block")
                policy.observe({"timestamp_ms": 1600, "readings": [{
                    "axis": "Arousal", "value": .5, "confidence": .9,
                    "timestamp_ms": 1600}]})
                self.assertEqual(policy.decide("enable_roaming", 1600), "allow")
                policy.observe(high(2000))
                policy.observe(high(2400))
                self.assertEqual(policy.decide("enable_roaming", 2400), "block")
                falling = lambda timestamp: {
                    "timestamp_ms": timestamp,
                    "readings": [{"axis": "Arousal", "value": .1,
                                  "confidence": .9, "timestamp_ms": timestamp}],
                }
                policy.observe(falling(2600))
                self.assertEqual(policy.decide("enable_roaming", 2600), "block")
                policy.observe(falling(2800))
                self.assertEqual(policy.decide("enable_roaming", 2800), "allow")
                # Match the runtime's f32 subtraction: decimal .7 is just below
                # the .2 deviation boundary after conversion and is nominal.
                boundary = lambda timestamp, value: {
                    "timestamp_ms": timestamp,
                    "readings": [{"axis": "Arousal", "value": value,
                                  "confidence": .9, "timestamp_ms": timestamp}],
                }
                policy.observe(boundary(3000, .7))
                policy.observe(boundary(3200, .7))
                self.assertEqual(policy.decide("enable_roaming", 3200), "allow")
                policy.observe(boundary(3400, .7000001))
                policy.observe(boundary(3600, .7000001))
                self.assertEqual(policy.decide("enable_roaming", 3600), "block")
                policy.observe({"timestamp_ms": 4001, "readings": []})
                self.assertEqual(policy.decide("enable_roaming", 4001), "block")
                policy.observe({"timestamp_ms": 4801, "readings": []})
                self.assertEqual(policy.decide("enable_roaming", 4801), "allow")

                empty = self.policy(mode, python, set(), all_tools, directory)
                self.addCleanup(empty.close)
                empty.observe(high(400))
                empty.observe(high(800))
                self.assertEqual(empty.decide("enable_roaming", 800), "allow")

    def test_real_tau_effect_is_gated_and_sidecar_failure_fails_closed(self):
        from tau2.domains.telecom.environment import get_environment, get_tasks
        binary = HERE.parent.parent / "target/debug/examples/voice_bridge"
        for mode, python in self.frameworks():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                environment = get_environment()
                initial = get_tasks()[0].initial_state
                environment.set_state(initial.initialization_data,
                                      initial.initialization_actions,
                                      initial.message_history or [])
                tools = environment.get_tools()
                bridge = Bridge(binary, tools, set(), session=f"test-{mode}")
                policy = self.policy(mode, python, GOVERNED_TOOLS,
                                     {tool.name for tool in tools}, directory)
                governor = GovernedEnvironment(environment, bridge, policy)
                self.addCleanup(governor.close)
                args = {"customer_id": "C1001", "line_id": "L1002"}
                for timestamp in (400, 800):
                    governor.timestamp_ms = timestamp
                    governor.observe({"timestamp_ms": timestamp, "readings": [{
                        "axis": "Arousal", "value": .95, "confidence": .9,
                        "timestamp_ms": timestamp}]})
                before = environment.get_db_hash()
                effects = len(governor.effects)
                with self.assertRaises(ValueError):
                    environment.make_tool_call("disable_roaming", requestor="assistant", **args)
                self.assertEqual(environment.get_db_hash(), before)
                self.assertEqual(len(governor.effects), effects)
                effects = len(governor.effects)
                environment.make_tool_call(
                    "transfer_to_human_agents", requestor="assistant", summary="test")
                self.assertEqual(len(governor.effects), effects + 1)

                policy.process.terminate()
                policy.process.wait(timeout=5)
                before = environment.get_db_hash()
                effects = len(governor.effects)
                with self.assertRaises(RuntimeError):
                    environment.make_tool_call("enable_roaming", requestor="assistant", **args)
                self.assertEqual(environment.get_db_hash(), before)
                self.assertEqual(len(governor.effects), effects)


if __name__ == "__main__":
    unittest.main()
