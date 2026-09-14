//! Single-session execution boundary. Hosts retain exclusive access to policy and approval methods.
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
    /// Completed, denied, or failed.
    pub outcome: String,
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
            log: Vec::new(),
        })
    }

    /// Current session identity.
    pub fn session(&self) -> &str {
        &self.session
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

    fn check(&self, request: &CallRequest) -> Result<bool, String> {
        if request.session != self.session {
            return Err("wrong session".into());
        }
        let id = ToolId::from(request.tool.clone());
        if !self.allowed.contains(&id) {
            return Err("permission denied".into());
        }
        let catalog = self.pipeline.current_catalog();
        let tool = catalog.find(&id).ok_or("tool currently unavailable")?;
        let registered = self.tools.get(&id).ok_or("unknown tool")?;
        if !registered.validator.is_valid(&request.arguments) {
            return Err("invalid arguments".into());
        }
        Ok(tool.requires_step_up)
    }

    /// Trusted approval channel only: call after independently obtaining user consent.
    /// A caller-provided `confirmed: true` is never accepted by the agent API.
    /// `now_ms` is trusted host time, not agent input.
    ///
    /// # Errors
    /// Rejects unauthorized requests, overflow, zero TTL or a full pending-approval queue.
    pub fn approve(
        &mut self,
        request: &CallRequest,
        now_ms: u64,
        ttl_ms: u64,
    ) -> Result<String, String> {
        self.check(request)?;
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
        let result = (|| {
            let needs_confirmation = self.check(&request)?;
            if needs_confirmation {
                let approval = request
                    .confirmation
                    .as_ref()
                    .and_then(|token| self.approvals.remove(token))
                    .ok_or("confirmation required")?;
                if approval.tool != request.tool
                    || approval.arguments != request.arguments
                    || approval.revision != self.revision
                    || now_ms >= approval.expires_at
                {
                    return Err("invalid or expired confirmation".into());
                }
            }
            outcome = "failed";
            let registered = self
                .tools
                .get(&ToolId::from(request.tool.clone()))
                .ok_or("unknown tool")?;
            let value = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                (registered.binding.handler)(&request.arguments)
            }))
            .map_err(|_| "tool callback panicked".to_string())??;
            outcome = "completed";
            Ok(value)
        })();
        self.log.push(ExecutionRecord {
            session: self.session.clone(),
            tool: request.tool,
            timestamp_ms: now_ms,
            revision: self.revision,
            outcome: outcome.into(),
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
