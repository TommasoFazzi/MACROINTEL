#!/usr/bin/env python3
"""
Theme Clustering — k-means-on-embedding champion + HDBSCAN challenger shadow

Implements narrative-clustering-embedding-based: replaces Louvain/Leiden on the
entity co-occurrence graph with k-means on `storylines.current_embedding`
(384-dim) as the algorithm that writes `storylines.community_id`. Two-tier
stability architecture (design.md § Decision 2):

    DAILY (every pipeline run):    nearest-centroid assignment against the
                                    active centroids in `narrative_themes` —
                                    no re-clustering, O(n_new * k), deterministic.
    PERIODIC (weekly or on drift): full k-means re-fit with warm-start (init
                                    from the previous active centroids) +
                                    Hungarian matching for cross-run lineage.
                                    HDBSCAN runs in parallel as a permanent
                                    challenger, writing storylines.community_id_shadow.

During the shadow-period validation (before promotion), k-means writes to the
temporary `storylines.community_id_kmeans_shadow` column instead of
`community_id` — see design.md § Open Question 1 (resolved) and migration 045.

Reused, not duplicated: `_fetch_storyline_embeddings`, `_vec_to_array`,
`_name_community`, `_persist_partition_history` from compute_communities.py.
The validation tool for the shadow rollout is scripts/diagnose_clustering_signal.py
(read-only, S1-S7) — not reimplemented here.

Spec reference: openspec/changes/narrative-clustering-embedding-based/
                design.md § Decisions 1-7, specs/narrative-theme-clustering/spec.md
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path
from typing import Optional

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    from sklearn.cluster import HDBSCAN
    HDBSCAN_AVAILABLE = True
except ImportError:
    HDBSCAN_AVAILABLE = False

try:
    from scipy.optimize import linear_sum_assignment
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

from psycopg2.extras import execute_values

from scripts.compute_communities import (
    _fetch_storyline_embeddings,
    _name_community,
    _persist_run_metrics,
    _score_dict,
    _vec_to_array,
)
from src.storage.database import DatabaseManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# k-means champion — fit + score (task 3.1)
# ---------------------------------------------------------------------------

def _run_kmeans_embedding_and_score(
    db: DatabaseManager,
    storyline_ids: list,
    k: int,
    init=None,
    random_state: int = 42,
    embedding_cache: tuple | None = None,
) -> tuple[dict, dict]:
    """Fit k-means on `current_embedding` for the given storyline IDs.

    Returns (partition, score_dict):
        partition: {storyline_id: cluster_label} for storylines with an embedding.
                   Storylines without current_embedding are silently excluded
                   (caller decides how to handle them — see design.md § UC4).
        score_dict: unified schema (same shape as _run_louvain_and_score /
                    _run_leiden_cpm_and_score in compute_communities.py), plus
                    "centroids" (ndarray, k x 384) for warm-start / lineage.

    init: optional ndarray (k_prev x 384) of previous centroids for warm-start
          (design.md § Decision 6). None = default k-means++ init (first run,
          or when the previous k differs from k_current).
    """
    if not SKLEARN_AVAILABLE:
        raise RuntimeError("scikit-learn is required for k-means theme clustering.")

    t0 = time.time()
    if embedding_cache is not None:
        current_by_id, _ = embedding_cache
    else:
        current_by_id, _ = _fetch_storyline_embeddings(db, storyline_ids)

    emb_ids = [sid for sid in storyline_ids if sid in current_by_id]
    if len(emb_ids) < k:
        logger.warning(
            "Not enough embedded storylines (%d) for k=%d — skipping k-means fit",
            len(emb_ids), k,
        )
        return {}, _score_dict("kmeans_embedding", {}, len(storyline_ids), 0.0, None, None, 0)

    X = np.stack([current_by_id[sid] for sid in emb_ids])

    kmeans_kwargs = dict(n_clusters=k, random_state=random_state, n_init=10)
    if init is not None and init.shape[0] == k:
        # Warm-start: init from previous active centroids (design.md § Decision 2).
        kmeans_kwargs["init"] = init
        kmeans_kwargs["n_init"] = 1
    km = KMeans(**kmeans_kwargs)
    labels = km.fit_predict(X)

    partition = {sid: int(label) for sid, label in zip(emb_ids, labels)}

    silhouette = None
    distinct_labels = set(labels.tolist())
    if 2 <= len(distinct_labels) < len(emb_ids):
        try:
            silhouette = float(silhouette_score(X, labels, metric="cosine"))
        except Exception as e:
            logger.debug("silhouette_score failed for kmeans_embedding: %s", e)

    runtime_ms = int((time.time() - t0) * 1000)
    score = _score_dict(
        "kmeans_embedding", partition, len(emb_ids), 0.0, silhouette, None, runtime_ms,
    )
    score["centroids"] = km.cluster_centers_
    score["k"] = k
    score["n_excluded_no_embedding"] = len(storyline_ids) - len(emb_ids)
    return partition, score


# ---------------------------------------------------------------------------
# Nearest-centroid assignment — daily path (task 3.3, 3.4)
# ---------------------------------------------------------------------------

def _cosine_similarity_matrix(X: "np.ndarray", Y: "np.ndarray") -> "np.ndarray":
    """Row-normalized cosine similarity between X (n x d) and Y (m x d)."""
    Xn = X / np.clip(np.linalg.norm(X, axis=1, keepdims=True), 1e-10, None)
    Yn = Y / np.clip(np.linalg.norm(Y, axis=1, keepdims=True), 1e-10, None)
    return Xn @ Yn.T


def assign_nearest_centroid(
    embedding: "np.ndarray",
    active_centroids: dict,
    outlier_threshold: float,
) -> Optional[tuple]:
    """Assign a single embedding to the nearest active centroid.

    active_centroids: {persistent_id: centroid ndarray(384,)}.

    Returns (persistent_id, cos_sim) or None if the embedding has no active
    centroids to compare against, or the best cosine similarity is below
    outlier_threshold (design.md § Decision 2 — UC4 outlier bucket).
    """
    if not active_centroids:
        return None
    ids = list(active_centroids.keys())
    C = np.stack([active_centroids[pid] for pid in ids])
    sims = _cosine_similarity_matrix(embedding.reshape(1, -1), C)[0]
    best_idx = int(np.argmax(sims))
    best_sim = float(sims[best_idx])
    if best_sim < outlier_threshold:
        return None
    return ids[best_idx], best_sim


def assign_storylines_nearest_centroid(
    db: DatabaseManager,
    storyline_ids: list,
    outlier_threshold: float,
    embedding_cache: tuple | None = None,
) -> dict:
    """Daily path (design.md § Decision 2): assign storylines to the nearest
    active theme centroid without re-fitting k-means.

    Returns {storyline_id: persistent_id}. Storylines without current_embedding,
    or whose best similarity is below outlier_threshold, are absent from the
    result — caller sets their community_id to NULL (UC4 bucket).
    """
    active_centroids = _fetch_active_centroids(db)
    if not active_centroids:
        logger.warning("No active narrative_themes centroids — cannot assign nearest-centroid")
        return {}

    if embedding_cache is not None:
        current_by_id, _ = embedding_cache
    else:
        current_by_id, _ = _fetch_storyline_embeddings(db, storyline_ids)

    assignment = {}
    for sid in storyline_ids:
        emb = current_by_id.get(sid)
        if emb is None:
            continue
        result = assign_nearest_centroid(emb, active_centroids, outlier_threshold)
        if result is not None:
            assignment[sid] = result[0]
    return assignment


def _fetch_active_centroids(db: DatabaseManager) -> dict:
    """Fetch {persistent_id: centroid ndarray} for lifecycle_status IN ('emerging','active')."""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT persistent_id, centroid FROM narrative_themes
                WHERE lifecycle_status IN ('emerging', 'active')
                """
            )
            rows = cur.fetchall()
    result = {}
    for pid, centroid in rows:
        arr = _vec_to_array(centroid)
        if arr is not None:
            result[pid] = arr
    return result


