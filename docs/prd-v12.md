# PGSO — Better Agents by Listening
## PRD Final (v12) · Diseño congelado · listo para implementar

**Paralinguistic Governance for State Orchestration** — un SDK que le da *oídos* a los agentes: percibe **cómo** habla el interlocutor (no solo qué dice) y gobierna el comportamiento del agente en consecuencia, de forma determinista y auditable.

**Fecha:** 1 jun 2026 · **Equipo:** tesista A (núcleo) · tesista B (evaluación) · co-autor (escritura)
**Validación de dominio:** AI sellers (real) · **Caso narrativo:** coaching / AI psychologist

---

## 1. Visión e impacto (la bandera)
Los agentes de hoy responden igual a *"me interesa"* dicho con entusiasmo y dicho con fastidio: leen las palabras, son sordos al tono. Su personalidad —impuesta en el prompt— es **fija**: un seller "consultivo y paciente" es paciente *siempre*, aunque debería leer la frustración y cambiar.

**PGSO hace que la personalidad de un agente sea contingente al canal paralingüístico.** Al darle oídos, la personalidad deja de ser un texto estático y se vuelve un texto + un repertorio de capacidades que se **modula según cómo suena el interlocutor**. El impacto no es "agentes más inteligentes" — es **agentes socialmente competentes**:
- Un **AI seller** que, al oír rechazo bajo un "sí" cortés, deja de empujar el cierre y pasa a clarificar → vende mejor por leer la sala.
- Un **AI psychologist** que, al oír angustia, pierde la herramienta de "avanzar de tema" y gana un protocolo de contención → impacta mejor por contener.

**Frase de impacto (la tesis vendible):** *la personalidad impuesta a un agente debe expresarse de forma contingente al estado del interlocutor, no de forma fija. PGSO es el mecanismo que lo permite.*

> **Honestidad de alcance (lo que NO afirmamos aún):** "los sellers venden más con PGSO" es la **hipótesis** que la validación de dominio mide, no una premisa. Afirmamos el mecanismo (gobernanza contingente) y *medimos* el impacto; no lo asumimos. Esta distinción es lo que hace la tesis inatacable.

---

## 2. La idea, madura (qué reclamamos y qué no)
Tras un ciclo completo de diseño y un experimento de calibración (Phase 0), la idea converge a su forma honesta:

- **NO reclamamos** detectar emoción de forma fiable (campo saturado; Barrett 2019: el afecto carece de firmas acústicas universales).
- **NO reclamamos** el mecanismo de gobernanza de herramientas como novedad (ya es commodity: Microsoft Agent Governance Toolkit, Lunar MCPX, OpenAI `is_enabled` — abril-mayo 2026).
- **SÍ reclamamos** lo único que ningún trabajo de 2026 tiene: **gobernar el comportamiento de un agente a partir del canal paralingüístico** — una señal que vive fuera del canal textual y que los demás sistemas de gobernanza no perciben.
- **Hipótesis central a validar:** la **discrepancia say–hear** (relación entre lo que se dice / léxico y cómo se dice / prosodia) es una señal de gobernanza viable en habla espontánea real. Phase 0 mostró señal prosódica fuerte (5.8σ) sobre habla actuada; la divergencia con texto no superó al audio solo *en ese corpus*, por lo que v1 arranca prosody-only y la discrepancia se reincorpora como hipótesis a probar en dominio real.

---

## 3. Posición frente a la garantía (resuelve la tensión del no-determinismo)
La prosodia es probabilística y nunca será una garantía — y no necesita serlo. La división es limpia:
- **La incertidumbre vive en la percepción** (la señal paralingüística): es el mundo, siempre es incierto. Se *cuantifica* (confianza calibrada + abstención), no se esconde.
- **La garantía vive en la acción** (el actuador): código verificable. Si una herramienta no se expone, el modelo no puede invocarla — eso sí es determinista.

PGSO no afirma "percibo de forma determinista"; afirma "percibo un canal que nadie percibe, y lo conecto a un actuador determinista". El no-determinismo de la señal deja de ser el talón de Aquiles porque deja de ser el claim de garantía.

---

## 4. Arquitectura de crates (Rust idiomático, independiente)

```
pgso-core            ← NÚCLEO PURO. Cero ML, cero HTTP, cero I/O pesado.
  ├── trait Signal       { fn extract(&AudioWindow) -> SignalReading }   ← qué percibe
  ├── trait Actuator     { fn current_catalog(); fn apply(ScopeDecision) } ← dónde actúa
  ├── DecisionEngine     baseline EMA tres-capas + histéresis + abstención
  ├── RuleEngine         pgso_rules! (compile-time) → ScopeDecision
  └── AuditLog           registro determinista (señal, ejes, umbral, acción, ts)
        compila en segundos · 100% testeable con proptest · determinista

pgso-signal-onnx     ← impl de Signal (feature "onnx") — el extractor de Phase 0, ya validado
pgso-signal-egemaps  ← impl de Signal (feature "egemaps") — DSP ligero, auditable (referencia post-MVP)

pgso-actuator-local  ← impl de Actuator de REFERENCIA, propia y mínima (feature "local")
pgso-actuator-http   ← adaptador opcional: patrón estilo-Olive (feature "http")
pgso-actuator-mcp    ← adaptador opcional: MCP tools/list + list_changed (feature "mcp")

pgso-py / pgso-node  ← bindings opt-in (PyO3 / napi-rs), crates separados
```

