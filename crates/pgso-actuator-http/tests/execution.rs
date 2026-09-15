use axum::{
    body::{to_bytes, Body},
    http::Request,
};
use pgso_actuator_http::{router, CallRequest, ExecutionReason, Runtime, ToolBinding};
use pgso_actuator_mcp::{McpActuator, ToolDefinition};
use pgso_core::*;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc, Mutex,
};
use tower::ServiceExt;

struct Empty;
impl Signal for Empty {
    fn extract(&mut self, audio: &AudioWindow) -> Vec<SignalReading> {
        audio
            .samples
            .first()
            .map(|&value| SignalReading {
                value,
                axis: Axis::Arousal,
                confidence: 0.9,
                timestamp_ms: audio.timestamp_ms,
            })
            .into_iter()
            .collect()
    }
}
fn setup() -> (Runtime<Empty>, Arc<AtomicUsize>) {
    let mut tool = Tool::new("quote", "Quote");
    tool.requires_step_up = true;
    let pipeline = Pgso::builder()
        .signal(Empty)
        .engine(DecisionEngine::new(EngineConfig {
            confidence_threshold: 0.5,
            deviation_threshold: 0.2,
            hysteresis_window: 1,
            ema_alpha: 0.0,
            warmup_readings: 0,
            population_prior: 0.5,
        }))
        .rules(RuleEngine::new(
            RuleSet::new(vec![Rule::new(
                "restrict",
                Axis::Arousal,
                0.2,
                0.5,
                vec![Action::Prune(ToolId::from("quote"))],
            )]),
            HashSet::new(),
        ))
        .actuator(McpActuator::new(Catalog::new(vec![tool]), HashSet::new()))
        .build()
        .unwrap();
    let count = Arc::new(AtomicUsize::new(0));
    let effects = count.clone();
    let binding = ToolBinding {
        id: ToolId::from("quote"),
        definition: ToolDefinition {
            description: "Quote an amount".into(),
            input_schema: json!({"type":"object","properties":{"amount":{"type":"integer","minimum":1}},"required":["amount"],"additionalProperties":false}),
        },
        handler: Box::new(move |args| {
            effects.fetch_add(1, Ordering::SeqCst);
            if args["amount"] == 13 {
                return Err("injected failure".into());
            }
            assert_ne!(args["amount"], 14, "injected panic");
            Ok(args.clone())
        }),
    };
    (
        Runtime::new(
            "s1".into(),
            pipeline,
            vec![binding],
            HashSet::from([ToolId::from("quote")]),
        )
        .unwrap(),
        count,
    )
}
fn request() -> CallRequest {
    CallRequest {
        session: "s1".into(),
        tool: "quote".into(),
        arguments: json!({"amount":1}),
        confirmation: None,
    }
}
#[test]
fn confirmations_are_bound_expiring_single_use_and_revocable() {
    let (mut runtime, effects) = setup();
    let mut r = request();
    assert!(runtime.call(r.clone(), 10).is_err());
    r.confirmation = Some("forged".into());
    assert!(runtime.call(r.clone(), 10).is_err());
    r.confirmation = Some(runtime.approve(&r, 10, 10).unwrap());
    let mut changed = r.clone();
    changed.arguments = json!({"amount":2});
    assert!(runtime.call(changed, 11).is_err());
    assert!(runtime.call(r.clone(), 11).is_err());
    r.confirmation = Some(runtime.approve(&r, 12, 10).unwrap());
    assert!(runtime.call(r.clone(), 22).is_err());
    r.confirmation = Some(runtime.approve(&r, 23, 10).unwrap());
    assert_eq!(runtime.call(r.clone(), 24).unwrap(), r.arguments);
    assert!(runtime.call(r.clone(), 25).is_err());
    r.confirmation = Some(runtime.approve(&r, 26, 10).unwrap());
    runtime
        .set_permissions(HashSet::from([ToolId::from("quote")]))
        .unwrap();
    assert!(runtime.call(r.clone(), 27).is_err());
    r.session = "other".into();
    assert!(runtime.approve(&r, 28, 10).is_err());
    r = request();
    r.arguments = json!({"amount":0});
    assert!(runtime.approve(&r, 29, 10).is_err());
    runtime.set_permissions(HashSet::new()).unwrap();
    assert!(runtime.call(request(), 30).is_err());
    assert_eq!(effects.load(Ordering::SeqCst), 1);
    let receipts = runtime.drain_execution_log();
    assert_eq!(
        receipts.iter().filter(|r| r.outcome == "completed").count(),
        1
    );
    assert!(receipts.iter().all(|r| r.session == "s1"));
    assert!(receipts
        .iter()
        .any(|record| record.reason == ExecutionReason::Completed));
    assert!(receipts
        .iter()
        .any(|record| record.reason == ExecutionReason::PermissionDenied));
    assert!(receipts
        .iter()
        .any(|record| record.reason == ExecutionReason::InvalidOrExpiredConfirmation));
    assert!(receipts
        .iter()
        .all(|record| record.policy_transition_count == 0));
}