# ---------------------------------------------------------------------------
# Warm-start periodic re-fit + Hungarian lineage (tasks 4.1-4.5)
# ---------------------------------------------------------------------------

def refit_with_warm_start(
    db: DatabaseManager,
    storyline_ids: list,
    k: int,
    tau_match: float,
    run_id: str,
    embedding_cache: tuple | None = None,
) -> dict:
    """Periodic re-fit (design.md § Decision 2, § Decision 3): k-means with
    warm-start from active centroids, Hungarian matching for lineage, persists
    results to `narrative_themes`.

    Returns the k-means score_dict (as from _run_kmeans_embedding_and_score),
    with lineage stats merged in: n_matched, n_emerging, n_dormant.
    """
    active_centroids = _fetch_active_centroids(db)
    ids_prev = list(active_centroids.keys())
    init = np.stack([active_centroids[pid] for pid in ids_prev]) if ids_prev else None

    partition, score = _run_kmeans_embedding_and_score(
        db, storyline_ids, k=k, init=init, embedding_cache=embedding_cache,
    )
    if not partition:
        return score

    new_centroids = score["centroids"]  # k x 384
    lineage = _hungarian_match_centroids(
        new_centroids, active_centroids, tau_match=tau_match,
    )
    persistent_ids = _apply_lineage(
        db, new_centroids, lineage, ids_prev, partition, run_id,
    )
    # Remap partition cluster labels (0..k-1) to persistent_id.
    remapped = {sid: persistent_ids[label] for sid, label in partition.items()}
    score["partition_persistent_id"] = remapped
    score["n_matched"] = sum(1 for v in lineage.values() if v is not None)
    score["n_emerging"] = sum(1 for v in lineage.values() if v is None)
    score["n_dormant"] = len(ids_prev) - score["n_matched"]
    return score


