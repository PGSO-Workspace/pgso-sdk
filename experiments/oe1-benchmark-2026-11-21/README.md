# OE1: comparación de extractores de activación (corrida del 21/11/2026)

**Corrida de desarrollo, no confirmatoria.** El manifiesto se congeló con huella SHA-256 antes de calcular métricas. La aprobó
el coautor Juan el 21/11/2026; no consta registro en PhDude. CREMA-D ya había sido examinado por el equipo, por lo que sus
cifras son de desarrollo. nEMO no se había examinado: los modelos y umbrales ajustados con CREMA-D se congelaron antes de leer
sus etiquetas, y nEMO se abrió el 21/11/2026 a las 03:02 por decisión del usuario.

Esta carpeta cumple el protocolo borrador de [docs/validation/charter-validation-protocol.md](../../docs/validation/charter-validation-protocol.md)
en lo que pide: manifiesto congelado, remuestreo por hablante (2000 réplicas, semilla 42), diferencias pareadas en la cobertura
común, calibración solo en el origen y extractores no comerciales tratados como referencias.

## Qué se midió

| Indicador | Medida | Corpus |
|---|---|---|
| OE1-I2 | Spearman entre cada extractor y la intensidad emocional percibida con solo voz, con IC 95 % por actor | CREMA-D, 30 actores de evaluación |
| OE1-I3 | AUC de activación alta frente a baja dentro de CREMA-D y al transferir a nEMO con todo congelado; dirección inversa | CREMA-D y nEMO |
| Ablación | Término de energía, término de F0 y combinación del SDK, con y sin normalización de nivel | CREMA-D, 91 actores |
| OE1-I4 | Factor de tiempo real, memoria, interpretabilidad y licencia | 200 clips de CREMA-D |

Candidatos: C1 `egemaps` (crate `pgso-signal-egemaps`, binario `experiments/phase1-arousal-mustard/extract`), C2 openSMILE
eGeMAPSv02 con un modelo lineal ajustado en el origen, C3 índice fijo de Praat (Parselmouth) y B0 nivel RMS del clip.

## Resultados principales

- **I2** (CREMA-D, 2454 clips, 30 actores): C1 ρ = 0,385 [0,318; 0,451]; C2 0,571 [0,510; 0,625]; C3 0,284 [0,228; 0,336];
  B0 0,416 [0,335; 0,492]. B0 − C1 = 0,038 [−0,005; 0,086]. Techo entre evaluadores (mitades, Spearman-Brown) 0,781.
- **Ablación** (91 actores): energía sola 0,452, combinación 0,418, F0 solo 0,222. Con el nivel normalizado: energía −0,171,
  F0 0,144 [0,100; 0,187], combinación 0,116.
- **I3** (AUC): C1 0,771 dentro de CREMA-D y 0,559 [0,436; 0,657] en nEMO; C2 0,895 y 0,531 (0,758 ajustado dentro de nEMO);
  C3 0,696 y 0,498; B0 0,831 y 0,542.
- **Costo** (un hilo): factor de tiempo real C1 0,0144 con 2,8 MB; C2 0,0228; C3 0,0025.
- **I4**: solo C1 es elegible para el SDK (MIT); openSMILE es solo de investigación y Parselmouth es GPL. C1 se mantiene como
  extractor por defecto con límites documentados: no supera al nivel de grabación en CREMA-D y no transfiere a nEMO.

Nada de esto muestra validez en habla espontánea ni con usuarios reales. La etiqueta de activación de nEMO se deriva de la
categoría actuada por una regla del equipo. La intensidad percibida de CREMA-D no equivale a activación.

## Reproducibilidad

- C1 se ejecutó con el binario de la revisión 0cbc4aa (v0.1.0). En la revisión 73a1302 (v0.2.0) el mismo binario produjo
  lecturas idénticas en los 200 clips de la medición de costo (`resultados/costo/c1_main_73a1302_200clips.csv`).
- El evaluador del repositorio se usó sin cambios; su huella está en `resultados/i2_evaluador_repo.json`.
- Los programas conservan las rutas absolutas del entorno donde se ejecutaron. El orden de ejecución está en
  `programas/run_extract.sh` y en el manifiesto.
- `transfer.py eval_nemo` verifica la huella de `resultados/i3/modelos_origen_crema.json` antes de leer etiquetas.

## Datos y licencias

No se redistribuye audio. `datos/` contiene las huellas SHA-256 de cada clip, las licencias de los corpus y las anotaciones
derivadas de CREMA-D. CREMA-D (Cao et al., 2014) se distribuye con la Open Database License 1.0 y la Database Contents
License 1.0; `datos/labels_crema.csv`, `predicciones/pred_i2_crema.csv` y las lecturas por clip de CREMA-D son bases derivadas
y quedan bajo la misma licencia. nEMO (Christop, 2024) se distribuye con CC BY-NC-SA 4.0; por eso aquí solo constan sus huellas
y resultados agregados, y no sus lecturas por clip. Los descriptores por clip de openSMILE y Praat, y el registro del consejo de
asesores que decidió el plan, están en el paquete de evidencia del equipo (Evidencia_OE1_2026-11-21.zip, OneDrive del curso).
