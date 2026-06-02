//! Pure-Rust eGeMAPS-style DSP signal extractor — the default, auditable PGSO signal.
//!
//! Computes an interpretable subset of low-level descriptors (F0, energy, jitter,
//! shimmer, voicing) from raw audio, with no ML runtime and no proprietary
//! dependency. Each `SignalReading` is explainable via [`ExplainedReading`].

// Window/frame arithmetic converts sample counts and rates between integer and
// float; the precision loss and f32<->usize/u64 conversions are intentional and
// bounded for streaming audio.
#![allow(
    clippy::cast_precision_loss,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss
)]
#![deny(missing_docs)]

mod dsp;
mod features;
mod windowing;

use features::RunningStats;
pub use features::{AcousticFeatures, AxisMapping};
use pgso_core::{AudioWindow, Axis, Signal, SignalReading};

/// Configuration for the DSP extractor. Defaults target 16 kHz speech.
#[derive(Debug, Clone)]
pub struct EgemapsConfig {
    /// Samples per analysis window (800 ms @ 16 kHz = 12800).
    pub window_size: usize, // samples per analysis window (800ms @16k = 12800)
    /// Hop between successive analysis windows in samples (400 ms @ 16 kHz = 6400).
    pub window_hop: usize, // overlap hop (400ms @16k = 6400)
    /// Samples per intra-window frame (≈ 64 ms @ 16 kHz).
    pub frame_size: usize, // intra-window frame (1024 @16k ≈ 64ms)
    /// Hop between successive frames in samples (16 ms @ 16 kHz = 256).
    pub frame_hop: usize, // frame hop (256 @16k = 16ms)
    /// Lowest F0 considered, in Hz.
    pub min_f0: f32,
    /// Highest F0 considered, in Hz.
    pub max_f0: f32,
    /// A frame is voiced if its voicing probability exceeds this value.
    pub voicing_threshold: f32, // frame voiced if voicing prob exceeds this
    /// A frame is treated as silent below this RMS energy.
    pub energy_floor: f32, // frame considered silent below this RMS
    /// A window emits a reading only if its voiced fraction is at least this.
    pub min_voiced_fraction: f32, // window emits a reading only above this
}

impl EgemapsConfig {
    /// Default configuration scaled to the given sample rate (Hz), targeting speech.
    #[must_use]
    pub fn for_sample_rate(sr: u32) -> Self {
        let s = sr as f32;
        Self {
            window_size: (0.800 * s) as usize,
            window_hop: (0.400 * s) as usize,
            frame_size: (0.064 * s) as usize,
            frame_hop: (0.016 * s) as usize,
            min_f0: 50.0,
            max_f0: 500.0,
            voicing_threshold: 0.5,
            energy_floor: 0.01,
            min_voiced_fraction: 0.25,
        }
    }
}

/// One reading paired with the interpretable features that produced it.
#[derive(Debug, Clone)]
pub struct ExplainedReading {
    /// The signal reading handed to the decision engine.
    pub reading: SignalReading,
    /// The interpretable low-level descriptors that produced the reading.
    pub features: AcousticFeatures,
}

/// The default PGSO signal: interpretable, lightweight, CPU-only DSP.
pub struct EgemapsSignal {
    config: EgemapsConfig,
    sample_rate: u32,
    mapping: AxisMapping,
    energy_baseline: RunningStats,
    f0_baseline: RunningStats,
}

impl EgemapsSignal {
    /// Build a signal for audio already resampled to `sample_rate` (typically
    /// 16 kHz). Windows fed to [`Self::extract`]/[`Self::extract_explained`] are
    /// assumed to be at this rate (see those methods).
    #[must_use]
    pub fn new(sample_rate: u32) -> Self {
        Self {
            config: EgemapsConfig::for_sample_rate(sample_rate),
            sample_rate,
            mapping: AxisMapping::default(),
            energy_baseline: RunningStats::new(),
            f0_baseline: RunningStats::new(),
        }
    }