def _hungarian_match_centroids(
    new_centroids: "np.ndarray", active_centroids: dict, tau_match: float,
) -> dict:
    """Hungarian matching (cost = cosine distance) between new k-means centroids
    and previous active centroids (design.md § Decision 3).

    Returns {new_cluster_label: matched_persistent_id_or_None}.
    A match below tau_match similarity (i.e. cosine distance above 1-tau_match)
    is discarded — treated as no-match (new theme), consistent with community_lineage.tau_match.
    """
    n_new = new_centroids.shape[0]
    result = {i: None for i in range(n_new)}
    if not active_centroids or not SCIPY_AVAILABLE:
        return result

    ids_prev = list(active_centroids.keys())
    C_prev = np.stack([active_centroids[pid] for pid in ids_prev])
    sims = _cosine_similarity_matrix(new_centroids, C_prev)  # n_new x n_prev
    cost = 1.0 - sims

    row_ind, col_ind = linear_sum_assignment(cost)
    for r, c in zip(row_ind, col_ind):
        if sims[r, c] >= tau_match:
            result[int(r)] = ids_prev[c]
    return result


def _apply_lineage(
    db: DatabaseManager,
    new_centroids: "np.ndarray",
    lineage: dict,
    ids_prev: list,
    partition: dict,
    run_id: str,
) -> dict:
    """Persist the re-fit outcome to narrative_themes (design.md § Decision 3):
    - matched cluster -> UPDATE existing row (centroid, last_seen, lifecycle='active')
    - unmatched cluster -> INSERT new row (lifecycle='emerging')
    - unmatched previous centroid (orphan) -> UPDATE lifecycle='dormant'

    Returns {new_cluster_label: persistent_id} for remapping the partition.
    """
    member_counts: dict[int, int] = {}
    for _, label in partition.items():
        member_counts[label] = member_counts.get(label, 0) + 1

    matched_prev_ids = {pid for pid in lineage.values() if pid is not None}
    label_to_pid: dict[int, int] = {}

    with db.get_connection() as conn:
        with conn.cursor() as cur:
            for label, matched_pid in lineage.items():
                centroid_list = new_centroids[label].tolist()
                n_members = member_counts.get(label, 0)
                if matched_pid is not None:
                    cur.execute(
                        """
                        UPDATE narrative_themes
                        SET centroid = %s::vector, lifecycle_status = 'active',
                            last_seen = NOW(), last_refit_run_id = %s,
                            n_members_last_refit = %s
                        WHERE persistent_id = %s
                        """,
                        (centroid_list, run_id, n_members, matched_pid),
                    )
                    label_to_pid[label] = matched_pid
                else:
                    cur.execute(
                        """
                        INSERT INTO narrative_themes
                            (centroid, lifecycle_status, last_seen,
                             last_refit_run_id, n_members_last_refit)
                        VALUES (%s::vector, 'emerging', NOW(), %s, %s)
                        RETURNING persistent_id
                        """,
                        (centroid_list, run_id, n_members),
                    )
                    label_to_pid[label] = cur.fetchone()[0]

            # Orphaned previous centroids (no match this re-fit) -> dormant.
            # Re-emergence handled implicitly: a dormant row can be matched again
            # in a future re-fit because _fetch_active_centroids only excludes
            # 'dormant'/'retired' — once re-matched it flips back to 'active' above.
            orphans = [pid for pid in ids_prev if pid not in matched_prev_ids]
            if orphans:
                cur.execute(
                    """
                    UPDATE narrative_themes SET lifecycle_status = 'dormant'
                    WHERE persistent_id = ANY(%s) AND lifecycle_status != 'dormant'
                    """,
                    (orphans,),
                )
        conn.commit()

    return label_to_pid


