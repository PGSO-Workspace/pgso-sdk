//! Pure per-utterance aggregation of window-level arousal readings.
//!
//! Each utterance yields N window readings `(arousal, confidence)`; the human
//! label is a single scalar, so we collapse to one predicted scalar. The
//! PRE-REGISTERED primary is the confidence-weighted mean; plain mean and
//! median are reported only as sensitivity checks (never for the verdict).

/// The three per-utterance aggregations of window arousal.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Aggregates {
    /// PRE-REGISTERED primary: `sum(arousal*confidence) / sum(confidence)`,
    /// falling back to the plain mean when total confidence is ~0.
    pub weighted_mean: f32,
    /// Sensitivity only: unweighted mean of window arousals.
    pub mean: f32,
    /// Sensitivity only: median of window arousals.
    pub median: f32,
}

/// Aggregate window `(arousal, confidence)` readings into per-utterance scalars.
///
/// Returns `None` when there are no readings — the utterance abstained and must
/// be EXCLUDED from the correlation, never imputed.
#[must_use]
pub fn aggregate(readings: &[(f32, f32)]) -> Option<Aggregates> {
    if readings.is_empty() {
        return None;
    }
    let n = readings.len() as f32;
    let mean = readings.iter().map(|&(a, _)| a).sum::<f32>() / n;

    let total_w: f32 = readings.iter().map(|&(_, c)| c).sum();
    let weighted_mean = if total_w > 1e-9 {
        readings.iter().map(|&(a, c)| a * c).sum::<f32>() / total_w
    } else {
        // All-zero confidence: fall back to the plain mean (never divide by 0).
        mean
    };

    let mut arousals: Vec<f32> = readings.iter().map(|&(a, _)| a).collect();
    arousals.sort_by(|x, y| x.partial_cmp(y).unwrap_or(std::cmp::Ordering::Equal));
    let mid = arousals.len() / 2;
    let median = if arousals.len() % 2 == 1 {
        arousals[mid]
    } else {
        (arousals[mid - 1] + arousals[mid]) / 2.0
    };

    Some(Aggregates {
        weighted_mean,
        mean,
        median,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-6
    }

    #[test]
    fn empty_readings_abstain() {
        assert!(aggregate(&[]).is_none());
    }

    #[test]
    fn single_reading_is_that_value() {
        let a = aggregate(&[(0.42, 0.9)]).expect("one reading");
        assert!(approx(a.weighted_mean, 0.42), "wmean={}", a.weighted_mean);
        assert!(approx(a.mean, 0.42), "mean={}", a.mean);
        assert!(approx(a.median, 0.42), "median={}", a.median);
    }

    #[test]
    fn weighted_mean_weights_by_confidence() {
        // (0.2*1 + 0.8*3) / (1+3) = 2.6/4 = 0.65 ; plain mean = 0.5
        let a = aggregate(&[(0.2, 1.0), (0.8, 3.0)]).expect("two readings");
        assert!(approx(a.weighted_mean, 0.65), "wmean={}", a.weighted_mean);
        assert!(approx(a.mean, 0.5), "mean={}", a.mean);
    }

    #[test]
    fn zero_total_confidence_falls_back_to_mean() {
        let a = aggregate(&[(0.3, 0.0), (0.7, 0.0)]).expect("two readings");
        assert!(approx(a.weighted_mean, 0.5), "wmean={}", a.weighted_mean);
        assert!(approx(a.mean, 0.5), "mean={}", a.mean);
    }

    #[test]
    fn median_odd_count_is_middle_value() {
        // unsorted input; median of {0.1, 0.5, 0.9} = 0.5
        let a = aggregate(&[(0.9, 1.0), (0.1, 1.0), (0.5, 1.0)]).expect("three");
        assert!(approx(a.median, 0.5), "median={}", a.median);
    }

    #[test]
    fn median_even_count_is_mean_of_middle_two() {
        // median of {0.2, 0.4, 0.6, 0.8} = (0.4+0.6)/2 = 0.5
        let a = aggregate(&[(0.2, 1.0), (0.8, 1.0), (0.4, 1.0), (0.6, 1.0)]).expect("four");
        assert!(approx(a.median, 0.5), "median={}", a.median);
    }
}
