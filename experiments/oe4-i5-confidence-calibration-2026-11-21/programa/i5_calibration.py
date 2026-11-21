# -*- coding: utf-8 -*-
"""OE4-I5: conjunto de calibración del umbral de confianza (theta_c) del motor (G4, abstención correcta).

Construye ventanas de 800 ms (una por archivo WAV de 12 800 muestras a 16 kHz), las pasa por el extractor del SDK
(binario phase1-extract, pgso-signal-egemaps sin modificar) y mide, para cada umbral theta_c:
  - aceptación de habla limpia (ventanas con sonoridad de referencia >= 0,5 según Praat, independiente del SDK);
  - falsa aceptación en ventanas sin habla: silencio y ruidos estocásticos (blanco, rosa, marrón, con forma de habla)
    y señales periódicas no vocales (tono puro, tono con vibrato, zumbido de red con armónicos), informadas aparte;
  - en habla con ruido añadido (SNR 20, 10, 5, 0 y -5 dB): aceptación y desviación absoluta de la lectura frente a la
    lectura de la misma ventana limpia.
Regla fijada antes de calcular: en la partición de calibración (actores de entrenamiento de CREMA-D), el umbral elegido
es el menor de la rejilla cuya falsa aceptación en silencio y ruidos estocásticos sea <= 1 %; se confirma en los actores
de evaluación. Las señales periódicas no vocales se informan como límite conocido de la confianza por periodicidad.
Uso: python3 i5_calibration.py <salida>
"""
import csv
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import parselmouth

SEED = 42
SR = 16000
WIN = 12800
HOP = 6400
CLIPS_PER_ACTOR = 6
N_SYNTH = 120  # ventanas por tipo de señal sin habla y partición
SNRS = [20, 10, 5, 0, -5]
GRID = [round(x, 2) for x in np.arange(0.30, 0.801, 0.05)]
STOCHASTIC = ['silencio', 'ruido_blanco', 'ruido_rosa', 'ruido_marron', 'ruido_forma_habla']
PERIODIC = ['tono_puro', 'tono_vibrato', 'zumbido_red']
CREMA = Path('/home/claude/oe1/data/crema')
LABELS = Path('/home/claude/oe1/work/labels_crema.csv')
BIN = '/home/claude/pgso-workspace/pgso-sdk/experiments/phase1-arousal-mustard/extract/target/release/phase1-extract'


def read_wav(p):
    with wave.open(str(p)) as w:
        assert w.getframerate() == SR and w.getsampwidth() == 2
        x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    return x


