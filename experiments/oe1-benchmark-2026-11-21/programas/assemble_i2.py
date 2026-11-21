# -*- coding: utf-8 -*-
"""Arma el CSV del evaluador del repositorio para I2 (CREMA-D) y ajusta C2 y C3 solo con la partición de ajuste.

Uso: python assemble_i2.py <c1.csv> <py.csv> <labels_crema.csv> <salida.csv> <modelo.json>
Columnas del evaluador: corpus, split, scene, speaker, candidate, human_arousal, prediction, status.
human_arousal contiene la intensidad emocional percibida (diferencia de constructo declarada en el manifiesto).
"""
import csv
import json
import sys

import numpy as np

EG = None  # nombres de los 88 funcionales, en el orden de openSMILE


def load(path):
    return {r['scene']: r for r in csv.DictReader(open(path))}


def fnum(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return np.nan
    return x if np.isfinite(x) else np.nan


def ridge_fit(X, y, alpha=1.0):
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    ym = y.mean()
    A = Z.T @ Z + alpha * np.eye(Z.shape[1])
    w = np.linalg.solve(A, Z.T @ (y - ym))
    return {'mu': mu.tolist(), 'sd': sd.tolist(), 'w': w.tolist(), 'b': float(ym), 'alpha': alpha}


def ridge_predict(m, X):
    return ((X - np.array(m['mu'])) / np.array(m['sd'])) @ np.array(m['w']) + m['b']


def main(c1p, pyp, labp, outp, modelp):
    c1, py, lab = load(c1p), load(pyp), load(labp)
    scenes = sorted(set(c1) & set(py))
    assert set(scenes) == set(c1) == set(py), 'unidades no alineadas entre extractores'
    first = py[scenes[0]]
    global EG
    EG = [k for k in first if k not in {'scene', 'c2_status', 'c2_seconds', 'c3_status', 'c3_seconds',
                                         'b0_status', 'b0_seconds', 'praat_intensity_db', 'praat_f0_sd_st',
                                         'praat_voiced_frames', 'rms_db', 'duration_s'}]
    assert len(EG) == 88, len(EG)
    train = [s for s in scenes if lab[s]['split'] == 'train']
    # C2: ridge sobre los 88 funcionales, ajuste solo con la partición de ajuste
    Xtr = np.array([[fnum(py[s][k]) for k in EG] for s in train if py[s]['c2_status'] == 'ok'])
    ytr = np.array([float(lab[s]['intensity']) for s in train if py[s]['c2_status'] == 'ok'])
    assert np.isfinite(Xtr).all()
    m2 = ridge_fit(Xtr, ytr)
    # C3: estadísticos de estandarización de la partición de ajuste
    pi = np.array([fnum(py[s]['praat_intensity_db']) for s in train])
    pf = np.array([fnum(py[s]['praat_f0_sd_st']) for s in train])
    ok = np.isfinite(pi) & np.isfinite(pf)
    m3 = {'int_mu': float(pi[ok].mean()), 'int_sd': float(pi[ok].std()),
          'f0_mu': float(pf[ok].mean()), 'f0_sd': float(pf[ok].std())}
    rows = []
    for s in scenes:
        L = lab[s]
        base = {'corpus': 'crema-d', 'split': L['split'], 'scene': s, 'speaker': L['speaker'],
                'human_arousal': L['intensity']}
        # C1
        v = fnum(c1[s]['pred_arousal_wmean'])
        rows.append({**base, 'candidate': 'egemaps', 'prediction': '' if np.isnan(v) else repr(v),
                     'status': 'abstained' if np.isnan(v) else 'ok'})
        # C2
        if py[s]['c2_status'] == 'ok':
            x = np.array([[fnum(py[s][k]) for k in EG]])
            p = float(ridge_predict(m2, x)[0])
            rows.append({**base, 'candidate': 'opensmile_lineal', 'prediction': repr(p), 'status': 'ok'})
        else:
            rows.append({**base, 'candidate': 'opensmile_lineal', 'prediction': '', 'status': 'error'})
        # C3
        a, b = fnum(py[s]['praat_intensity_db']), fnum(py[s]['praat_f0_sd_st'])
        if py[s]['c3_status'] != 'ok':
            rows.append({**base, 'candidate': 'praat_indice', 'prediction': '', 'status': 'error'})
        elif np.isnan(a) or np.isnan(b):
            rows.append({**base, 'candidate': 'praat_indice', 'prediction': '', 'status': 'abstained'})
        else:
            p = 0.5 * ((a - m3['int_mu']) / m3['int_sd'] + (b - m3['f0_mu']) / m3['f0_sd'])
            rows.append({**base, 'candidate': 'praat_indice', 'prediction': repr(p), 'status': 'ok'})
        # B0
        r = fnum(py[s]['rms_db'])
        rows.append({**base, 'candidate': 'rms_trivial', 'prediction': '' if np.isnan(r) else repr(r),
                     'status': 'error' if np.isnan(r) else 'ok'})
    with open(outp, 'x', newline='') as h:
        w = csv.DictWriter(h, fieldnames=['corpus', 'split', 'scene', 'speaker', 'candidate',
                                          'human_arousal', 'prediction', 'status'])
        w.writeheader()
        w.writerows(rows)
    with open(modelp, 'x') as h:
        json.dump({'c2_ridge': m2, 'c2_features': EG, 'c3_standardization': m3,
                   'train_clips': len(train)}, h)
    print(outp, len(rows))


if __name__ == '__main__':
    main(*sys.argv[1:6])
