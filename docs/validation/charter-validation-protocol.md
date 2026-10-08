# Validación de PGSO conforme al Charter

Fecha: 2026-10-08. **Borrador de desarrollo; no prerregistro aprobado ni resultado.**
Este documento prepara una ejecución posterior. No modifica el Charter, los
objetivos registrados en PhDude, el manuscript ni resultados históricos.

## Objetivo y frontera

Desarrollar un SDK de gobernanza determinista y auditable del tool-calling,
basado en señales paralingüísticas, para el control del comportamiento de agentes
de voz autónomos a partir del estado del interlocutor percibido sin transcripción.

La entrada perceptiva es acústica. El estado inicial de estudio es activación
vocal percibida y su desviación respecto a una referencia del hablante. No se
infiere consentimiento, intención ni emoción discreta. Un agente anfitrión puede
tener su propia transcripción, pero no alimenta la estimación afectiva de PGSO.

El usuario permite reconsiderar eGeMAPS como extractor predeterminado. Se
conservan los demás compromisos del Charter: Rust, MCP, comparación de al menos
tres extractores, transferencia entre corpus, propiedades, degradación ante
señal imperfecta y demostración controlada. Producción y conversión no son
criterios de aprobación de este estudio.

## Tres preguntas evaluadas por separado

1. Señal: ¿qué asociación y cobertura tiene cada extractor frente a anotación
   humana, y cuánto cambia al transferir de corpus sin reajuste?
2. Mecanismo: ¿aplica y retira correctamente la política configurada, preserva
   permisos independientes y permite reconstruir las decisiones?
3. Demostración: ¿con contenido léxico controlado una observación acústica produce
   las transiciones y efectos previstos? Esto no demuestra utilidad comercial.

Una correlación alta no certifica que bloquear una acción sea adecuado. Un
extractor débil no invalida por sí mismo una propiedad del runtime. Nunca se
presentará una garantía de software como precisión perceptiva.

## Inventario observado y trabajo que falta

| Elemento | Evidencia disponible | Cierre pendiente |
|---|---|---|
| Heurística acústica Rust | Implementación actual y evaluación histórica MUStARD++ | Pin de fuente/binary/configuración para la nueva comparación |
| wav2small | Adaptador Phase 2 existente; registry declara pesos NC | Fijar revisión, hashes, licencia y disponibilidad local; solo referencia académica mientras no cambie elegibilidad |
| wav2vec2-large MSP dimensional | Adaptador Phase 2 existente; registry declara pesos NC | Mismos controles y revisar solapamiento con MSP de entrenamiento |
| CREMA-D | RESULT-51c41ddbc2 / ANALYSIS-762c466961 en PhDude | Solo diagnóstico de intensidad actuada; no reemplaza etiquetas de arousal natural |
| Corpus espontáneos | No hay audio espontáneo registrado listo para este protocolo en el inventario PhDude consultado | Confirmar audio autorizado, anotaciones, splits, licencias y procedencia |
| Evaluador nuevo | CSV alineado, cobertura, bootstrap por hablante y hashes | Pruebas con datos reales únicamente después de habilitar la ejecución correspondiente |
| Integración Sami | Snapshot/TTL y traza histórica sin activación | No demuestra alimentación acústica continua ni efecto de una política activa |

Los tres candidatos existentes forman una comparación inicial viable de
investigación, **no una selección final aprobada**. eGeMAPS completo con predictor
es una alternativa de candidato, no se cuenta como implementado. Ningún modelo
NC se declara distribuible comercialmente. Si los candidatos elegibles no
alcanzan el criterio operativo, el resultado puede ser «sin extractor recomendado
para ese uso», sin bajar el criterio tras ver test.

## Congelar antes de ejecutar

Preparar un manifiesto de ejecución con:

- Corpus/versión/licencia, hash del manifiesto y de cada audio, IDs estables,
  idioma, hablante, sesión, split, escala de anotación y anotadores disponibles.
- Corpus de entrenamiento/ajuste y corpus destino; disjunción de hablantes,
  sesiones y duplicados. Revisar exposición del modelo preentrenado al test.
  Un modelo ajustado en MSP no se presenta como transferencia cero-shot a MSP.
- Modelo/revisión/hash de pesos, código y dependencias, configuración acústica,
  normalización, agregación, criterio de abstención, hardware e hilos.
- Fuente de baseline, warmup, adaptación, umbrales de motor y reglas, histéresis,
  gap y TTL. No se supone neutral el primer segmento ni se calibra con el test.
- Hipótesis, contrastes primarios, unidad de remuestreo, exclusiones, presupuesto
  de latencia/memoria y tolerancia a intervenciones indebidas. Valores operativos
  por acordar usando desarrollo; no hay cifras inventadas como requisitos.

El manifiesto debe fijar candidatos y contrastes antes del test. Los resultados
ya inspeccionados son desarrollo/históricos; no se renombran como confirmatorios.
La aprobación y el registro metodológico corresponden al investigador mediante
PhDude. Una decisión propuesta no equivale a una decisión aprobada.

## OE1: comparación de señal

Evaluar cada corpus por separado, con al menos tres candidatos sobre las mismas
unidades. Registrar una fila por candidato y locución, incluidos errores y
abstenciones. No imputar estos últimos como arousal cero ni suprimir silenciosamente
ejemplos. Guardar anotación, predicción, identidad y motivo detallado de no emisión.

