"""Trusted, sequential tau host bridge; never exposed as an agent tool.

The Rust callback remains on the Runtime::call stack while Python executes the
actual sandbox effect. This is not an authorization-token/check-then-call bridge.
"""
import json
import select
import subprocess


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


class GovernedEnvironment:
    """Install only after tau initialization; one environment/bridge per session.

    User tools are outside agent governance. Host code and benchmark initialization
    are trusted; this is not a sandbox against arbitrary malicious Python code.
    """
    def __init__(self, environment, bridge):
        self.environment = environment
        self.bridge = bridge
        self.timestamp_ms = 0
        self.effects = []
        self.attempts = []
        self.blocked_tools = set()
        self._original = environment.make_tool_call
        environment.make_tool_call = self._call

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
        attempt = {"tool": tool_name, "arguments": arguments,
                   "timestamp_ms": self.timestamp_ms}
        self.attempts.append(attempt)
        if tool_name in self.blocked_tools:
            attempt["error"] = "instantaneous threshold intervention"
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
        self.bridge.close()
