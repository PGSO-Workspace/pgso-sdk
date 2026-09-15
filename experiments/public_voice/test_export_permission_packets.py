"""Offline contract checks for blinded action-permission packet export."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from export_permission_packets import export


class PermissionPacketExport(unittest.TestCase):
    def fixture(self, root):
        pilot = root / "pilot"
        pilot.mkdir()
        policy = root / "policy.md"
        policy.write_text(
            "<main_policy>\nMAIN\n</main_policy>\n"
            "<tech_support_policy>\nTECH\n</tech_support_policy>"
        )
        policy_hash = hashlib.sha256(policy.read_bytes()).hexdigest()
        (pilot / "manifest.json").write_text(json.dumps({
            "seed": 7, "effective_policy": {"text": policy.read_text(),
                                              "sha256": policy_hash},
        }) + "\n")
        state = {"assistant_db": {"line": {"roaming": False}},
                 "user_db": {"device": {"roaming": True}}}
        messages = [
            {"role": "assistant", "content": "How can I help?", "timestamp": "hidden"},
            {"role": "user", "content": "Please fix roaming.",
             "audio_path": "/reveals/condition/audio.wav"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-a", "name": "enable_roaming",
                 "arguments": {"customer_id": "C1", "line_id": "L1"}},
                {"id": "call-b", "name": "refuel_data",
                 "arguments": {"customer_id": "C1", "line_id": "L1", "amount": 2}},
            ]},
            {"role": "tool", "content": "future result"},
        ]
        attempts = [
            {"tool": "enable_roaming", "arguments": {"customer_id": "C1", "line_id": "L1"},
             "tool_call_id": "call-a", "dialogue": {"prefix_length": 2,
                                                        "tool_call_message_index": 2},
             "pre_attempt_state": copy.deepcopy(state), "error": "blocked"},
            {"tool": "refuel_data",
             "arguments": {"customer_id": "C1", "line_id": "L1", "amount": 2},
             "tool_call_id": "call-b", "dialogue": {"prefix_length": 2,
                                                        "tool_call_message_index": 2},
             "pre_attempt_state": copy.deepcopy(state), "ok": True},
        ]
        results = [{"task_id": "task-secret", "condition": "P", "score": {"score": 1},
                    "attempts": attempts, "messages": messages,
                    "pgso_state": {"directives": ["secret"]},
                    "pgso_audit": [{"reason": "secret"}]}]
        (pilot / "results.json").write_text(json.dumps(results) + "\n")
        return pilot, policy, results

    def test_blinded_deterministic_export_and_strict_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pilot, policy, results = self.fixture(root)
            first, second = root / "first", root / "second"
            self.assertEqual(export(pilot, policy, first, 19), 2)
            self.assertEqual(export(pilot, policy, second, 19), 2)
            reviewer = json.loads((first / "reviewer/packets.json").read_text())
            private = json.loads((first / "private/linkage.json").read_text())
            self.assertEqual(reviewer,
                             json.loads((second / "reviewer/packets.json").read_text()))
            serialized = json.dumps(reviewer)
            for forbidden in ("task-secret", '"condition"', "pgso_state", "pgso_audit",
                              "future result", '"score"', "/reveals/", "call-a", "call-b"):
                self.assertNotIn(forbidden, serialized)
            self.assertIn("Please fix roaming.", serialized)
            self.assertIn("<tech_support_policy>",
                          (first / "reviewer/policy.md").read_text())
            self.assertEqual(private["seed"], 19)
            self.assertEqual({link["condition"] for link in private["links"]}, {"P"})
            self.assertEqual(len({packet["packet_id"] for packet in reviewer["packets"]}), 2)
            with self.assertRaises(ValueError):
                export(pilot, policy, first, 19)

            wrong_policy = root / "wrong-policy.md"
            wrong_policy.write_text("MAIN ONLY\n")
            with self.assertRaisesRegex(ValueError, "does not match"):
                export(pilot, wrong_policy, root / "wrong", 19)
            self.assertFalse((root / "wrong").exists())

            results[0]["attempts"][0]["dialogue"]["prefix_length"] = 1
            (pilot / "results.json").write_text(json.dumps(results) + "\n")
            with self.assertRaisesRegex(ValueError, "invalid dialogue locator"):
                export(pilot, policy, root / "invalid", 19)
            self.assertFalse((root / "invalid").exists())

            del results[0]["attempts"][0]["dialogue"]
            (pilot / "results.json").write_text(json.dumps(results) + "\n")
            with self.assertRaisesRegex(ValueError, "missing its dialogue locator"):
                export(pilot, policy, root / "missing", 19)

            malformed = root / "malformed"
            malformed.mkdir()
            (malformed / "manifest.json").write_text((pilot / "manifest.json").read_text())
            (malformed / "results.json").write_text(json.dumps([{"messages": []}]) + "\n")
            with self.assertRaisesRegex(ValueError, "attempts and messages arrays"):
                export(malformed, policy, root / "malformed-output", 19)


if __name__ == "__main__":
    unittest.main()
