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
            self.assertEqual(len(results), 12)
            self.assertEqual({result["condition"] for result in results}, set(pilot.CONDITIONS))
            self.assertEqual({result["task_id"] for result in results}, set(pilot.TASK_IDS))
            self.assertTrue(all(len(result["attempts"]) == len(result["effects"]) == 1
                                for result in results))
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
            self.assertEqual(user_calls, 24)

    def test_retired_directive_is_absent_from_next_agent_prompt(self):
        from tau2.agent.llm_agent import LLMAgent
        from tau2.data_model.message import AssistantMessage, UserMessage
        from tau2.domains.telecom.environment import get_environment
        env = get_environment()
        agent = LLMAgent(env.get_tools(), env.get_policy(), "offline")
        bridge = SimpleNamespace(state={"tools": [t.name for t in env.get_tools()],
                                        "directives": ["TEMPORARY_TEST_DIRECTIVE"]})
        host = SimpleNamespace(blocked_tools=set())
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