# ---------------------------------------------------------------------------
# HDBSCAN permanent challenger shadow (tasks 5.1-5.3)
# ---------------------------------------------------------------------------

def _run_hdbscan_shadow_and_score(
    db: DatabaseManager,
    storyline_ids: list,
    min_cluster_size: int,
    min_samples: int,
    embedding_cache: tuple | None = None,
) -> tuple[dict, dict]:
    """HDBSCAN on the same embeddings as the k-means re-fit — permanent
    challenger (design.md § Decision 1), never promotable (no warm-start).

    Returns (partition, score_dict). Degrades gracefully (empty partition,
    logged warning) if sklearn's HDBSCAN is unavailable — same pattern as
    LEIDEN_AVAILABLE in compute_communities.py.
    """
    if not HDBSCAN_AVAILABLE:
        logger.warning("sklearn.cluster.HDBSCAN unavailable — skipping shadow run")
        return {}, _score_dict("hdbscan_shadow", {}, len(storyline_ids), 0.0, None, None, 0)

    t0 = time.time()
    if embedding_cache is not None:
        current_by_id, _ = embedding_cache
    else:
        current_by_id, _ = _fetch_storyline_embeddings(db, storyline_ids)

    emb_ids = [sid for sid in storyline_ids if sid in current_by_id]
    if len(emb_ids) < min_cluster_size:
        logger.warning(
            "Not enough embedded storylines (%d) for HDBSCAN min_cluster_size=%d",
            len(emb_ids), min_cluster_size,
        )
        return {}, _score_dict("hdbscan_shadow", {}, len(storyline_ids), 0.0, None, None, 0)

    X = np.stack([current_by_id[sid] for sid in emb_ids])
    hdb = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples, metric="euclidean")
    labels = hdb.fit_predict(X)

    # HDBSCAN noise label (-1) maps to "no community" — excluded from the partition,
    # same semantics as the outlier bucket in the k-means daily path.
    partition = {sid: int(label) for sid, label in zip(emb_ids, labels) if label != -1}

    silhouette = None
    distinct = set(partition.values())
    if 2 <= len(distinct) < len(partition):
        try:
            sub_ids = list(partition.keys())
            Xs = np.stack([current_by_id[sid] for sid in sub_ids])
            ys = [partition[sid] for sid in sub_ids]
            silhouette = float(silhouette_score(Xs, ys, metric="cosine"))
        except Exception as e:
            logger.debug("silhouette_score failed for hdbscan_shadow: %s", e)

    runtime_ms = int((time.time() - t0) * 1000)
    score = _score_dict(
        "hdbscan_shadow", partition, len(emb_ids), 0.0, silhouette, None, runtime_ms,
    )
    score["n_noise"] = sum(1 for label in labels if label == -1)
    return partition, score


