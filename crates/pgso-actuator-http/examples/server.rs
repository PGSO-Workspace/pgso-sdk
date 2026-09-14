use pgso_actuator_http::{router, Runtime, ToolBinding};
use pgso_actuator_mcp::{McpActuator, ToolDefinition};
use pgso_core::*;
use serde_json::json;
use std::{
    collections::HashSet,
    sync::{Arc, Mutex},
};
struct NoAudio;
impl Signal for NoAudio {
    fn extract(&mut self, _: &AudioWindow) -> Vec<SignalReading> {
        vec![]
    }
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let secret = std::env::var("PGSO_BEARER")?;
    let pipeline = Pgso::builder()
        .signal(NoAudio)
        .engine(DecisionEngine::try_new(EngineConfig {
            confidence_threshold: 0.5,
            deviation_threshold: 0.2,
            hysteresis_window: 2,
            ema_alpha: 0.1,
            warmup_readings: 10,
            population_prior: 0.5,
        })?)
        .rules(RuleEngine::new(RuleSet::new(vec![]), HashSet::new()))
        .actuator(McpActuator::new(
            Catalog::new(vec![Tool::new("echo", "Echo")]),
            HashSet::new(),
        ))
        .build()?;
    let runtime = Runtime::new(
        "demo".into(),
        pipeline,
        vec![ToolBinding {
            id: ToolId::from("echo"),
            definition: ToolDefinition {
                description: "Echo a message".into(),
                input_schema: json!({"type":"object","properties":{"message":{"type":"string","maxLength":1000}},"required":["message"],"additionalProperties":false}),
            },
            handler: Box::new(|args| Ok(args.clone())),
        }],
        HashSet::from([ToolId::from("echo")]),
    )?;
    let app = router(Arc::new(Mutex::new(runtime)), secret)?;
    let bind = std::env::var("PGSO_BIND").unwrap_or_else(|_| "127.0.0.1:3000".into());
    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