#[test]
fn observed_clock_rollback_revokes_approval() {
    let (mut runtime, effects) = setup();
    let mut r = request();
    r.confirmation = Some(runtime.approve(&r, 100, 10).unwrap());
    // A later trusted operation observes time past expiry, without consuming
    // this token because it still lacks the confirmation in the request.
    assert!(runtime.call(request(), 120).is_err());
    assert!(runtime.call(r, 105).is_err());
    assert_eq!(effects.load(Ordering::SeqCst), 0);
    let receipts = runtime.drain_execution_log();
    assert_eq!(
        receipts.last().unwrap().reason,
        ExecutionReason::ClockRollback
    );
    assert_eq!(receipts.last().unwrap().revision, 1);
}

#[test]
fn invalid_arguments_consume_presented_confirmation() {
    let (mut runtime, effects) = setup();
    let mut approved = request();
    approved.confirmation = Some(runtime.approve(&approved, 1, 100).unwrap());
    let token = approved.confirmation.clone();
    let mut invalid = approved.clone();
    invalid.arguments = json!({"amount": 0});
    assert!(runtime.call(invalid, 2).is_err());
    approved.confirmation = token;
    assert!(runtime.call(approved, 3).is_err());
    assert_eq!(effects.load(Ordering::SeqCst), 0);
}

#[test]
fn wrong_session_cannot_consume_another_sessions_confirmation() {
    let (mut runtime, effects) = setup();
    let mut approved = request();
    approved.confirmation = Some(runtime.approve(&approved, 1, 100).unwrap());
    let token = approved.confirmation.clone();
    let mut wrong_session = approved.clone();
    wrong_session.session = "other".into();
    assert!(runtime.call(wrong_session, 2).is_err());
    approved.confirmation = token;
    assert!(runtime.call(approved, 3).is_ok());
    assert_eq!(effects.load(Ordering::SeqCst), 1);
}
const SECRET: &str = "test-only-32-byte-secret-not-for-production";
#[tokio::test]
async fn http_and_mcp_share_dispatch_and_do_not_expose_approval() {
    let (runtime, effects) = setup();
    let shared = Arc::new(Mutex::new(runtime));
    let app = router(shared.clone(), SECRET.into()).unwrap();
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/tools")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), 401);
    for (path, body) in [
        ("/call", serde_json::to_value(request()).unwrap()),
        (
            "/mcp",
            json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"quote","arguments":{"amount":1}}}),
        ),
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(path)
                    .header("authorization", format!("Bearer {SECRET}"))
                    .header("content-type", "application/json")
                    .body(Body::from(body.to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        let value: Value =
            serde_json::from_slice(&to_bytes(response.into_body(), 65536).await.unwrap()).unwrap();
        assert!(status == 400 || value["result"]["isError"] == true);
    }
    assert_eq!(effects.load(Ordering::SeqCst), 0);
    let mut approved = request();
    approved.confirmation = Some({
        let mut runtime = shared.lock().unwrap();
        let now = runtime.host_time_ms().unwrap();
        runtime.approve(&approved, now, 1_000).unwrap()
    });
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/call")
                .header("authorization", format!("Bearer {SECRET}"))
                .header("content-type", "application/json")
                .body(Body::from(serde_json::to_string(&approved).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), 200);
    assert_eq!(effects.load(Ordering::SeqCst), 1);
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/tools")
                .header("authorization", format!("Bearer {SECRET}"))
                .header("origin", "https://attacker.invalid")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), 401);
    let response = app
        .oneshot(
            Request::builder()
                .uri("/approve")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), 404);
}

