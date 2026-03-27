#!/usr/bin/env python3
"""
Regression check: confronta i risultati di un eval run con la baseline salvata.

Fallisce (exit code 1) se qualsiasi metrica scende di più di `threshold`
rispetto alla baseline. Usato nel workflow evals_nightly.yml.

Uso:
    python tests/compare_baselines.py eval_results.json tests/evals/baselines/latest.json
    python tests/compare_baselines.py eval_results.json tests/evals/baselines/latest.json --threshold 0.05

La baseline con tutti 0.0 (placeholder iniziale) viene saltata — non ha senso
confrontare contro una baseline non ancora popolata.
"""

import json
import sys
import argparse
from pathlib import Path


SKIP_KEYS = {"_meta"}
PLACEHOLDER_THRESHOLD = 0.01  # Se tutti i valori baseline sono < soglia, è un placeholder


def _is_placeholder(baseline: dict) -> bool:
    """Rileva se la baseline è ancora il placeholder iniziale (tutti 0.0)."""
    numeric_vals = []
    for k, v in baseline.items():
        if k in SKIP_KEYS:
            continue
        if isinstance(v, (int, float)):
            numeric_vals.append(v)
        elif isinstance(v, dict):
            numeric_vals.extend(
                vv for vv in v.values() if isinstance(vv, (int, float))
            )
    return all(abs(v) < PLACEHOLDER_THRESHOLD for v in numeric_vals) if numeric_vals else True


def _flatten(d: dict, prefix: str = "") -> dict:
    """Appiattisce un dict annidato in chiavi dot-separated."""
    out = {}
    for k, v in d.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, full_key))
        elif isinstance(v, (int, float)):
            out[full_key] = v
    return out


def check_regression(results_path: str, baseline_path: str, threshold: float = 0.05) -> int:
    """
    Restituisce 0 se nessuna regressione, 1 altrimenti.
    Stampa un report dettagliato su stdout.
    """
    results_file = Path(results_path)
    baseline_file = Path(baseline_path)

    if not results_file.exists():
        print(f"ERROR: results file not found: {results_path}")
        return 1

    if not baseline_file.exists():
        print(
            "INFO: Baseline file not found (first run). Skipping regression check.\n"
            "The workflow will auto-commit the current results as the initial baseline."
        )
        return 0

    results = json.loads(results_file.read_text())
    baseline = json.loads(baseline_file.read_text())

    if _is_placeholder(baseline):
        print(
            "INFO: Baseline is still the placeholder (all zeros). Skipping regression check.\n"
            "The workflow will auto-commit the current results as the initial baseline."
        )
        return 0

    flat_baseline = _flatten({k: v for k, v in baseline.items() if k not in SKIP_KEYS})
    flat_results = _flatten({k: v for k, v in results.items() if k not in SKIP_KEYS})

    regressions = []
    improvements = []
    missing = []

    for metric, base_score in flat_baseline.items():
        if metric not in flat_results:
            missing.append(metric)
            continue
        current = flat_results[metric]
        drop = base_score - current
        if drop > threshold:
            regressions.append({
                "metric": metric,
                "baseline": base_score,
                "current": current,
                "drop": drop,
            })
        elif current > base_score + 0.01:
            improvements.append({
                "metric": metric,
                "baseline": base_score,
                "current": current,
                "gain": current - base_score,
            })

    print(f"\n{'='*60}")
    print(f"EVAL REGRESSION CHECK (threshold={threshold:.0%})")
    print(f"{'='*60}")

    if improvements:
        print(f"\n✓ IMPROVEMENTS ({len(improvements)}):")
        for imp in improvements:
            print(f"  {imp['metric']}: {imp['baseline']:.3f} → {imp['current']:.3f} (+{imp['gain']:.3f})")

    if missing:
        print(f"\n⚠ MISSING in results ({len(missing)}):")
        for m in missing:
            print(f"  {m}")

    if regressions:
        print(f"\n✗ REGRESSIONS DETECTED ({len(regressions)}):")
        for reg in regressions:
            print(
                f"  {reg['metric']}: baseline={reg['baseline']:.3f}, "
                f"current={reg['current']:.3f}, drop={reg['drop']:.3f} > {threshold:.0%}"
            )
        print(f"\nFAILED: {len(regressions)} regression(s) exceed threshold {threshold:.0%}")
        return 1

    print(f"\n✓ No regressions detected. All metrics within {threshold:.0%} of baseline.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Compare eval results against baseline.")
    parser.add_argument("results", help="Path to eval_results.json from pytest-json-report")
    parser.add_argument("baseline", help="Path to baseline JSON (tests/evals/baselines/latest.json)")
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="Max allowed drop from baseline per metric (default: 0.05 = 5%%)")
    args = parser.parse_args()

    exit_code = check_regression(args.results, args.baseline, args.threshold)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