**Independencia (resuelve "¿no es depender de una caja negra?"):** el actuador de **referencia es propio y mínimo** (`pgso-actuator-local`). Microsoft / MCP / Olive son **adaptadores opcionales detrás del mismo trait `Actuator`** — se enchufan *si y solo si* se auditan y convienen. Nunca dependes de una caja negra; tu trait te mantiene soberano, igual que con la elección de extractor de señal.

**Dos puntos de extensión simétricos:** `Signal` (la percepción, intercambiable) y `Actuator` (la acción, intercambiable). El núcleo no sabe qué hay detrás de ninguno. Eso es lo que hace a PGSO un SDK y no un script.

---

## 5. El mecanismo end-to-end
1. El interlocutor habla → PGSO toma audio crudo en paralelo al agente.
2. `Signal::extract` → desviación paralingüística vs. baseline del hablante (+ confianza). Sin ASR en v1.
3. `DecisionEngine` → ¿cruza umbral?, ¿pasa histéresis?, ¿confianza suficiente o abstención? → estado.
4. `RuleEngine` → el estado dispara una regla de personalidad contingente (`pgso_rules!`).
5. `Actuator::apply` → al pedir el agente su catálogo, recibe el subconjunto permitido + un prompt modulado. La personalidad se expresa contingente al tono.
6. **No punitiva:** escala fricción / cambia protocolo / repregunta. Falso positivo = "una repregunta", nunca un bloqueo injusto.

---

## 6. Personalidad contingente (el marco de impacto)
La personalidad de un agente vive en **prompt + herramientas**. PGSO gobierna ambos según la señal. Por eso PGSO no es "filtrar herramientas" — es **el mecanismo por el que una personalidad estática se vuelve adaptativa**:

```rust
let rules = pgso_rules! {
    // AI seller "consultivo": al oír tensión sostenida, deja de empujar y clarifica
    when Prosody.arousal_dev > High && Prosody.confidence > 0.6 {
        action: RequireStepUp("cerrar_venta"),
        action: InjectPrompt("Señal de tensión. Cambia a clarificación; no empujes el cierre.")
    }
};
let pgso = Pgso::builder()
    .signal(Prosody::relative_to_speaker_baseline())  // percepción (incierta, cuantificada)
    .with_rules(rules)
    .actuator(LocalActuator::default())               // acción (determinista, propia)
    .build()?;
```

---

## 7. Decisiones técnicas congeladas
| Componente | Decisión | Razón |
|---|---|---|
| Núcleo | Rust + Tokio, puro | Determinista, auditable, sin GC, testeable |
| Señal MVP | ONNX (`ort`), el extractor de Phase 0 | Ya validado; valida la arquitectura rápido |
| Señal de referencia (post-MVP) | eGeMAPS (DSP) | Ligero, CPU, streaming, **auditable** |
| Baseline | tres-capas (poblacional→EMA→personal) | Control de falsos positivos (sistema mono-señal) |
| Actuador de referencia | propio y mínimo (`local`) | Independencia; nunca dependes de caja negra |
| Actuadores opcionales | HTTP (Olive), MCP | Agnosticismo al transporte; se auditan antes de usar |
| ASR | **fuera de v1** | prosody-only; la discrepancia léxica es hipótesis futura |
| Reglas | `pgso_rules!` + política firmada recargable | Compile-time safety + auditabilidad en producción |
| Bindings | PyO3/napi-rs, crates aparte, opt-in | El núcleo no conoce Python/Node |

---

## 8. Validación (la honestidad que sostiene la tesis)
- **Mecanismo (100% falsable, corazón de ingeniería):** corrección de filtrado (`proptest`: la herramienta vetada nunca aparece); latencia señal-a-decisión p50/p95/p99; histéresis (pico aislado no dispara); determinismo (misma traza → misma acción); **prueba de la abstracción** (un 2º `Signal` y un 2º `Actuator` mock entran sin tocar el núcleo).
- **Señal:** validada en Phase 0 (5.8σ); ablación eGeMAPS vs. ONNX y baseline relativo vs. absoluto.
- **Impacto (dominio = AI sellers):** una regla de personalidad contingente concreta; agente con PGSO vs. agente sordo al canal; métrica de resultado (conversión / seguimientos apropiados / evitación de cierres prematuros). **Aquí se prueba la discrepancia say-hear en habla espontánea real** — la frontera honesta de Phase 0 y el motor de validación del impacto.

---

