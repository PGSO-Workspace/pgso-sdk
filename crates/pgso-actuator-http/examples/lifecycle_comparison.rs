//! Compare timestamped lifecycle/dispatch episodes through the real PGSO runtime.
use pgso_actuator_http::{CallRequest, Runtime, ToolBinding};
use pgso_actuator_mcp::{McpActuator, ToolDefinition};
use pgso_core::{
    Action, AudioWindow, Axis, Catalog, DecisionEngine, Direction, EngineConfig, Pgso, Rule,
    RuleEngine, RuleSet, Signal, SignalReading, Tool, ToolId,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::collections::{HashSet, VecDeque};
use std::env;
use std::error::Error;
use std::fs;
use std::io::{self, Read};
use std::rc::Rc;
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};

type ReadingQueue = Rc<RefCell<VecDeque<Option<SignalReading>>>>;
type RuntimeSetup = (Runtime<Readings>, ReadingQueue, Arc<AtomicUsize>);

struct Readings(ReadingQueue);

impl Signal for Readings {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        self.0
            .borrow_mut()
            .pop_front()
            .flatten()
            .into_iter()
            .collect()
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Episode {
    id: String,
    events: Vec<Event>,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum Event {
    Observation(ObservationEvent),
    Silence(SilenceEvent),
    Expire(ExpireEvent),
    Call(CallEvent),
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ObservationEvent {
    #[serde(rename = "kind")]
    _kind: ObservationKind,
    value: f64,
    confidence: f64,
    timestamp_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SilenceEvent {
    #[serde(rename = "kind")]
    _kind: SilenceKind,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExpireEvent {
    #[serde(rename = "kind")]
    _kind: ExpireKind,
    cutoff_ms: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CallEvent {
    #[serde(rename = "kind")]
    _kind: CallKind,
    tool: ToolName,
    timestamp_ms: u64,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum ObservationKind {
    Observation,
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum SilenceKind {
    Silence,
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum ExpireKind {
    Expire,
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum CallKind {
    Call,
}

#[derive(Clone, Copy, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
enum ToolName {
    Quote,
    Human,
}

impl ToolName {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Quote => "quote",
            Self::Human => "human",
        }
    }
}

#[derive(Serialize)]
struct EpisodeOutput {
    id: String,
    outputs: Vec<CallOutput>,
    callback_count: usize,
}

#[derive(Serialize)]
struct CallOutput {
    timestamp_ms: u64,
    tool: ToolName,
    action: &'static str,
    callback_delta: usize,
    receipt: Option<Value>,
}

fn runtime(session: &str) -> Result<RuntimeSetup, Box<dyn Error>> {
    let protected = HashSet::from([ToolId::from("human")]);
    let queue = Rc::new(RefCell::new(VecDeque::new()));
    let pipeline = Pgso::builder()
        .signal(Readings(Rc::clone(&queue)))
        .engine(
            DecisionEngine::try_new(EngineConfig {
                population_prior: 0.5,
                ema_alpha: 0.0,
                warmup_readings: 0,
                hysteresis_window: 3,
                deviation_threshold: 0.3,
                confidence_threshold: 0.5,
            })?
            .with_max_gap_ms(1200)?,
        )
        .rules(RuleEngine::new(
            RuleSet::new(vec![Rule::new(
                "elevated",
                Axis::Arousal,
                0.3,
                0.5,
                vec![Action::Prune(ToolId::from("quote"))],
            )
            .with_direction(Direction::Rising)]),
            protected.clone(),
        ))
        .actuator(McpActuator::new(
            Catalog::new(vec![
                Tool::new("quote", "Quote"),
                Tool::new("human", "Human handoff"),
            ]),
            protected,
        ))
        .build()?;

    let count = Arc::new(AtomicUsize::new(0));
    let bindings = [ToolName::Quote, ToolName::Human]
        .into_iter()
        .map(|tool| {
            let count = Arc::clone(&count);
            ToolBinding {
                id: ToolId::from(tool.as_str()),
                definition: ToolDefinition {
                    description: format!("{} comparison callback", tool.as_str()),
                    input_schema: json!({"type":"object","additionalProperties":false}),
                },
                handler: Box::new(move |_| {
                    let ordinal = count.fetch_add(1, Ordering::SeqCst) + 1;
                    Ok(json!({"tool": tool.as_str(), "ordinal": ordinal}))
                }),
            }
        })
        .collect();
    let runtime = Runtime::new(
        session.into(),
        pipeline,
        bindings,
        HashSet::from([ToolId::from("quote"), ToolId::from("human")]),
    )?;
    Ok((runtime, queue, count))
}

fn invalid(message: impl Into<String>) -> Box<dyn Error> {
    io::Error::new(io::ErrorKind::InvalidData, message.into()).into()
}

fn invalid_input(message: impl Into<String>) -> Box<dyn Error> {
    invalid(format!("INVALID_INPUT: {}", message.into()))
}

#[allow(clippy::cast_possible_truncation)]
fn unit(value: f64) -> f32 {
    value as f32
}

fn parse_and_validate(input: &str) -> Result<Vec<Episode>, Box<dyn Error>> {
    let episodes: Vec<Episode> = serde_json::from_str(input)
        .map_err(|error| invalid_input(format!("invalid JSON contract: {error}")))?;
    if episodes.is_empty() {
        return Err(invalid_input("at least one episode is required"));
    }
    let mut ids = HashSet::new();
    for episode in &episodes {
        if episode.id.trim().is_empty() || !ids.insert(&episode.id) {
            return Err(invalid_input("episode ids must be non-empty and unique"));
        }
        if episode.events.is_empty() {
            return Err(invalid_input(format!(
                "episode {} has no events",
                episode.id
            )));
        }
        for event in &episode.events {
            if let Event::Observation(event) = event {
                for (name, value) in [
                    ("observation value", event.value),
                    ("confidence", event.confidence),
                ] {
                    if !value.is_finite() || !(0.0..=1.0).contains(&value) {
                        return Err(invalid_input(format!(
                            "episode {} {name} must be finite and in [0, 1]",
                            episode.id
                        )));
                    }
                }
            }
        }
    }
    Ok(episodes)
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut args = env::args_os().skip(1);
    let input = match (args.next(), args.next()) {
        (None, None) => {
            let mut input = String::new();
            io::stdin().read_to_string(&mut input)?;
            input
        }
        (Some(path), None) => fs::read_to_string(path)?,
        _ => return Err(invalid("usage: lifecycle_comparison [INPUT.json]")),
    };
    let episodes = parse_and_validate(&input)?;
    let mut results = Vec::with_capacity(episodes.len());

    for episode in episodes {
        let (mut runtime, queue, count) = runtime(&episode.id)?;
        let mut outputs = Vec::new();
        let mut latest_timestamp = 0;

        for event in episode.events {
            match event {
                Event::Observation(event) => {
                    let ObservationEvent {
                        value,
                        confidence,
                        timestamp_ms,
                        ..
                    } = event;
                    let value = unit(value);
                    let confidence = unit(confidence);
                    latest_timestamp = timestamp_ms;
                    queue.borrow_mut().push_back(Some(SignalReading {
                        value,
                        axis: Axis::Arousal,
                        confidence,
                        timestamp_ms,
                    }));
                    runtime.observe(&AudioWindow {
                        samples: Vec::new(),
                        sample_rate: 16_000,
                        timestamp_ms,
                    })?;
                }
                Event::Silence(_) => {
                    queue.borrow_mut().push_back(None);
                    runtime.observe(&AudioWindow {
                        samples: Vec::new(),
                        sample_rate: 16_000,
                        timestamp_ms: latest_timestamp,
                    })?;
                }
                Event::Expire(event) => runtime.expire_before(event.cutoff_ms)?,
                Event::Call(event) => {
                    let CallEvent {
                        tool, timestamp_ms, ..
                    } = event;
                    latest_timestamp = timestamp_ms;
                    let before = count.load(Ordering::SeqCst);
                    let result = runtime.call(
                        CallRequest {
                            session: episode.id.clone(),
                            tool: tool.as_str().into(),
                            arguments: json!({}),
                            confirmation: None,
                        },
                        timestamp_ms,
                    );
                    let after = count.load(Ordering::SeqCst);
                    let (action, receipt) = match result {
                        Ok(receipt) => ("allow", Some(receipt)),
                        Err(error) if error == "tool currently unavailable" => ("block", None),
                        Err(error) => {
                            return Err(invalid(format!(
                                "episode {} call failed unexpectedly: {error}",
                                episode.id
                            )))
                        }
                    };
                    outputs.push(CallOutput {
                        timestamp_ms,
                        tool,
                        action,
                        callback_delta: after - before,
                        receipt,
                    });
                }
            }
        }
        results.push(EpisodeOutput {
            id: episode.id,
            outputs,
            callback_count: count.load(Ordering::SeqCst),
        });
    }
    println!("{}", serde_json::to_string(&results)?);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::parse_and_validate;

    #[test]
    fn rejects_a_later_bad_episode_before_returning_the_batch() {
        let input = r#"[
            {"id":"valid","events":[{"kind":"call","tool":"human","timestamp_ms":0}]},
            {"id":"bad","events":[{"kind":"observation","value":1.00000000001,"confidence":0.5,"timestamp_ms":0}]}
        ]"#;
        let Err(error) = parse_and_validate(input) else {
            panic!("invalid later episode was accepted");
        };
        assert!(error.to_string().starts_with("INVALID_INPUT:"));
    }
}
