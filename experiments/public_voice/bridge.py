"""Trusted, sequential tau host bridge; never exposed as an agent tool.

The Rust callback remains on the Runtime::call stack while Python executes the
actual sandbox effect. This is not an authorization-token/check-then-call bridge.
"""
import json
import os
from pathlib import Path
import select
import subprocess
import time


class Bridge:
    def __init__(self, binary, tools, governed_tools, session="pilot", timeout=30,
                 intervention="prune"):
        self.timeout = timeout
        self.state = {}
        self.receipts = []
        definitions = []
        for tool in tools:
            function = tool.openai_schema["function"]
            definitions.append({"name": function["name"],
                                "description": function.get("description", ""),
                                "inputSchema": function["parameters"]})
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, bufsize=1)
        try:
            response = self.request({"tools": definitions,
                                     "governed_tools": sorted(governed_tools),
                                     "session": session,
                                     "intervention": intervention})
            if not response["ok"]:
                raise RuntimeError(response.get("error", "bridge initialization failed"))
            self.config = response.get("config", {})
        except BaseException:
            self.close()
            raise

    def _send(self, value):
        self.process.stdin.write(json.dumps(value, allow_nan=False) + "\n")
        self.process.stdin.flush()

    def request(self, command, callback=None):
        self._send(command)
        callback_seen = False
        while True:
            if not select.select([self.process.stdout], [], [], self.timeout)[0]:
                self.close()
                raise TimeoutError("PGSO bridge did not respond; no request retry")
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError("PGSO bridge exited before completing the request")
            response = json.loads(line)
            if "callback" in response:
                call = response["callback"]
                if (callback_seen or callback is None or command.get("op") != "call"
                        or call != {"tool": command["name"],
                                    "arguments": command["arguments"]}):
                    self.close()
                    raise RuntimeError("unexpected or repeated callback; session terminated")
                callback_seen = True
                try:
                    self._send({"result": callback(call["tool"], call["arguments"])})
                except Exception as error:
                    self._send({"error": str(error)})
                continue
            self.state = response.get("state", self.state)
            self.receipts.extend(response.get("audit", []))
            if command.get("op") == "call":
                records = response.get("audit", [])
                reason = records[0].get("reason") if len(records) == 1 else None
                needs_callback = reason in ("completed", "callback_failed", "callback_panicked")
                if reason is None or callback_seen != needs_callback or (
                        response.get("ok") and reason != "completed"):
                    self.close()
                    raise RuntimeError("callback/receipt mismatch; execution is indeterminate")
            return response

    def approve(self, name, arguments, timestamp_ms, ttl_ms):
        """Trusted-host approval; never expose this method as an agent or transcript tool."""
        response = self.request({"op": "approve", "name": name, "arguments": arguments,
                                 "timestamp_ms": timestamp_ms, "ttl_ms": ttl_ms})
        if not response["ok"]:
            raise ValueError(response.get("error", "PGSO approval failed"))
        return response["confirmation"]

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


class FrameworkPolicy:
    """Concrete persistent NeMo/Invariant policy sidecar for one task session."""
    def __init__(self, mode, python, governed_tools, all_tools, config, stderr_path,
                 timeout=30):
        if mode not in {"nemo", "invariant"}:
            raise ValueError("framework mode must be nemo or invariant")
        executable = Path(python)
        if not executable.is_file():
            raise ValueError(f"framework Python does not exist: {executable}")
        self.mode = mode
        self.timeout = timeout
        self.state = {}
        self.audit = []
        self._stdout_buffer = b""
        worker = Path(__file__).with_name("framework_sidecar.py")
        self._stderr = Path(stderr_path).open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [str(executable), str(worker), mode], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self._stderr,
        )
        try:
            response = self.request({"op": "init", "governed_tools": sorted(governed_tools),
                                     "all_tools": sorted(all_tools), "config": config})
            self.framework = response["framework"]
        except BaseException:
            self.close()
            raise

    def request(self, command):
        try:
            self.process.stdin.write(
                (json.dumps(command, allow_nan=False) + "\n").encode())
            self.process.stdin.flush()
        except (BrokenPipeError, ValueError) as error:
            self.close()
            raise RuntimeError(f"{self.mode} policy sidecar unavailable; call denied") from error
        line = self._readline()
        if not line:
            self.close()
            raise RuntimeError(f"{self.mode} policy sidecar exited; call denied")
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.close()
            raise RuntimeError(f"unexpected {self.mode} sidecar output; call denied") from error
        if not isinstance(response, dict):
            self.close()
            raise RuntimeError(f"unexpected {self.mode} sidecar output; call denied")
        if not response.get("ok"):
            error = response.get("error", f"{self.mode} policy failed; call denied")
            self.close()
            raise RuntimeError(error)
        self.state = response.get("state", self.state)
        if "record" in response:
            self.audit.append(response["record"])
        return response

    def _readline(self):
        deadline = time.monotonic() + self.timeout
        while b"\n" not in self._stdout_buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select(
                    [self.process.stdout], [], [], remaining)[0]:
                self.close()
                raise TimeoutError(f"{self.mode} policy sidecar timed out; call denied")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                line, self._stdout_buffer = self._stdout_buffer, b""
                return line
            self._stdout_buffer += chunk
            if len(self._stdout_buffer) > 1024 * 1024:
                self.close()
                raise RuntimeError(f"{self.mode} sidecar output exceeded limit; call denied")
        line, self._stdout_buffer = self._stdout_buffer.split(b"\n", 1)
        return line

    def observe(self, event):
        return self.request({"op": "observe", **event})

    def decide(self, name, timestamp_ms):
        response = self.request({"op": "call", "name": name,
                                 "timestamp_ms": timestamp_ms})
        if response.get("action") not in {"allow", "block"}:
            self.close()
            raise RuntimeError(f"invalid {self.mode} policy decision; call denied")
        return response["action"]

    def close(self):
        if getattr(self, "process", None) is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                try:
                    stream.close()
                except BrokenPipeError:
                    pass
        if not self._stderr.closed:
            self._stderr.close()


