# -*- coding: utf-8 -*-
"""Extractores C2 (openSMILE eGeMAPSv02), C3 (Praat/Parselmouth) y B0 (RMS) por clip.

Uso: python extract_py.py <manifest.csv> <salida.csv> [--only c2|c3|b0] [--jobs N]
Registra el tiempo de proceso de cada clip para el factor de tiempo real.
"""
import argparse
import csv
import time
from multiprocessing import Pool

import numpy as np
import soundfile as sf

_SMILE = None


def smile():
    global _SMILE
    if _SMILE is None:
        import opensmile
        _SMILE = opensmile.Smile(feature_set=opensmile.FeatureSet.eGeMAPSv02,
                                 feature_level=opensmile.FeatureLevel.Functionals)
    return _SMILE


def c2(path):
    t = time.perf_counter()
    df = smile().process_file(path)
    vals = df.iloc[0].to_dict()
    return vals, time.perf_counter() - t


def c3(path):
    import parselmouth
    t = time.perf_counter()
    snd = parselmouth.Sound(path)
    inten = snd.to_intensity(minimum_pitch=75.0)
    pitch = snd.to_pitch(pitch_floor=75.0, pitch_ceiling=600.0)
    f0 = pitch.selected_array['frequency']
    f0 = f0[f0 > 0]
    out = {'praat_intensity_db': float(np.mean(inten.values)) if inten.values.size else np.nan,
           'praat_f0_sd_st': np.nan, 'praat_voiced_frames': int(f0.size)}
    if f0.size >= 3:
        st = 12.0 * np.log2(f0 / 100.0)
        out['praat_f0_sd_st'] = float(np.std(st, ddof=1))
    return out, time.perf_counter() - t


def b0(path):
    t = time.perf_counter()
    x, _ = sf.read(path, dtype='float64')
    rms = float(np.sqrt(np.mean(x ** 2)))
    out = {'rms_db': 20.0 * np.log10(rms) if rms > 0 else np.nan, 'duration_s': len(x) / 16000.0}
    return out, time.perf_counter() - t


def work(args):
    scene, path, only = args
    row = {'scene': scene}
    for name, fn in [('c2', c2), ('c3', c3), ('b0', b0)]:
        if only and name not in only:
            continue
        try:
            vals, dt = fn(path)
            row.update(vals)
            row[f'{name}_status'] = 'ok'
        except Exception as exc:  # se registra el error; no se imputa
            row[f'{name}_status'] = 'error:' + type(exc).__name__
            dt = float('nan')
        row[f'{name}_seconds'] = dt
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('manifest')
    ap.add_argument('output')
    ap.add_argument('--only', nargs='*')
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.manifest)))
    if a.limit:
        rows = rows[:a.limit]
    tasks = [(r['scene'], r['wav_path'], a.only) for r in rows]
    if a.jobs > 1:
        with Pool(a.jobs) as p:
            res = p.map(work, tasks, chunksize=16)
    else:
        res = [work(t) for t in tasks]
    keys = []
    for r in res:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(a.output, 'x', newline='') as h:
        w = csv.DictWriter(h, fieldnames=keys)
        w.writeheader()
        w.writerows(res)
    print(a.output, len(res))


if __name__ == '__main__':
    main()
