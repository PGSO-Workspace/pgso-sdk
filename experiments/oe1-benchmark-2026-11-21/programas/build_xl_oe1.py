# -*- coding: utf-8 -*-
"""Libro de verificación de OE1 al 21/11/2026 a partir de los resultados de la corrida de desarrollo."""
import csv
import hashlib
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

import cuartiles

R = Path('/home/claude/oe1/res')
OUT = Path('/mnt/user-data/outputs/oe1/Verificacion_OE1_2026-11-21.xlsx')
FONT = 'Arial'
HFILL = PatternFill('solid', fgColor='1F3864')
NFILL = PatternFill('solid', fgColor='F2F2F2')
thin = Side(style='thin', color='BFBFBF')
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

i2 = json.load(open(R / 'i2_evaluador_repo.json'))
sec = json.load(open(R / 'i2_secundarias.json'))
abl = json.load(open(R / 'ablacion_crema.json'))
i3c = json.load(open(R / 'i3/i3_dentro_crema.json'))['resultados']
MAN = (Path('/home/claude/oe1/run/manifiesto.sha256').read_text().split()[0])
FROZ = (R / 'i3/congelado.sha256').read_text().split()[0]
test = [x for x in i2['reports'] if x['split'] == 'test'][0]
i3n = json.load(open(R / 'i3/i3_nemo.json'))

NAMES = {'egemaps': 'C1 egemaps (extractor del SDK)', 'opensmile_lineal': 'C2 openSMILE eGeMAPSv02 + modelo lineal',
         'praat_indice': 'C3 Praat (Parselmouth), índice fijo', 'rms_trivial': 'B0 nivel RMS del clip (línea base trivial)'}
DESC = {'egemaps': 'Energía de tramas sonoras y variabilidad de F0 combinadas con pesos fijos (0,6 y 0,4); escrito en Rust, sin datos de entrenamiento.',
        'opensmile_lineal': '88 descriptores acústicos estándar combinados por una regresión ajustada con 61 actores de CREMA-D.',
        'praat_indice': 'Intensidad media en dB y desviación de F0 en semitonos medidas con Praat, promediadas tras estandarizar.',
        'rms_trivial': 'Volumen medio de la grabación completa; mide cuánto se explica solo por el nivel.'}
LIC = {'egemaps': 'MIT (propio)', 'opensmile_lineal': 'openSMILE: solo investigación', 'praat_indice': 'GPL 3 (Parselmouth)',
       'rms_trivial': 'no aplica'}
CANDS = ['egemaps', 'opensmile_lineal', 'praat_indice', 'rms_trivial']


def ci(d, k='estimate', c='ci95', nd=3):
    if d is None or d.get(k) is None:
        return 'indefinido'
    v = d[k]
    if d.get(c):
        return f'{v:.{nd}f} [{d[c][0]:.{nd}f}; {d[c][1]:.{nd}f}]'.replace('.', ',')
    return f'{v:.{nd}f}'.replace('.', ',')


def num(x, nd=3):
    return f'{x:.{nd}f}'.replace('.', ',')