def write_wav(p, x):
    y = np.clip(np.round(x * 32767.0), -32768, 32767).astype('<i2')
    with wave.open(str(p), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(y.tobytes())


def praat_voiced_fraction(x):
    snd = parselmouth.Sound(x.astype(np.float64), sampling_frequency=SR)
    pitch = snd.to_pitch(time_step=0.01, pitch_floor=75.0, pitch_ceiling=600.0)
    f0 = pitch.selected_array['frequency']
    return float(np.mean(f0 > 0)) if len(f0) else 0.0


def colored_noise(rng, n, kind):
    w = rng.standard_normal(n)
    if kind == 'blanco':
        return w
    spec = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = f[1]
    spec = spec / (np.sqrt(f) if kind == 'rosa' else f)
    return np.fft.irfft(spec, n)


def shaped_noise(rng, n, ltas):
    spec = np.fft.rfft(rng.standard_normal(n)) * ltas
    return np.fft.irfft(spec, n)


def at_rms(x, rms):
    r = np.sqrt(np.mean(x ** 2))
    return x * (rms / r) if r > 0 else x


def synth(kind, rng, ltas):
    t = np.arange(WIN) / SR
    level = float(rng.choice([0.003, 0.01, 0.03, 0.1]))
    if kind == 'silencio':
        return rng.standard_normal(WIN) * 1e-4
    if kind.startswith('ruido_') and kind != 'ruido_forma_habla':
        return at_rms(colored_noise(rng, WIN, kind.split('_')[1]), level)
    if kind == 'ruido_forma_habla':
        return at_rms(shaped_noise(rng, WIN, ltas), level)
    f = float(rng.uniform(90, 300))
    if kind == 'tono_puro':
        return at_rms(np.sin(2 * np.pi * f * t), level)
    if kind == 'tono_vibrato':
        ph = 2 * np.pi * f * t + (f * 0.04 / 5.0) * np.sin(2 * np.pi * 5.0 * t)
        return at_rms(np.sin(ph), level)
    if kind == 'zumbido_red':
        base = float(rng.choice([50.0, 60.0]))
        x = sum(np.sin(2 * np.pi * base * k * t) / k for k in range(1, 6))
        return at_rms(x, level)
    raise ValueError(kind)


def main(out):
    out = Path(out)
    wavdir = out / 'wav'
    wavdir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    labels = list(csv.DictReader(open(LABELS)))
    by_actor = {}
    for r in labels:
        by_actor.setdefault((r['speaker'], r['split']), []).append(r['scene'])
    rows = []  # id, split, kind, snr, clean_id, praat_vf
    clean = {'train': [], 'test': []}
    for (spk, split), scenes in sorted(by_actor.items()):
        pick = rng.choice(sorted(scenes), size=min(CLIPS_PER_ACTOR, len(scenes)), replace=False)
        for sc in pick:
            x = read_wav(CREMA / f'{sc}.wav')
            for k, start in enumerate(range(0, max(0, len(x) - WIN) + 1, HOP)):
                seg = x[start:start + WIN]
                if len(seg) < WIN:
                    continue
                vf = praat_voiced_fraction(seg)
                wid = f'{sc}_w{k}'
                if vf >= 0.5:
                    kind = 'habla'
                elif vf <= 0.05:
                    kind = 'sin_voz_en_clip'
                else:
                    continue
                write_wav(wavdir / f'{wid}.wav', seg)
                rows.append((wid, split, kind, '', '', round(vf, 3)))
                if kind == 'habla':
                    clean[split].append((wid, seg))
    # Espectro medio de largo plazo del habla de calibración, para el ruido con forma de habla.
    mags = [np.abs(np.fft.rfft(s)) for _, s in clean['train']]
    ltas = np.mean(mags, axis=0)
    ltas = ltas / ltas.max()
    for split in ('train', 'test'):
        for kind in STOCHASTIC + PERIODIC:
            for i in range(N_SYNTH):
                wid = f'{split}_{kind}_{i}'
                write_wav(wavdir / f'{wid}.wav', synth(kind, rng, ltas))
                rows.append((wid, split, kind, '', '', ''))
        # Habla con ruido: hasta 300 ventanas limpias por partición.
        sel = rng.choice(len(clean[split]), size=min(300, len(clean[split])), replace=False)
        for j in sel:
            cid, seg = clean[split][j]
            ps = np.mean(seg ** 2)
            for noise in ('blanco', 'forma_habla'):
                nz = colored_noise(rng, WIN, 'blanco') if noise == 'blanco' else shaped_noise(rng, WIN, ltas)
                for snr in SNRS:
                    pn = ps / (10 ** (snr / 10))
                    y = seg + at_rms(nz, np.sqrt(pn))
                    wid = f'{cid}_{noise}_snr{snr}'
                    write_wav(wavdir / f'{wid}.wav', y)
                    rows.append((wid, split, f'habla_ruido_{noise}', snr, cid, ''))
    man = out / 'manifest.csv'
    with open(man, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['scene', 'speaker', 'show', 'sarcasm', 'human_valence', 'human_arousal', 'wav_path'])
        for r in rows:
            w.writerow([r[0], r[1], 'i5', '', '', '', str(wavdir / f'{r[0]}.wav')])
    with open(out / 'ventanas.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'particion', 'tipo', 'snr_db', 'id_limpia', 'sonoridad_praat'])
        w.writerows(rows)
    subprocess.run([BIN, str(man), str(out / 'pred.csv'), str(out / 'win.csv')], check=True,
                   stdout=open(out / 'extract.log', 'w'), stderr=subprocess.STDOUT)
    analyse(out)


def analyse(out):
    out = Path(out)
    meta = {r['id']: r for r in csv.DictReader(open(out / 'ventanas.csv'))}
    rd = {}
    for r in csv.DictReader(open(out / 'win.csv')):
        rd[r['scene']] = (float(r['confidence']), float(r['arousal_default']))
    res = {'semilla': SEED, 'rejilla': GRID, 'por_particion': {}}
    for split in ('train', 'test'):
        part = {}
        ids = [i for i, m in meta.items() if m['particion'] == split]

        def acc(kind, th, snr=None):
            sel = [i for i in ids if meta[i]['tipo'] == kind and (snr is None or meta[i]['snr_db'] == str(snr))]
            if not sel:
                return None, 0
            a = sum(1 for i in sel if i in rd and rd[i][0] >= th)
            return a / len(sel), len(sel)

        kinds = ['habla', 'sin_voz_en_clip'] + STOCHASTIC + PERIODIC
        for kind in kinds:
            part[kind] = {str(th): acc(kind, th)[0] for th in GRID}
            part[kind]['n'] = acc(kind, GRID[0])[1]
        stoch = [i for i in ids if meta[i]['tipo'] in STOCHASTIC + ['sin_voz_en_clip']]
        part['falsa_aceptacion_estocastica'] = {
            str(th): sum(1 for i in stoch if i in rd and rd[i][0] >= th) / len(stoch) for th in GRID}
        part['n_estocastica'] = len(stoch)
        noisy = {}
        for noise in ('blanco', 'forma_habla'):
            for snr in SNRS:
                sel = [i for i in ids if meta[i]['tipo'] == f'habla_ruido_{noise}' and meta[i]['snr_db'] == str(snr)]
                row = {'n': len(sel)}
                for th in GRID:
                    acc_ids = [i for i in sel if i in rd and rd[i][0] >= th]
                    devs = [abs(rd[i][1] - rd[meta[i]['id_limpia']][1]) for i in acc_ids if meta[i]['id_limpia'] in rd]
                    row[str(th)] = {'aceptacion': len(acc_ids) / len(sel) if sel else None,
                                    'desviacion_mediana': float(np.median(devs)) if devs else None}
                noisy[f'{noise}_snr{snr}'] = row
        part['habla_con_ruido'] = noisy
        res['por_particion'][split] = part
    cal = res['por_particion']['train']['falsa_aceptacion_estocastica']
    chosen = next((th for th in GRID if cal[str(th)] <= 0.01), None)
    res['regla'] = 'menor theta_c de la rejilla con falsa aceptacion <= 1 % en silencio y ruidos estocasticos (calibracion)'
    res['theta_elegido'] = chosen
    res['theta_vigente'] = 0.5
    for split in ('train', 'test'):
        p = res['por_particion'][split]
        res[f'resumen_{split}'] = {str(th): {'aceptacion_habla': p['habla'][str(th)],
                                             'falsa_aceptacion_estocastica': p['falsa_aceptacion_estocastica'][str(th)],
                                             'aceptacion_periodica_no_vocal': float(np.mean([p[k][str(th)] for k in PERIODIC]))}
                                   for th in (chosen, 0.5) if th is not None}
    json.dump(res, open(out / 'i5_resultados.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({k: res[k] for k in ('theta_elegido', 'theta_vigente', 'resumen_train', 'resumen_test')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[2] == '--solo-analisis':
        analyse(sys.argv[1])
    else:
        main(sys.argv[1])
