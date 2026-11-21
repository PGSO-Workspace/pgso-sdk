# -*- coding: utf-8 -*-
"""Verificación de cuartiles SJR (búsqueda restringida a scimagojr.com, 21/11/2026) de la bibliografía de la Memoria v9.6.

tipo: primario | revisión | conferencia | libro | otro. Ventana de cinco años: 2021-2026 (amplia) y 2022-2026 (estricta).
"""
REFS = [
    # (n, año, tipo, sede, cuartil SJR 2025 (mejor), categoría o nota, id SJR)
    (1, 2025, 'revisión', 'Frontiers of Computer Science', 'Q1', 'mejor cuartil 2025', '21100200426'),
    (2, 2024, 'revisión', 'Frontiers of Computer Science', 'Q1', 'mejor cuartil 2025', '21100200426'),
    (4, 2026, 'revisión', 'ACM Transactions on Software Engineering and Methodology', 'Q1', 'Software', '18121'),
    (6, 2026, 'por confirmar', 'Journal of Service Management', 'Q1', 'mejor cuartil 2025', '19600157901'),
    (7, 2024, 'primario', 'Nature', 'Q1', 'Multidisciplinary', '21206'),
    (9, 2026, 'primario', 'IEEE Open Journal of the Computer Society', 'Q1', 'Computer Science (miscellaneous)', '21101132925'),
    (10, 2026, 'primario', 'ACM Transactions on Software Engineering and Methodology', 'Q1', 'Software', '18121'),
    (13, 2025, 'revisión', 'Perspectives on Psychological Science', 'no verificado', 'revisión; no cuenta para I1', ''),
    (16, 2023, 'primario', 'IEEE Transactions on Pattern Analysis and Machine Intelligence', 'Q1', 'mejor cuartil 2025', '24254'),
    (17, 2025, 'revisión', 'Artificial Intelligence Review', 'Q1', 'revisión sistemática y metaanálisis', '24141'),
    (18, 2025, 'primario', 'Knowledge-Based Systems', 'Q1', 'mejor cuartil 2025', '24772'),
    (19, 2025, 'primario', 'IEEE Transactions on Affective Computing', 'Q1', 'Human-Computer Interaction; Software', '19700177034'),
    (20, 2025, 'primario', 'Journal of Business Research', 'Q1', 'Business and International Management', '20550'),
    (21, 2026, 'primario', 'Decision Support Systems', 'Q1', 'mejor cuartil 2025', '21933'),
    (22, 2026, 'primario', 'International Journal of Human-Computer Interaction', 'Q1', 'Human-Computer Interaction', '19420'),
    (23, 2026, 'primario', 'IEEE Transactions on Audio, Speech and Language Processing', 'Q1 (título anterior)',
     'SJR 2025 Q1 para IEEE/ACM TASLP (cobertura hasta 2025); el título renombrado no muestra cuartil 2025', '21100368801'),
    (24, 2026, 'primario', 'Knowledge-Based Systems', 'Q1', 'mejor cuartil 2025', '24772'),
    (38, 2024, 'revisión', 'The American Statistician', 'no verificado', 'revisión; no cuenta para I1', ''),
    (39, 2026, 'primario', 'Journal of Systems and Software', 'Q1', 'Hardware and Architecture', '19309'),
    (43, 2026, 'conferencia', 'Proceedings - International Conference on Software Engineering', 'sin cuartil',
     'SJR 1,606 (2025); las actas no reciben cuartil', '19242'),
    (47, 2026, 'primario', 'IEEE Access', 'Q1', 'Computer Science (miscellaneous)', '21100374601'),
    (48, 2026, 'primario', 'Frontiers in Artificial Intelligence', 'Q1', 'Artificial Intelligence', '21101062817'),
    (49, 2026, 'primario', 'IEEE Access', 'Q1', 'Computer Science (miscellaneous)', '21100374601'),
]

