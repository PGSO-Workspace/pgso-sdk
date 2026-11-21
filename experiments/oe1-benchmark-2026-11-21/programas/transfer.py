# -*- coding: utf-8 -*-
"""I3: generalización entre corpus con la etiqueta binaria de activación derivada de la categoría (regla del equipo).

Etapa 1 (fit_crema): ajusta todo en la partición de ajuste de CREMA-D, mide dentro de CREMA-D (partición de
evaluación) y congela modelos y umbrales con su huella, sin leer etiquetas de nEMO.
Etapa 2 (eval_nemo): abre las etiquetas de nEMO, aplica lo congelado y mide la degradación; ajusta también en nEMO
(validación dejando fuera a un actor) para el desempeño dentro de nEMO y la dirección inversa nEMO → CREMA-D.

Uso: python transfer.py fit_crema <outdir>
     python transfer.py eval_nemo <outdir>
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

from stats_oe1 import N_BOOT, SEED, auc, bal_acc, best_threshold

W = Path('/home/claude/oe1/work')
F = W / 'full'
CANDS = ['egemaps', 'opensmile_lineal', 'praat_indice', 'rms_trivial']
NONFEAT = {'scene', 'c2_status', 'c2_seconds', 'c3_status', 'c3_seconds', 'b0_status', 'b0_seconds',
           'praat_intensity_db', 'praat_f0_sd_st', 'praat_voiced_frames', 'rms_db', 'duration_s'}


def fnum(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return np.nan
    return x if np.isfinite(x) else np.nan


def load_corpus(name):
    c1 = {r['scene']: r for r in csv.DictReader(open(F / f'c1_{name}.csv'))}
    py = {r['scene']: r for r in csv.DictReader(open(F / f'py_{name}.csv'))}
    assert set(c1) == set(py)
    return c1, py


def features(c1, py, scenes, eg):
    """Matriz de entradas por candidato; NaN marca abstención o error."""
    X = {}
    X['egemaps'] = np.array([fnum(c1[s]['pred_arousal_wmean']) for s in scenes])
    X['opensmile_lineal'] = np.array([[fnum(py[s][k]) if py[s]['c2_status'] == 'ok' else np.nan for k in eg] for s in scenes])
    X['praat_indice'] = np.array([[fnum(py[s]['praat_intensity_db']), fnum(py[s]['praat_f0_sd_st'])] for s in scenes])
    X['rms_trivial'] = np.array([fnum(py[s]['rms_db']) for s in scenes])
    return X


def fit(X, y):
    """Ajuste en el origen. Devuelve un modelo serializable por candidato."""
    m = {}
    for c in ['egemaps', 'rms_trivial']:
        ok = np.isfinite(X[c])
        m[c] = {'type': 'raw', 'threshold': best_threshold(X[c][ok], y[ok])}
    Z = X['opensmile_lineal']
    ok = np.isfinite(Z).all(1)
    mu, sd = Z[ok].mean(0), Z[ok].std(0)
    sd[sd == 0] = 1.0
    lr = LogisticRegression(C=1.0, max_iter=5000).fit((Z[ok] - mu) / sd, y[ok])
    sc = lr.decision_function((Z[ok] - mu) / sd)
    m['opensmile_lineal'] = {'type': 'logistic', 'mu': mu.tolist(), 'sd': sd.tolist(), 'coef': lr.coef_[0].tolist(),
                             'intercept': float(lr.intercept_[0]), 'threshold': best_threshold(sc, y[ok])}
    P = X['praat_indice']
    ok = np.isfinite(P).all(1)
    pm, ps = P[ok].mean(0), P[ok].std(0)
    sc = ((P[ok] - pm) / ps).mean(1)
    m['praat_indice'] = {'type': 'zmean', 'mu': pm.tolist(), 'sd': ps.tolist(), 'threshold': best_threshold(sc, y[ok])}
    return m


def score(m, X):
    out = {}
    for c in CANDS:
        mc = m[c]
        if mc['type'] == 'raw':
            out[c] = X[c].astype(float)
        elif mc['type'] == 'logistic':
            Z = X[c]
            s = ((Z - np.array(mc['mu'])) / np.array(mc['sd'])) @ np.array(mc['coef']) + mc['intercept']
            s[~np.isfinite(Z).all(1)] = np.nan
            out[c] = s
        else:
            P = X[c]
            s = ((P - np.array(mc['mu'])) / np.array(mc['sd'])).mean(1)
            s[~np.isfinite(P).all(1)] = np.nan
            out[c] = s
    return out


def metrics(sc, y, spk, thr):
    ok = np.isfinite(sc)
    s, l, g = sc[ok], y[ok], spk[ok]
    keys = np.unique(g)
    blocks = [np.flatnonzero(g == k) for k in keys]
    rng = np.random.default_rng(SEED)
    A, B = [], []
    for _ in range(N_BOOT):
        idx = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        A.append(auc(s[idx], l[idx]))
        B.append(bal_acc(s[idx], l[idx], thr))
    ci = lambda v: [float(np.nanpercentile(v, 2.5)), float(np.nanpercentile(v, 97.5))]
    return {'n': int(ok.sum()), 'total': int(len(sc)), 'coverage': float(ok.mean()), 'speakers': int(len(keys)),
            'auc': auc(s, l), 'auc_ci95': ci(A), 'bal_acc_umbral_origen': bal_acc(s, l, thr), 'bal_acc_ci95': ci(B),
            'undefined_auc_replicates': int(np.sum(~np.isfinite(A)))}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def fit_crema(outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    lab = {r['scene']: r for r in csv.DictReader(open(W / 'labels_crema.csv'))}
    c1, py = load_corpus('crema')
    eg = [k for k in next(iter(py.values())) if k not in NONFEAT]
    assert len(eg) == 88
    sc_all = sorted(s for s in c1 if lab[s]['activation_binary'] != '')
    X = features(c1, py, sc_all, eg)
    y = np.array([int(lab[s]['activation_binary']) for s in sc_all])
    spk = np.array([lab[s]['speaker'] for s in sc_all])
    tr = np.array([lab[s]['split'] == 'train' for s in sc_all])
    m = fit({c: v[tr] for c, v in X.items()}, y[tr])
    m['_meta'] = {'origen': 'crema-d', 'clips_ajuste': int(tr.sum()), 'features_c2': eg,
                  'etiqueta': 'alta = ANG, FEA, HAP; baja = NEU, SAD; DIS excluido'}
    mp = out / 'modelos_origen_crema.json'
    with open(mp, 'x') as f:
        json.dump(m, f)
    S = score(m, {c: v[~tr] for c, v in X.items()})
    res = {c: metrics(S[c], y[~tr], spk[~tr], m[c]['threshold']) for c in CANDS}
    with open(out / 'i3_dentro_crema.json', 'x') as f:
        json.dump({'scope': 'CREMA-D, partición de evaluación; desarrollo', 'resultados': res}, f, indent=2)
    with open(out / 'congelado.sha256', 'x') as f:
        f.write(f'{sha(mp)}  {mp.name}\n')
    print('congelado', sha(mp))


def loso(X, y, spk):
    """Dentro de nEMO: para cada actor, ajusta en los otros ocho y puntúa al actor retenido."""
    S = {c: np.full(len(y), np.nan) for c in CANDS}
    thr = {c: np.full(len(y), np.nan) for c in CANDS}
    for k in np.unique(spk):
        te = spk == k
        m = fit({c: v[~te] for c, v in X.items()}, y[~te])
        s = score(m, {c: v[te] for c, v in X.items()})
        for c in CANDS:
            S[c][te] = s[c]
            thr[c][te] = m[c]['threshold']
    return S, thr


def eval_nemo(outdir):
    out = Path(outdir)
    frozen = (out / 'congelado.sha256').read_text().split()[0]
    mp = out / 'modelos_origen_crema.json'
    assert sha(mp) == frozen, 'el modelo congelado cambió'
    m = json.load(open(mp))
    labn = {r['scene']: r for r in csv.DictReader(open(W / 'labels_nemo.csv'))}
    c1, py = load_corpus('nemo')
    eg = m['_meta']['features_c2']
    sc_all = sorted(s for s in c1 if labn[s]['activation_binary'] != '')
    X = features(c1, py, sc_all, eg)
    y = np.array([int(labn[s]['activation_binary']) for s in sc_all])
    spk = np.array([labn[s]['speaker'] for s in sc_all])
    # transferencia CREMA-D -> nEMO con todo congelado
    S = score(m, X)
    transfer = {c: metrics(S[c], y, spk, m[c]['threshold']) for c in CANDS}
    # dentro de nEMO (dejando fuera a un actor): AUC propia y exactitud con umbral propio
    Sl, Tl = loso(X, y, spk)
    within = {}
    for c in CANDS:
        ok = np.isfinite(Sl[c])
        r = metrics(Sl[c], y, spk, 0.0)
        p = (Sl[c][ok] >= Tl[c][ok]).astype(int)
        l = y[ok]
        r['bal_acc_umbral_propio'] = float(((p[l == 1] == 1).mean() + (p[l == 0] == 0).mean()) / 2)
        r.pop('bal_acc_umbral_origen')
        r.pop('bal_acc_ci95')
        within[c] = r
    # dirección inversa: nEMO completo -> CREMA-D partición de evaluación
    labc = {r['scene']: r for r in csv.DictReader(open(W / 'labels_crema.csv'))}
    c1c, pyc = load_corpus('crema')
    scc = sorted(s for s in c1c if labc[s]['activation_binary'] != '' and labc[s]['split'] == 'test')
    Xc = features(c1c, pyc, scc, eg)
    yc = np.array([int(labc[s]['activation_binary']) for s in scc])
    spc = np.array([labc[s]['speaker'] for s in scc])
    mn = fit(X, y)
    Sc = score(mn, Xc)
    reverse = {c: metrics(Sc[c], yc, spc, mn[c]['threshold']) for c in CANDS}
    with open(out / 'i3_nemo.json', 'x') as f:
        json.dump({'scope': 'nEMO (9 actores) y dirección inversa; desarrollo; etiqueta derivada de la categoría',
                   'modelo_congelado_sha256': frozen, 'transferencia_crema_a_nemo': transfer,
                   'dentro_nemo_dejando_un_actor': within, 'inversa_nemo_a_crema_eval': reverse}, f, indent=2)
    print('ok')


if __name__ == '__main__':
    globals()[sys.argv[1]](sys.argv[2])