def write_hdbscan_shadow(db: DatabaseManager, partition: dict) -> None:
    """Write the HDBSCAN challenger partition to storylines.community_id_shadow
    exclusively — must never touch storylines.community_id (design.md § Decision 1).
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            if partition:
                execute_values(
                    cur,
                    """
                    UPDATE storylines AS s
                    SET community_id_shadow = v.cid
                    FROM (VALUES %s) AS v(sid, cid)
                    WHERE s.id = v.sid
                    """,
                    [(sid, cid) for sid, cid in partition.items()],
                )
            cur.execute(
                "UPDATE storylines SET community_id_shadow = NULL "
                "WHERE id != ALL(%s) AND community_id_shadow IS NOT NULL",
                (list(partition.keys()) or [-1],),
            )
        conn.commit()


def write_kmeans_shadow(db: DatabaseManager, partition: dict) -> None:
    """Write the k-means champion candidate to the temporary shadow-period sink
    storylines.community_id_kmeans_shadow (migration 045, design.md § Open
    Question 1, resolved). Used only before promotion — see tasks.md group 8.
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            if partition:
                execute_values(
                    cur,
                    """
                    UPDATE storylines AS s
                    SET community_id_kmeans_shadow = v.cid
                    FROM (VALUES %s) AS v(sid, cid)
                    WHERE s.id = v.sid
                    """,
                    [(sid, cid) for sid, cid in partition.items()],
                )
            cur.execute(
                "UPDATE storylines SET community_id_kmeans_shadow = NULL "
                "WHERE id != ALL(%s) AND community_id_kmeans_shadow IS NOT NULL",
                (list(partition.keys()) or [-1],),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Drift detection + retune trigger (tasks 6.1-6.3)
# ---------------------------------------------------------------------------

def detect_drift(db: DatabaseManager, cfg_drift, baseline_window_days: Optional[int] = None) -> dict:
    """Compare the latest community_detection run's metrics against the rolling
    p50/30d baseline in narrative_run_metrics (design.md § Decision 5).

    Returns {"drift": bool, "reasons": [str, ...], "latest": dict, "baseline": dict}.
    No history / not enough rows -> drift=False (nothing to compare against yet).
    """
    window_days = baseline_window_days or cfg_drift.baseline_window_days
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tcs, community_coherence_med, epr
                FROM narrative_run_metrics
                WHERE pipeline_step = 'community_detection'
                ORDER BY ts DESC LIMIT 1
                """
            )
            latest_row = cur.fetchone()
            if latest_row is None:
                return {"drift": False, "reasons": [], "latest": {}, "baseline": {}}

            cur.execute(
                """
                SELECT
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY tcs) AS tcs_p50,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY community_coherence_med) AS coh_p50,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY epr) AS epr_p50
                FROM narrative_run_metrics
                WHERE pipeline_step = 'community_detection'
                  AND ts >= NOW() - (%s || ' days')::interval
                """,
                (window_days,),
            )
            baseline_row = cur.fetchone()

    latest = {"tcs": latest_row[0], "coherence_med": latest_row[1], "epr": latest_row[2]}
    baseline = {"tcs_p50": baseline_row[0], "coherence_med_p50": baseline_row[1], "epr_p50": baseline_row[2]}

    reasons = []
    th = cfg_drift.thresholds
    if latest["tcs"] is not None and baseline["tcs_p50"]:
        if latest["tcs"] < th.tcs_drop_ratio * baseline["tcs_p50"]:
            reasons.append("tcs_drop")
    if latest["coherence_med"] is not None and baseline["coherence_med_p50"]:
        if latest["coherence_med"] < th.coherence_drop_ratio * baseline["coherence_med_p50"]:
            reasons.append("coherence_drop")
    if latest["epr"] is not None and baseline["epr_p50"]:
        if latest["epr"] < th.epr_drop_ratio * baseline["epr_p50"]:
            reasons.append("epr_drop")

    return {"drift": bool(reasons), "reasons": reasons, "latest": latest, "baseline": baseline}


def count_consecutive_drift_signals(db: DatabaseManager) -> int:
    """Count how many of the most recent community_detection runs (starting
    from the latest) had drift_signals populated with a non-empty reasons list.

    Used by the retune-k trigger (design.md § Decision 5): k-retune fires only
    on >=2 consecutive drift-flagged runs, not on a single isolated one.
    """
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT drift_signals FROM narrative_run_metrics
                WHERE pipeline_step = 'community_detection'
                ORDER BY ts DESC LIMIT 10
                """
            )
            rows = cur.fetchall()

    count = 0
    for (drift_signals,) in rows:
        if drift_signals and drift_signals.get("drift"):
            count += 1
        else:
            break
    return count


def sweep_k_for_best_silhouette(
    db: DatabaseManager,
    storyline_ids: list,
    k_sweep_range: list,
    embedding_cache: tuple | None = None,
) -> int:
    """Silhouette-sweep over k_sweep_range (design.md § Decision 5), used only
    when drift persists for >=2 consecutive re-fits. Returns the k with the
    best silhouette score (ties broken by smaller k).
    """
    if embedding_cache is not None:
        current_by_id, _ = embedding_cache
    else:
        current_by_id, _ = _fetch_storyline_embeddings(db, storyline_ids)

    best_k, best_sil = k_sweep_range[0], float("-inf")
    for k in range(k_sweep_range[0], k_sweep_range[1] + 1):
        if k >= len(current_by_id):
            continue
        _, score = _run_kmeans_embedding_and_score(
            db, storyline_ids, k=k, init=None, embedding_cache=(current_by_id, {}),
        )
        sil = score.get("silhouette")
        if sil is not None and sil > best_sil:
            best_sil = sil
            best_k = k
    return best_k


