//! Pure-Rust DSP primitives for paralinguistic LLD extraction.

/// Root-mean-square energy of a frame (intensity proxy).
pub fn rms_energy(frame: &[f32]) -> f32 {
    if frame.is_empty() {
        return 0.0;
    }
    let sum_sq: f32 = frame.iter().map(|x| x * x).sum();
    (sum_sq / frame.len() as f32).sqrt()
}

/// Estimate fundamental frequency (F0) via normalized autocorrelation.
///
/// Returns `(f0_hz, voicing_probability)`. Voicing is the normalized
/// autocorrelation peak height in [0,1]; F0 is 0.0 when unvoiced.
///
/// The search is bounded to `[min_f0, max_f0]` Hz, which maps to a lag
/// window `[sr/max_f0, sr/min_f0]` samples (so `f0 = sr / best_lag`).
pub fn estimate_f0(frame: &[f32], sample_rate: u32, min_f0: f32, max_f0: f32) -> (f32, f32) {
    let sr = sample_rate as f32;
    let min_lag = (sr / max_f0).floor().max(1.0) as usize;
    let max_lag = (sr / min_f0).ceil() as usize;
    if frame.len() <= max_lag {
        return (0.0, 0.0);
    }

    // Zero-lag energy (autocorrelation at lag 0).
    let r0: f32 = frame.iter().map(|x| x * x).sum();
    if r0 <= 1e-9 {
        return (0.0, 0.0);
    }

    let mut best_lag = 0usize;
    let mut best_r = 0.0f32;
    for lag in min_lag..=max_lag {
        // Autocorrelation at this lag: sum of frame[i] * frame[i + lag].
        let r: f32 = frame
            .iter()
            .zip(frame[lag..].iter())
            .map(|(a, b)| a * b)
            .sum();
        if r > best_r {
            best_r = r;
            best_lag = lag;
        }
    }

    if best_lag == 0 {
        return (0.0, 0.0);
    }
    let voicing = (best_r / r0).clamp(0.0, 1.0);
    let f0 = sr / best_lag as f32;
    (f0, voicing)
}

/// Peak absolute amplitude of a frame (for shimmer).
pub fn peak_amplitude(frame: &[f32]) -> f32 {
    frame.iter().fold(0.0f32, |acc, x| acc.max(x.abs()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f32::consts::PI;

    /// Generate a pure sine of given frequency, amplitude, length.
    fn sine(freq: f32, amp: f32, len: usize, sr: u32) -> Vec<f32> {
        (0..len).map(|i| amp * (2.0 * PI * freq * i as f32 / sr as f32).sin()).collect()
    }

    #[test]
    fn test_rms_energy_tracks_amplitude() {
        let quiet = sine(200.0, 0.1, 1024, 16000);
        let loud = sine(200.0, 0.5, 1024, 16000);
        assert!(rms_energy(&loud) > rms_energy(&quiet));
        // RMS of a sine with amplitude A is A/sqrt(2)
        let e = rms_energy(&loud);
        assert!((e - 0.5 / 2.0_f32.sqrt()).abs() < 0.02, "rms was {}", e);
    }

    #[test]
    fn test_rms_energy_silence_is_zero() {
        assert!(rms_energy(&vec![0.0; 1024]) < 1e-6);
    }

    #[test]
    fn test_estimate_f0_on_known_tone() {
        // 200 Hz tone at 16kHz → expect F0 ≈ 200 Hz, strongly voiced
        let frame = sine(200.0, 0.5, 1024, 16000);
        let (f0, voicing) = estimate_f0(&frame, 16000, 50.0, 500.0);
        assert!((f0 - 200.0).abs() < 10.0, "f0 was {}", f0);
        assert!(voicing > 0.5, "voicing was {}", voicing);
    }

    #[test]
    fn test_estimate_f0_silence_unvoiced() {
        let frame = vec![0.0; 1024];
        let (_f0, voicing) = estimate_f0(&frame, 16000, 50.0, 500.0);
        assert!(voicing < 0.1, "silence should be unvoiced, voicing was {}", voicing);
    }

    #[test]
    fn test_estimate_f0_higher_tone() {
        // 120 Hz tone → expect F0 ≈ 120 Hz
        let frame = sine(120.0, 0.4, 2048, 16000);
        let (f0, voicing) = estimate_f0(&frame, 16000, 50.0, 500.0);
        assert!((f0 - 120.0).abs() < 8.0, "f0 was {}", f0);
        assert!(voicing > 0.5);
    }
}
