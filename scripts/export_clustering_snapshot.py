#!/usr/bin/env python3
"""
Read-only snapshot export for the offline clustering validation study.

Freezes the active storylines (embeddings, summary vectors, live and shadow
community ids), the refit metric history, the active theme centroids, a
level-1 (articles -> storylines) baseline, and one reference k-means fit that
the local study reproduces to check numeric agreement with prod.

Runs inside the prod backend container, piped over stdin so it never has to
be in the image (run from the server's repo checkout):

    # 1. probe: cwd, imports, writable output directory
    docker compose -p app exec -T backend python - --probe < scripts/export_clustering_snapshot.py
    # 2. export
    docker compose -p app exec -T backend python - --git-sha "$(git rev-parse HEAD)" \
        < scripts/export_clustering_snapshot.py
    # 3. copy out
    docker compose -p app cp backend:/app/artifacts/clustering_snapshot_<date> ./artifacts/

Issues SELECT statements only. Spec reference:
openspec/changes/clustering-offline-validation-study/ design.md § D1, D10 and
specs/clustering-validation-study/spec.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Piped over stdin there is no __file__: resolve the repo root from cwd (/app).
_root = Path(__file__).resolve().parent.parent if "__file__" in globals() else Path.cwd()
sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402

REFERENCE_K = 18
REFERENCE_RANDOM_STATE = 42
ACTIVE_STATUSES = ("emerging", "active", "stabilized")

STORYLINES_SQL = """
    SELECT id, title, LEFT(summary, 600), key_entities, article_count,
           narrative_status, created_at, current_embedding, summary_vector,
           community_id, community_id_kmeans_shadow, community_id_shadow
    FROM storylines
    WHERE narrative_status IN %s
    ORDER BY id
"""

# Scalar fields of every shadow_partitions entry, long format. Partition dicts
# (objects) are skipped, so the export stays small and nothing gets truncated.
RUN_METRICS_SQL = """
    SELECT r.ts, r.run_id::text, r.pipeline_step, e->>'name', kv.key, kv.value #>> '{}'
    FROM narrative_run_metrics r,
         jsonb_array_elements(r.shadow_partitions) e,
         jsonb_each(e) kv
    WHERE jsonb_typeof(r.shadow_partitions) = 'array'
      AND jsonb_typeof(kv.value) NOT IN ('object', 'array')
    ORDER BY r.ts, e->>'name', kv.key
"""

THEMES_SQL = """
    SELECT persistent_id, label, lifecycle_status, centroid
    FROM narrative_themes
    WHERE lifecycle_status IN ('emerging', 'active')
    ORDER BY persistent_id
"""

# --- Level-1 baseline (design.md D10) --------------------------------------

L1_SIZE_BUCKETS_SQL = """
    SELECT narrative_status,
           to_char(date_trunc('month', created_at), 'YYYY-MM') AS month,
           CASE WHEN article_count <= 1 THEN '1'
                WHEN article_count = 2 THEN '2'
                WHEN article_count <= 5 THEN '3-5'
                WHEN article_count <= 20 THEN '6-20'
                ELSE '>20' END AS bucket,
           count(*)
    FROM storylines
    GROUP BY 1, 2, 3
    ORDER BY 2, 1, 3
"""

L1_COUNT_MISMATCH_SQL = """
    SELECT count(*)
    FROM storylines s
    LEFT JOIN (SELECT storyline_id, count(*) AS n FROM article_storylines GROUP BY 1) a
           ON a.storyline_id = s.id
    WHERE COALESCE(a.n, 0) <> COALESCE(s.article_count, 0)
"""

# Weekly coverage of embedded articles over the last 90 days. "neither" is
# split by why the daily loader could have missed the article: it only reads
# published_date within the last `--days` (1 in the pipeline), so a NULL date
# or a publish-to-ingest lag above 1 day means it was never even considered.
L1_COVERAGE_SQL = """
    WITH orphaned AS (SELECT DISTINCT unnest(article_ids) AS article_id FROM orphan_events),
    a AS (
        SELECT a.id,
               date_trunc('week', a.created_at) AS week,
               EXISTS (SELECT 1 FROM article_storylines s WHERE s.article_id = a.id) AS linked,
               EXISTS (SELECT 1 FROM orphaned o WHERE o.article_id = a.id) AS orphan,
               CASE WHEN a.published_date IS NULL THEN 'null_date'
                    WHEN a.created_at - a.published_date > INTERVAL '1 day' THEN 'lag_gt_1d'
                    ELSE 'lag_le_1d' END AS lag
        FROM articles a
        WHERE a.full_text_embedding IS NOT NULL
          AND a.created_at > NOW() - INTERVAL '90 days'
    )
    SELECT to_char(week, 'YYYY-MM-DD'),
           count(*) AS total,
           count(*) FILTER (WHERE linked) AS linked,
           count(*) FILTER (WHERE NOT linked AND orphan) AS in_orphan_pool,
           count(*) FILTER (WHERE NOT linked AND NOT orphan AND lag = 'lag_le_1d') AS neither_lag_le_1d,
           count(*) FILTER (WHERE NOT linked AND NOT orphan AND lag = 'lag_gt_1d') AS neither_lag_gt_1d,
           count(*) FILTER (WHERE NOT linked AND NOT orphan AND lag = 'null_date') AS neither_null_date
    FROM a
    GROUP BY week
    ORDER BY week
