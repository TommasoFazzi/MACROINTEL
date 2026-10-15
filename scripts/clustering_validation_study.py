#!/usr/bin/env python3
"""
Offline clustering validation study (local only, never run in prod).

Compares the k-means champion and the HDBSCAN+UMAP challenger on a frozen
snapshot exported by scripts/export_clustering_snapshot.py, through the same
functions prod runs, against a blind hand-labelled set of storyline pairs.

    # 1. labelling file (blind) + key, from the snapshot
    python scripts/clustering_validation_study.py sample-pairs --snapshot artifacts/clustering_snapshot_<date>
    # 2. the study (stability-only until labels exist)
    python scripts/clustering_validation_study.py run --snapshot artifacts/clustering_snapshot_<date> \
        [--labels data/clustering_reference/labels_<date>.csv --key data/clustering_reference/pairs_key_<date>.csv]
    # 3. real-data sample for the DBCV reference check (task 3.3b)
    python scripts/clustering_validation_study.py dbcv-sample --snapshot ... --out dbcv_sample.npz

Spec reference: openspec/changes/clustering-offline-validation-study/
design.md § D2-D10 and specs/clustering-validation-study/spec.md.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import platform
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import numpy as np  # noqa: E402
from sklearn.metrics import adjusted_rand_score, silhouette_score  # noqa: E402

from scripts.compute_communities import _coherence_med  # noqa: E402
from scripts.export_clustering_snapshot import inertia_from_labels  # noqa: E402
from scripts.theme_clustering import (  # noqa: E402
    _hdbscan_labels,
    _run_kmeans_embedding_and_score,
    _umap_reduce,
)
from src.nlp import clustering_validation as cv  # noqa: E402
from src.nlp.config import load_clustering_config  # noqa: E402

# Grids and seeds (design.md D3/D4). Fixed before any result is seen.
KMEANS_GRID = (12, 15, 18, 21, 24)
HDBSCAN_GRID = (5, 10, 15, 25)            # min_samples = min_cluster_size
SEEDS = (42, 43, 44, 45, 46)              # 42 = prod random_state for k-means and UMAP
N_SUBSAMPLES = 20
SUBSAMPLE_FRACTION = 0.8
SUBSAMPLE_SEED = 2026
SAMPLER_SEED = 1014                        # pair sampler (D6)
ORDER_SEED = 1015                          # row order / repeats (D6)
BOOT_SEED = 7                              # shared paired resamples (D6)
DEFAULT_HDBSCAN_MCS = 5                    # prod default, used by S-C/S-N and as fallback view
PINNED = ("scikit-learn", "umap-learn", "numpy")
PALETTE_SIZE = 15                          # graph colours the 15 largest themes (D8)
KAPPA_MIN, AGREEMENT_MIN = 0.6, 0.85
D9_SIL_TOL, D9_INERTIA_REL_TOL = 0.005, 0.001
DEFAULT_OUT = project_root / "artifacts" / "clustering_study"


# ---------------------------------------------------------------------------
# Environment and snapshot
# ---------------------------------------------------------------------------

def check_versions(requirements: Path = project_root / "requirements-prod.txt") -> dict:
    """Exit non-zero, before any compute, if the pinned libraries differ from prod (D9)."""
    pins = {}
    for line in requirements.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            pins[name.strip().lower()] = ver.strip()
    mismatches = [
        f"{pkg}: installed {version(pkg)}, prod pins {pins.get(pkg)}"
        for pkg in PINNED if pins.get(pkg) != version(pkg)
    ]
    if mismatches:
        raise SystemExit("Version mismatch vs requirements-prod.txt — " + "; ".join(mismatches))
    found = {pkg: version(pkg) for pkg in PINNED + ("scipy", "numba")}
    found["python"] = platform.python_version()
    return found


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class Snapshot:
    path: Path
    manifest: dict
    ids: list                    # storylines with current_embedding, snapshot (id) order
    current_by_id: dict
    summary_by_id: dict
    rows: dict                   # id -> storylines.jsonl row
    louvain: dict                # id -> live community_id (NULLs dropped)
    themes: list

    def X(self, ids=None) -> np.ndarray:
        return np.stack([self.current_by_id[i] for i in (self.ids if ids is None else ids)])


def load_snapshot(path: Path) -> Snapshot:
    manifest = json.loads((path / "manifest.json").read_text())
    for name, digest in manifest["file_sha256"].items():
        if _sha256(path / name) != digest:
            raise SystemExit(f"{name}: SHA-256 does not match manifest.json")
    snap = np.load(path / "snapshot.npz")
    all_ids = [int(i) for i in snap["ids"]]
    has_cur, has_sum = snap["has_current_embedding"], snap["has_summary_vector"]
    rows = {}
    with open(path / "storylines.jsonl") as f:
        for line in f:
            r = json.loads(line)
            rows[r["id"]] = r
    themes = [json.loads(line) for line in open(path / "themes.jsonl")]
    return Snapshot(
        path=path,
        manifest=manifest,
        ids=[i for i, ok in zip(all_ids, has_cur) if ok],
        current_by_id={i: snap["current_embedding"][k] for k, i in enumerate(all_ids) if has_cur[k]},
        summary_by_id={i: snap["summary_vector"][k] for k, i in enumerate(all_ids) if has_sum[k]},
        rows=rows,
        louvain={i: int(c) for i, c in zip(all_ids, snap["community_id"]) if not np.isnan(c)},
        themes=themes,
    )


def reference_check(snap: Snapshot) -> dict:
    """D9: blocking check on metric numerics (prod labels), informational refit."""
    ref = snap.manifest["reference_fit"]
    X, labels = snap.X(), np.asarray(ref["labels"])
    sil = float(silhouette_score(X, labels, metric="cosine"))
    inertia = inertia_from_labels(X, labels)
    d_sil = abs(sil - ref["silhouette"])
    d_inertia = abs(inertia - ref["inertia"]) / ref["inertia"]
    if d_sil > D9_SIL_TOL or d_inertia > D9_INERTIA_REL_TOL:
        raise SystemExit(
            f"Local numerics differ from prod: silhouette {sil:.6f} vs {ref['silhouette']:.6f}, "
            f"inertia {inertia:.4f} vs {ref['inertia']:.4f}"
        )
    partition, score = _run_kmeans_embedding_and_score(
        None, snap.ids, ref["k"], random_state=ref["random_state"],
        embedding_cache=(snap.current_by_id, snap.summary_by_id),
    )
    return {
        "silhouette_delta_on_prod_labels": d_sil,
        "inertia_rel_delta_on_prod_labels": d_inertia,
        "refit_ari_vs_prod_labels": float(adjusted_rand_score(labels, [partition[i] for i in snap.ids])),
        "refit_silhouette_delta": abs(score["silhouette"] - ref["silhouette"]),
    }


def umap_base_config():
    cfg = load_clustering_config().theme_clustering.hdbscan_shadow.umap
    if not cfg.enabled:
        raise SystemExit("hdbscan_shadow.umap.enabled is false in config — the study compares HDBSCAN+UMAP")
    return cfg


def _umap(snap: Snapshot, ids, seed: int) -> np.ndarray:
    X, used = _umap_reduce(snap.X(ids), umap_base_config().model_copy(update={"random_state": seed}))
    if not used:
        raise SystemExit("UMAP did not run (umap-learn missing or failed) — aborting")
    return X


def _hdbscan_partition(ids, X, mcs: int) -> dict:
    return {i: int(lab) for i, lab in zip(ids, _hdbscan_labels(X, mcs, mcs))}


def _kmeans_partition(snap: Snapshot, ids, k: int, seed: int) -> tuple[dict, dict]:
    return _run_kmeans_embedding_and_score(
        None, ids, k, random_state=seed, embedding_cache=(snap.current_by_id, snap.summary_by_id))


# ---------------------------------------------------------------------------
# sample-pairs
# ---------------------------------------------------------------------------

def cmd_sample_pairs(args) -> int:
    check_versions()
    snap = load_snapshot(args.snapshot)
    kmeans, _ = _kmeans_partition(snap, snap.ids, 18, SEEDS[0])
    hdbscan = _hdbscan_partition(snap.ids, _umap(snap, snap.ids, SEEDS[0]), DEFAULT_HDBSCAN_MCS)
    entities = {i: snap.rows[i]["key_entities"] for i in snap.ids}
    pairs = cv.sample_pairs(kmeans, hdbscan, entities, snap.summary_by_id, seed=SAMPLER_SEED)
    rows = cv.labelling_order(pairs, seed=ORDER_SEED)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    labels_path, key_path = out / f"labels_{stamp}.csv", out / f"pairs_key_{stamp}.csv"
    if labels_path.exists():
        raise SystemExit(f"{labels_path} exists — refusing to overwrite labels")

    def side(sid):
        r = snap.rows[sid]
        return r["title"], r.get("summary") or ""

    with open(labels_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["row_id", "left_title", "left_summary", "right_title", "right_summary", "label"])
        for r in rows:
            left, right = (r["b"], r["a"]) if r["swap"] else (r["a"], r["b"])
            w.writerow([r["row_id"], *side(left), *side(right), ""])
    with open(key_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["row_id", "a", "b", "stratum", "direction", "repeat_of", "swap"])
        for r in rows:
            w.writerow([r["row_id"], r["a"], r["b"], r["stratum"], r["direction"] or "",
                        "" if r["repeat_of"] is None else r["repeat_of"], int(r["swap"])])
    (out / f"labels_manifest_{stamp}.json").write_text(json.dumps({
        "snapshot_dir": str(args.snapshot),
        "snapshot_manifest_sha256": _sha256(args.snapshot / "manifest.json"),
        "snapshot_npz_sha256": snap.manifest["file_sha256"]["snapshot.npz"],
        "sampler_seed": SAMPLER_SEED,
        "order_seed": ORDER_SEED,
        "stratum_sizes": cv.STRATUM_SIZES,
        "n_rows": len(rows),
    }, indent=2))
    print(f"Wrote {labels_path} ({len(rows)} rows) and {key_path}. Do not open the key while labelling.")
    return 0


def read_labels(labels_path: Path, key_path: Path):
    with open(key_path) as f:
        key_rows = [{
            "row_id": int(r["row_id"]), "a": int(r["a"]), "b": int(r["b"]), "stratum": r["stratum"],
            "repeat_of": int(r["repeat_of"]) if r["repeat_of"] else None,
        } for r in csv.DictReader(f)]
    with open(labels_path) as f:
        labels = {int(r["row_id"]): r["label"].strip().lower() for r in csv.DictReader(f)}
    return cv.load_labelled_pairs(key_rows, labels)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@dataclass
class Config:
    key: str
    algorithm: str
    params: dict
    full: dict                       # partition on the full snapshot, SEEDS[0]; noise = -1
    seed_runs: list
    data_runs: list
    score: dict | None = None        # k-means score dict (full run)
    X_umap: np.ndarray | None = None # HDBSCAN: UMAP space of the full run


def subsample_ids(ids: list) -> list[list]:
    rng = np.random.default_rng(SUBSAMPLE_SEED)
    n = int(round(len(ids) * SUBSAMPLE_FRACTION))
    return [sorted(int(x) for x in rng.choice(ids, size=n, replace=False)) for _ in range(N_SUBSAMPLES)]


def fit_kmeans(snap: Snapshot, subs) -> list[Config]:
    configs = []
    for k in KMEANS_GRID:
        seed_runs, full, score = [], None, None
        for s in SEEDS:
            part, sc = _kmeans_partition(snap, snap.ids, k, s)
            seed_runs.append(part)
            if s == SEEDS[0]:
                full, score = part, sc
        data_runs = [_kmeans_partition(snap, sub, k, SEEDS[0])[0] for sub in subs]
        configs.append(Config(f"kmeans:k={k}", "kmeans", {"k": k}, full, seed_runs, data_runs, score=score))
        print(f"  k-means k={k}: done")
    return configs


def fit_hdbscan(snap: Snapshot, subs) -> list[Config]:
    """One UMAP per seed / subsample, reused across every min_cluster_size (D2)."""
    by_mcs = {m: Config(f"hdbscan_umap:mcs={m}", "hdbscan_umap", {"min_cluster_size": m}, {}, [], [])
              for m in HDBSCAN_GRID}
    for s in SEEDS:
        X = _umap(snap, snap.ids, s)
        for m, c in by_mcs.items():
            part = _hdbscan_partition(snap.ids, X, m)
            c.seed_runs.append(part)
            if s == SEEDS[0]:
                c.full, c.X_umap = part, X
    for sub in subs:
        X = _umap(snap, sub, SEEDS[0])
        for m, c in by_mcs.items():
            c.data_runs.append(_hdbscan_partition(sub, X, m))
    print("  HDBSCAN+UMAP: done")
    return list(by_mcs.values())


def _themed(partition: dict) -> dict:
    return {i: lab for i, lab in partition.items() if lab != cv.NOISE}


def _sizes(partition: dict) -> list[int]:
    return sorted(Counter(_themed(partition).values()).values(), reverse=True)


def palette_overflow(partition: dict, n_total: int) -> float:
    return sum(_sizes(partition)[PALETTE_SIZE:]) / n_total if n_total else 0.0


def build_result(c: Config, snap: Snapshot) -> cv.ConfigResult:
    coherence, _ = _coherence_med(_themed(c.full), snap.summary_by_id)
    common = dict(
        algorithm=c.algorithm, params=c.params,
        n_clusters=len(set(_themed(c.full).values())), size_distribution=_sizes(c.full),
        coherence_med=coherence, labelled=[], eligible=False,
    )
    if c.algorithm == "kmeans":
        return cv.ConfigResult(
            **common,
            silhouette_cosine=c.score.get("silhouette"),
            inertia=c.score.get("inertia"),
            seed_stability=cv.pairwise_ari_on_intersection(c.seed_runs),
            data_stability=cv.pairwise_ari_on_intersection(c.data_runs),
        )
    labels = np.array([c.full[i] for i in snap.ids])
    return cv.ConfigResult(
        **common,
        dbcv=cv.dbcv(c.X_umap, labels),
        noise_ratio=cv.noise_ratio(c.full),
        seed_stability=cv.pairwise_ari_hdbscan(c.seed_runs),
        data_stability=cv.pairwise_ari_hdbscan(c.data_runs),
    )


def candidate(c: Config, r: cv.ConfigResult, macro_f1, boot) -> cv.Candidate:
    hd = c.algorithm == "hdbscan_umap"
    return cv.Candidate(
        key=c.key, algorithm=c.algorithm, params=c.params,
        seed_ari=r.seed_stability.mean_ari, data_ari=r.data_stability.mean_ari,
        macro_f1=macro_f1, boot_macro_f1=boot,
        noise_full=r.noise_ratio if hd else None,
        noise_p90=r.data_stability.noise_ratio_p90 if hd else None,
    )


def cmd_run(args) -> int:
    versions = check_versions()
    snap = load_snapshot(args.snapshot)
    ref_check = reference_check(snap)
    print(f"Reference fit check passed: {ref_check}")

    subs = subsample_ids(snap.ids)
    configs = fit_kmeans(snap, subs) + fit_hdbscan(snap, subs)
    results = {c.key: build_result(c, snap) for c in configs}
    louvain_result = cv.ConfigResult(
        algorithm="louvain_live", params={}, n_clusters=len(set(snap.louvain.values())),
        size_distribution=_sizes(snap.louvain),
        coherence_med=_coherence_med(snap.louvain, snap.summary_by_id)[0],
        seed_stability=None, data_stability=None, labelled=[], eligible=False,
    )

    notes, kappa, agreement, deltas, selected, verdict = [], None, None, [], {}, "inconclusive"
    verdict_original, selected_original = None, {}
    nan_boot = np.full(1, np.nan)
    cands = []
    if args.labels and args.key:
        pairs, first, second = read_labels(args.labels, args.key)
        kappa, agreement = cv.rater_agreement(first, second)
        if kappa < KAPPA_MIN or agreement < AGREEMENT_MIN:
            notes.append(f"reliability gate failed: kappa={kappa:.3f}, agreement={agreement:.3f}; "
                         "labelled metrics not computed — revise the guideline and relabel")
        else:
            resamples = cv.bootstrap_indices(pairs, n_boot=cv.N_BOOT, seed=BOOT_SEED)
            for c in configs:
                pred = cv.predict_same(c.full, pairs)
                boot = cv.bootstrap_f1(pred, pairs, resamples)
                results[c.key].labelled = cv.label_metrics(pred, pairs, boot)
                macro = cv.point_metrics(pred, pairs)["macro"]["f1"]
                cands.append(candidate(c, results[c.key], macro, boot["macro"]))
            pred = cv.predict_same(snap.louvain, pairs)
            louvain_result.labelled = cv.label_metrics(pred, pairs, cv.bootstrap_f1(pred, pairs, resamples))
    else:
        notes.append("no labels yet: stability and eligibility only, no verdict")
    if not cands:
        cands = [candidate(c, results[c.key], None, nan_boot) for c in configs]
        for algo in ("kmeans", "hdbscan_umap"):
            cv.apply_eligibility([x for x in cands if x.algorithm == algo])
    else:
        # Original rule first, on copies, as a sensitivity check (design.md D7 deviation)
        original = cv.decide([copy.copy(x) for x in cands], relative_margin=True)
        verdict_original = original.verdict
        selected_original = {algo: cand.params for algo, cand in original.selected.items()}
        decision = cv.decide(cands)
        verdict, deltas = decision.verdict, decision.deltas
        selected = {algo: cand.params for algo, cand in decision.selected.items()}
    for x in cands:
        results[x.key].eligible, results[x.key].ineligible_reasons = x.eligible, list(x.reasons)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    out = (args.out or DEFAULT_OUT) / run_id
    out.mkdir(parents=True, exist_ok=True)
    report = cv.StudyReport(
        run_id=run_id,
        created_at=datetime.now(timezone.utc),
        manifest=cv.SnapshotManifest.model_validate(snap.manifest),
        library_versions=versions,
        constants=cv.D7_CONSTANTS,
        label_kappa=kappa,
        label_raw_agreement=agreement,
        results=list(results.values()) + [louvain_result],
        deltas=deltas,
        selected=selected,
        verdict=verdict,
        reference_fit_check=ref_check,
        palette_overflow={c.key: palette_overflow(c.full, len(snap.ids)) for c in configs},
        notes=notes,
        verdict_original_rule=verdict_original,
        selected_original_rule=selected_original,
    )
    (out / "report.json").write_text(report.model_dump_json(indent=2))

    from scripts import clustering_study_figures as figs
    view = {
        "kmeans": next(c for c in configs if c.params.get("k") == selected.get("kmeans", {}).get("k", 18)),
        "hdbscan_umap": next(c for c in configs if c.params.get("min_cluster_size")
                             == selected.get("hdbscan_umap", {}).get("min_cluster_size", DEFAULT_HDBSCAN_MCS)),
    }
    figs.render_all(out, snap, configs, results, louvain_result, report, view)
    print(f"Report and figures in {out} — verdict: {verdict}")
    for n in notes:
        print(f"  note: {n}")
    return 0


# ---------------------------------------------------------------------------
# dbcv-sample (task 3.3b)
# ---------------------------------------------------------------------------

def cmd_dbcv_sample(args) -> int:
    check_versions()
    snap = load_snapshot(args.snapshot)
    X = _umap(snap, snap.ids, SEEDS[0])
    labels = _hdbscan_labels(X, DEFAULT_HDBSCAN_MCS, DEFAULT_HDBSCAN_MCS)
    idx = np.sort(np.random.default_rng(SUBSAMPLE_SEED).choice(len(snap.ids), size=500, replace=False))
    np.savez(args.out, X=X[idx].astype(np.float64), labels=labels[idx])
    print(f"Wrote {args.out}: 500 points, ours dbcv = {cv.dbcv(X[idx], labels[idx])}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline clustering validation study")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample-pairs", help="write the blind labelling CSV and its key")
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--out", type=Path, default=project_root / "data" / "clustering_reference")
    p.set_defaults(func=cmd_sample_pairs)
    p = sub.add_parser("run", help="run the study (stability-only without labels)")
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--labels", type=Path)
    p.add_argument("--key", type=Path)
    p.add_argument("--out", type=Path)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("dbcv-sample", help="500-point UMAP sample for the DBCV reference check")
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_dbcv_sample)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
