//! Enforces the licensing + purity constraints (REQ-3.2, REQ-3.9, INV-1, R9).
//!
//! These tests assert on dependency *declarations*, not raw substrings: a naive
//! `manifest.contains("ort")` would false-positive on `features = ["html_reports"]`
//! (criterion) or any crate whose name embeds "ort". Instead we extract the set of
//! crate names declared under the various `[*dependencies]` tables and assert the
//! forbidden ML/proprietary crates are absent by exact (case-insensitive) name.

use std::collections::BTreeSet;
use std::path::PathBuf;

/// Crate names that would violate R9 / INV-1 if they appeared as a dependency.
/// Matched case-insensitively, by exact dependency name.
const FORBIDDEN: &[&str] = &[
    "ort",  // ONNX Runtime bindings
    "onnx", // any ONNX crate
    "onnxruntime",
    "tract", // ONNX/TF inference engine
    "tract-onnx",
    "opensmile", // proprietary, non-commercial — must never bind
    "tch",       // libtorch bindings
    "tch-rs",
    "torch-sys",
    "libtorch-sys",
    "candle-core", // neural runtime
    "burn",        // neural runtime
];

/// Audio-decoding / DSP-host crates the *core* must never pull in (INV-1).
/// (The egemaps crate itself does pure-Rust DSP with no such dependency either.)
const FORBIDDEN_AUDIO: &[&str] = &["hound", "symphonia", "rodio", "cpal", "dasp"];

/// Read a manifest relative to this crate's directory.
fn read_manifest(rel: &str) -> String {
    let mut path = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    path.push(rel);
    std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("read manifest {}: {e}", path.display()))
}

/// Returns true if a TOML section header names a dependency table, i.e.
/// `[dependencies]`, `[dev-dependencies]`, `[build-dependencies]`, or a
/// target-scoped variant like `[target.'cfg(...)'.dependencies]`.
fn is_dependency_section(header: &str) -> bool {
    header.ends_with("dependencies")
}

/// Extract the set of dependency *names* declared across all dependency tables
/// of a Cargo manifest. Handles both:
///   `name = "1.0"` / `name = { version = "1.0" }`   (key = name)
///   `[dependencies.name]`                            (dotted table = name)
fn dependency_names(manifest: &str) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    let mut in_dep_section = false;

    for raw in manifest.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }

        // Section header?
        if let Some(inner) = line.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
            // Strip an array-of-tables extra bracket, e.g. `[[bench]]` -> `bench`.
            let inner = inner.trim_start_matches('[').trim_end_matches(']');

            // Dotted dependency table: `dependencies.foo` or
            // `target.'cfg(unix)'.dependencies.foo` declares dependency `foo`.
            if let Some(idx) = inner.find("dependencies.") {
                let after = &inner[idx + "dependencies.".len()..];
                // Take the first dotted segment as the dep name.
                let name = after.split('.').next().unwrap_or("").trim();
                if !name.is_empty() {
                    names.insert(name.trim_matches(['"', '\'']).to_lowercase());
                }
                in_dep_section = false; // this header is the dep itself, not a table of deps
            } else {
                in_dep_section = is_dependency_section(inner);
            }
            continue;
        }

        // Inside a `[*dependencies]` table: the key before the first '=' is a dep name.
        if in_dep_section {
            if let Some((key, _)) = line.split_once('=') {
                let name = key.trim().trim_matches(['"', '\'']);
                if !name.is_empty() {
                    names.insert(name.to_lowercase());
                }
            }
        }
    }

    names
}

#[test]
fn test_no_proprietary_or_ml_dep() {
    let manifest = read_manifest("Cargo.toml");
    let deps = dependency_names(&manifest);

    // Sanity: our parser actually found the deps we expect (so absence below is
    // meaningful, not a parser that silently sees nothing).
    assert!(
        deps.contains("pgso-core"),
        "parser failed to find pgso-core; found: {deps:?}"
    );
    assert!(
        deps.contains("criterion"),
        "parser failed to find criterion dev-dep; found: {deps:?}"
    );

    for bad in FORBIDDEN {
        assert!(
            !deps.contains(&bad.to_lowercase()),
            "pgso-signal-egemaps must NOT depend on `{bad}` (R9, licensing/ML purity); deps were {deps:?}"
        );
    }
    for bad in FORBIDDEN_AUDIO {
        assert!(
            !deps.contains(&bad.to_lowercase()),
            "pgso-signal-egemaps must NOT pull an audio-decoding crate `{bad}` (pure-Rust DSP only); deps were {deps:?}"
        );
    }
}

#[test]
fn test_egemaps_substring_false_positive_guard() {
    // Regression guard: a naive substring check would flag this crate because
    // `features = ["html_reports"]` contains "ort". Prove the manifest indeed
    // contains that substring, yet our name-based check (above) passes.
    let manifest = read_manifest("Cargo.toml").to_lowercase();
    assert!(
        manifest.contains("html_reports"),
        "expected criterion html_reports feature in manifest"
    );
    // The substring "ort" IS present (inside html_repORTs) — naive checks fail here.
    assert!(
        manifest.contains("ort"),
        "the fragile substring is present, as expected"
    );
    // But no dependency is NAMED any forbidden crate:
    let deps = dependency_names(&manifest);
    assert!(!deps.iter().any(|d| FORBIDDEN.contains(&d.as_str())));
}

#[test]
fn test_core_has_no_audio_or_ml_dep() {
    let core = read_manifest("../pgso-core/Cargo.toml");
    let deps = dependency_names(&core);

    // Sanity: the parser sees pgso-core's known deps (thiserror runtime, proptest dev).
    assert!(
        deps.contains("thiserror"),
        "parser failed to find pgso-core's thiserror dep; found: {deps:?}"
    );

    for bad in FORBIDDEN {
        assert!(
            !deps.contains(&bad.to_lowercase()),
            "pgso-core must stay ML-free (INV-1); found forbidden dep `{bad}` in {deps:?}"
        );
    }
    for bad in FORBIDDEN_AUDIO {
        assert!(
            !deps.contains(&bad.to_lowercase()),
            "pgso-core must stay audio-free (INV-1); found `{bad}` in {deps:?}"
        );
    }
}