# Candidatos para completar I1 (búsqueda y verificación del 21/11/2026): artículos primarios de revista, 2023 a 2025,
# revistas Q1 en SJR 2025, DOI verificado en una fuente del editor, del repositorio del autor o de un índice bibliográfico.
# (autores, año, título, revista y datos, DOI, cuartil SJR 2025, id SJR, tema según el resumen, relación con OE1, prioridad)
CANDIDATOS = [
    ('W.-C. Lin, K. Sridhar y C. Busso', 2024,
     'An interpretable deep mutual information curriculum metric for a robust and generalized speech emotion recognition system',
     'IEEE/ACM Transactions on Audio, Speech, and Language Processing, vol. 32, pp. 5117-5130', '10.1109/TASLP.2024.3507562', 'Q1', '21100368801',
     'Métrica de dificultad de muestras para entrenar reconocedores de emoción más robustos y generalizables; atributos de activación, valencia y dominancia.',
     'I2 e I3: generalización y anotaciones ambiguas.', 'principal'),
    ('S.-G. Leem, D. Fulford, J.-P. Onnela, D. Gard y C. Busso', 2024,
     'Selective acoustic feature enhancement for speech emotion recognition with noisy speech',
     'IEEE/ACM Transactions on Audio, Speech, and Language Processing, vol. 32, pp. 917-929', '10.1109/TASLP.2023.3340603', 'Q1', '21100368801',
     'Mejora solo los descriptores acústicos sensibles al ruido y conserva los robustos; experimentos con MSP-Podcast limpio y ruidoso.',
     'Ablación y robustez: qué descriptores de bajo nivel resisten cambios de condición.', 'principal'),
    ('T. Yan, H. Meng, E. Parada-Cabaleiro, J. Tao, T. Li y B. W. Schuller', 2025,
     'A residual multi-scale convolutional neural network with transformers for speech emotion recognition',
     'IEEE Transactions on Affective Computing, vol. 16, no. 2, pp. 915-932', '10.1109/TAFFC.2024.3481253', 'Q1', '19700177034',
     'Red convolucional multiescala con transformadores para predecir activación, valencia y dominancia en MSP-Podcast.',
     'I2: referencia de extractor aprendido frente a descriptores interpretables.', 'principal'),
    ('H.-C. Chou, L. Goncalves, S.-G. Leem, A. N. Salman, C.-C. Lee y C. Busso', 2025,
     'Minority views matter: Evaluating speech emotion classifiers with human subjective annotations by an all-inclusive aggregation rule',
     'IEEE Transactions on Affective Computing, vol. 16, no. 1, pp. 41-55', '10.1109/TAFFC.2024.3411290', 'Q1', '19700177034',
     'Regla de agregación que conserva todas las calificaciones subjetivas para evaluar reconocedores sin descartar datos ambiguos.',
     'I2: construcción de la anotación humana y techo entre evaluadores.', 'principal'),
    ('W.-S. Chien, S. G. Upadhyay, W.-C. Lin, C. Busso y C.-C. Lee', 2025,
     'Differential impacts of monologue and conversation on speech emotion recognition',
     'IEEE Transactions on Affective Computing, vol. 16, no. 2, pp. 485-498', '10.1109/TAFFC.2024.3509138', 'Q1', '19700177034',
     'Compara habla emocional espontánea de monólogo y de conversación como fuente de datos para reconocedores.',
     'Límite de habla espontánea y validez externa.', 'principal'),
    ('S. G. Upadhyay, L. Martinez-Lucas, W. Katz, C. Busso y C.-C. Lee', 2025,
     'Phonetically-anchored domain adaptation for cross-lingual speech emotion recognition',
     'IEEE Transactions on Affective Computing, 2025 (volumen y páginas por confirmar)', '10.1109/TAFFC.2025.3530105', 'Q1', '19700177034',
     'Adaptación de dominio anclada en la fonética para reconocer emoción entre idiomas.',
     'I3: transferencia entre corpus de idiomas distintos, como CREMA-D y nEMO.', 'principal'),
    ('S. Latif, R. Rana, S. Khalifa, R. Jurdak y B. W. Schuller', 2023,
     'Self supervised adversarial domain adaptation for cross-corpus and cross-language speech emotion recognition',
     'IEEE Transactions on Affective Computing, vol. 14, no. 3, pp. 1912-1926', '10.1109/TAFFC.2022.3167013', 'Q1', '19700177034',
     'Adaptación de dominio adversaria y autosupervisada para reconocer emoción entre corpus e idiomas sin etiquetas del destino.',
     'I3: degradación entre corpus y estrategias para reducirla.', 'principal'),
    ('W.-C. Lin y C. Busso', 2024,
     'Deep temporal clustering features for speech emotion recognition',
     'Speech Communication, vol. 157, art. 103027', '10.1016/j.specom.2023.103027', 'Q1', '23671',
     'Rasgos de agrupamiento profundo con modelado temporal, semisupervisado, para activación, dominancia y valencia.',
     'I2: dinámica temporal de la señal frente a lecturas por ventana.', 'principal'),
    ('M. Agarla, S. Bianco, L. Celona, P. Napoletano, A. Petrovsky, F. Piccoli, R. Schettini e I. Shanin', 2024,
     'Semi-supervised cross-lingual speech emotion recognition',
     'Expert Systems with Applications, vol. 237, parte A, art. 121368', '10.1016/j.eswa.2023.121368', 'Q1', '24201',
     'Reconocimiento de emoción entre idiomas con pocos ejemplos etiquetados en el idioma de destino.',
     'I3: cuánto cuesta recuperar desempeño en el destino.', 'principal'),
    ('H. Lian, C. Lu, Y. Zhao, S. Li, T. Qi e Y. Zong', 2024,
     'Exploring corpus-invariant emotional acoustic feature for cross-corpus speech emotion recognition',
     'Expert Systems with Applications, art. 125162 (volumen por confirmar)', '10.1016/j.eswa.2024.125162', 'Q1', '24201',
     'Aprendizaje de subespacios para hallar rasgos acústicos invariantes entre corpus, con atención a la interpretabilidad del mapeo desde los descriptores.',
     'I3 e I4: generalización e interpretabilidad de descriptores de bajo nivel.', 'principal'),
    ('L. Martinez-Lucas, W.-C. Lin y C. Busso', 2024,
     'Analyzing continuous-time and sentence-level annotations for speech emotion recognition',
     'IEEE Transactions on Affective Computing, vol. 15, no. 3 (páginas por confirmar)', '10.1109/TAFFC.2024.3372380', 'Q1', '19700177034',
     'Compara anotaciones continuas y por frase de MSP-Conversation y MSP-Podcast, su acuerdo y su efecto en el reconocimiento.',
     'I2: anotación por enunciado frente a la lectura continua del SDK.', 'reserva'),
    ('M. A. Pastor, D. Ribas, A. Ortega, A. Miguel y E. Lleida', 2023,
     'Cross-corpus training strategy for speech emotion recognition using self-supervised representations',
     'Applied Sciences, vol. 13, no. 16, art. 9062', '10.3390/app13169062', 'Q2', '21100829268',
     'Amplía el entrenamiento con varios corpus y representaciones HuBERT y WavLM.',
     'I3: entrenamiento entre corpus como alternativa a la transferencia directa.', 'reserva'),
]


def resumen():
    prim = [r for r in REFS if r[2] == 'primario' and str(r[4]).startswith('Q1') or (r[2] == 'primario' and str(r[4]).startswith('Q2'))]
    amplia = [r for r in prim if 2021 <= r[1] <= 2026]
    estricta = [r for r in prim if 2022 <= r[1] <= 2026]
    dudoso = [r for r in REFS if r[2] == 'por confirmar']
    return len(amplia), len(estricta), len(dudoso)


if __name__ == '__main__':
    print(resumen())
