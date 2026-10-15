"""Behaviour-preservation test for the `_umap_reduce` / `_hdbscan_labels`
extraction (clustering-offline-validation-study, design.md D2, tasks 2.1-2.3).

`_reference_hdbscan_shadow_and_score` is a verbatim copy of the inline body of
`_run_hdbscan_shadow_and_score` before the refactor (module names prefixed with
`tc.`). Both run in the same process on the same data, so the comparison holds
on any platform: nothing platform-dependent (UMAP output under numba) is
hardcoded here.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import scripts.theme_clustering as tc  # noqa: E402


def _reference_hdbscan_shadow_and_score(
    db,
    storyline_ids: list,
    min_cluster_size: int,
    min_samples: int,
    embedding_cache: tuple | None = None,
    umap_cfg=None,
) -> tuple[dict, dict]:
    if not tc.HDBSCAN_AVAILABLE:
        tc.logger.warning("sklearn.cluster.HDBSCAN unavailable — skipping shadow run")
        return {}, tc._score_dict("hdbscan_shadow", {}, len(storyline_ids), 0.0, None, None, 0)

    t0 = time.time()
    if embedding_cache is not None:
        current_by_id, summary_by_id = embedding_cache
    else:
        current_by_id, summary_by_id = tc._fetch_storyline_embeddings(db, storyline_ids)

    emb_ids = [sid for sid in storyline_ids if sid in current_by_id]
    if len(emb_ids) < min_cluster_size:
        tc.logger.warning(
            "Not enough embedded storylines (%d) for HDBSCAN min_cluster_size=%d",
            len(emb_ids), min_cluster_size,
        )
        return {}, tc._score_dict("hdbscan_shadow", {}, len(storyline_ids), 0.0, None, None, 0)

    X_full = np.stack([current_by_id[sid] for sid in emb_ids])

    umap_requested = bool(umap_cfg is not None and getattr(umap_cfg, "enabled", False))
    X = X_full
    quality_metric = "cosine"
    umap_enabled = False
    if umap_requested:
        if not tc.UMAP_AVAILABLE:
            tc.logger.warning("umap-learn unavailable — falling back to full-dimensionality HDBSCAN")
        else:
            try:
                reducer = tc.umap.UMAP(
                    n_components=umap_cfg.n_components,
                    n_neighbors=umap_cfg.n_neighbors,
                    min_dist=umap_cfg.min_dist,
                    random_state=umap_cfg.random_state,
                )
                X = reducer.fit_transform(X_full)
                quality_metric = "euclidean"
                umap_enabled = True
            except Exception as e:
                tc.logger.warning("UMAP reduction failed (%s) — falling back to full-dimensionality HDBSCAN", e)
                X = X_full

    hdb = tc.HDBSCAN(min_cluster_size=min_cluster_size, min_samples=min_samples, metric="euclidean")
    labels = hdb.fit_predict(X)

    partition = {sid: int(label) for sid, label in zip(emb_ids, labels) if label != -1}

    silhouette = None
    calinski_harabasz = None
    distinct = set(partition.values())
    if 2 <= len(distinct) < len(partition):
        try:
            sub_idx = [i for i, sid in enumerate(emb_ids) if sid in partition]
            Xs = X[sub_idx]
            ys = [partition[emb_ids[i]] for i in sub_idx]
            silhouette = float(tc.silhouette_score(Xs, ys, metric=quality_metric))
        except Exception as e:
            tc.logger.debug("silhouette_score failed for hdbscan_shadow: %s", e)
        try:
            calinski_harabasz = float(tc.calinski_harabasz_score(Xs, ys))
        except Exception as e:
            tc.logger.debug("calinski_harabasz_score failed for hdbscan_shadow: %s", e)

    coherence_med, _ = tc._coherence_med(partition, summary_by_id)

    runtime_ms = int((time.time() - t0) * 1000)
    score = tc._score_dict(
        "hdbscan_shadow", partition, len(emb_ids), 0.0, silhouette, coherence_med, runtime_ms,
    )
    score["calinski_harabasz"] = calinski_harabasz
    score["n_noise"] = sum(1 for label in labels if label == -1)
    score["umap_enabled"] = umap_enabled
    score["partition"] = partition
    return partition, score


class _UMAPCfg:
    def __init__(self, enabled=True, n_components=4, n_neighbors=5, min_dist=0.0, random_state=42):
        self.enabled = enabled
        self.n_components = n_components
        self.n_neighbors = n_neighbors
        self.min_dist = min_dist
        self.random_state = random_state


def _cache(seed=3, n_per_cluster=12, n_clusters=3, dim=32):
    """Three well-separated blobs plus a few uniform outliers, so HDBSCAN
    yields both clusters and noise; summary vectors differ from the
    clustering vectors so coherence_med is exercised."""
    rng = np.random.default_rng(seed)
    current, summary = {}, {}
    sid = 0
    for c in range(n_clusters):
        center = rng.normal(loc=c * 5.0, scale=0.1, size=dim)
        for _ in range(n_per_cluster):
            current[sid] = (center + rng.normal(scale=0.05, size=dim)).astype(np.float32)
            summary[sid] = rng.normal(size=dim).astype(np.float32)
            sid += 1
    for _ in range(4):
        current[sid] = rng.uniform(-20, 20, size=dim).astype(np.float32)
        summary[sid] = rng.normal(size=dim).astype(np.float32)
        sid += 1
    return current, summary


def _without_runtime(score: dict) -> dict:
    return {k: v for k, v in score.items() if k != "runtime_ms"}


@pytest.mark.skipif(not tc.HDBSCAN_AVAILABLE, reason="sklearn HDBSCAN not available")
@pytest.mark.parametrize("umap_cfg", [
    None,
    _UMAPCfg(enabled=False),
    pytest.param(
        _UMAPCfg(enabled=True),
        marks=pytest.mark.skipif(not tc.UMAP_AVAILABLE, reason="umap-learn not available"),
    ),
], ids=["no_cfg", "umap_disabled", "umap_enabled"])
def test_refactored_hdbscan_matches_pre_refactor_reference(umap_cfg):
    cache = _cache()
    ids = list(cache[0].keys())
    kwargs = dict(db=None, storyline_ids=ids, min_cluster_size=5, min_samples=5,
                  embedding_cache=cache, umap_cfg=umap_cfg)

    ref_partition, ref_score = _reference_hdbscan_shadow_and_score(**kwargs)
    new_partition, new_score = tc._run_hdbscan_shadow_and_score(**kwargs)

    assert new_partition == ref_partition
    assert _without_runtime(new_score) == _without_runtime(ref_score)
    assert ref_partition, "fixture must produce at least one cluster"