Medida primaria propuesta: Spearman entre predicción y etiqueta de arousal,
acompañada de cobertura y número de hablantes. Intervalo percentil 95% con 2.000
remuestreos por hablante y semilla 42, como configuración de desarrollo. Si la
dependencia principal es una sesión/diálogo compartido, cambiar el agrupamiento
en el protocolo antes del análisis; el evaluador actual solo implementa hablantes.

Comparación pareada: diferencia de Spearman usando exactamente las mismas
locuciones y los mismos bloques remuestreados. Reportar el subconjunto común y
las coberturas individuales: una diferencia condicionada a no abstención no
demuestra mejor rendimiento sobre toda la población. No usar los p-valores de
permutación iid del harness histórico para observaciones agrupadas.

No inferir superioridad a partir de diferencias puntuales, de CIs individuales
solapados/no solapados ni de elegir el máximo del test. Fijar contrastes y control
de multiplicidad antes de una conclusión confirmatoria. Cuando proceda calcular
CCC/MAE, fijar la correspondencia de escalas en desarrollo; no normalizar con test.

Transferencia: ajustar, seleccionar y calibrar solo en origen/desarrollo, congelar
y evaluar destino. Reportar la degradación por candidato, sin mezclar corpus.
Si no se conocen los datos de entrenamiento de un modelo, declarar transferencia
no verificable. Un benchmark de locuciones completas no certifica streaming.

Mantener separado un ensayo causal de ventanas: todos los candidatos reciben
solo el audio disponible hasta cada instante, con presupuesto temporal comparable.
Medir acumulación de audio, extracción y decisión; no comparar una locución
completa con una ventana como si tuvieran la misma información futura.

## OE2–OE3: contrato e implementación

- Actualizar C4, estados y contratos de acuerdo con el código final; documentar
  por separado estado temporal de observación y estado efectivo de política.
- Distinguir calidad de audio, incertidumbre del estimador y vigencia. Voicing no
  es probabilidad emocional. Exponer los motivos de abstención y no activación.
- Verificar configuración, herramientas referenciadas y tiempos. Las correcciones
  locales de catálogo y expiry son trabajo de ingeniería, no resultados de señal.
- Registrar evidencia, regla, eje, baseline, desviación, estado anterior/posterior,
  expiración, intentos fallidos y recibos de ejecución. El log actual de transiciones
  por sí solo no satisface un replay completo de entradas y decisiones.
- Comprobar enforcement en dispatch; retirar una herramienta del catálogo no
  cubre rutas alternativas. Restaurar permisos no revierte efectos ya ejecutados.
- Medir núcleo, extracción, bridge y recorrido total por separado, con percentiles,
  hardware, cargas y memoria. No sustituir latencia de voz por nanosegundos del motor.

## OE4: corrección, degradación y demostración

Usar oráculos de política escritos independientemente del SDK. Casos mínimos:
umbral exacto, ambos lados de baseline, persistencia, recuperación, arranque
alterado, rampas, baja confianza, ausencia de observaciones, gaps, timestamps
fuera de orden, expiración, error de actuador, herramientas protegidas, permisos
del host y llamadas con catálogo obsoleto. Verificar abstención contra el umbral
no equivale a validar calibración afectiva.

Caracterizar ruido, sesgo, pérdida en ráfagas, retrasos y confianza incorrecta
separadamente. Reportar restricciones indebidas, restricciones omitidas, duración
del bloqueo y tiempo de recuperación respecto del oráculo declarado. Esas son
propiedades frente a una política configurada, no juicios humanos de pertinencia.
Dos trazas con la misma correlación pueden producir errores temporales distintos;
no reducir toda la calidad a un único rho.

Demostración con agente real: dependencias y flags fijados antes, contenido léxico
controlado, audio completo conservado con autorización, configuración inmutable,
observaciones y recibos vinculados por tiempos originales. Incluir neutralidad,
entusiasmo y una condición que satisfaga la política sin cambiar umbrales tras
ver resultados. La señal inyectada verifica mecánica por separado y se etiqueta
como tal. Conservar fallos y ausencia de activación. No repetir selectivamente
solo un brazo favorable ni denominar causal al A/B histórico de Sami.

## Paquete de cierre

Matriz OE/indicador → código/prueba/artefacto/límite; al menos tres extractores y
transferencia documentada; catálogo de garantías verificadas; demo trazable;
documentación de integración y migración; versiones y licencias; resultados
individuales y scripts reproducibles. Mantener la revisión mínima de 25 artículos
primarios Q1/Q2 exigida por OE1-I1, verificando periodo, relevancia y cuartil.

Separar cobertura técnica de utilidad conversacional. No es necesario demostrar
conversión para cerrar este Charter, ni basta pasar pruebas para prometer aceptación
editorial. Publicación/release son pasos posteriores, no autorizados por este archivo.

## Límites vigentes de ejecución

No se modifica el vault ni su política de red/ejecución. No se instalan modelos,
descargan corpus, lanzan APIs, reinician servicios o consumen sesiones/ledger de
Sami. Los tests del evaluador usan únicamente registros sintéticos temporales.
Para un experimento del vault se necesita un análisis declarado en PhDude y la
habilitación explícita correspondiente; no ejecutarlo desde este repositorio
como forma de eludir esa política.
