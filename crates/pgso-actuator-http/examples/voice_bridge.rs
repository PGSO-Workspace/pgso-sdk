//! NDJSON seam between a trusted tau host and the PGSO execution boundary.
//! Restart this process to reset all policy, clock, and audit state.

use pgso_actuator_http::{CallRequest, Runtime, ToolBinding, ToolHandler};
use pgso_actuator_mcp::{McpActuator, ToolDefinition};
use pgso_core::{
    Action, AudioWindow, Axis, Catalog, DecisionEngine, Direction, EngineConfig, Pgso, Rule,
    RuleEngine, RuleSet, Signal, SignalReading, Tool, ToolId,
};
use pgso_signal_egemaps::EgemapsSignal;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{HashSet, VecDeque},
    io::{self, BufRead, BufReader, Write},
    sync::{Arc, Mutex},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Init {
    session: String,
    tools: Vec<InitTool>,
    governed_tools: Vec<String>,
    #[serde(default)]
    intervention: Intervention,
}

#[derive(Clone, Copy, Default, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Intervention {
    #[default]
    Prune,
    StepUp,
}

impl Intervention {
    fn as_str(self) -> &'static str {
        match self {
            Self::Prune => "prune",
            Self::StepUp => "step_up",
        }
    }
}

#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct InitTool {
    name: String,
    description: String,
    #[serde(rename = "inputSchema")]
    input_schema: Value,
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Command {
    Extract {
        samples: Vec<f32>,
        timestamp_ms: u64,
    },
    Observe {
        readings: Vec<Reading>,
        timestamp_ms: u64,
    },
    Call {
        name: String,
        arguments: Value,
        timestamp_ms: u64,
        #[serde(default)]
        confirmation: Option<String>,
    },
    Approve {
        name: String,
        arguments: Value,
        timestamp_ms: u64,
        ttl_ms: u64,
    },
    State,
    Reset,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Reading {
    axis: String,
    value: f32,
    confidence: f32,
    timestamp_ms: u64,
}

#[derive(Clone)]
struct ReadingQueue(Queue);

type Queue = Arc<Mutex<VecDeque<Vec<SignalReading>>>>;

impl Signal for ReadingQueue {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0
            .lock()
            .ok()
            .and_then(|mut queue| queue.pop_front())
            .unwrap_or_default()
    }
}

fn send(stdout: &Arc<Mutex<io::Stdout>>, value: &Value) -> Result<(), String> {
    let mut stdout = stdout.lock().map_err(|_| "stdout lock poisoned")?;
    serde_json::to_writer(&mut *stdout, value).map_err(|error| error.to_string())?;
    stdout.write_all(b"\n").map_err(|error| error.to_string())?;
    stdout.flush().map_err(|error| error.to_string())
}

fn read_line(reader: &Arc<Mutex<BufReader<io::Stdin>>>) -> Result<String, String> {
    let mut line = String::new();
    let bytes = reader
        .lock()
        .map_err(|_| "stdin lock poisoned")?
        .read_line(&mut line)
        .map_err(|error| error.to_string())?;
    if bytes == 0 {
        Err("unexpected end of input".into())
    } else {
        Ok(line)
    }
}

fn callback(
    name: String,
    reader: Arc<Mutex<BufReader<io::Stdin>>>,
    stdout: Arc<Mutex<io::Stdout>>,
) -> ToolHandler {
    Box::new(move |arguments| {
        send(
            &stdout,
            &json!({"callback":{"tool":name,"arguments":arguments}}),
        )?;
        let reply: Value =
            serde_json::from_str(&read_line(&reader)?).map_err(|error| error.to_string())?;
        let reply = reply
            .as_object()
            .ok_or("callback reply must be an object")?;
        match (reply.get("result"), reply.get("error"), reply.len()) {
            (Some(result), None, 1) => Ok(result.clone()),
            (None, Some(Value::String(error)), 1) if !error.is_empty() => Err(error.clone()),
            _ => Err("callback reply must contain exactly one of result or error".into()),
        }
    })
}

fn config() -> Value {
    json!({
        "sample_rate": 16000,
        "window_samples": 12800,
        "window_ms": 800,
        "hop_ms": 400,
        "population_prior": 0.5,
        "confidence_threshold": 0.5,
        "deviation_threshold": 0.2,
        "hysteresis_window": 2,
        "warmup_readings": 0,
        "ema_alpha": 0.0,
        "direction": "Rising",
        "max_gap_ms": 1200,
        "stale_after_ms": 1200
    })
}

