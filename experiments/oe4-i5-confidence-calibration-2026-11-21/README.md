# OE4-I5: conjunto de calibración del umbral de confianza (21/11/2026)

**Calibración de desarrollo.** Caracteriza el umbral de confianza `confidence_threshold` (θc) del motor de decisión con el
extractor del SDK (`pgso-signal-egemaps`, configuración por defecto) sobre ventanas de 800 ms con etiqueta conocida.
La aprobó el tesista Juan Fabritzzio Pescoran Angulo el 21/11/2026; no consta registro en PhDude.

## Conjunto

Cada ventana es un archivo WAV de 12 800 muestras a 16 kHz, de modo que el extractor emite como mucho una lectura.

| Tipo | Origen | Etiqueta esperada |
|---|---|---|
| `habla` | Ventanas de CREMA-D (6 grabaciones por actor, semilla 42) con sonoridad de referencia ≥ 0,5 según Praat (Parselmouth 0.4.7, 75 a 600 Hz), independiente del SDK | aceptar |
| `sin_voz_en_clip` | Ventanas de las mismas grabaciones con sonoridad de referencia ≤ 0,05 | abstenerse |
| `silencio`, `ruido_blanco`, `ruido_rosa`, `ruido_marron`, `ruido_forma_habla` | Sintéticas, 120 por tipo y partición, RMS de 0,003 a 0,1 | abstenerse |
| `tono_puro`, `tono_vibrato`, `zumbido_red` | Sintéticas periódicas no vocales, 120 por tipo y partición | abstenerse |
| `habla_ruido_*` | 300 ventanas de habla por partición con ruido blanco o con forma de habla a 20, 10, 5, 0 y −5 dB | se informa aceptación y desviación de la lectura frente a la ventana limpia |

Particiones: calibración = 61 actores de entrenamiento de CREMA-D; confirmación = 30 actores de evaluación
(`(id − 1000) mod 3 = 0`), las mismas de la corrida de OE1.

Regla fijada antes de calcular: el umbral elegido es el menor de la rejilla 0,30 a 0,80 cuya falsa aceptación en silencio
y ruidos estocásticos sea ≤ 1 % en la partición de calibración; las señales periódicas no vocales se informan aparte.

## Resultados (θc = 0,5, el vigente)

| Tipo | Calibración | Confirmación |
|---|---|---|
| Habla aceptada | 52,8 % (919) | 47,0 % (460) |
| Silencio, ruido blanco, ruido con forma de habla, ventanas sin voz | 0 % | 0 % |
| Ruido rosa aceptado | 1,7 % | 0 % |
| Ruido marrón aceptado | 59,2 % | 51,7 % |
| Tono puro / con vibrato / zumbido de red aceptados | 60,8 / 65,8 / 61,7 % | 60,0 / 61,7 / 58,3 % |

- Ningún umbral de la rejilla cumple la regla: el ruido marrón se acepta en al menos el 46 % de las ventanas con cualquier θc de la rejilla, porque la
  confianza mide periodicidad y fracción sonora, y un ruido de baja frecuencia o una señal periódica la satisfacen. El
  umbral no puede corregirlo; hace falta otra compuerta (por ejemplo, plausibilidad espectral de la voz).
- Con habla y ruido, a θc = 0,5 la aceptación cae de alrededor del 50 % a 20 dB al 23 a 27 % a 10 dB, al 3 a 5 % a 5 dB y a 0 % a 0 y −5 dB;
  la mediana de la desviación de las lecturas aceptadas frente a la ventana limpia es de 0,002 a 0,047.
- Elevar θc reduce la cobertura del habla (16,6 % a 0,7) sin eliminar la aceptación de ruido marrón ni de señales periódicas.

La abstención es correcta para silencio y ruido de banda ancha con θc = 0,5; no lo es para ruido de baja frecuencia ni para
señales periódicas no vocales. Ese límite se suma a los declarados en `docs/known-issues.md` y no cambia el contrato G4: el
motor conserva su estado ante lecturas de baja confianza, pero una lectura de confianza alta no garantiza que haya voz.

## Reproducir

```sh
python3 programa/i5_calibration.py <salida>                 # genera ventanas, ejecuta el extractor y analiza
python3 programa/i5_calibration.py <salida> --solo-analisis  # recalcula desde win.csv y ventanas.csv
```

Requiere CREMA-D en `/home/claude/oe1/data/crema`, las etiquetas de la corrida de OE1 y el binario
`experiments/phase1-arousal-mustard/extract` compilado (rutas al inicio del programa). No se redistribuye audio:
`resultados/ventanas_sha256.txt` guarda la huella de cada ventana generada; `ventanas.csv` describe cada ventana;
`win.csv` trae la lectura del extractor por ventana. Las lecturas derivadas de CREMA-D quedan bajo su licencia
(Open Database License 1.0 y Database Contents License 1.0).
