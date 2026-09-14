# Protected HTTP and MCP execution

One `Runtime` owns one speaker/session, its PGSO pipeline, host permissions,
registered schemas and callbacks. Both HTTP and MCP dispatch through `Runtime::call`.
Tool discovery and the `pgso/requiresStepUp` metadata are hints; authorization is
checked again immediately before effects. Keep tool credentials in the callbacks.

| Route | Purpose |
|---|---|
| `GET /tools` | Current permitted tools and their real JSON Schemas |
| `GET /context` | Active directive blocks |
| `POST /call` | `{ "session":"...", "tool":"...", "arguments":{}, "confirmation":null }` |
| `POST /mcp` | JSON-RPC initialize, ping, tools/list and tools/call |

All routes require `Authorization: Bearer <secret>`. Supply a cryptographically
random secret of at least 32 bytes; length validation alone cannot guarantee entropy.
Browser Origin requests are rejected. Put TLS and rate limits at the deployment
boundary. This is a service-to-service adapter. MCP uses JSON responses, protocol
2025-06-18; it does not implement SSE or server notifications. Clients must refresh
`tools/list`. MCP confirmation tokens go in `params._meta["pgso/confirmation"]`.

The trusted host alone calls `observe`, `expire_before`, `set_permissions` and
`approve`. There are no network routes to mint approvals or alter policy. After
independent consent, read `runtime.host_time_ms()` under the session lock and pass
that value to `approve(request, host_time_ms, ttl_ms)`. It returns a random
256-bit token bound to the session, tool, exact arguments, policy revision and
expiry. It is consumed before dispatch, including malformed arguments and callback failures after session validation. Policy
changes revoke outstanding tokens. HTTP and trusted-host approvals share one monotonic clock anchored to UTC once.
Explicit-time Rust callers must use the same clock domain. Observed backwards
time revokes approvals and is rejected; an unobserved rollback in an external
clock cannot be detected by comparing supplied timestamps alone.

Cryptographic signatures of tool descriptions would not replace this execution
boundary. Server-held opaque confirmations provide binding without a separate
signing-key system. This does not defend against compromise of the trusted host,
leaked tool credentials, or a callback that invokes unauthorized external tools.
No model or subagent should receive a second route to execute callbacks directly.

The router holds the per-session mutex through synchronous execution. A callback
must not lock its own runtime. Run separate runtimes for separate sessions. Long
callbacks serialize that session; cancellation and distributed execution are not
implemented. Panics with unwind enabled become failed receipts; aborting processes
cannot produce an in-memory receipt. Receipts are not a durable or tamper-evident
log: the host must drain and persist execution receipts and policy transitions.

Run the local echo example with `PGSO_BEARER` set in your environment:

```sh
cargo run -p pgso-actuator-http --example server
```

It defaults to 127.0.0.1:3000 (`PGSO_BIND` overrides the address) and provides only an echo tool. It does not perform a
sales action or claim to infer emotion.

## Adapter migration

Custom `Actuator` implementations must implement `current_state` and atomic
`reconcile`: compute the full candidate state before committing it, and leave
state unchanged on error. `GovernanceState::reconcile` is the reference helper.
`RulePredicate` struct literals need the new `direction` field; `Rule::new`
preserves the previous either-direction behavior. These are source API changes.

Per-axis contributions replace only that axis's previous decisions. Valid nominal
readings retire them; missing/invalid/low-confidence readings hold them. Expiry is
an explicit host policy, never an inferred permission grant from silence. Using
`expire_before` restores nominal exposure, so choose that policy deliberately.
Static host permissions are always checked independently of prosodic policy.

Audio must use the extractor's configured sample rate and finite normalized PCM.
Readings receive subwindow timestamps. The extractor has no residual streaming
buffer: hosts must frame audio consistently. Equivalent reading sequences have
batch-independent state; arbitrary audio chunkings are not asserted equivalent.

The acoustic confidence remains a voicing-quality heuristic, not a calibrated
probability of emotion, empathy or safe action. Thresholds need validation on the
intended population. Unit tests establish implementation contracts, not superiority
against competing agents or real-world safety guarantees.
