# -*- coding: utf-8 -*-
"""Preparación de audio para la corrida de desarrollo de OE1 (manifiesto del 21/11/2026).

- nEMO: remuestreo de 24 kHz a 16 kHz con resample_poly(2, 3).
- CREMA-D: copia normalizada a RMS 0,05 (control de nivel), con límite de pico 0,99.
- Manifiestos de entrada del binario Rust (sin etiquetas: la anotación se une solo al evaluar).
"""
import csv
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

D = Path('/home/claude/oe1/data')
W = Path('/home/claude/oe1/work')
TARGET_RMS = 0.05
PEAK = 0.99


def to_int16(x):
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype(np.int16)


def nemo16():
    out = W / 'nemo16'
    out.mkdir(parents=True, exist_ok=True)
    for f in sorted((D / 'nemo').glob('*.wav')):
        x, sr = sf.read(f, dtype='float64')
        assert sr == 24000 and x.ndim == 1, (f, sr, x.shape)
        y = resample_poly(x, 2, 3)
        sf.write(out / f.name, to_int16(y), 16000, subtype='PCM_16')


def crema_norm():
    out = W / 'crema_norm'
    out.mkdir(parents=True, exist_ok=True)
    log = []
    for f in sorted((D / 'crema').glob('*.wav')):
        x, sr = sf.read(f, dtype='float64')
        assert sr == 16000 and x.ndim == 1
        rms = float(np.sqrt(np.mean(x ** 2)))
        g = TARGET_RMS / rms if rms > 0 else 1.0
        limited = False
        if np.max(np.abs(x)) * g > PEAK:
            g = PEAK / np.max(np.abs(x))
            limited = True
        sf.write(out / f.name, to_int16(x * g), 16000, subtype='PCM_16')
        log.append((f.stem, rms, g, int(limited)))
    with open(W / 'crema_norm_log.csv', 'w', newline='') as h:
        w = csv.writer(h)
        w.writerow(['scene', 'rms_original', 'ganancia', 'limitado_por_pico'])
        w.writerows(log)


def manifests():
    for name, folder in [('crema', D / 'crema'), ('crema_norm', W / 'crema_norm'), ('nemo', W / 'nemo16')]:
        rows = []
        for f in sorted(folder.glob('*.wav')):
            spk = f.stem.split('_')[0]
            rows.append({'scene': f.stem, 'speaker': spk, 'show': name, 'sarcasm': '',
                         'human_valence': '', 'human_arousal': '', 'wav_path': str(f)})
        with open(W / f'manifest_{name}.csv', 'w', newline='') as h:
            w = csv.DictWriter(h, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(name, len(rows))


if __name__ == '__main__':
    W.mkdir(parents=True, exist_ok=True)
    steps = sys.argv[1:] or ['nemo16', 'crema_norm', 'manifests']
    for s in steps:
        globals()[s]()
