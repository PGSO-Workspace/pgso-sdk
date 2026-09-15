//! Single-session execution boundary. Hosts retain exclusive access to policy and approval methods.
use crate::HostClock;
use pgso_actuator_mcp::{McpActuator, ToolDefinition};
use pgso_core::{AudioWindow, Pgso, Signal, ToolId};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet};
use std::time::Instant;

/// Agent-supplied request. Confirmation is an opaque host-issued, single-use token.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CallRequest {
    /// Must equal the runtime's authenticated session.
    pub session: String,
    /// Stable tool identifier.
    pub tool: String,
    /// Arguments checked against the registered schema.
    pub arguments: Value,
    /// Issued only through the trusted host approval API.
    #[serde(default)]
    pub confirmation: Option<String>,
}

/// Registered callback; keep credentials inside this host-owned function.
pub type ToolHandler = Box<dyn Fn(&Value) -> Result<Value, String> + Send + Sync>;

/// Tool implementation and its authoritative argument schema.
pub struct ToolBinding {
    /// Must refer to a nominal catalog tool.
    pub id: ToolId,
    /// Shared discovery and validation metadata.
    pub definition: ToolDefinition,
    /// Synchronous dispatch; do not call back into the same locked runtime.
    pub handler: ToolHandler,
}

struct RegisteredTool {
    binding: ToolBinding,
    validator: jsonschema::Validator,
}
struct Approval {
    tool: String,
    arguments: Value,
    revision: u64,
    expires_at: u64,
}

#[derive(Debug, Clone, Copy)]
enum AuthorizationError {
    WrongSession,
    PermissionDenied,
    ToolUnavailable,
    UnknownTool,
    InvalidArguments,
}

impl AuthorizationError {
    fn message(self) -> &'static str {
        match self {
            Self::WrongSession => "wrong session",
            Self::PermissionDenied => "permission denied",
            Self::ToolUnavailable => "tool currently unavailable",
            Self::UnknownTool => "unknown tool",
            Self::InvalidArguments => "invalid arguments",
        }
    }

    fn reason(self) -> ExecutionReason {
        match self {
            Self::WrongSession => ExecutionReason::WrongSession,
            Self::PermissionDenied => ExecutionReason::PermissionDenied,
            Self::ToolUnavailable => ExecutionReason::ToolUnavailable,
            Self::UnknownTool => ExecutionReason::InternalUnknown,
            Self::InvalidArguments => ExecutionReason::InvalidArguments,
        }
    }
}

/// Bounded diagnostic reason for an execution receipt.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionReason {
    /// Callback completed successfully.
    Completed,
    /// Trusted host time moved backwards.
    ClockRollback,
    /// Request did not match the authenticated session.
    WrongSession,
    /// Host permissions excluded the tool.
    PermissionDenied,
    /// Current policy excluded the tool.
    ToolUnavailable,
    /// Arguments did not match the registered schema.
    InvalidArguments,
    /// A required confirmation was absent, revoked, or otherwise unavailable.
    ConfirmationMissing,
    /// Presented confirmation did not match the request or had expired.
    InvalidOrExpiredConfirmation,
    /// Callback returned an error.
    CallbackFailed,
    /// Callback panicked.
    CallbackPanicked,
    /// An unexpected internal dispatch inconsistency occurred.
    InternalUnknown,
}

/// Execution receipt. No bearer or confirmation secrets are logged.
#[derive(Debug, Clone, Serialize)]
pub struct ExecutionRecord {
    /// Authenticated runtime session, not an untrusted request value.
    pub session: String,
    /// Requested tool.
    pub tool: String,
    /// Trusted host time.
    pub timestamp_ms: u64,
    /// Policy revision checked at dispatch.
    pub revision: u64,
    /// Number of committed policy transitions visible at dispatch.
    pub policy_transition_count: usize,
    /// Completed, denied, or failed.
    pub outcome: String,
    /// Bounded reason that excludes arguments, tokens, and callback errors.
    pub reason: ExecutionReason,
    /// Timing of authorization plus execution, in microseconds.
    pub duration_us: u128,
}

/// One speaker/session, with exclusive mutable dispatch and policy update APIs.
/// All actual calls must pass through this object; tool metadata alone is not authorization.
pub struct Runtime<S: Signal> {
    session: String,
    pipeline: Pgso<S, McpActuator>,
    tools: HashMap<ToolId, RegisteredTool>,
    allowed: HashSet<ToolId>,
    approvals: HashMap<String, Approval>,
    revision: u64,
    last_host_time: Option<u64>,
    clock: HostClock,
    log: Vec<ExecutionRecord>,
}