    /// Like [`Self::new`] but with an explicit [`EgemapsConfig`].
    ///
    /// Caller-trust boundary: `config` is assumed well-formed. A nonsensical
    /// config does not error but degrades gracefully toward abstention — e.g.
    /// `min_f0 >= max_f0` yields an empty lag window so every frame reads as
    /// unvoiced (no readings). The `debug_assert`s below catch the common
    /// mistakes in dev/test builds.
    #[must_use]
    pub fn with_config(sample_rate: u32, config: EgemapsConfig) -> Self {
        debug_assert!(
            config.min_f0 < config.max_f0,
            "EgemapsConfig.min_f0 must be < max_f0"
        );
        debug_assert!(
            config.window_size > 0
                && config.window_hop > 0
                && config.frame_size > 0
                && config.frame_hop > 0,
            "EgemapsConfig window/frame sizes and hops must be non-zero"
        );
        Self {
            config,
            sample_rate,
            mapping: AxisMapping::default(),
            energy_baseline: RunningStats::new(),
            f0_baseline: RunningStats::new(),
        }
    }

    /// Extract readings AND their interpretable features (auditability, REQ-3.6).
    ///
    /// `window.samples` MUST already be at the construction `sample_rate`; the
    /// per-window `window.sample_rate` field is not used to resample (a mismatch
    /// silently misreads F0). The `debug_assert` flags a mismatch in dev/test.
    pub fn extract_explained(&mut self, window: &AudioWindow) -> Vec<ExplainedReading> {
        debug_assert_eq!(
            window.sample_rate, self.sample_rate,
            "AudioWindow.sample_rate must match the signal's construction rate"
        );
        let cfg = &self.config;
        let mut out = Vec::new();

        for sub in windowing::sliding_windows(&window.samples, cfg.window_size, cfg.window_hop) {
            let frames = windowing::frames(sub, cfg.frame_size, cfg.frame_hop);
            if frames.is_empty() {
                continue;
            }

            let mut voiced_f0 = Vec::new();
            let mut voiced_amp = Vec::new();
            let mut voiced_energy = Vec::new();
            let mut voicing_sum = 0.0f32;
            let mut voiced_count = 0usize;

            for frame in &frames {
                let energy = dsp::rms_energy(frame);
                let (f0, voicing) =
                    dsp::estimate_f0(frame, self.sample_rate, cfg.min_f0, cfg.max_f0);
                let voiced = voicing >= cfg.voicing_threshold && energy >= cfg.energy_floor;
                if voiced {
                    voiced_count += 1;
                    voicing_sum += voicing;
                    voiced_f0.push(f0);
                    voiced_amp.push(dsp::peak_amplitude(frame));
                    voiced_energy.push(energy);
                }
            }

            let voiced_fraction = voiced_count as f32 / frames.len() as f32;
            if voiced_fraction < cfg.min_voiced_fraction || voiced_f0.is_empty() {
                continue; // REQ-3.8: abstain on insufficient voiced content
            }

            let mean_f0 = features::mean(&voiced_f0);
            let f0_std = features::std_dev(&voiced_f0);
            let mean_energy = features::mean(&voiced_energy);
            let mean_voicing = voicing_sum / voiced_count as f32;

            // Jitter from F0 periods (samples), shimmer from amplitudes.
            let periods: Vec<f32> = voiced_f0
                .iter()
                .filter(|f| **f > 0.0)
                .map(|f| self.sample_rate as f32 / f)
                .collect();
            let jitter = features::relative_perturbation(&periods);
            let shimmer = features::relative_perturbation(&voiced_amp);

            // Auditability deltas vs. the crate's running baseline.
            let energy_vs_baseline = self.energy_baseline.delta_vs_mean(mean_energy);
            let f0_vs_baseline = self.f0_baseline.delta_vs_mean(mean_f0);
            self.energy_baseline.update(mean_energy);
            self.f0_baseline.update(mean_f0);

            let features_payload = AcousticFeatures {
                mean_f0_hz: mean_f0,
                f0_std_hz: f0_std,
                mean_energy,
                jitter,
                shimmer,
                voiced_fraction,
                mean_voicing,
                energy_vs_baseline,
                f0_vs_baseline,
            };

            // value = normalized raw Arousal scalar (engine computes deviation).
            let arousal = self.mapping.arousal(mean_energy, f0_std);
            // confidence = voicing strength × how much of the window was voiced.
            let confidence = (mean_voicing * voiced_fraction).clamp(0.0, 1.0);

            let reading = SignalReading {
                value: arousal,
                axis: Axis::Arousal,
                confidence,
                timestamp_ms: window.timestamp_ms,
            };

            out.push(ExplainedReading {
                reading,
                features: features_payload,
            });
        }

        out
    }
}

