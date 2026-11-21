---
autor: Claude Agent
fecha: 2026-11-21
estado: borrador
tags: [OE1, manifiesto, desarrollo]
fuente: Protocolo docs/validation/charter-validation-protocol.md (pgso-sdk, 0cbc4aa); Project Charter v1.5, OE1-I1 a OE1-I4
---

# Manifiesto de la corrida de desarrollo de OE1 (21/11/2026)

Este manifiesto fija los datos, los candidatos, las particiones, las métricas y las reglas de decisión antes de calcular cualquier métrica. Se registra su huella SHA-256 antes de la ejecución. No es un prerregistro aprobado: la aprobación metodológica corresponde al investigador mediante PhDude, y esta corrida no la tiene. CREMA-D ya fue examinado por el equipo, por lo que sus cifras son de desarrollo. nEMO no se había examinado; su etiqueta de activación se deriva de la categoría por una regla del equipo, no de una anotación humana de activación.

## 1. Datos

- CREMA-D (Cao et al., 2014), 7442 clips de 91 actores, inglés actuado, 16 kHz, Open Database License 1.0 y Database Contents License 1.0. Origen: repositorio CheyneyComputerScience/CREMA-D (rama master), audio AudioWAV y respuestas finishedResponses.csv. Huellas en crema_sha256.txt.
- nEMO (Christop, 2024), 4481 clips de 9 actores, polaco actuado, 24 kHz, CC BY-NC-SA 4.0. Origen: repositorio amu-cai/nEMO (rama main), samples y data.tsv. Huellas en nemo_sha256.txt. Se remuestrea a 16 kHz con scipy.signal.resample_poly(2, 3) antes de todo extractor; todos los extractores reciben el mismo audio de 16 kHz.
- No se redistribuye audio. Se conservan huellas, listas de unidades y predicciones.

## 2. Anotación humana

- CREMA-D, anotación primaria: intensidad emocional percibida por evaluadores que solo escucharon la voz (queryType = 1). Por clip, media de respLevel (0 a 100) de todas las respuestas de voz, codificando como 0 las respuestas «neutral» (equivale a la proporción de evaluadores que percibieron una emoción por la intensidad media que le asignaron). Se descartan las respuestas con respLevel no numérico.
- Diferencia de constructo declarada: la intensidad percibida no es activación. Una tristeza intensa no implica activación alta. Por eso se reporta también la correlación por categoría actuada.
- Techo humano: fiabilidad entre evaluadores por mitades. En cada clip de la partición de evaluación, las respuestas de voz se dividen al azar en dos mitades (semilla 42); se calcula Spearman entre las dos medias y se corrige con Spearman-Brown.
- Etiqueta común para la transferencia (I3), regla del equipo fijada antes de abrir nEMO: activación alta = enfado, miedo, alegría (y sorpresa en nEMO); activación baja = neutral y tristeza; asco se excluye porque su asignación de activación no es unívoca. La categoría es la actuada (nombre del archivo en CREMA-D; columna emotion en nEMO).

## 3. Particiones

- CREMA-D: evaluación = actores cuyo número menos 1000 es múltiplo de 3 (1003, 1006, …, 1090; 30 actores); ajuste = los otros 61 actores. Ninguna decisión usa etiquetas de la partición de evaluación.
- nEMO: corpus de destino completo (9 actores). Para el desempeño dentro de nEMO se usa validación dejando fuera a un actor (9 pliegues).

## 4. Candidatos y línea base

Cada candidato produce un valor por clip; las abstenciones y los errores se registran y no se imputan.

