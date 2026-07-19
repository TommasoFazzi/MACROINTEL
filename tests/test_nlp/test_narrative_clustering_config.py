"""
Config loader tests for the narrative-clustering-embedding-based change.

Covers: theme_clustering defaults, extra="forbid" typo detection, and
YAML -> Pydantic round-trip for the new section.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.nlp.config import (
    HDBSCANShadowConfig,
    NarrativeClusteringConfig,
    ThemeClusteringConfig,
    UMAPConfig,
    load_clustering_config,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset_config_cache():
    reset_cache()
    yield
    reset_cache()


def test_theme_clustering_defaults():
    cfg = NarrativeClusteringConfig()
    assert cfg.theme_clustering.k_current == 18
    assert cfg.theme_clustering.k_sweep_range == [10, 30]
    assert cfg.theme_clustering.warm_start is True
    assert cfg.theme_clustering.outlier_threshold == 0.15
    assert cfg.theme_clustering.refit_cadence_days == 7
    assert cfg.theme_clustering.hdbscan_shadow.enabled is True
    assert cfg.theme_clustering.hdbscan_shadow.min_cluster_size == 5
    assert cfg.theme_clustering.hdbscan_shadow.min_samples == 5
    # UMAP pre-reduction (clustering-shadow-metrics-umap) — disabled by default,
    # so the pre-existing production path stays byte-identical (task 7.6).
    assert cfg.theme_clustering.hdbscan_shadow.umap.enabled is False
    assert cfg.theme_clustering.hdbscan_shadow.umap.n_components == 10
    assert cfg.theme_clustering.hdbscan_shadow.umap.n_neighbors == 15
    assert cfg.theme_clustering.hdbscan_shadow.umap.min_dist == 0.0
    assert cfg.theme_clustering.hdbscan_shadow.umap.random_state == 42


def test_theme_clustering_rejects_unknown_key():
    with pytest.raises(ValidationError):
        ThemeClusteringConfig(k_current=18, typo_field=True)


def test_hdbscan_shadow_rejects_unknown_key():
    with pytest.raises(ValidationError):
        HDBSCANShadowConfig(enabled=True, min_cluster_sizee=5)


def test_umap_rejects_unknown_key():
    with pytest.raises(ValidationError):
        UMAPConfig(enabled=True, n_componentss=10)


def test_theme_clustering_yaml_round_trip(tmp_path: Path):
    yaml_content = textwrap.dedent(
        """
        theme_clustering:
          k_current: 22
          k_sweep_range: [15, 35]
          warm_start: false
          outlier_threshold: 0.2
          refit_cadence_days: 14
          hdbscan_shadow:
            enabled: false
            min_cluster_size: 8
            min_samples: 4
            umap:
              enabled: true
              n_components: 5
              n_neighbors: 30
              min_dist: 0.1
              random_state: 7
        """
    )
    config_path = tmp_path / "narrative_clustering.yaml"
    config_path.write_text(yaml_content, encoding="utf-8")

    cfg = load_clustering_config(path=config_path, force_reload=True)

    assert cfg.theme_clustering.k_current == 22
    assert cfg.theme_clustering.k_sweep_range == [15, 35]
    assert cfg.theme_clustering.warm_start is False
    assert cfg.theme_clustering.outlier_threshold == 0.2
    assert cfg.theme_clustering.refit_cadence_days == 14
    assert cfg.theme_clustering.hdbscan_shadow.enabled is False
    assert cfg.theme_clustering.hdbscan_shadow.min_cluster_size == 8
    assert cfg.theme_clustering.hdbscan_shadow.min_samples == 4
    assert cfg.theme_clustering.hdbscan_shadow.umap.enabled is True
    assert cfg.theme_clustering.hdbscan_shadow.umap.n_components == 5
    assert cfg.theme_clustering.hdbscan_shadow.umap.n_neighbors == 30
    assert cfg.theme_clustering.hdbscan_shadow.umap.min_dist == 0.1
    assert cfg.theme_clustering.hdbscan_shadow.umap.random_state == 7


def test_full_project_config_loads_with_theme_clustering():
    """The actual config/narrative_clustering.yaml must parse with the new section."""
    cfg = load_clustering_config(force_reload=True)
    assert isinstance(cfg.theme_clustering, ThemeClusteringConfig)
    assert cfg.theme_clustering.k_current == 18
    assert cfg.community.algorithm == "louvain"
