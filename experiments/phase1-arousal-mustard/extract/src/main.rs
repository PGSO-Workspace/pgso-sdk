//! Phase 1 -- Step 2: run the eGeMAPS extractor over the manifest utterances.
//!
//! Reads `manifest.csv`, runs `pgso-signal-egemaps` over each utterance's wav,
//! aggregates the per-window arousal readings into per-utterance scalars
//! (PRE-REGISTERED primary = confidence-weighted mean; mean + median as
//! sensitivity), and writes `predicted.csv`. If a third path is given, also
//! writes per-window features to `windows.csv` (energy, f0_std, confidence)
//! so the label-free calibration study can recompute arousal under alternative
//! references. The extractor is used as-is at its shipped default config; no
//! DSP or mapping is reimplemented here.
//!
//! Usage: phase1-extract <manifest.csv> <predicted.csv> [windows.csv]

mod agg;

use std::fs::File;

use anyhow::{bail, Context, Result};
use pgso_core::AudioWindow;
use pgso_signal_egemaps::{EgemapsConfig, EgemapsSignal};
use serde::{Deserialize, Serialize};

const SAMPLE_RATE: u32 = 16_000;

/// Columns we read from the step-1 manifest (extra columns are ignored).
#[derive(Debug, Deserialize)]
struct ManifestRow {
    scene: String,
    speaker: String,
    show: String,
    sarcasm: String,
    human_valence: String,
    human_arousal: String,
    wav_path: String,
}

/// One output row: human labels passed through + predicted arousal + diagnostics.
#[derive(Debug, Serialize)]
struct PredictedRow {
    scene: String,
    speaker: String,
    show: String,
    sarcasm: String,
    human_valence: String,
    human_arousal: String,
    duration_s: f32,
    n_samples: usize,
    n_windows_possible: usize,
    n_readings: usize,
    abstained: bool,
    too_short: bool,
    // PRE-REGISTERED primary predictor; None when the utterance abstained.
    pred_arousal_wmean: Option<f32>,
    pred_arousal_mean: Option<f32>,
    pred_arousal_median: Option<f32>,
    mean_confidence: Option<f32>,
    mean_voiced_fraction: Option<f32>,
    mean_energy: Option<f32>,
    mean_f0_std: Option<f32>,
}

/// Per-window raw features (for the label-free calibration recompute).
#[derive(Debug, Serialize)]
struct WindowRow {
    scene: String,
    win_idx: usize,
    energy: f32,
    f0_std: f32,
    confidence: f32,
    voiced_fraction: f32,
    arousal_default: f32,
}

/// PCM-16 mono 16 kHz wav -> f32 samples in [-1, 1).
fn read_wav_samples(path: &str) -> Result<Vec<f32>> {
    let mut reader =
        hound::WavReader::open(path).with_context(|| format!("open wav: {path}"))?;
    let spec = reader.spec();
    if spec.channels != 1 {
        bail!("expected mono, got {} channels", spec.channels);
    }
    if spec.sample_rate != SAMPLE_RATE {
        bail!("expected {SAMPLE_RATE} Hz, got {} Hz", spec.sample_rate);
    }
    if spec.sample_format != hound::SampleFormat::Int || spec.bits_per_sample != 16 {
        bail!(
            "expected PCM-16; got {:?}/{} bits",
            spec.sample_format,
            spec.bits_per_sample
        );
    }
    reader
        .samples::<i16>()
        .map(|s| s.map(|v| f32::from(v) / 32768.0))
        .collect::<std::result::Result<_, _>>()
        .context("decode PCM-16 samples")
}

/// Number of analysis windows the extractor would place over `n_samples`.
fn n_windows_possible(n_samples: usize, cfg: &EgemapsConfig) -> usize {
    if n_samples < cfg.window_size {
        0
    } else {
        (n_samples - cfg.window_size) / cfg.window_hop + 1
    }
}

fn mean_or_none(xs: &[f32]) -> Option<f32> {
    if xs.is_empty() {
        None
    } else {
        Some(xs.iter().sum::<f32>() / xs.len() as f32)
    }
}

fn open_opt_writer(path: Option<String>) -> Result<Option<csv::Writer<File>>> {
    match path {
        Some(p) => Ok(Some(
            csv::Writer::from_path(&p).with_context(|| format!("create: {p}"))?,
        )),
        None => Ok(None),
    }
}

