"""Offline full-loop coverage for the metered public-voice pilot."""

import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, "/tmp/pgso-tau-bench/src")

import pilot  # noqa: E402


def _wav() -> bytes:
    """A deterministic 16 kHz mono fixture that exercises real Rust extraction."""
    samples = (struct.pack("<h", 16384 if i % 2 else -16384) for i in range(16000))
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"".join(samples))
    return output.getvalue()


class _Speech:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data


class _OpenAI:
    def __init__(self):
        self.audio = SimpleNamespace(
            speech=SimpleNamespace(create=lambda **_: _Speech(_WAV)),
            transcriptions=SimpleNamespace(create=lambda **_: SimpleNamespace(
                text="Please disable roaming on my line.",
            )),
        )


_WAV = _wav()


class PilotOfflineFullLoop(unittest.TestCase):
    def test_multiple_calls_keep_ids_prefix_and_intervening_state(self):
        from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
        from tau2.domains.telecom.environment import get_environment, get_tasks
        from bridge import Bridge, GovernedEnvironment

        environment = get_environment()
        initial = get_tasks()[0].initial_state
        environment.set_state(initial.initialization_data, initial.initialization_actions,
                              initial.message_history or [])
        bridge = Bridge(ROOT.parent.parent / "target/debug/examples/voice_bridge",
                        environment.get_tools(), set(), session="multi-trace")
        governor = GovernedEnvironment(environment, bridge)
        self.addCleanup(governor.close)
        args = {"customer_id": "C1001", "line_id": "L1002"}
        calls = [
            ToolCall(id="first", name="disable_roaming", arguments=args),
            ToolCall(id="second", name="enable_roaming", arguments=args),
        ]
        initial_assistant_db = environment.tools.db.model_dump(mode="json")
        initial_user_db = environment.user_tools.db.model_dump(mode="json")
        orchestrator = SimpleNamespace(
            trajectory=[UserMessage(role="user", content="Help"),
                        AssistantMessage(role="assistant", tool_calls=calls)],
            _execute_tool_calls=lambda pending: [environment.get_response(call)
                                                  for call in pending],
        )
        pilot._install_attempt_trace(orchestrator, governor)

        results = orchestrator._execute_tool_calls(calls)

        self.assertTrue(all(not result.error for result in results))
        self.assertEqual([attempt["tool_call_id"] for attempt in governor.attempts],
                         ["first", "second"])
        self.assertTrue(all(attempt["dialogue"] == {"prefix_length": 1,
                                                     "tool_call_message_index": 1}
                            for attempt in governor.attempts))
        self.assertEqual(governor.attempts[0]["pre_attempt_state"]["assistant_db"],
                         initial_assistant_db)
        self.assertEqual(governor.attempts[0]["pre_attempt_state"]["user_db"],
                         initial_user_db)
        self.assertNotEqual(
            governor.attempts[0]["pre_attempt_state"]["assistant_db"],
            governor.attempts[1]["pre_attempt_state"]["assistant_db"],
        )
        self.assertNotEqual(
            governor.attempts[0]["pre_attempt_state"]["user_db"],
            governor.attempts[1]["pre_attempt_state"]["user_db"],
        )

    def test_all_conditions_run_with_real_tau_and_bridge(self):
        """Mocks only network LLM/audio; orchestration and PGSO stay real."""
        from tau2.data_model.message import AssistantMessage, ToolCall

        llm_requests = []
        user_calls = 0

        def generate(*, call_name, messages, **_):
            nonlocal user_calls
            payload = "\n".join(str(getattr(message, "content", "")) for message in messages)
            llm_requests.append((call_name, payload))
            if call_name == "user_simulator_response":
                user_calls += 1
                content = "Please disable roaming on my line." if user_calls % 2 else "###STOP###"
                return AssistantMessage(role="assistant", content=content)
            if any(getattr(message, "role", None) == "tool" for message in messages):
                return AssistantMessage(role="assistant", content="The request is complete.")
            return AssistantMessage(
                role="assistant",
                tool_calls=[ToolCall(
                    id="offline-disable-roaming",
                    name="disable_roaming",
                    arguments={"customer_id": "C1001", "line_id": "L1002"},
                )],
            )

        args = [
            "run", "--tau-root", "/tmp/pgso-tau-bench",
            "--bridge-binary", str(ROOT.parent.parent / "target/debug/examples/voice_bridge"),
            "--output", "PLACEHOLDER", "--max-steps", "8",
        ]
        framework_paths = {
            "--nemo-python": os.environ.get("PGSO_NEMO_PYTHON"),
            "--invariant-python": os.environ.get("PGSO_INVARIANT_PYTHON"),
        }
        for flag, path in framework_paths.items():
            if path:
                args.extend([flag, path])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pilot"
            args[args.index("PLACEHOLDER")] = str(output)
            namespace = pilot.parser().parse_args(args)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-key"}), \
                 patch("openai.OpenAI", _OpenAI), \
                 patch("tau2.agent.llm_agent.generate", side_effect=generate), \
                 patch("tau2.user.user_simulator.generate", side_effect=generate):
                self.assertEqual(pilot.run(namespace), 0)

            results = json.loads((output / "results.json").read_text())
            manifest = json.loads((output / "manifest.json").read_text())
            effective_policy = manifest["effective_policy"]
            self.assertIn("<main_policy>", effective_policy["text"])
            self.assertIn("<tech_support_policy>", effective_policy["text"])
            self.assertEqual(hashlib.sha256(effective_policy["text"].encode()).hexdigest(),
                             effective_policy["sha256"])
            expected_conditions = set(pilot.CONDITIONS)
            if framework_paths["--nemo-python"]:
                expected_conditions.add("N")
            if framework_paths["--invariant-python"]:
                expected_conditions.add("I")
            self.assertEqual(len(results), 3 * len(expected_conditions))
            self.assertEqual({result["condition"] for result in results}, expected_conditions)
            self.assertEqual({result["task_id"] for result in results}, set(pilot.TASK_IDS))
            self.assertTrue(all(len(result["attempts"]) == len(result["effects"]) == 1
                                for result in results))
            for result in results:
                attempt = result["attempts"][0]
                index = attempt["dialogue"]["tool_call_message_index"]
                self.assertEqual(attempt["dialogue"]["prefix_length"], index)
                self.assertEqual(result["messages"][index]["tool_calls"][0]["id"],
                                 attempt["tool_call_id"])
                self.assertIsInstance(attempt["pre_attempt_state"]["assistant_db"], dict)
                self.assertIsInstance(attempt["pre_attempt_state"]["user_db"], dict)
            self.assertTrue(all((output / result["condition"] /
                                 hashlib.sha256(result["task_id"].encode()).hexdigest()[:12] /
                                 "result.json").is_file() for result in results))

            # Voice data belongs to the host/agent context; simulator history stays text-only.
            serialized = json.dumps(results)
            self.assertNotIn("voice_context", serialized)
            self.assertNotIn("<voice_context>", serialized)
            context_payloads = [payload for name, payload in llm_requests
                                if name == "agent_response" and "Current host voice context:" in payload]
            self.assertTrue(context_payloads)
            self.assertTrue(all(payload.count("Current host voice context:") == 1
                                for payload in context_payloads))
            self.assertTrue(all("Current host voice context:" not in payload
                                for name, payload in llm_requests
                                if name == "user_simulator_response"))
            self.assertTrue(all("pre_attempt_state" not in payload
                                for _, payload in llm_requests))
            self.assertEqual(user_calls, 6 * len(expected_conditions))

    def test_retired_directive_is_absent_from_next_agent_prompt(self):
        from tau2.agent.llm_agent import LLMAgent
        from tau2.data_model.message import AssistantMessage, UserMessage
        from tau2.domains.telecom.environment import get_environment
        env = get_environment()
        agent = LLMAgent(env.get_tools(), env.get_policy(), "offline")
        bridge = SimpleNamespace(state={"tools": [t.name for t in env.get_tools()],
                                        "directives": ["TEMPORARY_TEST_DIRECTIVE"]})
        host = SimpleNamespace(blocked_tools=set(), policy_state=bridge.state)
        pilot._install_agent_context(agent, bridge, host, "P")
        host.voice_context = {"observation_history": [], "directives": ["stale copy"]}
        state = agent.get_init_state()
        prompts = []
        def generate(**kwargs):
            prompts.append("\n".join(m.content or "" for m in kwargs["messages"]))
            return AssistantMessage(role="assistant", content="Hello")
        with patch("tau2.agent.llm_agent.generate", side_effect=generate):
            _, state = agent.generate_next_message(UserMessage(role="user", content="Hi"), state)
            bridge.state["directives"] = []
            agent.generate_next_message(UserMessage(role="user", content="Continue"), state)
        self.assertIn("TEMPORARY_TEST_DIRECTIVE", prompts[0])
        self.assertNotIn("TEMPORARY_TEST_DIRECTIVE", prompts[1])
        self.assertNotIn("stale copy", prompts[1])


if __name__ == "__main__":
    unittest.main()
