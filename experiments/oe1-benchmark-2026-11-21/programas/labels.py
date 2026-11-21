# -*- coding: utf-8 -*-
"""Anotaciones según el manifiesto del 21/11/2026.

CREMA-D: intensidad emocional percibida con solo voz (queryType = 1); «neutral» = 0;
respLevel no numérico descartado. Mitades aleatorias de evaluadores (semilla 42) para el techo.
Etiqueta binaria común: alta = ANG, FEA, HAP (+ surprised en nEMO); baja = NEU, SAD; asco excluido.
"""
import csv
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

D = Path('/home/claude/oe1/data')
W = Path('/home/claude/oe1/work')
CREMA_HIGH, CREMA_LOW = {'ANG', 'FEA', 'HAP'}, {'NEU', 'SAD'}
NEMO_HIGH, NEMO_LOW = {'anger', 'fear', 'happiness', 'surprised'}, {'neutral', 'sadness'}


def crema():
    resp = defaultdict(list)
    dropped = 0
    for r in csv.DictReader(open(D / 'finishedResponses.csv')):
        if r['queryType'] != '1':
            continue
        if not re.fullmatch(r'\d+(\.\d+)?', r['respLevel'] or ''):
            dropped += 1
            continue
        lvl = 0.0 if r['respEmo'] == 'N' else float(r['respLevel'])
        resp[r['clipName']].append(lvl)
    rng = np.random.default_rng(42)
    rows = []
    for f in sorted((D / 'crema').glob('*.wav')):
        scene = f.stem
        actor, sent, emo, lvl = scene.split('_')
        v = np.array(resp.get(scene, []), dtype=float)
        half_a = half_b = None
        if v.size >= 2:
            idx = rng.permutation(v.size)
            a, b = v[idx[: v.size // 2]], v[idx[v.size // 2:]]
            half_a, half_b = float(a.mean()), float(b.mean())
        binary = 1 if emo in CREMA_HIGH else 0 if emo in CREMA_LOW else ''
        split = 'test' if (int(actor) - 1000) % 3 == 0 else 'train'
        rows.append({'scene': scene, 'speaker': actor, 'split': split, 'sentence': sent,
                     'category': emo, 'nominal_level': lvl, 'n_voice_ratings': int(v.size),
                     'intensity': float(v.mean()) if v.size else '', 'half_a': half_a if half_a is not None else '',
                     'half_b': half_b if half_b is not None else '', 'activation_binary': binary})
    with open(W / 'labels_crema.csv', 'w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print('crema', len(rows), 'descartadas no numéricas', dropped,
          'sin calificación', sum(1 for r in rows if r['intensity'] == ''))


def nemo():
    rows = []
    for r in csv.DictReader(open(D / 'main_data.tsv'), delimiter='\t'):
        scene = r['file_id'].removesuffix('.wav')
        emo = r['emotion'].strip()
        binary = 1 if emo in NEMO_HIGH else 0 if emo in NEMO_LOW else ''
        rows.append({'scene': scene, 'speaker': r['speaker_id'].strip(), 'category': emo,
                     'activation_binary': binary})
    with open(W / 'labels_nemo.csv', 'w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print('nemo', len(rows))


if __name__ == '__main__':
    import sys
    for s in sys.argv[1:] or ['crema']:
        globals()[s]()
