"""Actual tau sandbox integration checks, not a conversational utility pilot.

Run with the pinned tau environment and PGSO_BRIDGE pointing at the Rust example.
"""
import os
import unittest
from pathlib import Path

from loguru import logger
from tau2.data_model.message import ToolCall
from tau2.domains.telecom.environment import get_environment, get_tasks

from bridge import Bridge, GovernedEnvironment

logger.remove()
BINARY = Path(os.environ.get("PGSO_BRIDGE", "target/debug/examples/voice_bridge")).resolve()


class RealEnvironmentChecks(unittest.TestCase):
    def setUp(self):
        self.env = get_environment()
        self.task = get_tasks()[0]
        initial = self.task.initial_state
        self.env.set_state(initial.initialization_data, initial.initialization_actions,
                           initial.message_history or [])
        self.bridge = Bridge(BINARY, self.env.get_tools(), {"disable_roaming"})
        self.host = GovernedEnvironment(self.env, self.bridge)
        self.addCleanup(self.host.close)

    def observe(self, timestamp, value):
        self.host.timestamp_ms = timestamp
        response = self.bridge.request({"op": "observe", "timestamp_ms": timestamp,
                                        "readings": [{"axis": "Arousal", "value": value,
                                                      "confidence": 0.9,
                                                      "timestamp_ms": timestamp}]})
        self.assertTrue(response["ok"], response)

    def call(self, name, arguments=None, requestor="assistant"):
        return self.env.get_response(ToolCall(name=name, arguments=arguments or {},
                                              requestor=requestor))

    def test_real_effect_block_recovery_and_directive_retirement(self):
        args = {"customer_id": "C1001", "line_id": "L1002"}
        self.assertFalse(self.call("disable_roaming", args).error)
        self.assertFalse(self.env.tools._get_line_by_id("L1002").roaming_enabled)
        self.assertFalse(self.call("enable_roaming", args).error)
        self.observe(800, 0.95)
        self.observe(1200, 0.95)
        self.assertTrue(self.bridge.state["directives"])
        before = self.env.get_db_hash()
        effects = len(self.host.effects)
        self.assertTrue(self.call("disable_roaming", args).error)
        self.assertEqual(self.env.get_db_hash(), before)
        self.assertEqual(len(self.host.effects), effects)
        for timestamp in range(1600, 5600, 400):
            self.observe(timestamp, 0.95)
        self.assertEqual(len(self.bridge.state["directives"]), 1)
        self.assertTrue(self.call("disable_roaming", args).error)
        self.observe(5600, 0.5)
        self.assertEqual(self.bridge.state["directives"], [])
        self.assertFalse(self.call("disable_roaming", args).error)
        self.assertFalse(self.env.tools._get_line_by_id("L1002").roaming_enabled)

    def test_bad_calls_have_no_callback_and_user_tools_still_work(self):
        before = len(self.host.effects)
        self.assertTrue(self.call("disable_roaming", {"customer_id": 3}).error)
        self.assertTrue(self.call("missing_tool").error)
        self.assertEqual(len(self.host.effects), before)
        self.observe(800, 0.95)
        self.observe(1200, 0.95)
        self.assertFalse(self.call("toggle_airplane_mode", requestor="user").error)
        self.assertFalse(self.call("transfer_to_human_agents", {"summary": "test"}).error)

    def test_original_live_task_predicates_and_session_reset(self):
        assertions = self.task.evaluation_criteria.env_assertions
        score = lambda: [self.env.run_env_assertion(a, raise_assertion_error=False)
                         for a in assertions]
        self.assertEqual(score(), [False, False])
        # Scripted known fixture repair ONLY verifies the scorer; no LLM receives it.
        for name in ("toggle_airplane_mode", "toggle_roaming"):
            self.assertFalse(self.call(name, requestor="user").error)
        self.assertEqual(score(), [True, True])
        self.observe(800, 0.95)
        self.observe(1200, 0.95)
        fresh = Bridge(BINARY, self.env.get_tools(), {"disable_roaming"}, session="fresh")
        try:
            self.assertEqual(fresh.state["directives"], [])
            self.assertIn("disable_roaming", fresh.state["tools"])
        finally:
            fresh.close()

    def test_silent_pcm_extract_and_clock_rejection(self):
        result = self.bridge.request({"op": "extract", "samples": [0.0] * 12800,
                                      "timestamp_ms": 800})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"], [])
        self.observe(1200, 0.5)
        result = self.bridge.request({"op": "observe", "timestamp_ms": 1000,
                                      "readings": []})
        self.assertFalse(result["ok"], result)

    def test_silence_expiry_null_result_and_callback_failure(self):
        self.observe(800, 0.95)
        self.observe(1200, 0.95)
        response = self.bridge.request({"op": "observe", "timestamp_ms": 2800,
                                        "readings": []})
        self.assertTrue(response["ok"], response)
        self.assertEqual(self.bridge.state["directives"], [])
        command = {"op": "call", "name": "get_customer_by_phone",
                   "arguments": {"phone_number": "555-123-2002"}, "timestamp_ms": 2800}
        response = self.bridge.request(command, callback=lambda *_: None)
        self.assertTrue(response["ok"], response)
        self.assertIsNone(response["result"])
        def fail(*_):
            raise ValueError("synthetic callback failure")
        response = self.bridge.request(command, callback=fail)
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["audit"][0]["reason"], "callback_failed")


if __name__ == "__main__":
    unittest.main()