# ---------------------------------------------------------------------------
# LLM naming — same mechanism, new input (tasks 7.1-7.3)
# ---------------------------------------------------------------------------

def name_themes(db: DatabaseManager, partition_by_persistent_id: dict) -> int:
    """Adapt `_name_community` (Gemini T5, unchanged mechanism) to operate on
    theme members instead of Louvain community members (design.md § Decision 6).

    partition_by_persistent_id: {persistent_id: [storyline_id, ...]}.
    Writes the generated name to both narrative_themes.label and
    storylines.community_name (propagation to the existing downstream contract).
    Singleton themes (1 member) are skipped, same as the Louvain path.

    Returns the number of themes successfully named.
    """
    named = 0
    with db.get_connection() as conn:
        for persistent_id, storyline_ids in partition_by_persistent_id.items():
            if len(storyline_ids) < 2:
                continue
            name = _name_community(persistent_id, storyline_ids, conn)
            if not name:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE narrative_themes SET label = %s WHERE persistent_id = %s",
                    (name, persistent_id),
                )
                cur.execute(
                    "UPDATE storylines SET community_name = %s WHERE id = ANY(%s)",
                    (name, storyline_ids),
                )
            conn.commit()
            logger.info("Theme %d (%d members) -> '%s'", persistent_id, len(storyline_ids), name)
            named += 1
            time.sleep(1.5)  # respect Gemini rate limits, same as compute_communities.py
    return named


# ---------------------------------------------------------------------------
# Orchestration — dispatch daily vs periodic, shadow-period sink (task 8.1-8.2)
# ---------------------------------------------------------------------------

def _active_storyline_ids(db: DatabaseManager) -> list:
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM storylines "
                "WHERE narrative_status IN ('emerging', 'active', 'stabilized')"
            )
            return [row[0] for row in cur.fetchall()]