## 9. Posicionamiento (delta verificado)
- vs. **Microsoft Agent Governance Toolkit / Lunar MCPX / OpenAI is_enabled** → gobiernan por señal textual/lógica; PGSO añade el canal paralingüístico que ninguno percibe. PGSO se enchufa a ellos como adaptador, no compite con su cañería.
- vs. **Beyond Text / E-STEER / AffectMind** → meten la emoción dentro del modelo (opaco); PGSO la convierte en gobernanza externa determinista y auditable.
- vs. **voice agents nativos (Realtime API)** → oyen prosodia pero la funden sin garantía; PGSO produce control verificable fuera de banda.

---

## 10. Riesgos y mitigaciones
| Riesgo | Mitigación |
|---|---|
| La señal no generaliza de sitcom a habla real | Es lo que mide la validación de sellers; declararlo, no ocultarlo. No es existencial: el hallazgo (positivo o negativo) es publicable |
| Falsos positivos (mono-señal) | baseline relativo + histéresis + abstención + reacción no punitiva |
| "El mecanismo ya existe (Microsoft)" | Cierto; la novedad es la *señal*, no la cañería. PGSO se enchufa a ese mecanismo |
| "¿Dependes de una caja negra de Microsoft?" | No: actuador de referencia propio; Microsoft es adaptador opcional auditado |
| Hook del transporte no se comporta como se asume | Hito 1 lo verifica el día uno con un actuador local trivial |
| Sobre-alcance con velocidad alta | Hitos diminutos y verificables; no construir todo de una |

---

## 11. ROADMAP (columna vertebral de hitos)
Principio: cada fase termina en un hito verificable y diminuto. No se avanza sin pasarlo.

1. **Actuador de referencia + prueba de exposición (riesgo primero).** `pgso-actuator-local`: catálogo de 3-4 herramientas dummy; ocultar una por flag hardcodeado. → **HITO 1:** el agente recibe una herramienta menos por un flag. Sin señal, sin núcleo. Todo cuelga de aquí.
2. **Núcleo determinista.** `pgso-core`: traits, `DecisionEngine` (baseline+histéresis+abstención), `RuleEngine`, `AuditLog`. → **HITO 2:** `proptest` verde sobre trazas sintéticas; histéresis y abstención correctas; cero dependencias de ML.
3. **Señal paralingüística en streaming.** `pgso-signal-onnx` (port de Phase 0 a ventana deslizante + VAD) detrás del trait; baseline relativo. → **HITO 3:** audio → `SignalReading` con desviación + confianza, dentro del presupuesto de latencia (medir p50/p95/p99).
4. **Cableado end-to-end.** Signal → Decision → Rule → Actuator. → **HITO 4:** prosodia alterada sostenida → el catálogo pierde la herramienta vetada + prompt modulado, determinista y auditable. **PoC completo de PGSO.**
5. **Prueba de agnosticismo.** `pgso-actuator-mcp` como 2º adaptador + `Signal` mock. → **HITO 5:** el mismo PoC corre sobre MCP cambiando solo el adaptador, sin tocar `pgso-core`.
6. **Validación de impacto (AI sellers).** Audio espontáneo real; regla de personalidad contingente; agente con PGSO vs. sordo. → **HITO 6:** métrica que muestre que la gobernanza por canal paralingüístico cambia el comportamiento de forma medible y deseable. Valida (o no) la señal fuera de sitcom.
7. **Endurecimiento + escritura.** Migrar a `pgso-signal-egemaps` (auditable); política firmada; bindings; release open-source como artefacto evaluable; workshop/preprint → journal aplicado.

---

## 12. Por qué esta es la idea (cierre)
Dio la vuelta completa y volvió a su origen —"darle oídos al agente para que escuche y reaccione"— pero ahora cada pieza sobrevivió a un ataque deliberado: el firewall biométrico cayó, la divergencia-como-hecho cayó, el mecanismo-como-novedad cayó. Lo que quedó en pie es lo único que siempre estuvo en pie y que ningún trabajo de 2026 tiene: **gobernar agentes a partir del canal paralingüístico.** Es novedosa (confirmada por ausencia en la literatura), defendible (la garantía está en la acción, no en la percepción), independiente (actuador propio, todo lo demás detrás de traits), y alineada con la visión: **Better Agents by Listening.**

La idea es segura. El resultado —cuánto mejora a los sellers, si la discrepancia se sostiene en habla real— es lo que vas a *descubrir*, y está bien que así sea: esa es la postura correcta para construir.

---

> **Pendientes de verificación (Fase 1, semana 1):** (1) forma exacta del listado de herramientas del transporte de referencia que uses; (2) latencia del extractor ONNX en streaming sobre CPU objetivo; (3) confirmar que un 2º `Signal`/`Actuator` mock entra sin tocar el núcleo; (4) acceso a audio espontáneo de AI sellers para Fase 6; (5) referencias del related work (Microsoft Agent Governance Toolkit 2026; Lunar MCPX 2026; Beyond Text arXiv:2402.03494; Barrett 2019).