fn process_row(row: &ManifestRow, cfg: &EgemapsConfig) -> Result<(PredictedRow, Vec<WindowRow>)> {
    let samples = read_wav_samples(&row.wav_path)?;
    let n_samples = samples.len();
    let duration_s = n_samples as f32 / SAMPLE_RATE as f32;

    let window = AudioWindow {
        samples,
        sample_rate: SAMPLE_RATE,
        timestamp_ms: 0,
    };
    // Fresh extractor per utterance: no cross-utterance baseline contamination.
    let mut signal = EgemapsSignal::new(SAMPLE_RATE);
    let readings = signal.extract_explained(&window);

    let pairs: Vec<(f32, f32)> = readings
        .iter()
        .map(|e| (e.reading.value, e.reading.confidence))
        .collect();
    let aggs = agg::aggregate(&pairs);

    let conf: Vec<f32> = readings.iter().map(|e| e.reading.confidence).collect();
    let vfrac: Vec<f32> = readings.iter().map(|e| e.features.voiced_fraction).collect();
    let energy: Vec<f32> = readings.iter().map(|e| e.features.mean_energy).collect();
    let f0_std: Vec<f32> = readings.iter().map(|e| e.features.f0_std_hz).collect();

    let windows: Vec<WindowRow> = readings
        .iter()
        .enumerate()
        .map(|(i, e)| WindowRow {
            scene: row.scene.clone(),
            win_idx: i,
            energy: e.features.mean_energy,
            f0_std: e.features.f0_std_hz,
            confidence: e.reading.confidence,
            voiced_fraction: e.features.voiced_fraction,
            arousal_default: e.reading.value,
        })
        .collect();

    let prow = PredictedRow {
        scene: row.scene.clone(),
        speaker: row.speaker.clone(),
        show: row.show.clone(),
        sarcasm: row.sarcasm.clone(),
        human_valence: row.human_valence.clone(),
        human_arousal: row.human_arousal.clone(),
        duration_s,
        n_samples,
        n_windows_possible: n_windows_possible(n_samples, cfg),
        n_readings: readings.len(),
        abstained: readings.is_empty(),
        too_short: n_samples < cfg.window_size,
        pred_arousal_wmean: aggs.map(|a| a.weighted_mean),
        pred_arousal_mean: aggs.map(|a| a.mean),
        pred_arousal_median: aggs.map(|a| a.median),
        mean_confidence: mean_or_none(&conf),
        mean_voiced_fraction: mean_or_none(&vfrac),
        mean_energy: mean_or_none(&energy),
        mean_f0_std: mean_or_none(&f0_std),
    };
    Ok((prow, windows))
}

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let manifest = args
        .next()
        .context("usage: phase1-extract <manifest.csv> <predicted.csv> [windows.csv]")?;
    let out = args
        .next()
        .context("usage: phase1-extract <manifest.csv> <predicted.csv> [windows.csv]")?;
    let mut win_wtr = open_opt_writer(args.next())?;

    let cfg = EgemapsConfig::for_sample_rate(SAMPLE_RATE);
    let mut rdr =
        csv::Reader::from_path(&manifest).with_context(|| format!("open manifest: {manifest}"))?;
    let mut wtr =
        csv::Writer::from_path(&out).with_context(|| format!("create predicted: {out}"))?;

    let (mut n_total, mut n_ok, mut n_abstain, mut n_fail) = (0usize, 0usize, 0usize, 0usize);
    for rec in rdr.deserialize() {
        let row: ManifestRow = rec.context("parse manifest row")?;
        n_total += 1;
        match process_row(&row, &cfg) {
            Ok((prow, wrows)) => {
                if prow.abstained {
                    n_abstain += 1;
                }
                wtr.serialize(prow).context("write predicted row")?;
                if let Some(w) = win_wtr.as_mut() {
                    for wr in wrows {
                        w.serialize(wr).context("write window row")?;
                    }
                }
                n_ok += 1;
            }
            Err(e) => {
                eprintln!("[extract-fail] {}: {e:#}", row.scene);
                n_fail += 1;
            }
        }
    }
    wtr.flush().context("flush predicted.csv")?;
    if let Some(mut w) = win_wtr {
        w.flush().context("flush windows.csv")?;
    }

    let pct = if n_ok > 0 {
        100.0 * n_abstain as f32 / n_ok as f32
    } else {
        0.0
    };
    println!(
        "[extract] {n_total} utterances | wrote {n_ok} | abstained {n_abstain} ({pct:.1}%) | failed {n_fail}"
    );
    println!("[extract] -> {out}");
    Ok(())
}