class GovernedEnvironment:
    """Install only after tau initialization; one environment/bridge per session.

    User tools are outside agent governance. Host code and benchmark initialization
    are trusted; this is not a sandbox against arbitrary malicious Python code.
    """
    def __init__(self, environment, bridge, framework_policy=None):
        self.environment = environment
        self.bridge = bridge
        self.framework_policy = framework_policy
        self.timestamp_ms = 0
        self.effects = []
        self.attempts = []
        self._attempt_contexts = []
        self.blocked_tools = set()
        self._original = environment.make_tool_call
        environment.make_tool_call = self._call

    def observe(self, event):
        if self.framework_policy is not None:
            return self.framework_policy.observe(event)
        return self.bridge.request({"op": "observe", **event})

    @property
    def policy_state(self):
        return (self.framework_policy.state if self.framework_policy is not None
                else self.bridge.state)

    def prepare_attempts(self, tool_calls, tool_call_message_index):
        if self._attempt_contexts:
            raise RuntimeError("unconsumed tool-call trace context")
        self._attempt_contexts = [
            {"tool": call.name, "arguments": call.arguments,
             "tool_call_id": call.id or None,
             "dialogue": {"prefix_length": tool_call_message_index,
                          "tool_call_message_index": tool_call_message_index}}
            for call in tool_calls
        ]

    def _snapshot(self):
        env = self.environment
        return {
            "assistant_db": (env.tools.db.model_dump(mode="json")
                             if env.tools is not None and env.tools.db is not None else None),
            "user_db": (env.user_tools.db.model_dump(mode="json")
                        if env.user_tools is not None and env.user_tools.db is not None else None),
        }

    def _execute(self, name, arguments, requestor):
        env = self.environment
        event = {"tool": name, "arguments": arguments, "requestor": requestor,
                 "timestamp_ms": self.timestamp_ms,
                 "before": [env.get_db_hash(), env.get_user_db_hash()]}
        try:
            result = self._original(name, requestor=requestor, **arguments)
            env.sync_tools()
            if not isinstance(result, str):
                result = json.loads(env.to_json_str(result))
            event["result"] = result
            return result
        except Exception as error:
            event["error"] = str(error)
            raise
        finally:
            event["after"] = [env.get_db_hash(), env.get_user_db_hash()]
            if "error" in event:
                event["effect_status"] = ("partial_or_indeterminate" if event["after"] != event["before"]
                                          else "failed_without_observed_state_change")
            else:
                event["effect_status"] = "completed"
            self.effects.append(event)

    def _call(self, tool_name, requestor="assistant", **arguments):
        if requestor == "user":
            return self._execute(tool_name, arguments, requestor)
        if requestor != "assistant":
            raise ValueError("invalid tool requestor")
        context = (self._attempt_contexts.pop(0) if self._attempt_contexts else
                   {"tool": tool_name, "arguments": arguments,
                    "tool_call_id": None, "dialogue": None})
        if context.pop("tool") != tool_name or context.pop("arguments") != arguments:
            raise RuntimeError("tool-call trace context mismatch")
        attempt = {"tool": tool_name, "arguments": arguments,
                   "timestamp_ms": self.timestamp_ms,
                   "pre_attempt_state": self._snapshot(), **context}
        self.attempts.append(attempt)
        if tool_name in self.blocked_tools:
            attempt["error"] = "instantaneous threshold intervention"
            raise ValueError(attempt["error"])
        if self.framework_policy is not None:
            try:
                action = self.framework_policy.decide(tool_name, self.timestamp_ms)
            except Exception as error:
                attempt["error"] = str(error)
                raise
            attempt["framework_policy"] = self.framework_policy.audit[-1]
            if action == "block":
                attempt["error"] = f"{self.framework_policy.mode} policy intervention"
                raise ValueError(attempt["error"])
        response = self.bridge.request(
            {"op": "call", "name": tool_name, "arguments": arguments,
             "timestamp_ms": self.timestamp_ms},
            callback=lambda name, args: self._execute(name, args, "assistant"))
        attempt["ok"] = response["ok"]
        if not response["ok"]:
            attempt["error"] = response.get("error", "PGSO call failed")
            raise ValueError(attempt["error"])
        return response["result"]

    def close(self):
        self.environment.make_tool_call = self._original
        try:
            self.bridge.close()
        finally:
            if self.framework_policy is not None:
                self.framework_policy.close()
