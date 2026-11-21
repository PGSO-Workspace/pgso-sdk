#!/bin/bash
# Costo computacional: 200 clips de CREMA-D (semilla 42), un hilo, misma máquina. Factor de tiempo real y memoria máxima.
set -e
cd /home/claude/oe1/run
C=/home/claude/oe1/work/cost; mkdir -p "$C"
python3 - <<'EOF'
import csv, numpy as np
rows = list(csv.DictReader(open('/home/claude/oe1/work/manifest_crema.csv')))
idx = np.sort(np.random.default_rng(42).choice(len(rows), 200, replace=False))
with open('/home/claude/oe1/work/cost/manifest200.csv', 'w', newline='') as h:
    w = csv.DictWriter(h, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows[i] for i in idx)
EOF
B=/home/claude/pgso-workspace/pgso-sdk/experiments/phase1-arousal-mustard/extract/target/release/phase1-extract
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
/usr/bin/time -v "$B" $C/manifest200.csv $C/c1.csv 2> $C/time_c1.txt > /dev/null
for x in c2 c3 b0; do
  /usr/bin/time -v python3 extract_py.py $C/manifest200.csv $C/py_$x.csv --only $x --jobs 1 2> $C/time_$x.txt > /dev/null
done
lscpu | grep -E "Model name|^CPU\(s\)" > $C/hardware.txt
echo DONE > $C/cost.done