def _is_refit_due(db: DatabaseManager, refit_cadence_days: int) -> bool:
    """True when no periodic re-fit has run within refit_cadence_days, or none
    has ever run (first-run bootstrap always re-fits)."""
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ts FROM narrative_run_metrics
                WHERE pipeline_step = 'community_detection'
                  AND drift_signals IS NOT NULL
                ORDER BY ts DESC LIMIT 1
                """
            )
            row = cur.fetchone()
    if row is None:
        return True
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT NOW() - %s > (%s || ' days')::interval", (row[0], refit_cadence_days))
            return bool(cur.fetchone()[0])


def run_theme_clustering(
    promoted: bool = False,
    dry_run: bool = False,
    max_name: int = 60,
) -> dict:
    """Entry point for the k-means champion + HDBSCAN challenger pipeline step.

    promoted: False (default) = shadow period — k-means writes
              storylines.community_id_kmeans_shadow (temporary sink, migration
              045). True = post-promotion — k-means writes storylines.community_id
              directly (design.md § Migration Plan step 5, tasks.md 8.5).

    Dispatches daily nearest-centroid assignment vs periodic warm-start re-fit
    based on theme_clustering.refit_cadence_days and detect_drift() — mirrors
    the two-tier architecture in design.md § Decision 2.
    """
    from src.nlp.config import load_clustering_config
    cfg = load_clustering_config()
    theme_cfg = cfg.theme_clustering
    db = DatabaseManager()
    run_id = str(uuid.uuid4())
    start_time = time.time()

    storyline_ids = _active_storyline_ids(db)
    embedding_cache = _fetch_storyline_embeddings(db, storyline_ids)

    stats = {
        "run_id": run_id,
        "storylines_total": len(storyline_ids),
        "nodes": len(storyline_ids),
        "promoted": promoted,
    }

    drift_result = detect_drift(db, cfg.drift_detection)
    refit_due = _is_refit_due(db, theme_cfg.refit_cadence_days) or drift_result["drift"]
    stats["drift_signals"] = drift_result

    k = theme_cfg.k_current
    if refit_due:
        # Retune k only when drift has persisted >=2 consecutive re-fits
        # (design.md § Decision 5) — a single isolated drift keeps k fixed.
        if drift_result["drift"] and count_consecutive_drift_signals(db) >= 1:
            k = sweep_k_for_best_silhouette(
                db, storyline_ids, theme_cfg.k_sweep_range, embedding_cache=embedding_cache,
            )
            logger.info("Drift persisted >=2 runs — retuned k to %d", k)

        kmeans_score = refit_with_warm_start(
            db, storyline_ids, k=k, tau_match=cfg.community_lineage.tau_match,
            run_id=run_id, embedding_cache=embedding_cache,
        )
        kmeans_partition = kmeans_score.get("partition_persistent_id", {})

        hdbscan_partition, hdbscan_score = ({}, {})
        if theme_cfg.hdbscan_shadow.enabled:
            hdbscan_partition, hdbscan_score = _run_hdbscan_shadow_and_score(
                db, storyline_ids,
                min_cluster_size=theme_cfg.hdbscan_shadow.min_cluster_size,
                min_samples=theme_cfg.hdbscan_shadow.min_samples,
                embedding_cache=embedding_cache,
            )

        stats["shadow_partitions"] = [
            {k_: v for k_, v in kmeans_score.items() if k_ != "centroids"},
            {k_: v for k_, v in hdbscan_score.items() if k_ != "centroids"},
        ]
        stats["n_communities"] = kmeans_score.get("n_communities", 0)
        stats["silhouette"] = kmeans_score.get("silhouette")
        stats["n_matched"] = kmeans_score.get("n_matched")
        stats["n_emerging"] = kmeans_score.get("n_emerging")
        stats["n_dormant"] = kmeans_score.get("n_dormant")
    else:
        kmeans_partition = assign_storylines_nearest_centroid(
            db, storyline_ids, theme_cfg.outlier_threshold, embedding_cache=embedding_cache,
        )
        hdbscan_partition = {}
        stats["n_communities"] = len(set(kmeans_partition.values()))

    if not dry_run:
        if promoted:
            with db.get_connection() as conn:
                with conn.cursor() as cur:
                    if kmeans_partition:
                        execute_values(
                            cur,
                            """
                            UPDATE storylines AS s SET community_id = v.cid
                            FROM (VALUES %s) AS v(sid, cid)
                            WHERE s.id = v.sid
                            """,
                            [(sid, cid) for sid, cid in kmeans_partition.items()],
                        )
                    cur.execute(
                        "UPDATE storylines SET community_id = NULL "
                        "WHERE id != ALL(%s) AND community_id IS NOT NULL",
                        (list(kmeans_partition.keys()) or [-1],),
                    )
                conn.commit()
        else:
            write_kmeans_shadow(db, kmeans_partition)

        if hdbscan_partition:
            write_hdbscan_shadow(db, hdbscan_partition)

        if refit_due and kmeans_partition:
            themes_members: dict[int, list] = {}
            for sid, pid in kmeans_partition.items():
                themes_members.setdefault(pid, []).append(sid)
            stats["themes_named"] = name_themes(db, themes_members)

    stats["runtime_seconds"] = round(time.time() - start_time, 3)
    _persist_run_metrics(db, stats, dry_run)
    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="k-means-on-embedding theme clustering (champion/challenger rollout)"
    )
    parser.add_argument(
        "--promoted", action="store_true",
        help=(
            "Write k-means output to storylines.community_id (post-promotion). "
            "Default = shadow period, writes to community_id_kmeans_shadow "
            "(design.md § Open Question 1, tasks.md 8.2)."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute partitions but do not write to DB or narrative_run_metrics",
    )
    parser.add_argument(
        "--max-name", type=int, default=60,
        help="Max number of themes to name with LLM (largest first, default: 60)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("THEME CLUSTERING (k-means champion / HDBSCAN challenger)")
    print("=" * 60)
    print(f"  Promoted (writes community_id): {args.promoted}")
    print(f"  Dry run:                        {args.dry_run}")
    print()

    stats = run_theme_clustering(promoted=args.promoted, dry_run=args.dry_run, max_name=args.max_name)

    print(f"Storylines (active):    {stats['storylines_total']}")
    print(f"Communities found:      {stats.get('n_communities', 'N/A')}")
    print(f"Silhouette:             {stats.get('silhouette', 'N/A')}")
    print(f"Drift detected:         {stats['drift_signals']['drift']} ({stats['drift_signals']['reasons']})")
    print(f"Themes named:           {stats.get('themes_named', 'N/A (daily path or dry-run)')}")
    if args.dry_run:
        print("\n[DRY RUN] No changes written to database.")
    print("\nDone!")


if __name__ == "__main__":
    main()