fn state(runtime: &Runtime<ReadingQueue>, intervention: Intervention) -> Value {
    let listed = runtime.tools_list();
    let tools: Vec<_> = listed["tools"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|tool| tool["name"].as_str().map(str::to_owned))
        .collect();
    let step_up_tools: Vec<_> = listed["tools"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|tool| tool["_meta"]["pgso/requiresStepUp"] == true)
        .filter_map(|tool| tool["name"].as_str().map(str::to_owned))
        .collect();
    json!({
        "tools": tools,
        "step_up_tools": step_up_tools,
        "intervention": intervention.as_str(),
        "directives": runtime.directives()
    })
}

fn build_runtime(
    init: Init,
    reader: Arc<Mutex<BufReader<io::Stdin>>>,
    stdout: Arc<Mutex<io::Stdout>>,
) -> Result<(Runtime<ReadingQueue>, Queue, Intervention), String> {
    if init.tools.is_empty() {
        return Err("tools must not be empty".into());
    }
    let names: HashSet<_> = init.tools.iter().map(|tool| tool.name.clone()).collect();
    if names.len() != init.tools.len() || names.iter().any(|name| name.is_empty()) {
        return Err("tool names must be non-empty and unique".into());
    }
    let mut governed = init.governed_tools;
    governed.sort();
    governed.dedup();
    if governed.iter().any(|name| !names.contains(name)) {
        return Err("governed_tools contains an unknown tool".into());
    }

    let catalog = Catalog::new(
        init.tools
            .iter()
            .map(|tool| Tool::new(tool.name.clone(), tool.name.clone()))
            .collect(),
    );
    let mut actions: Vec<_> = governed
        .iter()
        .cloned()
        .map(|name| match init.intervention {
            Intervention::Prune => Action::Prune(ToolId::from(name)),
            Intervention::StepUp => Action::RequireStepUp(ToolId::from(name)),
        })
        .collect();
    actions.push(Action::InjectDirective(
        "Ask the user for clarification before continuing.".into(),
    ));
    let rule = Rule::new("pilot_high_arousal", Axis::Arousal, 0.2, 0.5, actions)
        .with_direction(Direction::Rising);
    let queue = Arc::new(Mutex::new(VecDeque::new()));
    let pipeline = Pgso::builder()
        .signal(ReadingQueue(Arc::clone(&queue)))
        .engine(
            DecisionEngine::try_new(EngineConfig {
                population_prior: 0.5,
                confidence_threshold: 0.5,
                deviation_threshold: 0.2,
                hysteresis_window: 2,
                warmup_readings: 0,
                ema_alpha: 0.0,
            })
            .map_err(|error| error.to_string())?
            .with_max_gap_ms(1_200)
            .map_err(|error| error.to_string())?,
        )
        .rules(RuleEngine::new(RuleSet::new(vec![rule]), HashSet::new()))
        .actuator(McpActuator::new(catalog, HashSet::new()))
        .build()
        .map_err(|error| error.to_string())?;
    let bindings = init
        .tools
        .into_iter()
        .map(|tool| ToolBinding {
            id: ToolId::from(tool.name.clone()),
            definition: ToolDefinition {
                description: tool.description,
                input_schema: tool.input_schema,
            },
            handler: callback(tool.name, Arc::clone(&reader), Arc::clone(&stdout)),
        })
        .collect();
    let allowed = names.into_iter().map(ToolId::from).collect();
    Ok((
        Runtime::new(init.session, pipeline, bindings, allowed)?,
        queue,
        init.intervention,
    ))
}