impl<S: Signal> Runtime<S> {
    /// Register callbacks and compile schemas without HTTP/filesystem reference resolution.
    ///
    /// # Errors
    /// Rejects duplicate/missing tools, unknown permissions, invalid schemas or empty sessions.
    pub fn new(
        session: String,
        pipeline: Pgso<S, McpActuator>,
        bindings: Vec<ToolBinding>,
        allowed: HashSet<ToolId>,
    ) -> Result<Self, String> {
        if session.is_empty() {
            return Err("empty session".into());
        }
        let catalog = pipeline.current_catalog();
        let mut tools = HashMap::new();
        for binding in bindings {
            if !catalog.contains(&binding.id) || tools.contains_key(&binding.id) {
                return Err("unknown or duplicate tool binding".into());
            }
            if binding.definition.input_schema.get("type") != Some(&json!("object")) {
                return Err("tool schema must have object type".into());
            }
            let validator = jsonschema::validator_for(&binding.definition.input_schema)
                .map_err(|e| e.to_string())?;
            tools.insert(binding.id.clone(), RegisteredTool { binding, validator });
        }
        if tools.len() != catalog.len() || allowed.iter().any(|id| !tools.contains_key(id)) {
            return Err("incomplete bindings or unknown permission".into());
        }
        Ok(Self {
            session,
            pipeline,
            tools,
            allowed,
            approvals: HashMap::new(),
            revision: 0,
            last_host_time: None,
            clock: HostClock::new()?,
            log: Vec::new(),
        })
    }

    /// Current session identity.
    pub fn session(&self) -> &str {
        &self.session
    }

    /// Return trusted host time from the same monotonic clock used by HTTP.
    /// Hosts approving a request for this runtime should pass this value to
    /// [`Runtime::approve`] so both APIs share one time domain.
    pub fn host_time_ms(&self) -> Result<u64, String> {
        self.clock.now_ms()
    }

    /// Active directives for the agent host to include as a distinct context block.
    pub fn directives(&self) -> &[String] {
        self.pipeline.actuator().directives()
    }

    /// Discovery is filtered by both the active policy and host permissions.
    pub fn tools_list(&self) -> Value {
        let tools: Vec<_> = self
            .pipeline
            .current_catalog()
            .tools()
            .iter()
            .filter(|tool| self.allowed.contains(&tool.id))
            .filter_map(|tool| {
                self.tools.get(&tool.id).map(|registered| {
                    json!({
                        "name": tool.id.as_str(), "title": tool.name,
                        "description": registered.binding.definition.description,
                        "inputSchema": registered.binding.definition.input_schema,
                        "_meta": {"pgso/requiresStepUp":tool.requires_step_up}
                    })
                })
            })
            .collect();
        json!({"tools":tools})
    }

    fn revoke_approvals(&mut self) {
        self.approvals.clear();
        self.revision = self.revision.saturating_add(1);
    }

    /// Observe trusted host time and reject a rollback. A rollback revokes all
    /// approvals before returning, so an old timestamp cannot extend consent.
    fn observe_host_time(&mut self, now_ms: u64) -> Result<(), String> {
        if self.last_host_time.is_some_and(|last| now_ms < last) {
            self.revoke_approvals();
            return Err("host clock moved backwards".into());
        }
        self.last_host_time = Some(now_ms);
        Ok(())
    }

    /// Trusted host audio path. Agent-facing HTTP routes cannot set observations.
    ///
    /// # Errors
    /// Propagates the pipeline error; any already committed transitions revoke approvals.
    pub fn observe(&mut self, audio: &AudioWindow) -> Result<(), String> {
        let before = self.pipeline.audit_log().transitions().len();
        let result = self
            .pipeline
            .process_window(audio)
            .map(|_| ())
            .map_err(|e| e.to_string());
        if self.pipeline.audit_log().transitions().len() != before {
            self.revoke_approvals();
        }
        result
    }

    /// Expire stale contributions under the same exclusive access as dispatch.
    ///
    /// # Errors
    /// Propagates reconciliation errors without granting permission.
    pub fn expire_before(&mut self, cutoff_ms: u64) -> Result<(), String> {
        let before = self.pipeline.audit_log().transitions().len();
        self.pipeline
            .expire_before(cutoff_ms)
            .map_err(|e| e.to_string())?;
        if self.pipeline.audit_log().transitions().len() != before {
            self.revoke_approvals();
        }
        Ok(())
    }

    /// Change host permissions; every outstanding confirmation is revoked.
    ///
    /// # Errors
    /// Unknown permissions are rejected without changing the existing policy.
    pub fn set_permissions(&mut self, allowed: HashSet<ToolId>) -> Result<(), String> {
        if allowed.iter().any(|id| !self.tools.contains_key(id)) {
            return Err("unknown tool".into());
        }
        self.allowed = allowed;
        self.revoke_approvals();
        Ok(())
    }

    fn check_session(&self, request: &CallRequest) -> Result<(), AuthorizationError> {
        if request.session != self.session {
            return Err(AuthorizationError::WrongSession);
        }
        Ok(())
    }

