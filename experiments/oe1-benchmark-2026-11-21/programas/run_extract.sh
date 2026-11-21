#!/bin/bash
set -e
cd /home/claude/oe1/run
W=/home/claude/oe1/work/full; mkdir -p "$W"
B=/home/claude/pgso-workspace/pgso-sdk/experiments/phase1-arousal-mustard/extract/target/release/phase1-extract
for n in crema crema_norm nemo; do $B ../work/manifest_$n.csv $W/c1_$n.csv $W/c1_windows_$n.csv > $W/c1_$n.log 2>&1; done
python3 extract_py.py ../work/manifest_crema.csv $W/py_crema.csv --jobs 2 > $W/py_crema.log 2>&1
python3 extract_py.py ../work/manifest_nemo.csv $W/py_nemo.csv --jobs 2 > $W/py_nemo.log 2>&1
echo DONE > $W/extract.done
