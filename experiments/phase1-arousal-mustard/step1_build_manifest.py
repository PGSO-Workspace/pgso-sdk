"""
Phase 1 -- Step 1: build the eval manifest from MUStARD++ and decode audio.

Keeps target utterances (KEY ends with `_u`) that have human Valence+Arousal
AND a video on disk; decodes each mp4 to 16 kHz mono PCM-16 wav (ffmpeg via
imageio-ffmpeg); writes out/manifest.csv. Reports counts, label ranges, the
duration distribution vs the extractor's 800 ms analysis window, and any
decode failures. Only writes under out/; never touches the corpus.
"""
import csv
import statistics
import subprocess
import sys
import wave
from pathlib import Path

import config as C
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

MANIFEST_FIELDS = [
    "scene", "speaker", "show", "sarcasm",
    "human_valence", "human_arousal",
    "wav_path", "duration_s", "n_samples",
]


def target_rows():
    """Yield (row, scene, video) for target utterances usable in this eval."""
    with open(C.CSV_PATH, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("KEY", "").endswith("_u"):
                continue
            if row["Valence"] == "" or row["Arousal"] == "":
                continue
            scene = row["SCENE"]
            video = C.VIDEO_DIR / f"{scene}_u.mp4"
            if not video.exists():
                continue
            yield row, scene, video


def decode_to_wav(video: Path, wav: Path) -> None:
    """Decode mp4 -> 16 kHz mono PCM-16 wav via ffmpeg (raises on failure)."""
    cmd = [
        FFMPEG, "-y", "-i", str(video),
        "-f", "wav", "-acodec", "pcm_s16le",
        "-ar", str(C.SAMPLE_RATE), "-ac", "1",
        "-loglevel", "error", str(wav),
    ]
    res = subprocess.run(cmd, capture_output=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.decode(errors="replace").strip())


def wav_duration_s(wav: Path) -> float:
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def main() -> int:
    C.print_header()
    C.WAV_DIR.mkdir(parents=True, exist_ok=True)

    rows = list(target_rows())
    print(f"[manifest] {len(rows)} target utterances with human V+A AND video on disk")

    written, decode_fail = 0, 0
    durations = []
    with open(C.MANIFEST, "w", newline="", encoding="utf-8") as mf:
        writer = csv.DictWriter(mf, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row, scene, video in rows:
            wav = C.WAV_DIR / f"{scene}_u.wav"
            try:
                if not wav.exists():
                    decode_to_wav(video, wav)
                dur = wav_duration_s(wav)
            except Exception as e:  # noqa: BLE001 - report and skip, never fabricate
                decode_fail += 1
                print(f"  [decode-fail] {scene}: {e}", file=sys.stderr)
                continue
            durations.append(dur)
            writer.writerow({
                "scene": scene,
                "speaker": row["SPEAKER"],
                "show": row["SHOW"],
                "sarcasm": row["Sarcasm"],
                "human_valence": row["Valence"],
                "human_arousal": row["Arousal"],
                "wav_path": str(wav),
                "duration_s": f"{dur:.3f}",
                "n_samples": int(round(dur * C.SAMPLE_RATE)),
            })
            written += 1

    print(f"[manifest] wrote {written} rows -> {C.MANIFEST}  (decode failures: {decode_fail})")
    if not durations:
        print("[duration] no usable audio decoded -- nothing to report", file=sys.stderr)
        return 1

    durations.sort()
    n = len(durations)
    ge = sum(1 for d in durations if d >= C.DEFAULT_WINDOW_S)
    print("[duration] seconds:")
    print(f"  min={durations[0]:.2f}  median={statistics.median(durations):.2f}  "
          f"mean={statistics.fmean(durations):.2f}  max={durations[-1]:.2f}")
    print(f"  >= {C.DEFAULT_WINDOW_S:.1f}s (yield >=1 reading at default cfg): "
          f"{ge}/{n} ({100 * ge / n:.1f}%)")
    print(f"  <  {C.DEFAULT_WINDOW_S:.1f}s (FULLY abstain at default cfg):    "
          f"{n - ge}/{n} ({100 * (n - ge) / n:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
