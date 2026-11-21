# -*- coding: utf-8 -*-
"""Métricas secundarias de I2 en CREMA-D (partición de evaluación): dentro del hablante, por categoría,
techo humano por mitades y línea base trivial. La métrica primaria la calcula el evaluador del repositorio.

Uso: python analysis_i2.py <pred_i2.csv> <labels_crema.csv> <salida.json>
"""
import csv
import json
import sys

import numpy as np

from stats_oe1 import centered, cluster_boot, rho

CANDS = ['egemaps', 'opensmile_lineal', 'praat_indice', 'rms_trivial']


def main(predp, labp, outp):
    lab = {r['scene']: r for r in csv.DictReader(open(labp))}
    rows = [r for r in csv.DictReader(open(predp)) if r['split'] == 'test']
    out = {'scope': 'CREMA-D, partición de evaluación (30 actores); desarrollo', 'within_speaker': {},
           'by_category': {}, 'paired_within_vs_egemaps': {}}
    by = {c: {r['scene']: r for r in rows if r['candidate'] == c} for c in CANDS}
    for c in CANDS:
        ok = [r for r in by[c].values() if r['status'] == 'ok']
        p = np.array([float(r['prediction']) for r in ok])
        h = np.array([float(r['human_arousal']) for r in ok])
        s = np.array([r['speaker'] for r in ok])
        pc, hc = centered(p, s), centered(h, s)
        out['within_speaker'][c] = cluster_boot(lambda i: rho(pc[i], hc[i]), s)
        cats = np.array([lab[r['scene']]['category'] for r in ok])
        out['by_category'][c] = {}
        for k in sorted(set(cats)):
            m = cats == k
            pk, hk = p[m], h[m]
            out['by_category'][c][k] = cluster_boot(lambda i, pk=pk, hk=hk: rho(pk[i], hk[i]), s[m])
    # diferencia pareada dentro del hablante frente a C1, en la cobertura común
    for c in CANDS[1:]:
        common = sorted(k for k in by[c] if by[c][k]['status'] == 'ok' and by['egemaps'][k]['status'] == 'ok')
        a = np.array([float(by[c][k]['prediction']) for k in common])
        b = np.array([float(by['egemaps'][k]['prediction']) for k in common])
        h = np.array([float(by[c][k]['human_arousal']) for k in common])
        s = np.array([by[c][k]['speaker'] for k in common])
        ac, bc, hc = centered(a, s), centered(b, s), centered(h, s)
        out['paired_within_vs_egemaps'][c] = cluster_boot(lambda i: rho(ac[i], hc[i]) - rho(bc[i], hc[i]), s)
    # techo humano: mitades de evaluadores, Spearman-Brown
    test = [r for r in lab.values() if r['split'] == 'test' and r['half_a'] != '' and r['half_b'] != '']
    ha = np.array([float(r['half_a']) for r in test])
    hb = np.array([float(r['half_b']) for r in test])
    s = np.array([r['speaker'] for r in test])

    def sb(i):
        r = rho(ha[i], hb[i])
        return 2 * r / (1 + r)
    out['human_ceiling_split_half_spearman_brown'] = cluster_boot(sb, s)
    out['human_ceiling_split_half_raw'] = cluster_boot(lambda i: rho(ha[i], hb[i]), s)
    out['ratings_per_clip_test'] = {'median': float(np.median([int(r['n_voice_ratings']) for r in test])),
                                    'min': int(min(int(r['n_voice_ratings']) for r in test))}
    with open(outp, 'x') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(outp)


if __name__ == '__main__':
    main(*sys.argv[1:4])