def sheet(wb, title, header, rows, widths, note=None, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    r0 = 1
    if note:
        ws.cell(1, 1, note).font = Font(name=FONT, size=9, italic=True)
        ws.cell(1, 1).alignment = Alignment(wrap_text=True, vertical='top')
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(header))
        ws.row_dimensions[1].height = 15 * max(2, len(note) // 150 + 1)
        r0 = 3
    for j, h in enumerate(header, 1):
        c = ws.cell(r0, j, h)
        c.font = Font(name=FONT, bold=True, color='FFFFFF', size=10)
        c.fill = HFILL
        c.alignment = Alignment(wrap_text=True, vertical='center')
        c.border = BORDER
    for i, row in enumerate(rows, r0 + 1):
        for j, v in enumerate(row, 1):
            c = ws.cell(i, j, v)
            c.font = Font(name=FONT, size=10)
            c.alignment = Alignment(wrap_text=True, vertical='top')
            c.border = BORDER
            if (i - r0) % 2 == 0:
                c.fill = NFILL
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + j)].width = w
    ws.freeze_panes = ws.cell(r0 + 1, 1)
    return ws


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    nota_alcance = ('Corrida de desarrollo del 21/11/2026 ejecutada por Claude Agent en el entorno de trabajo, con el manifiesto congelado '
                    f'antes de calcular métricas (SHA-256 {MAN[:16]}…). No es un prerregistro aprobado ni tiene la aprobación metodológica '
                    'del investigador en PhDude. CREMA-D ya había sido examinado por el equipo; sus cifras son de desarrollo. Los resultados '
                    'no están todavía en el repositorio del equipo.')
    c1 = test['candidates']['egemaps']['spearman']
    c2 = test['candidates']['opensmile_lineal']['spearman']
    c3 = test['candidates']['praat_indice']['spearman']
    b0 = test['candidates']['rms_trivial']['spearman']
    n1, n2 = cuartiles.resumen()[1], len(cuartiles.CANDIDATOS)
    resumen = [
        ('OE1-I1', 'Revisión de al menos 25 artículos primarios Q1/Q2 de los últimos cinco años, con matriz de extracción que identifique la brecha.',
         'Parcial (candidatos verificados)',
         f'Cuartiles verificados en SJR (2025): {n1} artículos primarios de revista de 2022 a 2026 en la bibliografía de la Memoria v9.6, todos Q1; '
         '[6] está por confirmar como primario. El 21/11/2026 se verificaron 10 candidatos primarios en revistas Q1 (2023 a 2025), con DOI, y 2 de reserva '
         '(hoja «Candidatos I1»).',
         'Leer los candidatos y completar la matriz de extracción (hoja «Matriz I1»). Con los 10 se llegaría a 25 exactos; conviene leer también las reservas.'),
        ('OE1-I2', 'Capacidad discriminativa de al menos tres extractores sobre un corpus de referencia, con correlación frente a la anotación humana e intervalo por remuestreo.',
         'Ejecutado en desarrollo',
         f'CREMA-D, 2454 clips de 30 actores, frente a la intensidad emocional percibida con solo voz. Spearman: C1 {ci(c1)}; C2 {ci(c2)}; '
         f'C3 {ci(c3)}; línea base B0 {ci(b0)}. Techo humano por mitades: {ci(sec["human_ceiling_split_half_spearman_brown"])}.',
         'Aprobación metodológica; incorporación al repositorio. La anotación es intensidad percibida, no activación; corpus actuado ya examinado.'),
        ('OE1-I3', 'Generalización entre corpus: desempeño dentro del corpus frente a transferencia, documentando la degradación.',
         'Ejecutado en desarrollo',
         'AUC de activación alta frente a baja: C1 cae de 0,771 dentro de CREMA-D a 0,559 [0,436; 0,657] en nEMO; C2 de 0,895 a 0,531 '
         '[0,410; 0,617], aunque ajustado dentro de nEMO llega a 0,758; C3 de 0,696 a 0,498; B0 de 0,831 a 0,542. Modelos congelados '
         f'(huella {FROZ[:16]}…) antes de leer las etiquetas de nEMO, abiertas el 21/11/2026 por decisión del usuario.',
         'Aprobación metodológica (nEMO se abrió sin ella). Entre corpus actuados; el habla espontánea queda como límite (MSP-Podcast requiere convenio institucional).'),
        ('OE1-I4', 'Selección justificada del extractor por defecto ponderando discriminación, generalización, costo e interpretabilidad.',
         'Ejecutado en desarrollo',
         'Filtro de licencias: solo C1 es elegible para el SDK (MIT). C1 se mantiene como extractor por defecto y también queda primero en la '
         'ponderación secundaria; C2 lo supera dentro de corpus, C1 no se distingue del nivel trivial B0 y ningún candidato transfiere a nEMO.',
         'Aprobación metodológica. La selección no implica que C1 generalice.'),
    ]
    sheet(wb, 'Resumen', ['Indicador', 'Texto del indicador (resumen; el literal está en el Charter)', 'Estado al 21/11/2026',
                          'Evidencia', 'Pendiente'], resumen, [10, 45, 18, 70, 50], nota_alcance, first=True)

    rows = []
    for c in CANDS:
        v = test['candidates'][c]
        d = test['paired_vs_baseline'].get(c)
        rows.append((NAMES[c], DESC[c], LIC[c], v['total'], num(v['coverage'], 3), ci(v['spearman']),
                     ci(d['delta_spearman']) if d else 'referencia', ci(sec['within_speaker'][c]),
                     ci(sec['paired_within_vs_egemaps'].get(c)) if c != 'egemaps' else 'referencia'))
    rows.append(('Techo humano', 'Acuerdo entre dos mitades aleatorias de los evaluadores de cada clip (Spearman-Brown).', 'no aplica',
                 '', '', ci(sec['human_ceiling_split_half_spearman_brown']), '', '', ''))
    sheet(wb, 'I2 Discriminación', ['Candidato', 'Qué mide (una línea)', 'Licencia', 'Clips', 'Cobertura', 'Spearman [IC 95 %]',
                                    'Diferencia frente a C1 [IC 95 %]', 'Spearman dentro del hablante', 'Diferencia dentro del hablante'],
          rows, [34, 50, 22, 8, 10, 22, 24, 24, 24],
          'CREMA-D, partición de evaluación (actores 1003 a 1090 con número múltiplo de 3; 30 actores). Anotación: intensidad emocional percibida con '
          'solo voz, «neutral» = 0. Intervalos percentiles por 2000 remuestreos de actores (semilla 42) con el evaluador del repositorio '
          '(experiments/phase2-arousal-benchmark/evaluate_predictions.py, sin cambios). Diferencias en la cobertura común de 2383 clips. C2 se ajustó '
          'con los otros 61 actores del mismo corpus; su ventaja es dentro de corpus.')

    cats = ['ANG', 'DIS', 'FEA', 'HAP', 'NEU', 'SAD']
    rows = [(NAMES[c], *[ci(sec['by_category'][c][k]) for k in cats]) for c in CANDS]
    sheet(wb, 'I2 Por categoría', ['Candidato', 'Enfado', 'Asco', 'Miedo', 'Alegría', 'Neutral', 'Tristeza'], rows,
          [34, 20, 20, 20, 20, 20, 20],
          'Spearman con la intensidad percibida dentro de cada categoría actuada (partición de evaluación). La intensidad percibida no equivale a '
          'activación: en tristeza y neutral la lectura no sigue a la intensidad.')

    rows = []
    for cond, lab in [('original', 'Audio original'), ('normalizado_rms', 'Audio normalizado a RMS 0,05')]:
        for v, nv in [('energia', 'Término de energía'), ('f0', 'Término de F0'), ('combinacion', 'Combinación del SDK (0,6 y 0,4)')]:
            x = abl[cond][v]
            rows.append((lab, nv, ci(x['spearman']), ci(x['spearman_dentro_hablante']), ci(x['auc_activacion']),
                         ci(abl[cond].get(v + '_menos_combinacion')) if v != 'combinacion' else 'referencia'))
    for v, nv in [('energia', 'Término de energía'), ('f0', 'Término de F0'), ('combinacion', 'Combinación del SDK')]:
        rows.append(('Normalizado menos original', nv, ci(abl[v + '_normalizado_menos_original']), '', '', ''))
    sheet(wb, 'Tono frente a energía', ['Condición', 'Lectura', 'Spearman [IC 95 %]', 'Spearman dentro del hablante', 'AUC activación alta/baja',
                                        'Diferencia frente a la combinación'], rows, [28, 30, 24, 26, 24, 26],
          f'CREMA-D completo (91 actores), {abl["clips"]} clips con lectura en ambas condiciones. Ventanas del extractor C1 sin modificar; agregación '
          'por media ponderada por confianza. La normalización iguala el nivel de cada clip y elimina tanto el esfuerzo vocal como la distancia al '
          'micrófono.')

    rows = []
    for c in CANDS:
        v = i3c[c]
        t = i3n['transferencia_crema_a_nemo'][c]
        d = i3n['dentro_nemo_dejando_un_actor'][c]
        b = i3n['inversa_nemo_a_crema_eval'][c]
        iv = lambda x, k='auc', k2='auc_ci95': f'{num(x[k])} [{num(x[k2][0])}; {num(x[k2][1])}]'
        rows.append((NAMES[c], v['n'], num(v['coverage']), iv(v), iv(v, 'bal_acc_umbral_origen', 'bal_acc_ci95'),
                     iv(t), iv(t, 'bal_acc_umbral_origen', 'bal_acc_ci95'), iv(d), num(d['bal_acc_umbral_propio']),
                     num(t['auc'] - v['auc']), iv(b)))
    sheet(wb, 'I3 Generalización', ['Candidato', 'Clips (CREMA-D, evaluación)', 'Cobertura', 'AUC dentro de CREMA-D [IC 95 %]',
                                    'Exactitud balanceada dentro de CREMA-D (umbral del origen)', 'AUC en nEMO con lo congelado [IC 95 %]',
                                    'Exactitud balanceada en nEMO con el umbral de CREMA-D', 'AUC dentro de nEMO (dejando un actor fuera)',
                                    'Exactitud balanceada en nEMO con umbral propio', 'Degradación de la AUC (nEMO − CREMA-D)',
                                    'Inversa: AUC en CREMA-D con lo ajustado en nEMO'], rows, [34, 12, 10, 22, 22, 22, 22, 22, 16, 16, 22],
          'Etiqueta común derivada de la categoría por regla del equipo (alta: enfado, miedo, alegría y sorpresa; baja: neutral y tristeza; asco excluido). '
          f'Modelos y umbrales ajustados con los 61 actores de ajuste de CREMA-D y congelados (SHA-256 {FROZ}) antes de leer etiquetas de nEMO. '
          'nEMO (4481 clips, 9 actores) se abrió el 21/11/2026 a las 03:02 por decisión del usuario, sin la aprobación metodológica del investigador. '
          'Con 9 actores los intervalos son amplios. En C1 y B0 la AUC no depende del ajuste, por eso coincide dentro de nEMO y en la transferencia: la caída se '
          'debe al corpus y no a la calibración. Diagnóstico posterior sin etiquetas: el nivel de grabación varía menos en nEMO (desviación media '
          'dentro del hablante de 2,8 dB frente a 6,6 dB en CREMA-D), lo que es compatible con que la señal de volumen esté atenuada; es una hipótesis, '
          'no un resultado. Piso histórico previo: correlaciones entre corpus de −0,029 y 0,188 con descriptores de EMOVOME.')

    cost = {'egemaps': ('0,0144 (7,21 s de pared para 501,87 s)', '2,8 MB (binario nativo)', '2 descriptores; forma cerrada con constantes fijas', 'Alta'),
            'opensmile_lineal': ('0,0228', '193 MB (proceso Python)', '88 descriptores; combinación lineal aprendida', 'Media'),
            'praat_indice': ('0,0025', '118 MB (proceso Python)', '2 medidas fonéticas; forma cerrada con estandarización', 'Alta'),
            'rms_trivial': ('0,0001', '36 MB (proceso Python)', '1 medida; forma cerrada', 'Alta (trivial)')}
    rows = [(NAMES[c], *cost[c], LIC[c], 'Sí' if c == 'egemaps' else ('No' if c != 'rms_trivial' else 'No es candidato')) for c in CANDS]
    sheet(wb, 'Costo e interpretabilidad', ['Candidato', 'Factor de tiempo real (un hilo)', 'Memoria residente máxima', 'Entradas de la lectura',
                                            'Interpretabilidad (criterio del manifiesto)', 'Licencia', 'Elegible como extractor por defecto'],
          rows, [34, 26, 24, 36, 20, 26, 18],
          '200 clips de CREMA-D elegidos con semilla 42 (501,87 s de audio); un hilo; Intel Xeon a 2,10 GHz, 2 núcleos. En C2, C3 y B0 el tiempo es la '
          'suma del proceso por clip; la memoria incluye el intérprete de Python. Factor de tiempo real = segundos de proceso por segundo de audio.')

    c1d = test['paired_vs_baseline']['rms_trivial']['delta_spearman']
    rows = [
        ('1. Filtro de licencias', 'Solo C1 (MIT) puede distribuirse con el SDK sin restricción no comercial ni copyleft. C2 (solo investigación) y C3 (GPL 3) quedan como referencias, igual que wav2small y wav2vec2-MSP, que no se ejecutaron aquí.'),
        ('2. Mejor elegible en I2', f'C1, único elegible: Spearman {ci(c1)}.'),
        ('3. Costo de quedarse con C1', f'C2 − C1 = {ci(test["paired_vs_baseline"]["opensmile_lineal"]["delta_spearman"])} (ventaja de un modelo ajustado en el mismo corpus); C3 − C1 = {ci(test["paired_vs_baseline"]["praat_indice"]["delta_spearman"])}.'),
        ('4. Comparación con el nivel trivial', f'B0 − C1 = {ci(c1d)}: el intervalo de C1 − B0 no queda entero por debajo de cero, así que la regla 3 no se activa; aun así, C1 no supera al nivel de grabación y, dentro del hablante, B0 lo supera ({ci(sec["paired_within_vs_egemaps"]["rms_trivial"])}).'),
        ('5. Ponderación secundaria (pesos iguales de 0,25 sobre rangos)', 'Discriminación: C2 1, C1 2, C3 3. Generalización (AUC en nEMO): C1 1, C2 2, C3 3, con intervalos solapados. Costo (factor de tiempo real): C3 1, C1 2, C2 3. Interpretabilidad: C1 y C3 1,5, C2 3. Rango medio: C1 1,625; C3 2,125; C2 2,25.'),
        ('Resultado (desarrollo)', 'C1 se mantiene como extractor por defecto por elegibilidad de licencia y por la ponderación secundaria, con límites documentados: no supera al nivel de grabación en CREMA-D, su término de F0 no mejora al de energía en audio original y ningún candidato transfiere de CREMA-D a nEMO. La decisión selecciona un extractor por defecto; no certifica que la señal generalice. Una configuración solo de energía o con otros pesos sería una hipótesis para una corrida posterior.'),
    ]
    sheet(wb, 'I4 Selección', ['Paso de la regla del manifiesto', 'Resultado'], rows, [34, 120])

    rows = [(f'[{n}]', y, t, v, q, cat, f'https://www.scimagojr.com/journalsearch.php?q={sid}&tip=sid' if sid else '')
            for n, y, t, v, q, cat, sid in cuartiles.REFS]
    sheet(wb, 'Cuartiles SJR', ['Ref.', 'Año', 'Tipo', 'Sede', 'Cuartil SJR 2025', 'Categoría o nota', 'Ficha SJR'], rows,
          [7, 7, 14, 46, 18, 50, 60],
          'Verificación del 21/11/2026 con la búsqueda restringida a scimagojr.com (fichas SJR 2025). Se listan las referencias de revista o actas de '
          '2021 a 2026 de la Memoria v9.6. Para I1 cuentan los artículos primarios de revista Q1 o Q2 de los últimos cinco años (2022 a 2026). '
          f'Resultado: {cuartiles.resumen()[1]} primarios, todos Q1; [6] por confirmar; las revisiones y las actas no cuentan.')

    rows = [(i, pr, a, y, t, v, f'https://doi.org/{d}', q, f'https://www.scimagojr.com/journalsearch.php?q={sid}&tip=sid', tema, rel)
            for i, (a, y, t, v, d, q, sid, tema, rel, pr) in enumerate(cuartiles.CANDIDATOS, 1)]
    sheet(wb, 'Candidatos I1', ['N.º', 'Prioridad', 'Autores', 'Año', 'Título', 'Revista y datos', 'DOI', 'Cuartil SJR 2025', 'Ficha SJR',
                                'Tema según el resumen', 'Relación con OE1'], rows,
          [5, 10, 34, 7, 50, 40, 34, 10, 44, 50, 34],
          'Artículos primarios de revista de 2023 a 2025 hallados y verificados el 21/11/2026: DOI comprobado en la versión del autor, el editor o un índice '
          '(dblp, NSF PAR, repositorio institucional) y cuartil de la revista en la ficha SJR 2025 (búsqueda restringida a scimagojr.com). Ninguno figura en la '
          'bibliografía de la Memoria v9.6. Los temas resumen el resumen publicado; no sustituyen la lectura. Solo cuentan para I1 cuando el equipo los lea y '
          'los incorpore a la matriz. Seis de los diez provienen del mismo grupo de investigación (UT Dallas y colaboradores); las reservas ayudan a diversificar.')

    mrows = [(f'[{n}] Memoria v9.6', y, v, q, '', '', '', '', '', '', '', '') for n, y, tp, v, q, cat, sid in cuartiles.REFS
             if tp == 'primario' and 2022 <= y <= 2026]
    mrows += [(f'Candidato {i} ({pr})', y, v.split(',')[0], q, '', '', '', '', '', '', '', '')
              for i, (a, y, t, v, d, q, sid, tema, rel, pr) in enumerate(cuartiles.CANDIDATOS, 1)]
    sheet(wb, 'Matriz I1', ['Artículo', 'Año', 'Revista', 'Cuartil SJR 2025', 'Problema o pregunta', 'Datos o corpus', 'Método', 'Resultado principal',
                            'Indicador de OE1 al que aporta', 'Aporte a la brecha (de la percepción a la gobernanza)', 'Leído por', 'Fecha de lectura'],
          mrows, [26, 7, 40, 12, 30, 24, 24, 30, 18, 36, 14, 12],
          'Plantilla de la matriz de extracción que pide OE1-I1. Las columnas de contenido quedan vacías a propósito: las completa el equipo al leer cada artículo.')

    rows = [
        ('Aprobación metodológica del manifiesto', 'Usuario; registro del investigador en PhDude', '21/11/2026 03:12', 'Aprobada por el usuario; falta el registro en PhDude', 'El protocolo del repositorio pide registrarla en PhDude; nEMO se abrió antes de la aprobación.'),
        ('Abrir nEMO con lo congelado', 'Usuario', '21/11/2026 03:02', 'Hecho sin aprobación', 'Se verificó la huella del modelo congelado antes de leer las etiquetas; la corrida de nEMO queda como desarrollo.'),
        ('Licencia de MSP-Podcast (habla espontánea)', 'Equipo con la UPC', 'Enviar ya; fuera del camino crítico', 'No solicitada', 'Requiere un convenio de transferencia y uso de datos (FDP DTUA) firmado por la institución receptora.'),
        ('Fecha real de entrega del documento al jurado', 'Equipo', 'Inmediato', 'Por confirmar', 'El consejo advirtió que probablemente es anterior al 14/12/2026.'),
        ('Incorporar la evidencia al repositorio', 'Equipo', 'Tras la aprobación', 'Pendiente', 'Hasta entonces, las cifras no pueden prevalecer sobre el repositorio ni entrar en la Memoria como resultados del proyecto.'),
        ('Leer y matrizar al menos 10 artículos Q1/Q2 adicionales', 'Equipo', 'Antes de la entrega', 'Pendiente', '10 candidatos verificados con DOI y cuartil, más 2 de reserva (hoja «Candidatos I1»).'),
    ]
    sheet(wb, 'Solicitudes', ['Solicitud', 'Responsable', 'Cuándo', 'Estado', 'Nota'], rows, [40, 22, 24, 14, 70])
    wb.save(OUT)
    print(OUT, hashlib.sha256(OUT.read_bytes()).hexdigest()[:16])


if __name__ == '__main__':
    main()
