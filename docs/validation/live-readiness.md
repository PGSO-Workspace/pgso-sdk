# Preparación local de la validación con Sami

Esta candidata es de ingeniería, no una mejora demostrada del extractor ni una
release. El protocolo científico sigue en `charter-validation-protocol.md`.
No autoriza llamadas a proveedores, sesiones de voz, despliegues o cambios al vault.

## Qué se congela

- Base Git: `da2808a259c5a4c574d61ad8e09c460d369e9571` más el snapshot de trabajo
  del paquete. El commit solo no reproduce la candidata: hay cambios sin commit.
- Binario `voice_bridge`, wrapper `experiments/public_voice/bridge.py`, fuentes,
  Cargo.lock y hashes quedan juntos en el paquete local de validación.
- El SHA anterior de Sami es
  `0bf2d7b22dc94694060bb79600e41347bfef21105908b608ee6f0a1231b0442d`.
  No reemplazar un proceso activo. El host debe registrar explícitamente el SHA
  elegido y conservar el binario/configuración anteriores para volver a ellos.
- Señal: heurística Rust eGeMAPS-style; no es openSMILE eGeMAPS completo ni
  clasificador de emociones. La confianza representa voicing, no calibración afectiva.
- Configuración de continuidad: 16 kHz mono, muestras f32 normalizadas, ventana
  12.800 muestras (800 ms), hop 400 ms, prior 0.5, confidence threshold 0.5,
  deviation threshold 0.2, histéresis 2, warmup 0, EMA 0, Rising, max gap 1.200 ms.
  El host debe enviar `stale_after_ms: 20000` explícitamente: el default es 1.200 ms.
- `intervention: prune`, `governed_tools: [quote_purchase]` y directiva genérica
  de aclaración se conservan solo para reproducir la condición anterior. No son
  una política comercial validada. Una voz entusiasta no justifica impedir una
  compra; la prueba debe registrar esa restricción como posible efecto indebido.
- El host entrega su catálogo real; no copiar un catálogo de prueba a producción.
  Registrar flags, permisos independientes, snapshot de merchant y versión del host.
  Waruna 344 y snapshot `672c5f519c811e04a2ad111083f0ca79f1ea959fd7321c4bb15eedf2586c0a4b`
  fueron comunicados desde Sami; este paquete no los verifica.

## Puerta offline

Desde la raíz del SDK, sin instalar dependencias ni usar proveedores:

```sh
cargo test --workspace --locked --offline
cargo build --release --example voice_bridge --locked --offline
python3 crates/pgso-actuator-http/examples/check_voice_bridge.py target/release/examples/voice_bridge
python3 -m unittest discover -s experiments/phase2-arousal-benchmark -p 'test_evaluate_predictions.py' -v
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked --offline -- -D warnings
git diff --check
```

El check NDJSON usa procesos locales efímeros y callbacks contados, sin efectos
externos. PCM sintético comprueba transporte/extracción; lecturas inyectadas
comprueban política. Ninguno mide reconocimiento afectivo humano.
Exigir salida exitosa, cero callbacks durante bloqueo y recuperación después del
TTL. Comprobar hash del binario antes y después del check.

## Puerta de integración, antes de gastar una sesión

1. Identificar una sola candidata por hash y confirmar el ACK de configuración.
   Un cambio de hash/configuración/flags durante el ensayo invalida su comparación.
2. Verificar que cada ventana entra a `extract`, su resultado llega a `observe`
   con el mismo timestamp y la consulta/ejecución usa tiempo de host monotónico.
   `extract` por sí solo no actualiza política. No reestampar audio antiguo como nuevo.
3. Probar en el host con efectos simulados y lecturas inyectadas el bloqueo en la
   ruta real de dispatch, catálogo obsoleto, expiración y recuperación. Un resultado
   correcto del ejecutable aislado no demuestra enforcement en Sami.
4. Confirmar qué ruta es shadow y cuál aplica enforcement. No interpretar un
   `pgso.ready` shadow como prueba de bloqueo ni una directiva como recibo de ejecución.
5. Conservar intención explícita y permisos del host como entradas independientes.
   La prosodia no concede consentimiento, revoca una negativa ni identifica enojo.
   Ante duda, usar aclaración explícita; medir si resulta innecesaria o repetitiva.

## Registro mínimo por ventana y acción

Guardar session/turn/window IDs, timestamp original del audio, llegada al host,
inicio/fin de extracción, lectura o ausencia, confianza, ACK de observe, catálogo
y directivas antes/después, timestamp del intento de tool-call, decisión y recibo.
Vincular el snapshot/flags, SHA del SDK y configuración a la misma sesión.
El bridge no emite por sí solo todo este registro: el host debe conservar las
solicitudes/respuestas. Su `audit: []` en observe no prueba ausencia de cambios.
No registrar audio ni datos personales fuera de lo autorizado.

| Diagnóstico | Evidencia que lo distingue |
|---|---|
| No llegó audio | No hay ventana recibida, aunque el usuario haya hablado |
| No hubo lectura | `extract` respondió con lista vacía; no convertirla en arousal 0 |
| Baja confianza | Lectura presente, confianza menor que 0.5; separarla de silencio |
| No activación | Lectura aceptable pero política no persistió sobre el criterio; revisar valor, desviación, histéresis y gaps |
| Fallo de entrega | Lectura extraída sin observe correspondiente o con error de ACK |
| Expiración | Hubo restricción previa y luego recuperación ligada al cutoff; edad al intento mayor que TTL |
| Fallo de enforcement | Política restrictiva vigente pero se ejecutó callback gobernado |
| Mapeo inadecuado | El mecanismo funcionó, pero bloqueó una intención explícita adecuada o pidió aclaración innecesaria |

## Ensayo humano, cuando Sami disponga de autorización y presupuesto

Preparar condiciones neutrales, activación vocal y entusiasmo con el mismo texto
cuando sea posible; incluir petición explícita de compra y negativa explícita.
Fijar el orden/contrabalanceo antes de escuchar resultados, mantener distancia de
micrófono y registrar cambios de ganancia/ruido. Evaluar por separado arranque,
habla continua, pausa y acción tardía. No asumir que el primer segmento es neutral.
Los timestamps deben permitir comparar el TTL con la edad real al dispatch.

Conservar las condiciones sin activación: no repetir hasta obtener éxito ni bajar
umbrales. Una señal inyectada alta es un control del mecanismo, nunca evidencia
acústica. No interpretar entusiasmo como malestar por el solo arousal.

Éxito técnico: cadena completa trazable, política congruente con entradas,
bloqueo/recuperación correctos y cero efectos no autorizados. Si faltan trazas,
configuración o enforcement simulado, no gastar la sesión para descubrirlo.
Ausencia de activación acústica es un resultado posible, no un fallo del ensayo.

## Qué sigue pendiente aunque pase la puerta offline

- Validación del host Sami y de Waruna; ninguna prueba local la sustituye.
- Validez perceptiva/cobertura en voz natural y transferencia, con al menos tres
  extractores según el Charter. El evaluador está preparado, los resultados no.
- Selección de política útil y tolerancia a falsas intervenciones. La autonomía
  para preparar el SDK no equivale a aprobación científica del protocolo.
- La comparación histórica A/B cambió flags; no usarla como efecto causal.
  La última sesión no activó política: su acción a 29.790 ms frente a TTL 20.000 ms
  no demuestra que la expiración haya causado la ausencia de intervención.
