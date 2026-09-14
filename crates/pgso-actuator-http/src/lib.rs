//! Session-scoped HTTP and MCP adapters over one execution boundary.
//!
//! Mount one router per isolated speaker/session. The bearer credential is held
//! by the trusted agent host, never put in model context. Policy updates and
//! approval issuance are Rust host APIs, deliberately not agent-facing routes.
//! Use TLS at the deployment boundary. Tools must not expose bypass credentials.
#![deny(missing_docs)]
mod runtime;
pub use runtime::{CallRequest, ExecutionRecord, Runtime, ToolBinding, ToolHandler};

use axum::{
    extract::{DefaultBodyLimit, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use pgso_core::Signal;
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use subtle::ConstantTimeEq;

struct HttpState<S: Signal> {
    runtime: Arc<Mutex<Runtime<S>>>,
    bearer: String,
}

#[derive(Clone, Copy)]
pub(crate) struct HostClock {
    epoch_ms: u64,
    started: Instant,
}

impl HostClock {
    pub(crate) fn new() -> Result<Self, String> {
        let duration = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| "invalid host clock")?;
        Ok(Self {
            epoch_ms: u64::try_from(duration.as_millis())
                .map_err(|_| "host clock overflow".to_string())?,
            started: Instant::now(),
        })
    }

    pub(crate) fn now_ms(self) -> Result<u64, String> {
        self.epoch_ms
            .checked_add(
                self.started
                    .elapsed()
                    .as_millis()
                    .try_into()
                    .map_err(|_| "host clock overflow".to_string())?,
            )
            .ok_or_else(|| "host clock overflow".into())
    }
}

fn authorized<S: Signal>(state: &HttpState<S>, headers: &HeaderMap) -> bool {
    // Service-to-service adapter: reject browser-origin requests by default.
    if headers.contains_key("origin") {
        return false;
    }
    headers
        .get("authorization")
        .and_then(|h| h.to_str().ok())
        .and_then(|h| h.strip_prefix("Bearer "))
        .is_some_and(|token| bool::from(token.as_bytes().ct_eq(state.bearer.as_bytes())))
}

async fn with_runtime<S, F>(state: Arc<HttpState<S>>, action: F) -> Result<Value, String>
where
    S: Signal + Send + 'static,
    F: FnOnce(&mut Runtime<S>) -> Result<Value, String> + Send + 'static,
{
    // ponytail: one session lock spans authorization and synchronous effects;
    // use explicit reservations/cancellation if long-running async tools are required.
    tokio::task::spawn_blocking(move || {
        let mut runtime = state.runtime.lock().map_err(|_| "runtime unavailable")?;
        action(&mut runtime)
    })
    .await
    .map_err(|_| "execution worker failed".to_string())?
}

fn response(result: Result<Value, String>) -> Response {
    match result {
        Ok(value) => Json(value).into_response(),
        Err(error) => (StatusCode::BAD_REQUEST, Json(json!({"error":error}))).into_response(),
    }
}

async fn list<S: Signal + Send + 'static>(
    State(state): State<Arc<HttpState<S>>>,
    headers: HeaderMap,
) -> Response {
    if !authorized(&state, &headers) {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    response(with_runtime(state, |runtime| Ok(runtime.tools_list())).await)
}

async fn context<S: Signal + Send + 'static>(
    State(state): State<Arc<HttpState<S>>>,
    headers: HeaderMap,
) -> Response {
    if !authorized(&state, &headers) {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    response(
        with_runtime(state, |runtime| {
            Ok(json!({"directives":runtime.directives()}))
        })
        .await,
    )
}

async fn call<S: Signal + Send + 'static>(
    State(state): State<Arc<HttpState<S>>>,
    headers: HeaderMap,
    Json(request): Json<CallRequest>,
) -> Response {
    if !authorized(&state, &headers) {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    response(
        with_runtime(state, move |runtime| {
            let now = runtime.host_time_ms()?;
            runtime.call(request, now)
        })
        .await,
    )
}

async fn mcp<S: Signal + Send + 'static>(
    State(state): State<Arc<HttpState<S>>>,
    headers: HeaderMap,
    Json(message): Json<Value>,
) -> Response {
    if !authorized(&state, &headers) {
        return StatusCode::UNAUTHORIZED.into_response();
    }
    let id = message.get("id").cloned().unwrap_or(Value::Null);
    if message.get("jsonrpc") != Some(&json!("2.0"))
        || !(id.is_null() || id.is_string() || id.is_number())
    {
        return Json(
            json!({"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Invalid Request"}}),
        )
        .into_response();
    }
    let method = message.get("method").and_then(Value::as_str).unwrap_or("");
    if method == "notifications/initialized"
        && !message.as_object().is_some_and(|o| o.contains_key("id"))
    {
        return StatusCode::ACCEPTED.into_response();
    }
    if id.is_null() {
        return Json(json!({"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Request id required"}})).into_response();
    }
    let result = match method {
        "initialize" => Ok(
            json!({"protocolVersion":"2025-06-18", "capabilities":{"tools":{"listChanged":false}}, "serverInfo":{"name":"pgso","version":env!("CARGO_PKG_VERSION")}}),
        ),
        "ping" => Ok(json!({})),
        "tools/list" => with_runtime(state, |runtime| Ok(runtime.tools_list())).await,
        "tools/call" => {
            let params = message.get("params").cloned().unwrap_or(Value::Null);
            if let Some(name) = params.get("name").and_then(Value::as_str) {
                let tool = name.to_string();
                let arguments = params.get("arguments").cloned().unwrap_or(json!({}));
                let confirmation = params
                    .get("_meta")
                    .and_then(|m| m.get("pgso/confirmation"))
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                let result = with_runtime(state, move |runtime| {
                    let now = runtime.host_time_ms()?;
                    runtime.call(
                        CallRequest {
                            session: runtime.session().into(),
                            tool,
                            arguments,
                            confirmation,
                        },
                        now,
                    )
                })
                .await;
                Ok(match result {
                    Ok(value) => {
                        json!({"content":[{"type":"text","text":value.to_string()}],"isError":false})
                    }
                    Err(error) => json!({"content":[{"type":"text","text":error}],"isError":true}),
                })
            } else {
                Err("invalid tool call parameters".into())
            }
        }
        _ => return Json(
            json!({"jsonrpc":"2.0","id":id,"error":{"code":-32601,"message":"Method not found"}}),
        )
        .into_response(),
    };
    Json(match result {
        Ok(value) => json!({"jsonrpc":"2.0","id":id,"result":value}),
        Err(error) => json!({"jsonrpc":"2.0","id":id,"error":{"code":-32602,"message":error}}),
    })
    .into_response()
}

/// Build authenticated `/tools`, `/context`, `/call` and `/mcp` routes.
/// The host supplies an unpredictable bearer secret (at least 32 bytes).
/// MCP implements JSON responses for initialize/ping/tools; no SSE or server notifications.
///
/// # Errors
/// Rejects an empty/short credential before exposing routes.
pub fn router<S: Signal + Send + 'static>(
    runtime: Arc<Mutex<Runtime<S>>>,
    bearer: String,
) -> Result<Router, String> {
    if bearer.len() < 32 {
        return Err("bearer secret must have at least 32 bytes".into());
    }
    let state = Arc::new(HttpState { runtime, bearer });
    Ok(Router::new()
        .route("/tools", get(list::<S>))
        .route("/context", get(context::<S>))
        .route("/call", post(call::<S>))
        .route("/mcp", post(mcp::<S>))
        .layer(DefaultBodyLimit::max(64 * 1024))
        .with_state(state))
}
