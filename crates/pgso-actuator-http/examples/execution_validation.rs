//! Thirteen direct Runtime::call checks, not an HTTP transport test.
//! Callback counts are cumulative within each shared-runtime sequence.
use pgso_actuator_http::{CallRequest, ExecutionReason, ExecutionRecord, Runtime, ToolBinding};
use pgso_actuator_mcp::{McpActuator, ToolDefinition};
use pgso_core::{
    Action, AudioWindow, Axis, Catalog, DecisionEngine, EngineConfig, Pgso, Rule, RuleEngine,
    RuleSet, Signal, SignalReading, Tool, ToolId,
};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};

struct ReadingSignal;

impl Signal for ReadingSignal {
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

fn setup(session: &str) -> (Runtime<ReadingSignal>, Arc<AtomicUsize>) {
    let quote = Tool::new("quote", "Quote");
    let human = Tool::new("human", "Human handoff");
    let protected = HashSet::from([ToolId::from("human")]);
    let pipeline = Pgso::builder()
        .signal(ReadingSignal)
        .engine(DecisionEngine::new(EngineConfig {
            population_prior: 0.5,
            ema_alpha: 0.0,
            warmup_readings: 0,
            hysteresis_window: 1,
            deviation_threshold: 0.2,
            confidence_threshold: 0.5,
        }))
        .rules(RuleEngine::new(
            RuleSet::new(vec![Rule::new(
                "restrict",
                Axis::Arousal,
                0.2,
                0.5,
                vec![
                    Action::Prune(ToolId::from("quote")),
                    Action::Prune(ToolId::from("human")),
                ],
            )]),
            protected.clone(),
        ))
        .actuator(McpActuator::new(
            Catalog::new(vec![quote, human]),
            protected,
        ))
        .build()
        .expect("valid validation pipeline");

    let count = Arc::new(AtomicUsize::new(0));
    let bindings = ["quote", "human"]
        .into_iter()
        .map(|name| {
            let effects = Arc::clone(&count);
            ToolBinding {
                id: ToolId::from(name),
                definition: ToolDefinition {
                    description: format!("Validation {name} callback"),
                    input_schema: json!({
                        "type": "object",
                        "properties": {"amount": {"type": "integer", "minimum": 1}},
                        "required": ["amount"],
                        "additionalProperties": false
                    }),
                },
                handler: Box::new(move |arguments| {
                    effects.fetch_add(1, Ordering::SeqCst);
                    if arguments["amount"] == 13 {
                        Err("injected callback failure".into())
                    } else {
                        Ok(arguments.clone())
                    }
                }),
            }
        })
        .collect();

    let runtime = Runtime::new(
        session.into(),
        pipeline,
        bindings,
        HashSet::from([ToolId::from("quote"), ToolId::from("human")]),
    )
    .expect("valid validation runtime");
    (runtime, count)
}

fn request(session: &str, tool: &str, amount: i64) -> CallRequest {
    CallRequest {
        session: session.into(),
        tool: tool.into(),
        arguments: json!({"amount": amount}),
        confirmation: None,
    }
}

fn restrict(runtime: &mut Runtime<ReadingSignal>, timestamp_ms: u64) {
    runtime
        .observe(&AudioWindow {
            samples: vec![0.95],
            sample_rate: 16_000,
            timestamp_ms,
        })
        .expect("restriction observation accepted");
}

fn outcome(
    result: Result<Value, String>,
    runtime: &mut Runtime<ReadingSignal>,
) -> (String, ExecutionRecord) {
    let mut receipts = runtime.drain_execution_log();
    assert_eq!(receipts.len(), 1, "one receipt per attempted call");
    let text = match result {
        Ok(_) => "completed".into(),
        Err(error) => error,
    };
    (text, receipts.pop().expect("one receipt"))
}

fn record(
    cases: &mut Vec<Value>,
    name: &str,
    expected: &str,
    actual: (String, ExecutionRecord),
    expected_callbacks: usize,
    callback_count: usize,
) {
    let expected_reason = match name {
        "allowed_call" | "granted_token" | "session_isolation_preserves_token" => {
            ExecutionReason::Completed
        }
        "pruned_quote" => ExecutionReason::ToolUnavailable,
        "wrong_arguments" => ExecutionReason::InvalidArguments,
        "expired_token" => ExecutionReason::InvalidOrExpiredConfirmation,
        "session_isolation" => ExecutionReason::WrongSession,
        "callback_failure" => ExecutionReason::CallbackFailed,
        "protected_human_step_up_missing"
        | "token_replay"
        | "wrong_arguments_consumes_token"
        | "policy_transition_sequence_revokes_token"
        | "callback_failure_consumes_token" => ExecutionReason::ConfirmationMissing,
        _ => panic!("undeclared diagnostic case"),
    };
    let (actual, receipt) = actual;
    let pass = actual == expected
        && callback_count == expected_callbacks
        && receipt.reason == expected_reason;
    let mut diagnostic = serde_json::to_value(receipt).expect("serializable receipt");
    diagnostic
        .as_object_mut()
        .expect("receipt object")
        .remove("duration_us");
    cases.push(json!({
        "case": name,
        "expected_reason": expected_reason,
        "actual_receipt": diagnostic,
        "expected": {"outcome": expected, "callback_count": expected_callbacks},
        "actual": {"outcome": actual, "callback_count": callback_count},
        "callback_count": callback_count,
        "pass": pass
    }));
}

fn main() {
    let mut cases = Vec::new();

    let (mut runtime, count) = setup("allowed");
    let actual = outcome(
        runtime.call(request("allowed", "quote", 1), 1),
        &mut runtime,
    );
    record(
        &mut cases,
        "allowed_call",
        "completed",
        actual,
        1,
        count.load(Ordering::SeqCst),
    );

    let (mut runtime, count) = setup("pruned");
    restrict(&mut runtime, 1);
    let actual = outcome(runtime.call(request("pruned", "quote", 1), 2), &mut runtime);
    record(
        &mut cases,
        "pruned_quote",
        "tool currently unavailable",
        actual,
        0,
        count.load(Ordering::SeqCst),
    );

    let (mut runtime, count) = setup("missing");
    restrict(&mut runtime, 1);
    let actual = outcome(
        runtime.call(request("missing", "human", 1), 2),
        &mut runtime,
    );
    record(
        &mut cases,
        "protected_human_step_up_missing",
        "confirmation required",
        actual,
        0,
        count.load(Ordering::SeqCst),
    );

    let (mut runtime, count) = setup("single-use");
    restrict(&mut runtime, 1);
    let mut approved = request("single-use", "human", 1);
    approved.confirmation = Some(runtime.approve(&approved, 2, 100).expect("approval issued"));
    let actual = outcome(runtime.call(approved.clone(), 3), &mut runtime);
    record(
        &mut cases,
        "granted_token",
        "completed",
        actual,
        1,
        count.load(Ordering::SeqCst),
    );
    let actual = outcome(runtime.call(approved, 4), &mut runtime);
    record(
        &mut cases,
        "token_replay",
        "confirmation required",
        actual,
        1,
        count.load(Ordering::SeqCst),
    );

    let (mut runtime, count) = setup("wrong-args");
    restrict(&mut runtime, 1);
    let valid = request("wrong-args", "human", 1);
    let token = runtime.approve(&valid, 2, 100).expect("approval issued");
    let mut invalid = request("wrong-args", "human", 0);
    invalid.confirmation = Some(token.clone());
    let actual = outcome(runtime.call(invalid, 3), &mut runtime);
    record(
        &mut cases,
        "wrong_arguments",
        "invalid arguments",
        actual,
        0,
        count.load(Ordering::SeqCst),
    );
    let mut replay = valid;
    replay.confirmation = Some(token);
    let actual = outcome(runtime.call(replay, 4), &mut runtime);
    record(
        &mut cases,
        "wrong_arguments_consumes_token",
        "confirmation required",
        actual,
        0,
        count.load(Ordering::SeqCst),
    );

    let (mut runtime, count) = setup("recovery");
    restrict(&mut runtime, 1);
    let base = request("recovery", "human", 1);
    let token = runtime.approve(&base, 2, 100).expect("approval issued");
    runtime.expire_before(2).expect("policy recovery accepted");
    restrict(&mut runtime, 3);
    let mut stale = base;
    stale.confirmation = Some(token);
    let actual = outcome(runtime.call(stale, 4), &mut runtime);
    record(
        &mut cases,
        "policy_transition_sequence_revokes_token",
        "confirmation required",
        actual,
        0,
        count.load(Ordering::SeqCst),
    );

    let (mut runtime, count) = setup("expired");
    restrict(&mut runtime, 1);
    let mut expired = request("expired", "human", 1);
    expired.confirmation = Some(runtime.approve(&expired, 2, 10).expect("approval issued"));
    let actual = outcome(runtime.call(expired, 12), &mut runtime);
    record(
        &mut cases,
        "expired_token",
        "invalid or expired confirmation",
        actual,
        0,
        count.load(Ordering::SeqCst),
    );

    let (mut runtime, count) = setup("isolated");
    restrict(&mut runtime, 1);
    let mut approved = request("isolated", "human", 1);
    approved.confirmation = Some(runtime.approve(&approved, 2, 100).expect("approval issued"));
    let mut foreign = approved.clone();
    foreign.session = "other".into();
    let actual = outcome(runtime.call(foreign, 3), &mut runtime);
    record(
        &mut cases,
        "session_isolation",
        "wrong session",
        actual,
        0,
        count.load(Ordering::SeqCst),
    );
    let actual = outcome(runtime.call(approved, 4), &mut runtime);
    record(
        &mut cases,
        "session_isolation_preserves_token",
        "completed",
        actual,
        1,
        count.load(Ordering::SeqCst),
    );

    let (mut runtime, count) = setup("callback-failure");
    restrict(&mut runtime, 1);
    let mut failing = request("callback-failure", "human", 13);
    failing.confirmation = Some(runtime.approve(&failing, 2, 100).expect("approval issued"));
    let actual = outcome(runtime.call(failing.clone(), 3), &mut runtime);
    record(
        &mut cases,
        "callback_failure",
        "injected callback failure",
        actual,
        1,
        count.load(Ordering::SeqCst),
    );
    let actual = outcome(runtime.call(failing, 4), &mut runtime);
    record(
        &mut cases,
        "callback_failure_consumes_token",
        "confirmation required",
        actual,
        1,
        count.load(Ordering::SeqCst),
    );

    println!(
        "{}",
        serde_json::to_string(&cases).expect("serializable cases")
    );
    assert_eq!(cases.len(), 13, "all execution cases must be recorded");
    assert!(cases.iter().all(|case| case["pass"] == true));
}
