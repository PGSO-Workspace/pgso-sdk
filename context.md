# PGSO-SDK (Paralinguistic Governance for State Orchestration) - Technical Context

## 1. Project Goal
Build an agnostic, high-performance neuro-symbolic middleware SDK in Rust. Its purpose is to intercept LLM Tool Calling (MCP standard or standard JSON arrays) and govern the agent's tool catalog dynamically — non-punitively (require step-up by default, prune only when warranted) — based on a Multimodal Discrepancy metric (Acoustic vs. Semantic discrepancy). (The earlier "prevent unsafe executions during acute user stress" framing is retired: PGSO governs on paralinguistic incongruence between what is said and how it is said, not on a "user stress" judgement.)

## 2. Core Architecture & Stack
* Language: Rust (2021 edition)
* Async Runtime: `tokio`
* JSON Manipulation: `serde`, `serde_json`
* ML Edge Inference (Audio): `ort` (ONNX Runtime)
* Pattern: Builder Pattern for SDK initialization (`PgsoBuilder`).

## 3. The Math Engine (Discrepancy)
The SDK calculates a deterministic Discrepancy Score (D):
D = |R_text - R_voice| * (1 + Sigma_jitter)
* R_text: Semantic risk (0.0 to 1.0)
* R_voice: Vocal activation/stress risk (0.0 to 1.0)
* Sigma_jitter: Normalized variance of vocal jitter.

## 4. Finite State Machine (FSM) Rules
The discrepancy score feeds an FSM that dictates the Tool Pruning policy:
* S0 (Nominal): D < threshold AND R_voice < 0.3. Action: Return 100% original tools JSON.
* S1 (Mild Incongruence): D >= threshold AND R_voice < 0.5. Action: Inject warning tags into context.
* S2 (Stress Detected): D < threshold AND R_voice >= 0.5. Action: Prune high-latency tools.
* S3 (Panic / Contradiction): D >= threshold AND R_voice >= 0.7. Action: Strict JSON pruning, remove state-modifying/destructive tools.
* S4 (Affective Containment): Forced by S3. Action: Expose ONLY emotional support safe-tools.
* S5 (Fallback/Audit): Pipeline exception. Action: Return empty tools array, strict logging to disk.

## 5. Coding Guidelines for this Session
* Follow strict Rust idiomatic naming (`snake_case` for modules/functions, `UpperCamelCase` for Structs/Enums).
* Ensure zero-copy parsing where possible when manipulating `serde_json::Value`.
* The SDK MUST remain LLM-agnostic. It receives a JSON string of tools and a byte array of audio, and returns a safe JSON string of tools.
