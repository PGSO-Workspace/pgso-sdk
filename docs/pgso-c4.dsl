workspace "PGSO" "Gobernanza Paralingüística para Orquestación de Estado — modelo C4 del SDK en Rust que le da oídos a un agente y gobierna de forma determinista qué herramientas expone su catálogo." {

    !identifiers hierarchical

    model {

        # ---------------------------------------------------------------------
        # Personas y sistemas externos
        # ---------------------------------------------------------------------

        interlocutor = person "Interlocutor" "La persona que le habla al agente. PGSO percibe *cómo* suena (tono, energía, jitter, ritmo), no lo que dice."

        agent = softwareSystem "Agente de IA (Host)" "Un agente basado en LLM que mantiene un catálogo de herramientas y las invoca para actuar. Integra y controla el SDK de PGSO; PGSO es agnóstico al LLM." {
            tags "External"
        }

        llm = softwareSystem "Proveedor de LLM" "El modelo que respalda al agente (p. ej. Claude). Recibe el prompt y las herramientas *expuestas*; fuera del alcance de PGSO." {
            tags "External"
        }

        mcpClient = softwareSystem "Cliente MCP / Runtime del agente" "Consume el catálogo de herramientas gobernado vía el Model Context Protocol (tools/list + notifications/tools/list_changed)." {
            tags "External"
        }

        # ---------------------------------------------------------------------
        # Sistema en foco: el SDK de PGSO
        # ---------------------------------------------------------------------

        pgso = softwareSystem "SDK de PGSO" "Capa de gobernanza externa y determinista (un workspace de Rust). Percibe el canal paralingüístico a partir de audio crudo (sin ASR) y gobierna de forma determinista qué herramientas expone el catálogo del agente. Falla hacia la inacción, no hacia la intervención." {

            # --- Contenedores = crates de Rust ---

            core = container "pgso-core" "Núcleo de gobernanza puro y determinista: traits Signal/Actuator, DecisionEngine, RuleEngine + pgso_rules!, AuditLog y el pipeline genérico Pgso. Cero dependencias ML/HTTP/IO (solo thiserror). [INV-1]" "Crate de Rust" {

                # --- Componentes dentro del núcleo ---

                signalTrait = component "Signal (trait)" "Frontera de percepción. extract(window) -> Vec<SignalReading>. Intercambiable; los extractores concretos la implementan, nunca el núcleo. [INV-2]" "Trait de Rust" {
                    tags "Trait"
                }

                actuatorTrait = component "Actuator (trait)" "Frontera de acción. apply(ScopeDecision) gobierna el catálogo servido. Transporte intercambiable; el núcleo nunca alcanza hacia afuera. [INV-2/INV-4]" "Trait de Rust" {
                    tags "Trait"
                }

                pipeline = component "Pipeline Pgso + builder" "Driver genérico Pgso<S, A>. process_window() ejecuta: Signal::extract -> DecisionEngine -> RuleEngine -> Actuator::apply. PgsoBuildError tipado; sin panics." "Struct de Rust"

                decisionEngine = component "DecisionEngine" "Línea base del hablante en tres capas (prior poblacional -> media de calentamiento -> EMA). Exige una desviación *sostenida* (histéresis) y se abstiene por debajo del umbral de confianza. Disparado por nivel, sin reloj/RNG. [G4/INV-3]" "Struct de Rust"

                ruleEngine = component "RuleEngine + pgso_rules!" "Mapea un EngineOutput sostenido a Actions de gobernanza vía reglas en tiempo de compilación. Aplica la allowlist inviolable: un Prune de una herramienta protegida se degrada a RequireStepUp. [G2/G3]" "Struct + macro de Rust"

                auditLog = component "AuditLog" "Registra un AuditRecord (señal, eje, umbral, id de regla, timestamp del llamador) por cada intervención *y* reversión — trazable y reversible. [G5/INV-5]" "Struct de Rust"

                domainTypes = component "Tipos de dominio" "Catalog, Tool, ToolId, SignalReading, EngineOutput, ScopeDecision, Action, AuditRecord, AudioWindow — los datos que cruzan las fronteras." "Tipos de Rust"

                # Relaciones a nivel de componente (flujo de control/datos dentro del núcleo)
                pipeline -> signalTrait "extract(window) -> readings"
                pipeline -> decisionEngine "alimenta SignalReadings"
                decisionEngine -> ruleEngine "EngineOutput (desviación sostenida)"
                ruleEngine -> actuatorTrait "ScopeDecision (Prune / RequireStepUp / Allow)"
                ruleEngine -> auditLog "escribe AuditRecord"
                pipeline -> actuatorTrait "aplica la decisión / restaura al volver a nominal"
                decisionEngine -> domainTypes "usa"
                ruleEngine -> domainTypes "usa"
            }

            signalEgemaps = container "pgso-signal-egemaps" "Impl Signal por defecto: extractor DSP estilo eGeMAPS en Rust puro (F0, energía, jitter, shimmer, voicing) mapeado a una lectura de arousal. Sin runtime ML, sin descarga de modelo. Lecturas explicables." "Crate de Rust"

            signalOnnx = container "pgso-signal-onnx" "Planeado: extractor neuronal wav2vec2 (ort / ONNX Runtime), opt-in, para la ablación DSP vs. neuronal." "Crate de Rust" {
                tags "Planned"
            }

            actuatorLocal = container "pgso-actuator-local" "Actuator de referencia en memoria. Aplica decisiones de forma determinista (sin I/O), restaura con Allow (G1) y respalda la allowlist inviolable (G2)." "Crate de Rust"

            actuatorMcp = container "pgso-actuator-mcp" "Actuator que sirve el catálogo gobernado vía MCP: construye el result de tools/list y rastrea cuándo debe emitirse notifications/tools/list_changed. Agregado con cero líneas de diff en pgso-core (prueba de agnosticismo M5)." "Crate de Rust"

            actuatorHttp = container "pgso-actuator-http" "Planeado: adaptador de transporte HTTP / patrón Olive." "Crate de Rust" {
                tags "Planned"
            }
        }

        # ---------------------------------------------------------------------
        # Relaciones de Contexto del Sistema
        # ---------------------------------------------------------------------

        interlocutor -> agent "Conversa con (habla)"
        interlocutor -> pgso "Voz — audio crudo capturado en paralelo (ventanas mono a 16 kHz)"
        agent -> pgso "Conecta y controla el pipeline; transmite ventanas de audio por turno"
        pgso -> agent "Devuelve el catálogo de herramientas gobernado (qué se expone este turno)"
        agent -> llm "Envía el prompt + las herramientas expuestas; recibe llamadas a herramientas"
        pgso -> mcpClient "Sirve el catálogo gobernado (MCP tools/list)"

        # ---------------------------------------------------------------------
        # Relaciones de contenedores (flujo de datos: audio -> lecturas -> decisión -> catálogo)
        # ---------------------------------------------------------------------

        interlocutor -> pgso.signalEgemaps "Ventanas de audio crudo (mono a 16 kHz)"
        agent -> pgso.core "Construye y controla el pipeline Pgso (builder, process_window)"

        pgso.signalEgemaps -> pgso.core "impl Signal — SignalReading (valor, eje, confianza)"
        pgso.signalOnnx -> pgso.core "impl Signal (planeado)"

        pgso.core -> pgso.actuatorLocal "impl Actuator — ScopeDecision + AuditRecord"
        pgso.core -> pgso.actuatorMcp "impl Actuator — ScopeDecision + AuditRecord"
        pgso.core -> pgso.actuatorHttp "impl Actuator (planeado)"

        pgso.actuatorLocal -> agent "Expone el catálogo gobernado en memoria"
        pgso.actuatorMcp -> mcpClient "Catálogo gobernado como JSON de tools/list (+ list_changed)"
    }

    # =====================================================================
    # Vistas
    # =====================================================================

    views {

        systemContext pgso "ContextoDelSistema" "Cómo PGSO se ubica junto al agente: escucha al interlocutor y gobierna el catálogo de herramientas servido." {
            include *
            autolayout lr
        }

        container pgso "Contenedores" "El workspace de Rust de PGSO: un núcleo puro flanqueado por dos fronteras de extensión simétricas e intercambiables — Signal (percepción) y Actuator (acción)." {
            include *
            autolayout lr
        }

        component pgso.core "ComponentesDelNucleo" "Dentro de pgso-core: el pipeline determinista desde la frontera de percepción hasta la frontera de acción." {
            include *
            autolayout lr
        }

        dynamic pgso.core "CicloDeGobernanza" "Una ventana de audio a través del núcleo determinista." {
            pgso.core.pipeline -> pgso.core.signalTrait "1. extract() -> SignalReadings"
            pgso.core.pipeline -> pgso.core.decisionEngine "2. línea base + histéresis + abstención"
            pgso.core.decisionEngine -> pgso.core.ruleEngine "3. EngineOutput sostenido"
            pgso.core.ruleEngine -> pgso.core.auditLog "4. registra AuditRecord"
            pgso.core.ruleEngine -> pgso.core.actuatorTrait "5. ScopeDecision (allowlist aplicada)"
            pgso.core.pipeline -> pgso.core.actuatorTrait "6. apply -> catálogo servido / restaura al volver a nominal"
            autolayout lr
        }

        styles {
            element "Person" {
                shape person
                background #08427b
                color #ffffff
            }
            element "Software System" {
                background #1168bd
                color #ffffff
            }
            element "External" {
                background #999999
                color #ffffff
            }
            element "Container" {
                background #438dd5
                color #ffffff
            }
            element "Component" {
                background #85bbf0
                color #000000
            }
            element "Trait" {
                shape hexagon
            }
            element "Planned" {
                background #d4d4d4
                color #555555
                border dashed
            }
        }

        theme default
    }
}
