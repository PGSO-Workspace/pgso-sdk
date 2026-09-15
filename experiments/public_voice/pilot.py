#!/usr/bin/env python3
"""Metered TAU telecom pilot for a post-utterance OpenAI TTS/ASR cascade.

This development pilot intentionally reports only TAU's original live
environment assertions.  It does not replay attempted tool calls or use the
task's golden actions when scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import sys
import time
import wave
from pathlib import Path


TAU_COMMIT = "2174a603f6d014ef94473ffa95957f6ce27100db"
CONDITIONS = ("B0", "B1", "T", "P")
TASK_IDS = (
    "[mobile_data_issue]user_abroad_roaming_enabled_off[PERSONA:None]",
    "[mobile_data_issue]user_abroad_roaming_disabled_on[PERSONA:Easy]",
    "[mobile_data_issue]user_abroad_roaming_disabled_off[PERSONA:Hard]",
)
GOVERNED_TOOLS = {
    "suspend_line",
    "resume_line",
    "send_payment_request",
    "enable_roaming",
    "disable_roaming",
    "refuel_data",
}
THRESHOLD = 0.7
MIN_CONFIDENCE = 0.5
RUNTIME_CONFIG = {
    "sample_rate": 16000, "window_samples": 12800, "window_ms": 800, "hop_ms": 400,
    "population_prior": 0.5, "ema_alpha": 0.0, "confidence_threshold": MIN_CONFIDENCE,
    "deviation_threshold": 0.2, "hysteresis_window": 2, "warmup_readings": 0,
    "max_gap_ms": 1200, "stale_after_ms": 1200, "direction": "Rising",
}
ADAPTATION_OBJECTIVE = (
    "The host supplies uncertain acoustic observations, not emotion or intent labels. "
    "Follow the domain policy and explicit user requests. When positive arousal deviation "
    "from population_prior reaches deviation_threshold with adequate confidence for "
    "hysteresis_window consecutive windows, ask for clarification before the governed "
    "tools. Resume ordinary assistance when the deviation ends or observations expire. "
    "Use the supplied configuration and timestamps. Do not invent permission or diagnosis."
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="make metered API calls and run the pilot")
    run.add_argument("--tau-root", type=Path, default=Path("/tmp/pgso-tau-bench"))
    run.add_argument("--bridge-binary", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--agent-model", default="openai/gpt-4.1-mini-2025-04-14")
    run.add_argument("--user-model", default="openai/gpt-4.1-mini-2025-04-14")
    run.add_argument("--tts-model", default="gpt-4o-mini-tts")
    run.add_argument("--tts-voice", default="alloy")
    run.add_argument("--asr-model", default="gpt-4o-mini-transcribe")
    run.add_argument("--seed", type=int, default=20260915)
    run.add_argument("--max-steps", type=int, default=30)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pcm16_mono_16k(wav_bytes: bytes) -> list[float]:
    import audioop
    with wave.open(io.BytesIO(wav_bytes), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        frames = source.readframes(source.getnframes())
    if width != 2:
        raise RuntimeError(f"TTS returned unsupported {width * 8}-bit WAV")
    if channels == 2:
        frames = audioop.tomono(frames, width, 0.5, 0.5)
    elif channels != 1:
        raise RuntimeError(f"TTS returned unsupported {channels}-channel WAV")
    if rate != 16000:
        frames, _ = audioop.ratecv(frames, width, 1, rate, 16000, None)
    samples = [sample / 32768.0 for sample in memoryview(frames).cast("h")]
    if len(samples) < 12800:
        return [0.0] * (12800 - len(samples)) + samples
    return samples


def _instantaneous_blocks(readings: list[dict]) -> bool:
    from ctypes import c_float
    for reading in readings:
        axis = str(reading.get("axis", "")).lower()
        value = reading.get("value")
        confidence = reading.get("confidence", 1.0)
        if axis == "arousal" and value is not None and confidence is not None:
            if (float(confidence) >= MIN_CONFIDENCE and
                    c_float(c_float(float(value)).value - 0.5).value >= c_float(0.2).value):
                return True
    return False


def _live_assertions(environment, task) -> dict:
    assertions = list(task.evaluation_criteria.env_assertions or [])
    if not assertions or [str(b.value) for b in task.evaluation_criteria.reward_basis] != ["ENV_ASSERTION"]:
        raise ValueError("pilot requires nonempty ENV_ASSERTION-only tasks")
    checks = [
        {
            "assertion": assertion.model_dump(mode="json"),
            "met": environment.run_env_assertion(assertion, raise_assertion_error=False),
        }
        for assertion in assertions
    ]
    return {
        "component": "live_original_environment_assertions_only",
        "score": float(all(check["met"] for check in checks)),
        "checks": checks,
    }


def _install_attempt_trace(orchestrator, governor):
    """Attach audit-only state and dialogue locators immediately before tool dispatch."""
    execute = orchestrator._execute_tool_calls

    def traced(tool_calls):
        message_index = len(orchestrator.trajectory) - 1
        assistant_calls = [call for call in tool_calls if call.requestor == "assistant"]
        governor.prepare_attempts(assistant_calls, message_index)
        return execute(tool_calls)

    orchestrator._execute_tool_calls = traced


def _install_agent_context(agent, bridge, governor, condition):
    from tau2.data_model.message import SystemMessage
    original = agent.generate_next_message
    nominal_tools = list(agent.tools)
    base_messages = None
    governor.voice_context = None

    def generate_next_message(message, state):
        nonlocal base_messages
        if base_messages is None:
            base_messages = list(state.system_messages)
        state.system_messages = list(base_messages)
        context = governor.voice_context
        if context is not None:
            if condition == "P":
                context = {**context, "directives": bridge.state["directives"]}
            objective = (ADAPTATION_OBJECTIVE if condition in ("B1", "P") else
                         "Follow the domain policy and the current host clarification directive. "
                         "Acoustic observations are uncertain, not emotion or intent labels.")
            state.system_messages.append(SystemMessage(role="system", content=(
                objective + "\nCurrent host voice context:\n" +
                json.dumps(context, separators=(",", ":")))))
        if condition == "P":
            assert not governor.blocked_tools
        available = set(bridge.state["tools"]) - governor.blocked_tools
        agent.tools = [tool for tool in nominal_tools if tool.name in available]
        return original(message, state)

    agent.generate_next_message = generate_next_message


def _install_cascade(user, client, bridge, governor, condition: str, output: Path, models: dict):
    original = user.generate_next_message
    utterance = 0
    audio_timestamp_ms = 0
    observation_history = []

    def generate_next_message(message, state):
        nonlocal utterance, audio_timestamp_ms
        user_message, next_state = original(message, state)
        if user_message.is_tool_call() or not user_message.content or user.is_stop(user_message):
            return user_message, next_state
        utterance += 1
        stem = output / f"utterance-{utterance:03d}"
        original_text = str(user_message.content)
        speech = client.audio.speech.create(
            model=models["tts"], voice=models["voice"], input=original_text,
            response_format="wav",
        )
        wav_bytes = speech.read()
        wav_path = stem.with_suffix(".original.wav")
        wav_path.write_bytes(wav_bytes)
        utterance_observations = []
        samples = _pcm16_mono_16k(wav_bytes)
        start_ms = audio_timestamp_ms + (400 if utterance > 1 else 0)
        starts = list(range(0, len(samples) - 12800 + 1, 6400))
        if starts[-1] != len(samples) - 12800:
            starts.append(len(samples) - 12800)
        for start in starts:
            audio_timestamp_ms = start_ms + (start + 12800) // 16
            extract = bridge.request({"op": "extract", "samples": samples[start:start + 12800],
                                      "timestamp_ms": audio_timestamp_ms})
            if not extract.get("ok"):
                raise RuntimeError(extract.get("error", "voice extraction failed"))
            readings = extract.get("result", [])
            if not isinstance(readings, list):
                raise RuntimeError("voice extractor returned no readings list")
            event = {"timestamp_ms": audio_timestamp_ms, "readings": readings}
            utterance_observations.append(event)
            observation_history.append(event)
            if condition == "P":
                observed = bridge.request({"op": "observe", **event})
                if not observed.get("ok"):
                    raise RuntimeError(observed.get("error", "PGSO observation failed"))
        governor.timestamp_ms = audio_timestamp_ms
        threshold_active = bool(utterance_observations and
                                _instantaneous_blocks(utterance_observations[-1]["readings"]))
        governor.blocked_tools = GOVERNED_TOOLS if condition == "T" and threshold_active else set()
        audio_file = io.BytesIO(wav_bytes)
        audio_file.name = "utterance.wav"
        transcription = client.audio.transcriptions.create(model=models["asr"], file=audio_file)
        transcribed_text = transcription.text
        if not transcribed_text.strip():
            raise RuntimeError("ASR returned an empty transcript")
        # The simulator keeps its own generated utterance; do not mutate its history.
        user_message = user_message.model_copy(deep=True)
        user_message.content = transcribed_text
        voice_context = None
        if condition in ("B1", "P"):
            voice_context = {"fixed_voice_config": RUNTIME_CONFIG,
                             "observation_history": observation_history,
                             "governed_tools": sorted(GOVERNED_TOOLS)}
            if condition == "P":
                voice_context["directives"] = bridge.state.get("directives", [])
        elif condition == "T" and threshold_active:
            voice_context = {
                "directive": "Ask the user for clarification before continuing.",
                "instantaneous_threshold": {"value": THRESHOLD,
                                            "min_confidence": MIN_CONFIDENCE},
            }
        governor.voice_context = voice_context
        stem.with_suffix(".original.txt").write_text(original_text + "\n")
        stem.with_suffix(".transcribed.txt").write_text(transcribed_text + "\n")
        stem.with_suffix(".json").write_text(json.dumps({
            "timestamp_ms": audio_timestamp_ms,
            "original_transcript": original_text,
            "transcribed_transcript": transcribed_text,
            "audio": wav_path.name,
            "observations": utterance_observations,
        }, indent=2) + "\n")
        return user_message, next_state

    user.generate_next_message = generate_next_message


def run(args: argparse.Namespace) -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required; no pilot results were created")
    if not args.bridge_binary.is_file():
        raise SystemExit(f"bridge binary does not exist: {args.bridge_binary}")
    if args.output.exists():
        raise SystemExit(f"output already exists: {args.output}")
    if args.max_steps < 1 or args.max_steps > 200:
        raise SystemExit("max-steps must be between 1 and 200")
    if not (args.tau_root / "src" / "tau2").is_dir():
        raise SystemExit(f"TAU source tree does not exist: {args.tau_root}")
    import subprocess
    tau_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=args.tau_root, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    if tau_commit != TAU_COMMIT:
        raise SystemExit(f"TAU commit must be {TAU_COMMIT}, found {tau_commit}")
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                           cwd=args.tau_root, check=True, text=True, capture_output=True)
    if dirty.stdout.strip():
        raise SystemExit("TAU tracked files differ from the pinned revision")

    sys.path.insert(0, str(args.tau_root / "src"))
    from openai import OpenAI
    from tau2.agent.llm_agent import LLMAgent
    from tau2.orchestrator.orchestrator import Orchestrator
    from tau2.runner import build_environment, build_user, get_tasks
    from bridge import Bridge, GovernedEnvironment

    tasks_by_id = {task.id: task for task in get_tasks("telecom", task_split_name="small")}
    tasks = [tasks_by_id[task_id] for task_id in TASK_IDS]
    for task in tasks:
        if not task.evaluation_criteria.env_assertions or task.evaluation_criteria.reward_basis != ["ENV_ASSERTION"]:
            raise SystemExit("pilot fixture must have ENV_ASSERTION-only outcomes")
    args.output.mkdir(parents=True)
    models = {"agent": args.agent_model, "user": args.user_model,
              "tts": args.tts_model, "voice": args.tts_voice, "asr": args.asr_model}
    manifest = {
        "scope": "development pilot; live original environment assertions only",
        "tau_commit": tau_commit,
        "bridge_binary": {"path": str(args.bridge_binary.resolve()), "sha256": _sha256(args.bridge_binary)},
        "conditions": list(CONDITIONS), "task_ids": list(TASK_IDS), "models": models,
        "seed": args.seed, "max_steps": args.max_steps,
        "temperature": 0, "runtime_config": RUNTIME_CONFIG,
        "instantaneous_threshold": THRESHOLD,
        "governed_tools": sorted(GOVERNED_TOOLS),
        "python": sys.version,
        "dependencies": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "source_hashes": {p.name: _sha256(p) for p in (Path(__file__), Path(__file__).with_name("bridge.py"))},
        "task_hashes": {task.id: hashlib.sha256(task.model_dump_json().encode()).hexdigest() for task in tasks},
        "telecom_file_hashes": {str(p.relative_to(args.tau_root)): _sha256(p)
                               for p in sorted((args.tau_root / "data/tau2/domains/telecom").rglob("*")) if p.is_file()},
        "clock": "concatenated user audio with 400ms inter-utterance gap; no processing or assistant-speech time",
        "scope_limitations": ["input-voice cascade only; agent responses are text",
                              "three development tasks; not a powered confirmatory study",
                              "no independent intervention-appropriateness labels",
                              "B1/P contrast bundles representation and orchestration",
                              "NeMo/Invariant portability not executed here"],
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    client = OpenAI()
    results = []
    condition_order = list(CONDITIONS)
    import random
    random.Random(args.seed).shuffle(condition_order)
    manifest["execution_order"] = condition_order
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for condition in condition_order:
        for task in tasks:
            run_dir = args.output / condition / hashlib.sha256(task.id.encode()).hexdigest()[:12]
            run_dir.mkdir(parents=True)
            environment = build_environment("telecom")
            agent = LLMAgent(environment.get_tools(), environment.get_policy(), models["agent"],
                             {"temperature": 0})
            user = build_user("user_simulator", environment, task, llm=models["user"],
                              llm_args={"temperature": 0})
            orchestrator = Orchestrator("telecom", agent, user, environment, task,
                                        max_steps=args.max_steps, seed=args.seed)
            from tau2.utils.utils import get_now
            orchestrator._run_start_time = get_now()
            orchestrator._run_start_perf = time.perf_counter()
            orchestrator.initialize()
            bridge = Bridge(args.bridge_binary, environment.get_tools(),
                            GOVERNED_TOOLS if condition == "P" else set(),
                            session=f"{condition}:{task.id}")
            governor = GovernedEnvironment(environment, bridge)
            if bridge.config != RUNTIME_CONFIG:
                governor.close()
                raise RuntimeError("Rust/Python pilot configuration mismatch")
            _install_agent_context(agent, bridge, governor, condition)
            _install_cascade(user, client, bridge, governor, condition, run_dir, models)
            _install_attempt_trace(orchestrator, governor)
            try:
                while not orchestrator.done:
                    orchestrator.step()
                    orchestrator._check_termination()
                simulation = orchestrator._finalize()
                score = _live_assertions(environment, task)
                result = {
                    "condition": condition, "task_id": task.id, "seed": args.seed,
                    "termination_reason": simulation.termination_reason,
                    "score": score,
                    "attempts": governor.attempts, "effects": governor.effects,
                    "pgso_state": bridge.state, "pgso_audit": bridge.receipts,
                    "messages": [message.model_dump(mode="json") for message in simulation.messages],
                }
                (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                results.append(result)
            except Exception as error:
                failure = {"condition": condition, "task_id": task.id,
                           "status": "execution_failed_not_a_task_score", "error": str(error),
                           "attempts": governor.attempts, "effects": governor.effects,
                           "pgso_audit": bridge.receipts,
                           "messages": [m.model_dump(mode="json") for m in orchestrator.trajectory]}
                (run_dir / "failure.json").write_text(json.dumps(failure, indent=2) + "\n")
                raise
            finally:
                governor.close()
    (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(args.output)
    return 0


def main() -> int:
    args = parser().parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