    fn check(&self, request: &CallRequest) -> Result<bool, AuthorizationError> {
        self.check_session(request)?;
        let id = ToolId::from(request.tool.clone());
        if !self.allowed.contains(&id) {
            return Err(AuthorizationError::PermissionDenied);
        }
        let catalog = self.pipeline.current_catalog();
        let tool = catalog
            .find(&id)
            .ok_or(AuthorizationError::ToolUnavailable)?;
        let registered = self.tools.get(&id).ok_or(AuthorizationError::UnknownTool)?;
        if !registered.validator.is_valid(&request.arguments) {
            return Err(AuthorizationError::InvalidArguments);
        }
        Ok(tool.requires_step_up)
    }

    /// Trusted approval channel only: call after independently obtaining user consent.
    /// A caller-provided `confirmed: true` is never accepted by the agent API.
    /// `now_ms` is trusted, monotonic host time, not agent input. The runtime
    /// rejects observed rollback and revokes outstanding approvals; an
    /// unobserved rollback cannot be detected through this Rust API alone.
    ///
    /// # Errors
    /// Rejects unauthorized requests, overflow, zero TTL or a full pending-approval queue.
    pub fn approve(
        &mut self,
        request: &CallRequest,
        now_ms: u64,
        ttl_ms: u64,
    ) -> Result<String, String> {
        self.observe_host_time(now_ms)?;
        self.check(request).map_err(|error| error.message())?;
        let expires_at = now_ms
            .checked_add(ttl_ms)
            .filter(|&end| end > now_ms)
            .ok_or("invalid approval TTL")?;
        self.approvals
            .retain(|_, approval| approval.expires_at > now_ms);
        if self.approvals.len() >= 128 {
            return Err("too many pending approvals".into());
        }
        let bytes: [u8; 32] = rand::random();
        let token: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
        self.approvals.insert(
            token.clone(),
            Approval {
                tool: request.tool.clone(),
                arguments: request.arguments.clone(),
                revision: self.revision,
                expires_at,
            },
        );
        Ok(token)
    }

    /// Validate and execute against the current policy, consuming confirmation before effects.
    /// A failed callback also consumes its confirmation; retries need fresh consent.
    ///
    /// # Errors
    /// Rejects invalid/unauthorized calls and callback failures; each attempt gets a receipt.
    pub fn call(&mut self, request: CallRequest, now_ms: u64) -> Result<Value, String> {
        let start = Instant::now();
        let mut outcome = "denied";
        let mut reason = ExecutionReason::InternalUnknown;
        let result = (|| {
            if let Err(error) = self.observe_host_time(now_ms) {
                reason = ExecutionReason::ClockRollback;
                return Err(error);
            }
            // A token is single-use even when the presented arguments fail
            // schema validation. Session identity is checked first so a token
            // cannot be consumed by a request from another session.
            if let Err(error) = self.check_session(&request) {
                reason = error.reason();
                return Err(error.message().into());
            }
            let presented = request
                .confirmation
                .as_ref()
                .and_then(|token| self.approvals.remove(token));
            let needs_confirmation = self.check(&request).map_err(|error| {
                reason = error.reason();
                error.message().to_string()
            })?;
            if needs_confirmation {
                let approval = presented.ok_or_else(|| {
                    reason = ExecutionReason::ConfirmationMissing;
                    "confirmation required".to_string()
                })?;
                if approval.tool != request.tool
                    || approval.arguments != request.arguments
                    || approval.revision != self.revision
                    || now_ms >= approval.expires_at
                {
                    reason = ExecutionReason::InvalidOrExpiredConfirmation;
                    return Err("invalid or expired confirmation".into());
                }
            }
            outcome = "failed";
            let registered = self
                .tools
                .get(&ToolId::from(request.tool.clone()))
                .ok_or_else(|| {
                    reason = ExecutionReason::InternalUnknown;
                    "unknown tool".to_string()
                })?;
            let value = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                (registered.binding.handler)(&request.arguments)
            }))
            .map_err(|_| {
                reason = ExecutionReason::CallbackPanicked;
                "tool callback panicked".to_string()
            })?
            .inspect_err(|_| {
                reason = ExecutionReason::CallbackFailed;
            })?;
            outcome = "completed";
            reason = ExecutionReason::Completed;
            Ok(value)
        })();
        self.log.push(ExecutionRecord {
            session: self.session.clone(),
            tool: request.tool,
            timestamp_ms: now_ms,
            revision: self.revision,
            policy_transition_count: self.pipeline.audit_log().transitions().len(),
            outcome: outcome.into(),
            reason,
            duration_us: start.elapsed().as_micros(),
        });
        result
    }

    /// Move execution receipts to host storage; secrets are excluded.
    pub fn drain_execution_log(&mut self) -> Vec<ExecutionRecord> {
        std::mem::take(&mut self.log)
    }

    /// Policy transitions for persistence by the trusted host.
    pub fn policy_audit(&self) -> &pgso_core::AuditLog {
        self.pipeline.audit_log()
    }
}