fn run() -> Result<(), String> {
    let reader = Arc::new(Mutex::new(BufReader::new(io::stdin())));
    let stdout = Arc::new(Mutex::new(io::stdout()));
    let init: Init =
        serde_json::from_str(&read_line(&reader)?).map_err(|error| error.to_string())?;
    let (mut runtime, queue, intervention) =
        build_runtime(init, Arc::clone(&reader), Arc::clone(&stdout))?;
    let mut extractor = EgemapsSignal::new(16_000);
    let mut last_timestamp = None;
    send(
        &stdout,
        &json!({"ok":true,"state":state(&runtime, intervention),"audit":[],"config":config()}),
    )?;

    loop {
        let command: Command =
            serde_json::from_str(&read_line(&reader)?).map_err(|error| error.to_string())?;
        match command {
            Command::Extract {
                samples,
                timestamp_ms,
            } => {
                if samples.len() != 12_800
                    || samples
                        .iter()
                        .any(|sample| !sample.is_finite() || !(-1.0..=1.0).contains(sample))
                    || last_timestamp.is_some_and(|last| timestamp_ms < last)
                {
                    return Err(
                        "extract requires 12800 finite f32 samples at 16000 Hz and monotonic host time"
                            .into(),
                    );
                }
                let readings: Vec<_> = extractor
                    .extract(&AudioWindow {
                        samples,
                        sample_rate: 16_000,
                        timestamp_ms,
                    })
                    .into_iter()
                    .filter(|reading| reading.axis == Axis::Arousal)
                    .map(|reading| {
                        json!({
                            "value": reading.value,
                            "axis": "Arousal",
                            "confidence": reading.confidence,
                            "timestamp_ms": reading.timestamp_ms
                        })
                    })
                    .collect();
                last_timestamp = Some(timestamp_ms);
                send(&stdout, &json!({"ok":true,"result":readings}))?;
            }
            Command::Observe {
                readings,
                timestamp_ms,
            } => {
                if readings.len() > 1
                    || readings.iter().any(|reading| {
                        reading.axis != "Arousal"
                            || reading.timestamp_ms != timestamp_ms
                            || !reading.value.is_finite()
                            || !reading.confidence.is_finite()
                            || !(0.0..=1.0).contains(&reading.value)
                            || !(0.0..=1.0).contains(&reading.confidence)
                    })
                {
                    return Err(
                        "observe accepts at most one current Arousal reading with value/confidence in [0,1]"
                            .into(),
                    );
                }
                if last_timestamp.is_some_and(|last| timestamp_ms < last) {
                    return Err("host timestamp moved backwards".into());
                }
                let batch = readings
                    .into_iter()
                    .map(|reading| SignalReading {
                        value: reading.value,
                        axis: Axis::Arousal,
                        confidence: reading.confidence,
                        timestamp_ms: reading.timestamp_ms,
                    })
                    .collect();
                runtime.expire_before(timestamp_ms.saturating_sub(1_200))?;
                queue
                    .lock()
                    .map_err(|_| "reading queue lock poisoned")?
                    .push_back(batch);
                runtime.observe(&AudioWindow {
                    samples: Vec::new(),
                    sample_rate: 16_000,
                    timestamp_ms,
                })?;
                last_timestamp = Some(timestamp_ms);
                send(
                    &stdout,
                    &json!({"ok":true,"state":state(&runtime, intervention),"audit":[]}),
                )?;
            }
            Command::Call {
                name,
                arguments,
                timestamp_ms,
                confirmation,
            } => {
                if last_timestamp.is_some_and(|last| timestamp_ms < last) {
                    return Err("host timestamp moved backwards".into());
                }
                last_timestamp = Some(timestamp_ms);
                runtime.expire_before(timestamp_ms.saturating_sub(1_200))?;
                let result = runtime.call(
                    CallRequest {
                        session: runtime.session().to_string(),
                        tool: name,
                        arguments,
                        confirmation,
                    },
                    timestamp_ms,
                );
                let audit = runtime.drain_execution_log();
                let response = match result {
                    Ok(result) => {
                        json!({"ok":true,"result":result,"state":state(&runtime, intervention),"audit":audit})
                    }
                    Err(error) => {
                        json!({"ok":false,"error":error,"state":state(&runtime, intervention),"audit":audit})
                    }
                };
                send(&stdout, &response)?;
            }
            Command::Approve {
                name,
                arguments,
                timestamp_ms,
                ttl_ms,
            } => {
                if last_timestamp.is_some_and(|last| timestamp_ms < last) {
                    return Err("host timestamp moved backwards".into());
                }
                last_timestamp = Some(timestamp_ms);
                runtime.expire_before(timestamp_ms.saturating_sub(1_200))?;
                let request = CallRequest {
                    session: runtime.session().to_string(),
                    tool: name,
                    arguments,
                    confirmation: None,
                };
                let response = match runtime.approve(&request, timestamp_ms, ttl_ms) {
                    Ok(confirmation) => json!({"ok":true,"confirmation":confirmation,
                                               "state":state(&runtime, intervention),"audit":[]}),
                    Err(error) => json!({"ok":false,"error":error,
                                        "state":state(&runtime, intervention),"audit":[]}),
                };
                send(&stdout, &response)?;
            }
            Command::State => send(
                &stdout,
                &json!({"ok":true,"state":state(&runtime, intervention),"audit":[]}),
            )?,
            Command::Reset => {
                send(
                    &stdout,
                    &json!({"ok":false,"error":"restart process to reset state"}),
                )?;
                return Ok(());
            }
        }
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("voice_bridge: {error}");
        let _ = serde_json::to_writer(io::stdout(), &json!({"ok":false,"error":error}));
        let _ = io::stdout().write_all(b"\n");
        std::process::exit(1);
    }
}
