use axum::{
    body::{to_bytes, Body},
    http::Request,
};
use pgso_actuator_http::{router, CallRequest, Runtime, ToolBinding};
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
    r.confirmation = Some(runtime.approve(&r, 10, 10).unwrap());
    assert!(runtime.call(r.clone(), 20).is_err());
    r.confirmation = Some(runtime.approve(&r, 20, 10).unwrap());
    assert_eq!(runtime.call(r.clone(), 21).unwrap(), r.arguments);
    assert!(runtime.call(r.clone(), 21).is_err());
    r.confirmation = Some(runtime.approve(&r, 20, 10).unwrap());
    runtime
        .set_permissions(HashSet::from([ToolId::from("quote")]))
        .unwrap();
    assert!(runtime.call(r.clone(), 21).is_err());
    r.session = "other".into();
    assert!(runtime.approve(&r, 20, 10).is_err());
    r = request();
    r.arguments = json!({"amount":0});
    assert!(runtime.approve(&r, 20, 10).is_err());
    runtime.set_permissions(HashSet::new()).unwrap();
    assert!(runtime.call(request(), 21).is_err());
    assert_eq!(effects.load(Ordering::SeqCst), 1);
    let receipts = runtime.drain_execution_log();
    assert_eq!(
        receipts.iter().filter(|r| r.outcome == "completed").count(),
        1
    );
    assert!(receipts.iter().all(|r| r.session == "s1"));
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
    let mut r = request();
    r.confirmation = Some(shared.lock().unwrap().approve(&r, 0, u64::MAX).unwrap());
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/call")
                .header("authorization", format!("Bearer {SECRET}"))
                .header("content-type", "application/json")
                .body(Body::from(serde_json::to_string(&r).unwrap()))
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
}

#[test]
fn failed_and_panicking_callbacks_consume_confirmation_and_leave_receipts() {
    let (mut runtime, effects) = setup();
    for amount in [13, 14] {
        let mut r = request();
        r.arguments = json!({"amount":amount});
        r.confirmation = Some(runtime.approve(&r, 1, 100).unwrap());
        assert!(runtime.call(r.clone(), 2).is_err());
        assert!(runtime.call(r, 3).is_err());
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
    r.confirmation = Some(runtime.approve(&r, 3, 100).unwrap());
    assert!(runtime.call(r, 4).is_ok());
}