#[test]
fn stale_discovery_and_confirmation_cannot_bypass_new_restriction() {
    let (mut runtime, effects) = setup();
    assert_eq!(runtime.tools_list()["tools"].as_array().unwrap().len(), 1);
    let mut r = request();
    r.confirmation = Some(runtime.approve(&r, 1, 100).unwrap());
    runtime
        .observe(&AudioWindow {
            samples: vec![0.95],
            sample_rate: 16000,
            timestamp_ms: 2,
        })
        .unwrap();
    assert!(runtime.tools_list()["tools"].as_array().unwrap().is_empty());
    assert!(runtime.call(r.clone(), 3).is_err());
    runtime.expire_before(3).unwrap();
    assert!(runtime.call(r, 4).is_err());
    assert_eq!(effects.load(Ordering::SeqCst), 0);
    assert!(runtime
        .drain_execution_log()
        .iter()
        .any(|record| record.reason == ExecutionReason::ToolUnavailable));
}

#[test]
fn failed_and_panicking_callbacks_consume_confirmation_and_leave_receipts() {
    let (mut runtime, effects) = setup();
    for (i, amount) in [13, 14].into_iter().enumerate() {
        let base = 1 + i as u64 * 10;
        let mut r = request();
        r.arguments = json!({"amount":amount});
        r.confirmation = Some(runtime.approve(&r, base, 100).unwrap());
        assert!(runtime.call(r.clone(), base + 1).is_err());
        assert!(runtime.call(r, base + 2).is_err());
    }
    assert_eq!(effects.load(Ordering::SeqCst), 2);
    assert_eq!(
        runtime
            .drain_execution_log()
            .iter()
            .filter(|r| r.outcome == "failed")
            .count(),
        2
    );
    let mut r = request();
    r.confirmation = Some(runtime.approve(&r, 24, 100).unwrap());
    assert!(runtime.call(r, 25).is_ok());
}

#[test]
fn receipts_report_bounded_denial_and_failure_reasons_without_secrets() {
    let (mut runtime, _) = setup();
    assert!(runtime.call(request(), 1).is_err());

    let mut wrong_session = request();
    wrong_session.session = "secret-session".into();
    assert!(runtime.call(wrong_session, 2).is_err());

    let mut invalid = request();
    invalid.arguments = json!({"amount": "secret-argument"});
    assert!(runtime.call(invalid, 3).is_err());

    let mut failed = request();
    failed.arguments = json!({"amount": 13});
    failed.confirmation = Some(runtime.approve(&failed, 4, 100).unwrap());
    let failed_confirmation = failed.confirmation.clone().unwrap();
    assert_eq!(runtime.call(failed, 5).unwrap_err(), "injected failure");

    let mut panicked = request();
    panicked.arguments = json!({"amount": 14});
    panicked.confirmation = Some(runtime.approve(&panicked, 6, 100).unwrap());
    let panicked_confirmation = panicked.confirmation.clone().unwrap();
    assert_eq!(
        runtime.call(panicked, 7).unwrap_err(),
        "tool callback panicked"
    );

    let receipts = runtime.drain_execution_log();
    assert_eq!(
        receipts
            .iter()
            .map(|record| record.reason)
            .collect::<Vec<_>>(),
        vec![
            ExecutionReason::ConfirmationMissing,
            ExecutionReason::WrongSession,
            ExecutionReason::InvalidArguments,
            ExecutionReason::CallbackFailed,
            ExecutionReason::CallbackPanicked,
        ]
    );
    let serialized = serde_json::to_string(&receipts).unwrap();
    assert!(serialized.contains("callback_failed"));
    for secret in [
        "secret-session",
        "secret-argument",
        "injected failure",
        "injected panic",
        &failed_confirmation,
        &panicked_confirmation,
    ] {
        assert!(!serialized.contains(secret));
    }
}

#[test]
fn transition_count_links_same_timestamp_receipts_across_drains() {
    let (mut runtime, _) = setup();
    assert!(runtime.call(request(), 1).is_err());
    let first = runtime.drain_execution_log().pop().unwrap();
    assert_eq!(first.policy_transition_count, 0);

    runtime
        .observe(&AudioWindow {
            samples: vec![0.95],
            sample_rate: 16000,
            timestamp_ms: 1,
        })
        .unwrap();
    assert!(runtime.call(request(), 1).is_err());
    let second = runtime.drain_execution_log().pop().unwrap();
    assert_eq!(second.timestamp_ms, first.timestamp_ms);
    assert_eq!(second.policy_transition_count, 1);
    assert_eq!(runtime.policy_audit().transitions().len(), 1);
}
