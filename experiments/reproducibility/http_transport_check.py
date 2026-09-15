#!/usr/bin/env python3
"""Loopback HTTP/MCP wire check for the compiled PGSO server example."""

import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


def choose_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(
    port: int,
    method: str,
    path: str,
    bearer: str | None = None,
    body: Any | None = None,
    origin: str | None = None,
) -> tuple[int, Any]:
    headers: dict[str, str] = {}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    if origin is not None:
        headers["Origin"] = origin
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        if not payload:
            decoded: Any = None
        else:
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError:
                decoded = payload.decode("utf-8", errors="replace")
        return response.status, decoded
    finally:
        connection.close()


def add_case(
    cases: list[dict[str, Any]],
    name: str,
    expected_status: int,
    body_validation: str,
    result: tuple[int, Any],
    validator: Callable[[Any], bool],
) -> None:
    status, body = result
    try:
        body_pass = bool(validator(body))
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        body_pass = False
    cases.append(
        {
            "case": name,
            "expected_status": expected_status,
            "body_validation": body_validation,
            "actual_status": status,
            "actual_body": body,
            "pass": status == expected_status and body_pass,
        }
    )


def rpc(method: str, request_id: int, params: Any | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def evaluate(binary: Path) -> list[dict[str, Any]]:
    binary = binary.resolve()
    if not binary.is_file():
        raise ValueError(f"server binary does not exist: {binary}")

    port = choose_port()
    bearer = secrets.token_hex(32)
    environment = os.environ.copy()
    environment.update({"PGSO_BIND": f"127.0.0.1:{port}", "PGSO_BEARER": bearer})
    process = subprocess.Popen(
        [str(binary)],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while True:
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"server exited during startup: {stderr.strip()}")
            try:
                request(port, "GET", "/tools", bearer)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("server did not accept loopback connections within 5s")
                time.sleep(0.05)

        cases: list[dict[str, Any]] = []
        add_case(cases, "no_bearer", 401, "empty body", request(port, "GET", "/tools"), lambda body: body is None)
        add_case(cases, "wrong_bearer", 401, "empty body", request(port, "GET", "/tools", "x" * 64), lambda body: body is None)
        add_case(cases, "browser_origin", 401, "empty body", request(port, "GET", "/tools", bearer, origin="https://example.invalid"), lambda body: body is None)
        add_case(
            cases,
            "authorized_tools",
            200,
            "one echo tool with object input schema",
            request(port, "GET", "/tools", bearer),
            lambda body: isinstance(body, dict)
            and len(body.get("tools", [])) == 1
            and body["tools"][0].get("name") == "echo"
            and body["tools"][0].get("inputSchema", {}).get("type") == "object",
        )
        add_case(cases, "context", 200, "empty directives array", request(port, "GET", "/context", bearer), lambda body: body == {"directives": []})
        add_case(
            cases,
            "call_valid",
            200,
            "echoes the message argument",
            request(port, "POST", "/call", bearer, {"session": "demo", "tool": "echo", "arguments": {"message": "wire"}}),
            lambda body: body == {"message": "wire"},
        )
        add_case(
            cases,
            "call_invalid_arguments",
            400,
            "invalid arguments error",
            request(port, "POST", "/call", bearer, {"session": "demo", "tool": "echo", "arguments": {"message": 7}}),
            lambda body: body == {"error": "invalid arguments"},
        )
        add_case(
            cases,
            "call_wrong_session",
            400,
            "wrong session error",
            request(port, "POST", "/call", bearer, {"session": "other", "tool": "echo", "arguments": {"message": "wire"}}),
            lambda body: body == {"error": "wrong session"},
        )
        add_case(
            cases,
            "call_unknown_tool",
            400,
            "permission denied error",
            request(port, "POST", "/call", bearer, {"session": "demo", "tool": "missing", "arguments": {}}),
            lambda body: body == {"error": "permission denied"},
        )
        add_case(
            cases,
            "mcp_initialize",
            200,
            "JSON-RPC result advertises protocol 2025-06-18 and pgso server",
            request(port, "POST", "/mcp", bearer, rpc("initialize", 1, {})),
            lambda body: body.get("jsonrpc") == "2.0"
            and body.get("id") == 1
            and body.get("result", {}).get("protocolVersion") == "2025-06-18"
            and body.get("result", {}).get("serverInfo", {}).get("name") == "pgso",
        )
        add_case(
            cases,
            "mcp_tools_list",
            200,
            "JSON-RPC result lists echo",
            request(port, "POST", "/mcp", bearer, rpc("tools/list", 2)),
            lambda body: body.get("jsonrpc") == "2.0"
            and body.get("id") == 2
            and len(body.get("result", {}).get("tools", [])) == 1
            and body["result"]["tools"][0].get("name") == "echo",
        )
        add_case(
            cases,
            "mcp_tools_call",
            200,
            "JSON-RPC tool result isError=false and contains echoed JSON text",
            request(port, "POST", "/mcp", bearer, rpc("tools/call", 3, {"name": "echo", "arguments": {"message": "mcp"}})),
            lambda body: body.get("jsonrpc") == "2.0"
            and body.get("id") == 3
            and body.get("result", {}).get("isError") is False
            and json.loads(body["result"]["content"][0]["text"]) == {"message": "mcp"},
        )
        add_case(
            cases,
            "mcp_invalid_tool_arguments",
            200,
            "JSON-RPC tool result isError=true with validation error",
            request(port, "POST", "/mcp", bearer, rpc("tools/call", 4, {"name": "echo", "arguments": {"message": 7}})),
            lambda body: body.get("jsonrpc") == "2.0"
            and body.get("id") == 4
            and body.get("result", {}).get("isError") is True
            and body["result"]["content"][0].get("text") == "invalid arguments",
        )
        add_case(
            cases,
            "mcp_unknown_method",
            200,
            "JSON-RPC -32601 Method not found error",
            request(port, "POST", "/mcp", bearer, rpc("unknown/method", 5)),
            lambda body: body.get("jsonrpc") == "2.0"
            and body.get("id") == 5
            and body.get("error") == {"code": -32601, "message": "Method not found"},
        )

        if not all(case["pass"] for case in cases):
            raise AssertionError("one or more HTTP/MCP transport checks failed")
        return cases
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: http_transport_check.py SERVER_BINARY")
    print(json.dumps(evaluate(Path(sys.argv[1])), separators=(",", ":")))
