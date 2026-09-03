"""
Narrative Clustering Config Loader

Pydantic-validated loader for config/narrative_clustering.yaml. Single source
of truth for tunable thresholds across narrative_processor.py and
compute_communities.py.

Schema reference: openspec/changes/upgrade-narrative-clustering-algorithms/
                  design.md § Decision 14.

Usage:
    from src.nlp.config import load_clustering_config
    cfg = load_clustering_config()
    threshold = cfg.matching.threshold
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Section models
# ---------------------------------------------------------------------------

class _StrictBase(BaseModel):
    # Reject unknown keys so YAML typos surface immediately.
    model_config = ConfigDict(extra="forbid")


class MicroClusterConfig(_StrictBase):
    threshold: float = 0.90
    linkage: str = "average"


class MatchingConfig(_StrictBase):
    threshold: float = 0.75
    time_decay_factor: float = 0.05
    entity_boost: float = 0.10
    entity_jaccard_threshold: float = 0.05


class HDBSCANConfig(_StrictBase):
    min_cluster_size_base: int = 2
    min_samples: int = 2
    metric: str = "euclidean"


class OrphanConfig(_StrictBase):
    ttl_days: int = 30
    max_pool_size: int = 5000
    max_retry_count: int = 10
    batch_consolidation_interval_days: int = 3


class EvolutionConfig(_StrictBase):
    drift_weight_old: float = 0.85
    drift_weight_new: float = 0.15


class MomentumConfig(_StrictBase):
    ewma_lambda: float = 0.3
    burst_threshold: float = 2.0
    rate_floor: float = 0.05


class DecayConfig(_StrictBase):
    stale_days_for_decay: int = 7
    active_to_stabilized_momentum: float = 0.3
    stabilized_to_archived_days: int = 30
    emerging_ttl_days: int = 30  # consumed by rule_5 in _apply_decay()
    edge_weight_decay_days: int = 30
    edge_weight_change_threshold: float = 0.02


class QualityGateConfig(_StrictBase):
    n_communities_min: int = 20
    n_communities_max: int = 60
    max_community_ratio: float = 0.15
    singleton_ratio_max: float = 0.30
    coherence_median_min: float = 0.45
    epr_min: float = 0.5


class ShadowComparisonConfig(_StrictBase):
    # Phase 1E (Decision 22) — 4-way shadow comparison framework toggle. When
    # disabled, compute_communities.py skips the shadow partitions and only runs
    # the applied algorithm (instant reversibility, no DB schema change).
    enabled: bool = True


class CommunityConfig(_StrictBase):
    algorithm: str = "louvain"
    min_weight: float = 0.05               # Louvain edge filter (legacy)
    resolution: float = 0.8                # Louvain resolution (legacy)
    resolution_parameter: float = 0.01     # Leiden+CPM γ
    resolution_sweep: List[float] = Field(
        default_factory=lambda: [0.005, 0.01, 0.02, 0.03, 0.05]
    )
    quality_gate: QualityGateConfig = Field(default_factory=QualityGateConfig)
    shadow_mode: bool = True
    shadow_comparison: ShadowComparisonConfig = Field(default_factory=ShadowComparisonConfig)


class UMAPConfig(_StrictBase):
    enabled: bool = False
    n_components: int = 10
    n_neighbors: int = 15
    min_dist: float = 0.0
    random_state: int = 42


class HDBSCANShadowConfig(_StrictBase):
    enabled: bool = True
    min_cluster_size: int = 5
    min_samples: int = 5
    umap: UMAPConfig = Field(default_factory=UMAPConfig)


class ThemeClusteringConfig(_StrictBase):
    k_current: int = 18
    k_sweep_range: List[int] = Field(default_factory=lambda: [10, 30])
    warm_start: bool = True
    outlier_threshold: float = 0.15
    refit_cadence_days: int = 7
    hdbscan_shadow: HDBSCANShadowConfig = Field(default_factory=HDBSCANShadowConfig)


class SparsificationConfig(_StrictBase):
    algorithm: str = "disparity_filter"
    alpha: float = 0.3
    fallback_threshold: float = 0.10


class SummaryCacheConfig(_StrictBase):
    delta_embedding_threshold: float = 0.05
    min_new_articles: int = 2


class SourceDiversityConfig(_StrictBase):
    enabled: bool = True
    epsilon: float = 0.1


class CompositeWeights(_StrictBase):
    weighted_jaccard: float = 0.45
    centroid_cosine: float = 0.40
    inclusion: float = 0.15


class CommunityLineageConfig(_StrictBase):
    composite_weights: CompositeWeights = Field(default_factory=CompositeWeights)
    tau_match: float = 0.45
    tau_split: float = 0.30
    tau_merge: float = 0.30
    inclusion_override: float = 0.60


class DriftThresholds(_StrictBase):
    tcs_drop_ratio: float = 0.80
    coherence_drop_ratio: float = 0.85
    epr_drop_ratio: float = 0.80
    churn_shift_ratio: float = 0.30


class DriftDetectionConfig(_StrictBase):
    enabled: bool = True
    baseline_window_days: int = 30
    thresholds: DriftThresholds = Field(default_factory=DriftThresholds)


class BaselineMetrics(_StrictBase):
    tcs_p50_30d: Optional[float] = None
    coherence_med_p50_30d: Optional[float] = None
    epr_p50_30d: Optional[float] = None
    new_per_day_p50_30d: Optional[float] = None


class MetaConfig(_StrictBase):
    schema_version: int = 1
    last_retuned_at: Optional[datetime] = None
    baseline_metrics: BaselineMetrics = Field(default_factory=BaselineMetrics)


class NarrativeClusteringConfig(_StrictBase):
    micro_cluster: MicroClusterConfig = Field(default_factory=MicroClusterConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    hdbscan: HDBSCANConfig = Field(default_factory=HDBSCANConfig)
    orphan: OrphanConfig = Field(default_factory=OrphanConfig)
    evolution: EvolutionConfig = Field(default_factory=EvolutionConfig)
    momentum: MomentumConfig = Field(default_factory=MomentumConfig)
    decay: DecayConfig = Field(default_factory=DecayConfig)
    community: CommunityConfig = Field(default_factory=CommunityConfig)
    theme_clustering: ThemeClusteringConfig = Field(default_factory=ThemeClusteringConfig)
    sparsification: SparsificationConfig = Field(default_factory=SparsificationConfig)
    summary_cache: SummaryCacheConfig = Field(default_factory=SummaryCacheConfig)
    source_diversity: SourceDiversityConfig = Field(default_factory=SourceDiversityConfig)
    community_lineage: CommunityLineageConfig = Field(default_factory=CommunityLineageConfig)
    drift_detection: DriftDetectionConfig = Field(default_factory=DriftDetectionConfig)
    meta: MetaConfig = Field(default_factory=MetaConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "narrative_clustering.yaml"
)

_CACHED_CONFIG: Optional[NarrativeClusteringConfig] = None
_CACHED_PATH: Optional[Path] = None


def load_clustering_config(
    path: Optional[Path] = None,
    force_reload: bool = False,
) -> NarrativeClusteringConfig:
    """Load and validate the narrative clustering config.

    Cached per process: the pipeline is batch and config is immutable per run.
    Pass force_reload=True (or a different path) in tests.

    Missing file → defaults (Pydantic field defaults). Validation errors raise.
    """
    global _CACHED_CONFIG, _CACHED_PATH
    target = Path(path) if path is not None else _DEFAULT_CONFIG_PATH

    if (
        _CACHED_CONFIG is not None
        and not force_reload
        and _CACHED_PATH == target
    ):
        return _CACHED_CONFIG

    if not target.exists():
        logger.warning(
            "Clustering config not found at %s; using Pydantic defaults", target
        )
        _CACHED_CONFIG = NarrativeClusteringConfig()
        _CACHED_PATH = target
        return _CACHED_CONFIG

    with open(target, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    _CACHED_CONFIG = NarrativeClusteringConfig.model_validate(raw)
    _CACHED_PATH = target
    logger.info("Loaded narrative clustering config from %s", target)
    return _CACHED_CONFIG


def reset_cache() -> None:
    """Clear the singleton cache (for tests)."""
    global _CACHED_CONFIG, _CACHED_PATH
    _CACHED_CONFIG = None
    _CACHED_PATH = None
