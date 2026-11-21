# -*- coding: utf-8 -*-
"""Ablación tono frente a energía con las ventanas del extractor C1 (CREMA-D, 91 actores, sin ajuste).

Lecturas por ventana: energía = clamp(E/0,3); F0 = clamp(σF0/50); combinación del SDK = clamp(0,6·e + 0,4·p).
Agregación: media ponderada por confianza, como C1. Se repite con el audio normalizado a RMS 0,05.
Uso: python ablation.py <windows_raw.csv> <windows_norm.csv> <labels_crema.csv> <salida.json>
"""
import csv
import json
import sys
from collections import defaultdict

import numpy as np

from stats_oe1 import auc, centered, cluster_boot, rho


def agg(path):
    acc = defaultdict(lambda: np.zeros(4))
    for r in csv.DictReader(open(path)):
        e = min(max(float(r['energy']) / 0.3, 0.0), 1.0)
        p = min(max(float(r['f0_std']) / 50.0, 0.0), 1.0)
        comb = min(max(0.6 * e + 0.4 * p, 0.0), 1.0)
        w = float(r['confidence'])
        a = acc[r['scene']]
        a += np.array([w * e, w * p, w * comb, w])
    out = {}
    for k, a in acc.items():
        if a[3] > 0:
            out[k] = {'energia': a[0] / a[3], 'f0': a[1] / a[3], 'combinacion': a[2] / a[3]}
    return out


def evaluate(vals, lab, common):
    h = np.array([float(lab[k]['intensity']) for k in common])
    s = np.array([lab[k]['speaker'] for k in common])
    res = {}
    for v in ['energia', 'f0', 'combinacion']:
        x = np.array([vals[k][v] for k in common])
        xc, hc = centered(x, s), centered(h, s)
        res[v] = {'spearman': cluster_boot(lambda i: rho(x[i], h[i]), s),
                  'spearman_dentro_hablante': cluster_boot(lambda i: rho(xc[i], hc[i]), s)}
        bm = np.array([lab[k]['activation_binary'] != '' for k in common])
        bl = np.array([int(lab[k]['activation_binary']) for k in np.array(common)[bm]])
        xb, sbm = x[bm], s[bm]
        res[v]['auc_activacion'] = cluster_boot(lambda i: auc(xb[i], bl[i]), sbm)
    comb = np.array([vals[k]['combinacion'] for k in common])
    for v in ['energia', 'f0']:
        x = np.array([vals[k][v] for k in common])
        res[v + '_menos_combinacion'] = cluster_boot(lambda i: rho(x[i], h[i]) - rho(comb[i], h[i]), s)
    return res


def main(rawp, normp, labp, outp):
    lab = {r['scene']: r for r in csv.DictReader(open(labp))}
    raw, norm = agg(rawp), agg(normp)
    common = sorted(set(raw) & set(norm) & set(lab))
    out = {'scope': 'CREMA-D completo (91 actores), desarrollo; clips con lectura en audio original y normalizado',
           'clips': len(common), 'clips_con_lectura_original': len(raw), 'clips_con_lectura_normalizado': len(norm),
           'total_clips': len(lab), 'original': evaluate(raw, lab, common), 'normalizado_rms': evaluate(norm, lab, common)}
    s = np.array([lab[k]['speaker'] for k in common])
    h = np.array([float(lab[k]['intensity']) for k in common])
    for v in ['energia', 'f0', 'combinacion']:
        a = np.array([raw[k][v] for k in common])
        b = np.array([norm[k][v] for k in common])
        out[v + '_normalizado_menos_original'] = cluster_boot(lambda i: rho(b[i], h[i]) - rho(a[i], h[i]), s)
    with open(outp, 'x') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(outp)


if __name__ == '__main__':
    main(*sys.argv[1:5])
