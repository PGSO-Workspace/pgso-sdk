"""Actual tau sandbox integration checks, not a conversational utility pilot.

Run with the pinned tau environment and PGSO_BRIDGE pointing at the Rust example.
"""
import os
import unittest
from pathlib import Path

from loguru import logger
from tau2.data_model.message import ToolCall
from tau2.domains.telecom.environment import get_environment, get_tasks
from tau2.runner import get_tasks as get_task_split

from bridge import Bridge, GovernedEnvironment
from pilot import GOVERNED_TOOLS, TASK_IDS

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

    def test_sustained_signal_can_withhold_selected_task_repair(self):
        tasks = {task.id: task for task in get_task_split("telecom", task_split_name="small")}
        task = tasks[TASK_IDS[1]]
        env = get_environment()
        initial = task.initial_state
        env.set_state(initial.initialization_data, initial.initialization_actions,
                      initial.message_history or [])
        bridge = Bridge(BINARY, env.get_tools(), GOVERNED_TOOLS, session="limitation")
        host = GovernedEnvironment(env, bridge)
        self.addCleanup(host.close)
        assertions = task.evaluation_criteria.env_assertions
        score = lambda: [env.run_env_assertion(a, raise_assertion_error=False)
                         for a in assertions]
        args = {"customer_id": "C1001", "line_id": "L1002"}

        self.assertEqual(task.id, TASK_IDS[1])
        self.assertIn("enable_roaming", GOVERNED_TOOLS)
        self.assertEqual(score(), [False, False])
        for timestamp in range(800, 5600, 400):
            host.timestamp_ms = timestamp
            response = bridge.request({
                "op": "observe", "timestamp_ms": timestamp,
                "readings": [{"axis": "Arousal", "value": 0.95, "confidence": 0.9,
                              "timestamp_ms": timestamp}],
            })
            self.assertTrue(response["ok"], response)
        before = env.get_db_hash()
        effects = len(host.effects)
        self.assertTrue(env.get_response(ToolCall(name="enable_roaming", arguments=args,
                                                  requestor="assistant")).error)
        self.assertEqual(env.get_db_hash(), before)
        self.assertEqual(len(host.effects), effects)
        self.assertEqual(host.attempts[-1]["pre_attempt_state"]["assistant_db"],
                         env.tools.db.model_dump(mode="json"))
        self.assertEqual(host.attempts[-1]["pre_attempt_state"]["user_db"],
                         env.user_tools.db.model_dump(mode="json"))
        self.assertEqual(score(), [False, False])

        host.timestamp_ms = 5600
        response = bridge.request({
            "op": "observe", "timestamp_ms": 5600,
            "readings": [{"axis": "Arousal", "value": 0.5, "confidence": 0.9,
                          "timestamp_ms": 5600}],
        })
        self.assertTrue(response["ok"], response)
        self.assertEqual(bridge.state["directives"], [])
        self.assertFalse(env.get_response(ToolCall(name="enable_roaming", arguments=args,
                                                   requestor="assistant")).error)
        self.assertEqual(score(), [True, True])

    def test_trusted_step_up_approval_is_bound_and_single_use(self):
        tasks = {task.id: task for task in get_task_split("telecom", task_split_name="small")}
        task = tasks[TASK_IDS[1]]
        env = get_environment()
        initial = task.initial_state
        env.set_state(initial.initialization_data, initial.initialization_actions,
                      initial.message_history or [])
        bridge = Bridge(BINARY, env.get_tools(), GOVERNED_TOOLS, session="step-up",
                        intervention="step_up")
        host = GovernedEnvironment(env, bridge)
        self.addCleanup(host.close)
        assertions = task.evaluation_criteria.env_assertions
        score = lambda: [env.run_env_assertion(a, raise_assertion_error=False)
                         for a in assertions]
        args = {"customer_id": "C1001", "line_id": "L1002"}

        for timestamp in range(800, 5600, 400):
            host.timestamp_ms = timestamp
            response = bridge.request({
                "op": "observe", "timestamp_ms": timestamp,
                "readings": [{"axis": "Arousal", "value": 0.95, "confidence": 0.9,
                              "timestamp_ms": timestamp}],
            })
            self.assertTrue(response["ok"], response)
        self.assertIn("enable_roaming", bridge.state["tools"])
        self.assertIn("enable_roaming", bridge.state["step_up_tools"])
        self.assertEqual(bridge.state["intervention"], "step_up")
        effects = len(host.effects)
        self.assertTrue(env.get_response(ToolCall(name="enable_roaming", arguments=args,
                                                  requestor="assistant")).error)
        self.assertEqual(len(host.effects), effects)
        self.assertEqual(bridge.receipts[-1]["reason"], "confirmation_missing")

        token = bridge.approve("enable_roaming", args, 5200, 100)
        command = {"op": "call", "name": "enable_roaming", "arguments": args,
                   "timestamp_ms": 5200, "confirmation": token}
        response = bridge.request(
            command, callback=lambda name, values: host._execute(name, values, "assistant"))
        self.assertTrue(response["ok"], response)
        self.assertEqual(score(), [True, True])

        effects = len(host.effects)
        response = bridge.request(
            command, callback=lambda name, values: host._execute(name, values, "assistant"))
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["audit"][0]["reason"], "confirmation_missing")
        self.assertEqual(len(host.effects), effects)

        token = bridge.approve("enable_roaming", args, 5200, 100)
        changed = {**args, "line_id": "L1001"}
        response = bridge.request(
            {"op": "call", "name": "enable_roaming", "arguments": changed,
             "timestamp_ms": 5200, "confirmation": token},
            callback=lambda name, values: host._execute(name, values, "assistant"),
        )
        self.assertFalse(response["ok"], response)
        self.assertEqual(response["audit"][0]["reason"],
                         "invalid_or_expired_confirmation")
        self.assertEqual(len(host.effects), effects)

    def test_invalid_intervention_and_approval_are_rejected(self):
        with self.assertRaises(RuntimeError):
            Bridge(BINARY, self.env.get_tools(), {"disable_roaming"},
                   intervention="automatic_consent")
        with self.assertRaises(ValueError):
            self.bridge.approve("missing_tool", {}, 0, 100)
        untrusted = Bridge(BINARY, self.env.get_tools(), {"disable_roaming"},
                           intervention="step_up")
        self.addCleanup(untrusted.close)
        with self.assertRaises(RuntimeError):
            untrusted.request({"op": "call", "name": "disable_roaming",
                               "arguments": {"customer_id": "C1001", "line_id": "L1002"},
                               "timestamp_ms": 0, "confirmed": True})

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