- C1 egemaps: extractor del SDK, crate pgso-signal-egemaps de la versión 0.1.0 (commit 0cbc4aa), configuración por defecto, binario experiments/phase1-arousal-mustard/extract. Valor primario: media ponderada por confianza de las lecturas de la ventana (pred_arousal_wmean). Sin ajuste con etiquetas. Licencia MIT.
- C2 opensmile_lineal: openSMILE 2.6.0, eGeMAPSv02, 88 funcionales por clip; estandarización con la media y la desviación de la partición de ajuste del origen; ridge con alfa = 1,0 sobre la intensidad (I2) o regresión logística L2 con C = 1,0 sobre la etiqueta binaria (I3), ajustada solo en el origen. Sin búsqueda de hiperparámetros. Licencia de openSMILE: solo investigación.
- C3 praat_indice: Praat mediante Parselmouth 0.4.7. Intensidad media en dB (To Intensity, tono mínimo 75 Hz) y desviación de F0 en semitonos sobre las tramas sonoras (To Pitch, 75 a 600 Hz). Índice = media de los dos valores estandarizados con la partición de ajuste del origen. Fórmula fija, sin etiquetas. Abstención con menos de 3 tramas sonoras. Licencia GPL 3.
- B0 rms_trivial: 20·log10 del RMS del clip completo. Línea base trivial del nivel de grabación.

## 5. Métricas

- I2 (corpus de referencia CREMA-D, partición de evaluación): Spearman entre cada candidato y la intensidad percibida, con intervalo percentil del 95 % por 2000 remuestreos de actores (semilla 42), con el evaluador del repositorio experiments/phase2-arousal-benchmark/evaluate_predictions.py sin cambios; diferencia pareada frente a C1 en la cobertura común. Secundarias: Spearman dentro del hablante (predicción y anotación centradas por actor), Spearman por categoría actuada y techo humano.
- I3 (transferencia): AUC de activación alta frente a baja con intervalo por remuestreo de actores (2000, semilla 42). Para cada dirección, CREMA-D → nEMO (principal) y nEMO → CREMA-D (secundaria): AUC dentro del origen (partición de evaluación de CREMA-D o validación dejando fuera a un actor en nEMO) frente a AUC en el destino con todo lo ajustado en el origen; degradación = diferencia. Exactitud balanceada con el umbral fijado en el origen (el que maximiza la exactitud balanceada en la partición de ajuste del origen) frente al umbral propio del destino. Con 9 actores, los intervalos en nEMO serán amplios; un resultado no concluyente se informa como tal.
- Ablación tono frente a energía (CREMA-D, 91 actores, sin ajuste): con las ventanas del extractor C1 se calculan tres lecturas agregadas por la media ponderada por confianza: término de energía (clamp(E/0,3)), término de F0 (clamp(σF0/50)) y combinación del SDK (0,6 y 0,4). Se repite con cada clip normalizado a RMS 0,05 (si el pico supera 0,99 se reduce la ganancia hasta 0,99 y se registra). Métricas: Spearman con la intensidad percibida, Spearman dentro del hablante y AUC binaria; diferencias pareadas frente a la combinación por remuestreo de actores.
- Costo: factor de tiempo real (segundos de proceso por segundo de audio) en un hilo, sobre 200 clips de CREMA-D elegidos con semilla 42, y memoria residente máxima; se registra el hardware.
- Interpretabilidad (criterio operativo): número de descriptores que entran en la lectura; si la lectura se escribe en forma cerrada con constantes fijas; si cada descriptor es una medida fonética estándar con unidades físicas. Alta: hasta dos descriptores en forma cerrada; media: combinación lineal aprendida de muchos descriptores; baja: representación aprendida no inspeccionable.

## 6. Regla de selección (I4)

1. Filtro de elegibilidad: puede ser extractor por defecto del SDK (licencia MIT) solo un candidato cuya licencia permita distribuirlo con el SDK sin restricción no comercial ni copyleft. Con eso, solo C1 es elegible; C2 y C3 son referencias de investigación, como wav2small y wav2vec2-MSP, que no se ejecutan aquí.
2. Entre los elegibles, se elige el de mejor Spearman primario en I2. Se informa el costo de quedarse con él: diferencias pareadas frente a C2 y C3.
3. Si la diferencia pareada C1 − B0 tiene su intervalo completo por debajo de cero, el informe dice que el extractor por defecto no supera al nivel de grabación en este corpus.
4. Como información secundaria, ponderación con pesos iguales (0,25) de los rangos en discriminación (I2), generalización (AUC en destino), costo e interpretabilidad.

## 7. Qué no se afirmará

Que el extractor detecta emociones; validez en habla espontánea o con usuarios reales; aporte del tono si la ablación no lo muestra; que nEMO tiene activación anotada; comparación pareada con wav2small; que la corrida es confirmatoria o un prerregistro aprobado.