"""

L1_ORPHANS_SQL = """
    SELECT count(*),
           percentile_cont(ARRAY[0.5, 0.9]) WITHIN GROUP (
               ORDER BY EXTRACT(EPOCH FROM NOW() - created_at) / 86400.0),
           percentile_cont(ARRAY[0.5, 0.9]) WITHIN GROUP (ORDER BY retry_count),
           COALESCE(sum(cardinality(article_ids)), 0)
    FROM orphan_events
"""

L1_RUN_METRICS_SQL = """
    SELECT ts, run_id::text, n_storylines_total, n_storylines_active, n_archived,
           match_stats::text, decay_stats::text, runtime_seconds
    FROM narrative_run_metrics
    WHERE pipeline_step = 'narrative_processing'
    ORDER BY ts
"""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_labels_sha256(labels: list[int]) -> str:
    """SHA-256 of a label vector after renumbering labels by first appearance,
    so two partitions that differ only in label names hash equal."""
    mapping: dict[int, int] = {}
    canon = [mapping.setdefault(int(x), len(mapping)) for x in labels]
    return hashlib.sha256(json.dumps(canon).encode()).hexdigest()


def inertia_from_labels(X: np.ndarray, labels: np.ndarray) -> float:
    """Sum of squared distances to each cluster's mean — equals KMeans.inertia_
    at convergence, and needs no change to the deployed k-means code."""
    total = 0.0
    for c in np.unique(labels):
        Xc = X[labels == c]
        total += float(((Xc - Xc.mean(axis=0)) ** 2).sum())
    return total


def _nan_or(value) -> float:
    return float("nan") if value is None else float(value)


def _execute(cur, sql: str, params=None):
    """Single choke point for every statement: refuse anything but SELECT/WITH."""
    head = sql.lstrip().split(None, 1)[0].upper()
    if head not in ("SELECT", "WITH"):
        raise RuntimeError(f"export is read-only; refused statement starting with {head}")
    cur.execute(sql, params)
    return cur.fetchall()


def _write_csv(path: Path, header: list[str], rows) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([x.isoformat() if hasattr(x, "isoformat") else x for x in r])


def export(db, out: Path, git_sha: str) -> dict:
    from scripts.compute_communities import _vec_to_array
    from scripts.theme_clustering import _run_kmeans_embedding_and_score

    out.mkdir(parents=True, exist_ok=True)
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            storylines = _execute(cur, STORYLINES_SQL, (ACTIVE_STATUSES,))
            run_metrics = _execute(cur, RUN_METRICS_SQL)
            themes = _execute(cur, THEMES_SQL)
            l1_sizes = _execute(cur, L1_SIZE_BUCKETS_SQL)
            l1_mismatch = _execute(cur, L1_COUNT_MISMATCH_SQL)[0][0]
            l1_coverage = _execute(cur, L1_COVERAGE_SQL)
            l1_orphans = _execute(cur, L1_ORPHANS_SQL)[0]
            l1_runs = _execute(cur, L1_RUN_METRICS_SQL)

    # --- snapshot.npz + storylines.jsonl ---------------------------------
    n = len(storylines)
    ids = np.array([r[0] for r in storylines], dtype=np.int64)
    current = np.zeros((n, 384), dtype=np.float32)
    summary = np.zeros((n, 384), dtype=np.float32)
    has_current = np.zeros(n, dtype=bool)
    has_summary = np.zeros(n, dtype=bool)
    with open(out / "storylines.jsonl", "w") as f:
        for i, r in enumerate(storylines):
            sid, title, summ, ents, count, status, created, cur_emb, sum_emb = r[:9]
            ca, sa = _vec_to_array(cur_emb), _vec_to_array(sum_emb)
            if ca is not None:
                current[i], has_current[i] = ca, True
            if sa is not None:
                summary[i], has_summary[i] = sa, True
            f.write(json.dumps({
                "id": sid, "title": title, "summary": summ, "key_entities": ents or [],
                "article_count": count, "narrative_status": status,
                "created_at": created.isoformat() if created else None,
            }, ensure_ascii=False) + "\n")
    np.savez_compressed(
        out / "snapshot.npz",
        ids=ids,
        current_embedding=current, has_current_embedding=has_current,
        summary_vector=summary, has_summary_vector=has_summary,
        # NULL community ids are NaN (float64 keeps them distinguishable from labels)
        community_id=np.array([_nan_or(r[9]) for r in storylines]),
        community_id_kmeans_shadow=np.array([_nan_or(r[10]) for r in storylines]),
        community_id_shadow=np.array([_nan_or(r[11]) for r in storylines]),
    )

    # --- run_metrics.csv, themes.jsonl -------------------------------------
    _write_csv(out / "run_metrics.csv", ["ts", "run_id", "pipeline_step", "entry", "key", "value"], run_metrics)
    with open(out / "themes.jsonl", "w") as f:
        for pid, label, status, centroid in themes:
            arr = _vec_to_array(centroid)
            f.write(json.dumps({
                "persistent_id": pid, "label": label, "lifecycle_status": status,
                "centroid": arr.tolist() if arr is not None else None,
            }, ensure_ascii=False) + "\n")

    # --- level-1 baseline ----------------------------------------------------
    orphan_count, age_p, retry_p, orphan_articles = l1_orphans
    with open(out / "l1_baseline.json", "w") as f:
        json.dump({
            "size_buckets": [
                {"status": s, "month": m, "bucket": b, "count": c} for s, m, b, c in l1_sizes
            ],
            "article_count_mismatches": l1_mismatch,
            "weekly_coverage": [
                dict(zip(["week", "total", "linked", "in_orphan_pool",
                          "neither_lag_le_1d", "neither_lag_gt_1d", "neither_null_date"], row))
                for row in l1_coverage
            ],
            "orphan_pool": {
                "events": orphan_count,
                "articles": int(orphan_articles),
                "age_days_p50_p90": [float(x) for x in age_p] if age_p else None,
                "retry_count_p50_p90": [float(x) for x in retry_p] if retry_p else None,
            },
        }, f, indent=2)
    _write_csv(
        out / "l1_run_metrics.csv",
        ["ts", "run_id", "n_storylines_total", "n_storylines_active", "n_archived",
         "match_stats", "decay_stats", "runtime_seconds"],
        l1_runs,
    )

    # --- reference fit (design.md D1 / D9) -----------------------------------
    emb_ids = [int(s) for s, ok in zip(ids, has_current) if ok]
    current_by_id = {int(s): current[i] for i, s in enumerate(ids) if has_current[i]}
    summary_by_id = {int(s): summary[i] for i, s in enumerate(ids) if has_summary[i]}
    partition, score = _run_kmeans_embedding_and_score(
        None, emb_ids, k=REFERENCE_K, random_state=REFERENCE_RANDOM_STATE,
        embedding_cache=(current_by_id, summary_by_id),
    )
    labels = [partition[s] for s in emb_ids]
    X = np.stack([current_by_id[s] for s in emb_ids])
    reference_fit = {
        "k": REFERENCE_K,
        "random_state": REFERENCE_RANDOM_STATE,
        "silhouette": score["silhouette"],
        "inertia": inertia_from_labels(X, np.asarray(labels)),
        "labels": labels,
        "labels_sha256": canonical_labels_sha256(labels),
    }

    # --- manifest --------------------------------------------------------------
    data_files = ["snapshot.npz", "storylines.jsonl", "run_metrics.csv", "themes.jsonl",
                  "l1_baseline.json", "l1_run_metrics.csv"]
    manifest = {
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "n_storylines": n,
        "n_missing_current_embedding": int(n - has_current.sum()),
        "n_missing_summary_vector": int(n - has_summary.sum()),
        "n_run_metrics_rows": len(run_metrics),
        "file_sha256": {name: _sha256(out / name) for name in data_files},
        "reference_fit": reference_fit,
        "n_active_themes": len(themes),
    }
    with open(out / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def probe(out_parent: Path) -> int:
    print(f"cwd: {os.getcwd()}")
    import scripts.theme_clustering  # noqa: F401
    print("import scripts.theme_clustering: ok")
    out_parent.mkdir(parents=True, exist_ok=True)
    test = out_parent / ".probe"
    test.write_text("ok")
    test.unlink()
    print(f"write {out_parent}: ok")
    return 0


def main(argv=None) -> int:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=Path(f"/app/artifacts/clustering_snapshot_{today}"))
    parser.add_argument("--git-sha", default="unknown",
                        help="commit of the checkout the script was piped from")
    parser.add_argument("--probe", action="store_true",
                        help="only check cwd, imports and that the output parent is writable")
    args = parser.parse_args(argv)

    if args.probe:
        return probe(args.out.parent)

    from src.storage.database import DatabaseManager
    manifest = export(DatabaseManager(), args.out, args.git_sha)
    ref = manifest["reference_fit"]
    print(f"Snapshot written to {args.out}")
    print(f"  storylines: {manifest['n_storylines']} "
          f"(missing current_embedding: {manifest['n_missing_current_embedding']})")
    print(f"  run_metrics rows: {manifest['n_run_metrics_rows']}, active themes: {manifest['n_active_themes']}")
    print(f"  reference fit k={ref['k']}: silhouette={ref['silhouette']}, inertia={ref['inertia']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
