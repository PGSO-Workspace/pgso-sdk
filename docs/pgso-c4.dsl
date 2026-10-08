workspace "PGSO — integración de referencia" "C4 del runtime HTTP implementado. Los crates son componentes de código, no procesos independientes; véase architecture/README.md para las vistas 4+1 y la variante bridge." {
    !identifiers hierarchical
    model {
        operator = person "Integrador del agente" "Actor secundario: configura señal, reglas, permisos y política temporal."
        interlocutor = person "Interlocutor" "Persona que conversa con el agente de voz; no consume directamente la API del SDK."
        agent = softwareSystem "Agente de IA de voz" "Consumidor principal de PGSO. Su host integra la señal y propone llamadas; descubrir herramientas no autoriza ejecutarlas."
        audio = softwareSystem "Captura y framing del host" "Entrega PCM y tiempos originales; fuera del núcleo."
        effects = softwareSystem "Servicios de negocio" "Efectos ejecutados por callbacks confiables."
        storage = softwareSystem "Persistencia del host" "Destino opcional de trazas; PGSO mantiene auditoría en memoria."
        app = softwareSystem "Aplicación que integra PGSO" "Integración de referencia con frontera de ejecución HTTP/MCP; no describe producción de Sami." {
            runtime = container "Proceso host con Runtime PGSO" "Una instancia por sesión; serializa acceso y verifica permisos antes de efectos." "Rust / HTTP / JSON-RPC" {
                transport = component "Router HTTP/MCP" "Bearer; GET /tools, GET /context, POST /call y POST /mcp. Sin SSE ni notificaciones de servidor." "pgso-actuator-http"
                execution = component "Runtime" "Sesión, reloj, permisos del host, esquemas, confirmación de un uso y recibos." "pgso-actuator-http"
                signal = component "Extractor acústico" "Signal intercambiable; referencia eGeMAPS-style. Confianza de voicing, no probabilidad emocional." "pgso-signal-egemaps"
                pipeline = component "Pgso" "Procesa lecturas y reconcilia contribuciones por eje; expiry explícito del host." "pgso-core"
                engine = component "DecisionEngine" "Prior, warmup y EMA configurables; desviación, histéresis y abstención." "pgso-core"
                rules = component "RuleEngine" "Predicados, dirección, acciones y protección de herramientas." "pgso-core"
                catalog = component "McpActuator" "Reconcilia atómicamente catálogo, step-up y directivas; no ejecuta efectos." "pgso-actuator-mcp"
                audit = component "Auditoría en memoria" "AuditRecords, PolicyTransitions y ExecutionRecords; no es log durable ni replay completo." "SDK"
                transport -> execution "Dispatch bajo mutex de sesión"
                execution -> pipeline "observe / expire_before"
                pipeline -> signal "Signal::extract"
                pipeline -> engine "Procesa cada lectura"
                pipeline -> rules "Evalúa salida sostenida"
                pipeline -> catalog "Actuator::reconcile"
                pipeline -> audit "Registra decisiones y cambios efectivos"
                execution -> catalog "Consulta política vigente"
                execution -> audit "Registra recibo de intento"
                execution -> effects "Callback registrado tras autorización"
            }
        }
        interlocutor -> agent "Conversa mediante voz"
        agent -> audio "Su host controla captura y framing"
        operator -> app "Configura y controla mediante API Rust confiable"
        audio -> app.runtime "Ventanas PCM por API del host; no endpoint público de audio"
        agent -> app.runtime "Consulta catálogo y propone tools/call o POST /call"
        app.runtime -> storage "El host drena y persiste registros"
        deploymentEnvironment "Referencia HTTP" {
            deploymentNode "Máquina del integrador" "Topología ilustrativa, no infraestructura productiva verificada" "Host" {
                containerInstance app.runtime
            }
        }
    }
    views {
        systemContext app "ContextoDelSistema" "Fronteras de la integración de referencia." {
            include *
            include interlocutor
            autolayout lr
        }
        container app "Contenedores" "Unidad ejecutable; los crates no son servicios separados." {
            include *
            autolayout lr
        }
        component app.runtime "ComponentesDelRuntime" "Percepción, política y ejecución son responsabilidades distintas." {
            include *
            autolayout lr
        }
        dynamic app.runtime "CicloDeGobernanza" "Flujo condicionado a lectura sostenida y reconciliación exitosa." {
            app.runtime.execution -> app.runtime.pipeline "1. observe"
            app.runtime.pipeline -> app.runtime.signal "2. extract"
            app.runtime.pipeline -> app.runtime.engine "3. evaluar lectura"
            app.runtime.pipeline -> app.runtime.rules "4. evaluar reglas si Triggered"
            app.runtime.pipeline -> app.runtime.catalog "5. reconcile contribuciones"
            app.runtime.pipeline -> app.runtime.audit "6. registrar commit efectivo"
            autolayout lr
        }
        deployment app "Referencia HTTP" "DespliegueHTTP" "Una posible integración, no requisito de despliegue del SDK." {
            include *
            autolayout lr
        }
        styles {
            element "Element" {
                color #ffffff
                background #245b87
            }
            element "Person" {
                shape person
            }
        }
    }
}
