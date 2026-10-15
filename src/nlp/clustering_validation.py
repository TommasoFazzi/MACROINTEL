"""
Clustering Validation — pure functions for the offline validation study

Stability (seed vs data variance), DBCV, pair-level metrics against a
hand-labelled reference set (with stratified and paired bootstrap), rater
reliability, the pre-registered D7 selection rule, and the report models.

No DB access and no I/O: the study script (`scripts/clustering_validation_study.py`)
feeds these functions from a frozen snapshot.

Spec reference: openspec/changes/clustering-offline-validation-study/
                design.md § D3, D5, D6, D7 and specs/clustering-validation-study/spec.md.

Partitions are dicts {storyline_id: label}. For HDBSCAN, noise is label -1
and is kept in the dict (stability needs it); pair predictions treat -1 and
missing ids alike as "no theme".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Literal, Optional

import numpy as np
from pydantic import BaseModel
from scipy.sparse.csgraph import minimum_spanning_tree
from scipy.spatial.distance import cdist, pdist, squareform
from scipy.special import logsumexp
from sklearn.metrics import adjusted_rand_score

from ..utils.logger import get_logger

logger = get_logger(__name__)

NOISE = -1

# ---------------------------------------------------------------------------
# D7 constants — frozen 2026-10-14 (tasks.md 0.3). Written verbatim into the report.
# ---------------------------------------------------------------------------

STABILITY_MARGIN = 0.05
ARI_FLOOR = 0.60
MAX_NOISE_RATIO = 0.20
MIN_DELTA_F1 = 0.05
N_BOOT = 2000
INCUMBENT_K = 18
MACRO_STRATA = ("S-A", "S-B", "S-C")

D7_CONSTANTS = {
    "STABILITY_MARGIN": STABILITY_MARGIN,
    "ARI_FLOOR": ARI_FLOOR,
    "MAX_NOISE_RATIO": MAX_NOISE_RATIO,
    "MIN_DELTA_F1": MIN_DELTA_F1,
    "N_BOOT": float(N_BOOT),
    "INCUMBENT_K": float(INCUMBENT_K),
}


# ---------------------------------------------------------------------------
# Report models (spec § Data models)
# ---------------------------------------------------------------------------

class ReferenceFit(BaseModel):
    k: int
    random_state: int
    silhouette: float
    inertia: float
    labels: list[int]                     # snapshot id order
    labels_sha256: str                    # after canonical relabelling


class SnapshotManifest(BaseModel):
    snapshot_at: datetime
    git_sha: str
    n_storylines: int
    n_missing_current_embedding: int
    n_missing_summary_vector: int
    n_run_metrics_rows: int
    file_sha256: dict[str, str]
    reference_fit: ReferenceFit
    n_active_themes: int


class StabilityStats(BaseModel):
    mean_ari: Optional[float]
    p10_ari: Optional[float]
    n_pairs: int
    noise_agreement: Optional[float] = None   # HDBSCAN only
    coverage_mean: Optional[float] = None     # HDBSCAN only
    noise_ratio_p90: Optional[float] = None   # HDBSCAN only, across runs


class LabelMetrics(BaseModel):
    stratum: Literal["S-A", "S-B", "S-C", "S-N", "macro"]
    n_pairs: int
    precision: Optional[float]                # None when no `same` predicted
    recall: Optional[float]                   # None when no `same` in the gold labels
    f1: Optional[float]
    f1_ci95: Optional[tuple[float, float]]


class PairedDelta(BaseModel):
    a: str
    b: str
    delta_macro_f1: float                     # a - b, point estimate
    ci95: tuple[float, float]
    purpose: Literal["tie_within", "cross_algorithm"]


class ConfigResult(BaseModel):
    algorithm: Literal["kmeans", "hdbscan_umap", "louvain_live"]
    params: dict[str, int]
    n_clusters: int
    size_distribution: list[int]
    silhouette_cosine: Optional[float] = None
    inertia: Optional[float] = None
    dbcv: Optional[float] = None
    noise_ratio: Optional[float] = None
    coherence_med: Optional[float]
    seed_stability: Optional[StabilityStats]
    data_stability: Optional[StabilityStats]
    labelled: list[LabelMetrics]
    eligible: bool
    ineligible_reasons: list[str] = []


class StudyReport(BaseModel):
    run_id: str
    created_at: datetime
    manifest: SnapshotManifest
    library_versions: dict[str, str]
    constants: dict[str, float]
    label_kappa: Optional[float]
    label_raw_agreement: Optional[float]
    results: list[ConfigResult]
    deltas: list[PairedDelta]
    selected: dict[str, dict[str, int]]
    verdict: Literal["kmeans", "hdbscan_umap", "inconclusive"]
    reference_fit_check: dict[str, float]
    palette_overflow: dict[str, float]
    notes: list[str] = []                 # e.g. labels missing, reliability gate failed


# ---------------------------------------------------------------------------
# Stability (design.md D3)
# ---------------------------------------------------------------------------

def _summary(values: list[float]) -> tuple[Optional[float], Optional[float]]:
    if not values:
        return None, None
    arr = np.asarray(values, dtype=float)
    return float(arr.mean()), float(np.percentile(arr, 10))


def pairwise_ari_on_intersection(partitions: list[dict]) -> StabilityStats:
    """Mean and 10th-percentile ARI over all run pairs, each pair compared on
    the ids both runs labelled. Pairs sharing fewer than 2 ids are skipped."""
    aris = []
    for pa, pb in combinations(partitions, 2):
        common = sorted(set(pa) & set(pb), key=str)
        if len(common) < 2:
            continue
        aris.append(adjusted_rand_score([pa[i] for i in common], [pb[i] for i in common]))
    mean, p10 = _summary(aris)
    return StabilityStats(mean_ari=mean, p10_ari=p10, n_pairs=len(aris))


def noise_ratio(partition: dict) -> float:
    if not partition:
        return 0.0
    return sum(1 for v in partition.values() if v == NOISE) / len(partition)


def pairwise_ari_hdbscan(partitions: list[dict]) -> StabilityStats:
    """HDBSCAN variant: ARI only on ids that are non-noise in both runs, plus
    noise agreement (Jaccard of the noise sets on the shared ids), mean
    coverage and the 90th percentile of the per-run noise ratio (design.md D3).

    A configuration that calls most points noise cannot look stable here
    without also showing low coverage and a high noise ratio."""
    aris, jaccards = [], []
    for pa, pb in combinations(partitions, 2):
        common = set(pa) & set(pb)
        core = sorted((i for i in common if pa[i] != NOISE and pb[i] != NOISE), key=str)
        if len(core) >= 2:
            aris.append(adjusted_rand_score([pa[i] for i in core], [pb[i] for i in core]))
        noise_a = {i for i in common if pa[i] == NOISE}
        noise_b = {i for i in common if pb[i] == NOISE}
        union = noise_a | noise_b
        jaccards.append(len(noise_a & noise_b) / len(union) if union else 1.0)
    mean, p10 = _summary(aris)
    ratios = [noise_ratio(p) for p in partitions]
    return StabilityStats(
        mean_ari=mean,
        p10_ari=p10,
        n_pairs=len(aris),
        noise_agreement=float(np.mean(jaccards)) if jaccards else None,
        coverage_mean=float(1.0 - np.mean(ratios)) if ratios else None,
        noise_ratio_p90=float(np.percentile(ratios, 90)) if ratios else None,
    )


# ---------------------------------------------------------------------------
# DBCV (design.md D5) — descriptive only, never an input to D7
# ---------------------------------------------------------------------------

def _all_points_core_distance(dist: np.ndarray, dim: int) -> np.ndarray:
    """core(i) = (sum_{j: d_ij > 0} d_ij^-dim / (n - 1)) ^ (-1/dim), in log space.

    Same definition as hdbscan.validity.all_points_core_distance (zero
    distances are left out of the sum but counted in n - 1), computed with
    logsumexp so that d_ij^-dim cannot overflow at dim = 10."""
    n = dist.shape[0]
    if n < 2:
        return np.zeros(n)
    with np.errstate(divide="ignore"):
        log_terms = np.where(dist > 0, -dim * np.log(dist), -np.inf)
    log_sum = logsumexp(log_terms, axis=1) - np.log(n - 1)
    core = np.exp(-log_sum / dim)
    core[~np.isfinite(core)] = 0.0  # a row of only zero distances (all duplicates)
    return core


def _internal_mst(mr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """MST over mutual-reachability distances; returns (internal node indices,
    weights of edges between internal nodes). Mirrors
    hdbscan.validity.internal_minimum_spanning_tree: internal nodes have MST
    degree > 1; if there are none, node 0 stands in; if no edge joins two
    internal nodes, all MST edges are used."""
    n = mr.shape[0]
    if n < 2:
        return np.array([0]), np.array([0.0])
    tree = minimum_spanning_tree(mr).tocoo()
    rows, cols, w = tree.row, tree.col, tree.data
    degree = np.bincount(np.concatenate([rows, cols]), minlength=n)
    internal = np.flatnonzero(degree > 1)
    if internal.size == 0:
        internal = np.array([0])
    both = np.isin(rows, internal) & np.isin(cols, internal)
    return internal, (w[both] if both.any() else w)


def dbcv(X: np.ndarray, labels: np.ndarray) -> Optional[float]:
    """Density-Based Clustering Validation (Moulavi et al. 2014), following
    hdbscan.validity.validity_index where the paper leaves choices open:
    the dimensional exponent is X.shape[1]; noise (-1) is excluded from every
    cluster but counts in the size-weighted denominator.

    Exact duplicate rows are dropped first (logged). Returns None when fewer
    than 2 non-noise clusters exist. Result lies in [-1, 1].
    """
    X = np.asarray(X, dtype=np.float64)
    labels = np.asarray(labels)
    _, keep = np.unique(X, axis=0, return_index=True)
    if keep.size < X.shape[0]:
        logger.info("dbcv: dropped %d duplicate rows", X.shape[0] - keep.size)
        keep = np.sort(keep)
        X, labels = X[keep], labels[keep]

    cluster_ids = [c for c in np.unique(labels) if c != NOISE]
    if len(cluster_ids) < 2:
        return None

    dim = X.shape[1]
    members, core, internal, sparseness = {}, {}, {}, {}
    for c in cluster_ids:
        Xc = X[labels == c]
        dist = squareform(pdist(Xc)) if len(Xc) > 1 else np.zeros((1, 1))
        core[c] = _all_points_core_distance(dist, dim)
        mr = np.maximum(dist, np.maximum.outer(core[c], core[c]))
        np.fill_diagonal(mr, 0.0)
        internal[c], edges = _internal_mst(mr)
        sparseness[c] = float(edges.max()) if edges.size else 0.0
        members[c] = Xc

    separation = {c: np.inf for c in cluster_ids}
    for a, b in combinations(cluster_ids, 2):
        ia, ib = internal[a], internal[b]
        d = cdist(members[a][ia], members[b][ib])
        mr = np.maximum(d, np.maximum.outer(core[a][ia], core[b][ib]))
        sep = float(mr.min())
        separation[a] = min(separation[a], sep)
        separation[b] = min(separation[b], sep)

    n_total = float(X.shape[0])
    score = 0.0
    for c in cluster_ids:
        denom = max(separation[c], sparseness[c])
        vc = (separation[c] - sparseness[c]) / denom if denom > 0 else 0.0
        score += (members[c].shape[0] / n_total) * vc
    return float(score)


# ---------------------------------------------------------------------------
# Pair metrics against the labelled reference set (design.md D6)
# ---------------------------------------------------------------------------

@dataclass
class LabelledPairs:
    """Scored pairs (`unsure` already removed). gold[i] is True for `same`."""
    a: list
    b: list
    stratum: list[str]
    gold: np.ndarray

    def __post_init__(self):
        self.gold = np.asarray(self.gold, dtype=bool)
        self.stratum = list(self.stratum)


def predict_same(partition: dict, pairs: LabelledPairs) -> np.ndarray:
    """True where both storylines carry the same non-noise label. Noise and
    missing ids predict `different` (design.md D6)."""
    out = np.zeros(len(pairs.a), dtype=bool)
    for i, (x, y) in enumerate(zip(pairs.a, pairs.b)):
        lx, ly = partition.get(x, NOISE), partition.get(y, NOISE)
        out[i] = lx != NOISE and lx == ly
    return out


def _counts(pred: np.ndarray, gold: np.ndarray) -> tuple[int, int, int]:
    tp = int(np.sum(pred & gold))
    fp = int(np.sum(pred & ~gold))
    fn = int(np.sum(~pred & gold))
    return tp, fp, fn


def _f1(tp: int, fp: int, fn: int) -> Optional[float]:
    denom = 2 * tp + fp + fn
    return 2 * tp / denom if denom else None


def _stratum_index(pairs: LabelledPairs) -> dict[str, np.ndarray]:
    strata = np.asarray(pairs.stratum)
    return {s: np.flatnonzero(strata == s) for s in dict.fromkeys(pairs.stratum)}


def _macro(f1_by_stratum: dict[str, Optional[float]]) -> Optional[float]:
    vals = [f1_by_stratum.get(s) for s in MACRO_STRATA]
    if any(v is None for v in vals):
        return None
    return float(np.mean(vals))


def point_metrics(pred: np.ndarray, pairs: LabelledPairs) -> dict[str, dict]:
    """Per-stratum precision/recall/F1 plus macro F1 over MACRO_STRATA."""
    out = {}
    for s, idx in _stratum_index(pairs).items():
        tp, fp, fn = _counts(pred[idx], pairs.gold[idx])
        out[s] = {
            "n_pairs": int(idx.size),
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "f1": _f1(tp, fp, fn),
        }
    out["macro"] = {
        "n_pairs": int(sum(out[s]["n_pairs"] for s in MACRO_STRATA if s in out)),
        "precision": None,
        "recall": None,
        "f1": _macro({s: out.get(s, {}).get("f1") for s in MACRO_STRATA}),
    }
    return out


def bootstrap_indices(pairs: LabelledPairs, n_boot: int = N_BOOT, seed: int = 0) -> list[dict[str, np.ndarray]]:
    """Stratified resamples, drawn once and shared by every configuration so
    that differences between configurations are paired (design.md D6)."""
    rng = np.random.default_rng(seed)
    strata = _stratum_index(pairs)
    return [
        {s: rng.choice(idx, size=idx.size, replace=True) for s, idx in strata.items()}
        for _ in range(n_boot)
    ]


def bootstrap_f1(pred: np.ndarray, pairs: LabelledPairs, resamples: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    """Per-stratum and macro F1 for every resample (NaN where undefined)."""
    keys = list(resamples[0]) if resamples else []
    out = {s: np.full(len(resamples), np.nan) for s in keys + ["macro"]}
    for r, sample in enumerate(resamples):
        f1s = {}
        for s, idx in sample.items():
            f1s[s] = _f1(*_counts(pred[idx], pairs.gold[idx]))
            out[s][r] = np.nan if f1s[s] is None else f1s[s]
        m = _macro(f1s)
        out["macro"][r] = np.nan if m is None else m
    return out


def ci95(samples: np.ndarray) -> Optional[tuple[float, float]]:
    finite = samples[np.isfinite(samples)]
    if finite.size == 0:
        return None
    lo, hi = np.percentile(finite, [2.5, 97.5])
    return float(lo), float(hi)


def label_metrics(pred: np.ndarray, pairs: LabelledPairs, boot: dict[str, np.ndarray]) -> list[LabelMetrics]:
    point = point_metrics(pred, pairs)
    return [
        LabelMetrics(stratum=s, f1_ci95=ci95(boot[s]) if s in boot else None, **m)
        for s, m in point.items()
    ]


# ---------------------------------------------------------------------------
# Pair sampling (design.md D6)
# ---------------------------------------------------------------------------

STRATUM_SIZES = {"S-C": 150, "S-N": 10, "S-A": 50, "S-B": 50}
N_REPEATS = 30
MIN_REPEAT_GAP = 60


def _canon(x, y) -> tuple:
    return (x, y) if str(x) < str(y) else (y, x)


def _within_group_pairs(partition: dict, keep) -> list[tuple]:
    """All id pairs sharing a label in `partition` (noise excluded) for which keep(a, b) holds."""
    groups: dict = {}
    for sid, lab in partition.items():
        if lab != NOISE:
            groups.setdefault(lab, []).append(sid)
    return [_canon(a, b) for members in groups.values()
            for a, b in combinations(sorted(members, key=str), 2) if keep(a, b)]


def _draw(rng, candidates: list[tuple], n: int, taken: set) -> list[tuple]:
    pool = sorted(set(candidates) - taken, key=lambda p: (str(p[0]), str(p[1])))
    if len(pool) < n:
        logger.warning("pair sampler: only %d candidates for %d requested", len(pool), n)
    picked = [pool[i] for i in rng.choice(len(pool), size=min(n, len(pool)), replace=False)] if pool else []
    taken.update(picked)
    return picked


def sample_pairs(
    kmeans: dict,
    hdbscan: dict,
    entities: dict,
    summary_vectors: dict,
    seed: int,
    sizes: dict = STRATUM_SIZES,
) -> list[dict]:
    """Draw the stratified reference pairs, in order S-C, S-N, S-A, S-B, each
    excluding pairs already drawn.

    kmeans / hdbscan: {id: label} on the full snapshot (hdbscan noise = -1).
    entities: {id: list of key_entities}. summary_vectors: {id: ndarray}.
    Returns [{"a", "b", "stratum", "direction"}]; direction is set for S-C only.
    """
    rng = np.random.default_rng(seed)
    taken: set = set()
    out: list[dict] = []

    def hd(sid):
        return hdbscan.get(sid, NOISE)

    # S-C: disagreement with both storylines non-noise, balanced by direction
    km_same = _within_group_pairs(
        kmeans, lambda a, b: hd(a) != NOISE and hd(b) != NOISE and hd(a) != hd(b))
    hd_same = _within_group_pairs(
        {s: lab for s, lab in hdbscan.items() if s in kmeans},
        lambda a, b: kmeans[a] != kmeans[b])
    half = sizes["S-C"] // 2
    for direction, cands, n in (("kmeans_same", km_same, half),
                                ("hdbscan_same", hd_same, sizes["S-C"] - half)):
        out += [{"a": a, "b": b, "stratum": "S-C", "direction": direction}
                for a, b in _draw(rng, cands, n, taken)]

    # S-N: same k-means cluster, exactly one storyline is HDBSCAN noise
    noise_pairs = _within_group_pairs(kmeans, lambda a, b: (hd(a) == NOISE) != (hd(b) == NOISE))
    out += [{"a": a, "b": b, "stratum": "S-N", "direction": None}
            for a, b in _draw(rng, noise_pairs, sizes["S-N"], taken)]

    # S-A: at least 2 shared key_entities (case-insensitive), independent of embeddings
    by_entity: dict = {}
    for sid, ents in entities.items():
        for e in {str(x).lower() for x in ents or []}:
            by_entity.setdefault(e, []).append(sid)
    shared: dict = {}
    for members in by_entity.values():
        for a, b in combinations(sorted(members, key=str), 2):
            key = _canon(a, b)
            shared[key] = shared.get(key, 0) + 1
    out += [{"a": a, "b": b, "stratum": "S-A", "direction": None}
            for a, b in _draw(rng, [p for p, c in shared.items() if c >= 2], sizes["S-A"], taken)]

    # S-B: top-3 summary_vector neighbours of random anchors (space C, not current_embedding)
    ids = sorted(summary_vectors, key=str)
    M = np.stack([summary_vectors[i] for i in ids]).astype(np.float64)
    M /= np.maximum(np.linalg.norm(M, axis=1, keepdims=True), 1e-10)
    picked_b = []
    for anchor in rng.permutation(len(ids)):
        if len(picked_b) >= sizes["S-B"]:
            break
        sims = M @ M[anchor]
        sims[anchor] = -np.inf
        top3 = np.argpartition(-sims, 3)[:3]
        pair = _canon(ids[anchor], ids[int(rng.choice(top3))])
        if pair not in taken:
            taken.add(pair)
            picked_b.append(pair)
    out += [{"a": a, "b": b, "stratum": "S-B", "direction": None} for a, b in picked_b]
    return out


def labelling_order(pairs: list[dict], seed: int, n_repeats: int = N_REPEATS,
                    min_gap: int = MIN_REPEAT_GAP) -> list[dict]:
    """Shuffle the pairs into rows and add repeats for the reliability check.

    Repeats sit in the second half of the file, each at least `min_gap` rows
    after its first occurrence: they are drawn from the earliest rows and
    shuffled only among the second half. Left/right order is randomised per row.
    """
    rng = np.random.default_rng(seed)
    order = [dict(pairs[i]) for i in rng.permutation(len(pairs))]
    total = len(order) + n_repeats
    half = total // 2
    eligible = [i for i in range(min(half, len(order))) if i + min_gap <= half]
    rep_idx = sorted(rng.choice(eligible, size=min(n_repeats, len(eligible)), replace=False).tolist())
    second = order[half:] + [dict(order[i], repeat_of=i) for i in rep_idx]
    second = [second[i] for i in rng.permutation(len(second))]
    rows = order[:half] + second
    # Rows before `half` keep their position, so a repeat's `repeat_of` index is
    # already its first occurrence's row_id.
    for row_id, row in enumerate(rows):
        row["row_id"] = row_id
        row.setdefault("repeat_of", None)
        if row["repeat_of"] is None:
            row["swap"] = bool(rng.random() < 0.5)
        else:
            # a repeat shows the opposite left/right order of its first occurrence
            row["swap"] = not rows[row["repeat_of"]]["swap"]
    return rows


def load_labelled_pairs(key_rows: list[dict], labels: dict) -> tuple[LabelledPairs, list[str], list[str]]:
    """Join the sampler key with the user's labels.

    key_rows: rows from the key file (row_id, a, b, stratum, repeat_of).
    labels: {row_id: "same" | "different" | "unsure"}.
    Returns (scored pairs without `unsure` or repeats, first labels, repeat labels)
    for the reliability gate.
    """
    allowed = {"same", "different", "unsure"}
    bad = {r: v for r, v in labels.items() if v not in allowed}
    if bad:
        raise ValueError(f"unexpected labels: {bad}")
    a, b, strata, gold, first, second = [], [], [], [], [], []
    for row in key_rows:
        lab = labels.get(row["row_id"])
        if lab is None:
            raise ValueError(f"row {row['row_id']} is not labelled")
        if row["repeat_of"] is not None:
            first.append(labels[row["repeat_of"]])
            second.append(lab)
        elif lab != "unsure":
            a.append(row["a"])
            b.append(row["b"])
            strata.append(row["stratum"])
            gold.append(lab == "same")
    return LabelledPairs(a=a, b=b, stratum=strata, gold=gold), first, second


# ---------------------------------------------------------------------------
# Rater reliability (design.md D6)
# ---------------------------------------------------------------------------

def rater_agreement(first: list[str], second: list[str]) -> tuple[float, float]:
    """(Cohen's kappa, raw agreement) between two labellings of the same pairs."""
    if len(first) != len(second) or not first:
        raise ValueError("rater_agreement needs two non-empty lists of equal length")
    a, b = np.asarray(first), np.asarray(second)
    p_o = float(np.mean(a == b))
    cats = np.union1d(a, b)
    p_e = float(sum(np.mean(a == c) * np.mean(b == c) for c in cats))
    if p_e >= 1.0:
        kappa = 1.0 if p_o == 1.0 else 0.0
    else:
        kappa = (p_o - p_e) / (1.0 - p_e)
    return kappa, p_o


# ---------------------------------------------------------------------------
# Pre-registered selection rule (design.md D7)
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    key: str                               # e.g. "kmeans:k=18"
    algorithm: Literal["kmeans", "hdbscan_umap"]
    params: dict[str, int]
    seed_ari: Optional[float]
    data_ari: Optional[float]
    macro_f1: Optional[float]              # point estimate
    boot_macro_f1: np.ndarray              # shared paired resamples
    noise_full: Optional[float] = None     # HDBSCAN only
    noise_p90: Optional[float] = None      # HDBSCAN only
    eligible: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class Decision:
    verdict: Literal["kmeans", "hdbscan_umap", "inconclusive"]
    selected: dict[str, Candidate]
    deltas: list[PairedDelta]
    tie_sets: dict[str, list[str]]


def _delta(a: Candidate, b: Candidate, purpose) -> PairedDelta:
    diff = a.boot_macro_f1 - b.boot_macro_f1
    interval = ci95(diff) or (float("nan"), float("nan"))
    point = (a.macro_f1 or 0.0) - (b.macro_f1 or 0.0)
    return PairedDelta(a=a.key, b=b.key, delta_macro_f1=float(point), ci95=interval, purpose=purpose)


def apply_eligibility(cands: list[Candidate]) -> None:
    """Noise gate first (HDBSCAN), then stability bars relative to the best
    configuration among those that passed it, with ARI_FLOOR as a minimum."""
    passing = []
    for c in cands:
        c.reasons = []
        if c.algorithm == "hdbscan_umap":
            if c.noise_full is None or c.noise_full > MAX_NOISE_RATIO:
                c.reasons.append(f"noise_full {c.noise_full} > {MAX_NOISE_RATIO}")
            if c.noise_p90 is None or c.noise_p90 > MAX_NOISE_RATIO:
                c.reasons.append(f"noise_p90 {c.noise_p90} > {MAX_NOISE_RATIO}")
        if not c.reasons:
            passing.append(c)

    def bar(attr: str) -> float:
        vals = [getattr(c, attr) for c in passing if getattr(c, attr) is not None]
        return max(ARI_FLOOR, max(vals) - STABILITY_MARGIN) if vals else ARI_FLOOR

    data_bar, seed_bar = bar("data_ari"), bar("seed_ari")
    for c in passing:
        if c.data_ari is None or c.data_ari < data_bar:
            c.reasons.append(f"data_ari {c.data_ari} < {data_bar:.3f}")
        if c.seed_ari is None or c.seed_ari < seed_bar:
            c.reasons.append(f"seed_ari {c.seed_ari} < {seed_bar:.3f}")
    for c in cands:
        c.eligible = not c.reasons


def _select(cands: list[Candidate], deltas: list[PairedDelta]) -> tuple[Optional[Candidate], list[str]]:
    eligible = [c for c in cands if c.eligible and c.macro_f1 is not None]
    if not eligible:
        return None, []
    best = max(eligible, key=lambda c: c.macro_f1)
    tied = [best]
    for c in eligible:
        if c is best:
            continue
        d = _delta(best, c, "tie_within")
        deltas.append(d)
        if d.ci95[0] <= 0.0 <= d.ci95[1]:
            tied.append(c)
    incumbent = [c for c in tied if c.algorithm == "kmeans" and c.params.get("k") == INCUMBENT_K]
    chosen = incumbent[0] if incumbent else max(tied, key=lambda c: (c.data_ari or 0.0))
    return chosen, [c.key for c in tied]


def _switch_justified(h: Candidate, k: Candidate, deltas: list[PairedDelta]) -> bool:
    d = _delta(h, k, "cross_algorithm")
    deltas.append(d)
    return d.ci95[0] > 0.0 and d.delta_macro_f1 >= MIN_DELTA_F1


def decide(candidates: list[Candidate]) -> Decision:
    """Apply D7 mechanically: eligibility, ties, selection and the verdict table."""
    deltas: list[PairedDelta] = []
    by_algo = {a: [c for c in candidates if c.algorithm == a] for a in ("kmeans", "hdbscan_umap")}
    for cands in by_algo.values():
        apply_eligibility(cands)

    selected, tie_sets = {}, {}
    for algo, cands in by_algo.items():
        chosen, ties = _select(cands, deltas)
        if chosen is not None:
            selected[algo] = chosen
            tie_sets[algo] = ties

    k_sel, h_sel = selected.get("kmeans"), selected.get("hdbscan_umap")
    if k_sel and h_sel:
        verdict = "hdbscan_umap" if _switch_justified(h_sel, k_sel, deltas) else "kmeans"
    elif k_sel:
        verdict = "kmeans"
    elif h_sel:
        scored = [c for c in by_algo["kmeans"] if c.macro_f1 is not None]
        k_ref = max(scored, key=lambda c: c.macro_f1) if scored else None
        verdict = "hdbscan_umap" if k_ref and _switch_justified(h_sel, k_ref, deltas) else "inconclusive"
    else:
        verdict = "inconclusive"
    return Decision(verdict=verdict, selected=selected, deltas=deltas, tie_sets=tie_sets)
