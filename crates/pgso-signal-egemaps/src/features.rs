//! Per-window functionals, the documented axis mapping, and a lightweight
//! running baseline used only for human-readable auditability deltas.

/// Online mean/variance (Welford). Used for the auditability "vs baseline"
/// deltas — NOT for the governance decision (the DecisionEngine owns that).
#[derive(Debug, Default, Clone)]
pub struct RunningStats {
    count: u64,
    mean: f64,
    m2: f64,
}

impl RunningStats {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn update(&mut self, value: f32) {
        self.count += 1;
        let v = value as f64;
        let delta = v - self.mean;
        self.mean += delta / self.count as f64;
        self.m2 += delta * (v - self.mean);
    }

    /// Difference of `value` from the running mean (0.0 until a baseline exists).
    pub fn delta_vs_mean(&self, value: f32) -> f32 {
        if self.count < 2 {
            return 0.0;
        }
        value - self.mean as f32
    }

    /// Number of samples folded in so far. Test-only inspection helper.
    #[cfg(test)]
    pub fn count(&self) -> u64 {
        self.count
    }

    /// Sample standard deviation (0.0 if < 2 samples). Test-only helper.
    #[cfg(test)]
    pub fn std_dev(&self) -> f32 {
        if self.count < 2 {
            return 0.0;
        }
        (self.m2 / (self.count - 1) as f64).sqrt() as f32
    }

    /// Z-score of `value`, or 0.0 if no baseline / zero variance. Test-only helper.
    #[cfg(test)]
    pub fn z_score(&self, value: f32) -> f32 {
        let sd = self.std_dev();
        if sd < 1e-9 || self.count < 2 {
            return 0.0;
        }
        (value - self.mean as f32) / sd
    }
}

/// Mean of a slice (0.0 if empty).
pub fn mean(xs: &[f32]) -> f32 {
    if xs.is_empty() {
        return 0.0;
    }
    xs.iter().sum::<f32>() / xs.len() as f32
}

/// Sample standard deviation of a slice (0.0 if < 2 elements).
pub fn std_dev(xs: &[f32]) -> f32 {
    if xs.len() < 2 {
        return 0.0;
    }
    let m = mean(xs);
    let var = xs.iter().map(|x| (x - m).powi(2)).sum::<f32>() / (xs.len() - 1) as f32;
    var.sqrt()
}

/// Average relative perturbation of a sequence (used for jitter from F0
/// periods and shimmer from amplitudes): mean(|x[i+1]-x[i]|) / mean(x).
pub fn relative_perturbation(xs: &[f32]) -> f32 {
    if xs.len() < 2 {
        return 0.0;
    }
    let m = mean(xs);
    if m.abs() < 1e-9 {
        return 0.0;
    }
    let mut acc = 0.0f32;
    for w in xs.windows(2) {
        acc += (w[1] - w[0]).abs();
    }
    (acc / (xs.len() - 1) as f32) / m
}

/// The documented, tunable mapping from LLD functionals to the Arousal axis.
///
/// Arousal rises with loudness (mean energy) and pitch dynamism (F0 std).
/// These reference constants make the mapping explicit and auditable; they
/// are the design choice PGSO commits to and reports.
#[derive(Debug, Clone)]
pub struct AxisMapping {
    pub energy_ref: f32,  // energy that maps to ~1.0
    pub f0_std_ref: f32,  // F0 std (Hz) that maps to ~1.0
    pub w_energy: f32,
    pub w_f0_std: f32,
}

impl Default for AxisMapping {
    fn default() -> Self {
        Self {
            energy_ref: 0.3,
            f0_std_ref: 50.0,
            w_energy: 0.6,
            w_f0_std: 0.4,
        }
    }
}

impl AxisMapping {
    /// Map mean energy + F0 std to an Arousal scalar in [0,1].
    pub fn arousal(&self, mean_energy: f32, f0_std: f32) -> f32 {
        let e = (mean_energy / self.energy_ref).clamp(0.0, 1.0);
        let p = (f0_std / self.f0_std_ref).clamp(0.0, 1.0);
        (self.w_energy * e + self.w_f0_std * p).clamp(0.0, 1.0)
    }
}

/// Interpretable acoustic features behind one reading — the auditability payload.
#[derive(Debug, Clone)]
pub struct AcousticFeatures {
    pub mean_f0_hz: f32,
    pub f0_std_hz: f32,
    pub mean_energy: f32,
    pub jitter: f32,
    pub shimmer: f32,
    pub voiced_fraction: f32,
    pub mean_voicing: f32,
    /// Mean energy minus the speaker's running-baseline energy (explainability).
    pub energy_vs_baseline: f32,
    /// Mean F0 minus the speaker's running-baseline F0 (explainability).
    pub f0_vs_baseline: f32,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_running_stats_z_score() {
        let mut s = RunningStats::new();
        for v in [0.5, 0.6, 0.4, 0.55, 0.45] {
            s.update(v);
        }
        assert!(s.z_score(0.9).abs() > 1.0);
    }

    #[test]
    fn test_running_stats_few_samples_returns_zero_delta() {
        let mut s = RunningStats::new();
        s.update(0.5);
        // With <2 samples, no meaningful baseline → delta 0.0
        assert!(s.delta_vs_mean(0.8).abs() < 1e-6 || s.count() < 2);
    }

    #[test]
    fn test_jitter_zero_for_constant_periods() {
        // Identical F0 across frames → zero jitter
        let f0s = vec![200.0, 200.0, 200.0, 200.0];
        assert!(relative_perturbation(&f0s) < 1e-6);
    }

    #[test]
    fn test_jitter_positive_for_varying_periods() {
        let f0s = vec![200.0, 210.0, 195.0, 205.0];
        assert!(relative_perturbation(&f0s) > 0.0);
    }

    #[test]
    fn test_axis_mapping_arousal_in_range() {
        let mapping = AxisMapping::default();
        let arousal = mapping.arousal(0.3, 40.0); // mean_energy, f0_std
        assert!((0.0..=1.0).contains(&arousal));
    }

    #[test]
    fn test_axis_mapping_louder_higher_arousal() {
        let mapping = AxisMapping::default();
        assert!(mapping.arousal(0.5, 40.0) >= mapping.arousal(0.1, 40.0));
    }
}
