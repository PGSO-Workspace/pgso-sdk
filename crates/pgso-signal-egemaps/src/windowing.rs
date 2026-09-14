//! Sliding-window segmentation and intra-window framing.

/// Split a sample buffer into overlapping windows of `window_size`,
/// advancing by `hop_size`. Returns slices of exactly `window_size`.
pub fn sliding_windows(samples: &[f32], window_size: usize, hop_size: usize) -> Vec<&[f32]> {
    let mut out = Vec::new();
    if window_size == 0 || hop_size == 0 {
        return out;
    }
    let mut start = 0;
    while start <= samples.len() && window_size <= samples.len() - start {
        out.push(&samples[start..start + window_size]);
        let Some(next) = start.checked_add(hop_size) else {
            break;
        };
        start = next;
    }
    out
}

/// Split a window into analysis frames of `frame_size`, advancing by `hop_size`.
pub fn frames(window: &[f32], frame_size: usize, hop_size: usize) -> Vec<&[f32]> {
    sliding_windows(window, frame_size, hop_size)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_window_count_800_400() {
        // 2s at 16kHz = 32000 samples. Window=12800 (800ms), hop=6400 (400ms).
        // Windows start at 0, 6400, 12800, 19200 → 4 windows.
        let samples = vec![0.0f32; 32000];
        let windows = sliding_windows(&samples, 12800, 6400);
        assert_eq!(windows.len(), 4);
        assert_eq!(windows[0].len(), 12800);
    }

    #[test]
    fn test_short_audio_no_window() {
        let samples = vec![0.0f32; 5000];
        assert!(sliding_windows(&samples, 12800, 6400).is_empty());
    }

    #[test]
    fn test_frame_count() {
        // 12800-sample window, frame=1024, hop=256 → (12800-1024)/256 + 1 = 47 frames
        let window = vec![0.0f32; 12800];
        let frames = frames(&window, 1024, 256);
        assert_eq!(frames.len(), 47);
        assert_eq!(frames[0].len(), 1024);
    }
}