impl Signal for EgemapsSignal {
    fn extract(&mut self, window: &AudioWindow) -> Vec<SignalReading> {
        self.extract_explained(window)
            .into_iter()
            .map(|e| e.reading)
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pgso_core::{AudioWindow, Axis, Signal};
    use std::f32::consts::PI;

    fn sine_window(freq: f32, amp: f32, len: usize, sr: u32, ts: u64) -> AudioWindow {
        let samples = (0..len)
            .map(|i| amp * (2.0 * PI * freq * i as f32 / sr as f32).sin())
            .collect();
        AudioWindow {
            samples,
            sample_rate: sr,
            timestamp_ms: ts,
        }
    }

    #[test]
    fn test_signal_trait_impl() {
        // Accepted where the core expects a Signal.
        fn takes_signal<S: Signal>(_s: &S) {}
        let sig = EgemapsSignal::new(16000);
        takes_signal(&sig);
    }

    #[test]
    fn test_reading_shape() {
        let mut sig = EgemapsSignal::new(16000);
        // 1s of voiced 180 Hz tone
        let window = sine_window(180.0, 0.4, 16000, 16000, 0);
        let readings = sig.extract(&window);
        assert!(!readings.is_empty(), "voiced tone should yield readings");
        for r in &readings {
            assert!(
                (0.0..=1.0).contains(&r.value),
                "value out of range: {}",
                r.value
            );
            assert!(
                (0.0..=1.0).contains(&r.confidence),
                "confidence out of range"
            );
            assert!(matches!(r.axis, Axis::Arousal | Axis::Valence));
        }
    }

    #[test]
    fn test_vad_skips_silence() {
        let mut sig = EgemapsSignal::new(16000);
        let silence = AudioWindow {
            samples: vec![0.0; 16000],
            sample_rate: 16000,
            timestamp_ms: 0,
        };
        assert!(
            sig.extract(&silence).is_empty(),
            "silence must yield no readings (G4)"
        );
    }

    #[test]
    fn test_audit_exposes_llds() {
        let mut sig = EgemapsSignal::new(16000);
        let window = sine_window(180.0, 0.4, 16000, 16000, 0);
        let explained = sig.extract_explained(&window);
        assert!(!explained.is_empty());
        let features = &explained[0].features;
        // Pitch of a 180 Hz tone should be recovered to within tolerance.
        assert!(
            (features.mean_f0_hz - 180.0).abs() < 15.0,
            "mean_f0 was {}",
            features.mean_f0_hz
        );
        assert!(features.mean_energy > 0.0);
        assert!((0.0..=1.0).contains(&features.voiced_fraction));
    }

    #[test]
    fn test_confidence_low_when_mostly_unvoiced() {
        let mut sig = EgemapsSignal::new(16000);
        // Very quiet noise-free near-silence: a tiny tone barely above floor
        let window = sine_window(180.0, 0.002, 16000, 16000, 0);
        let readings = sig.extract(&window);
        // Either no readings, or low confidence (abstention-friendly)
        if let Some(r) = readings.first() {
            assert!(r.confidence < 0.5, "near-silence should be low confidence");
        }
    }
}
