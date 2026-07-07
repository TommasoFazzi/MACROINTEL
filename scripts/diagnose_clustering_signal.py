#!/usr/bin/env python3
"""
Clustering Signal Diagnostics — read-only, offline diagnostic bench.

Implements the experimental protocol (S1–S7) of OpenSpec change
`redesign-narrative-clustering-signal` (design.md § Protocollo sperimentale).
The tool characterises BOTH layers of the narrative pipeline on a single DB
snapshot and NEVER writes to the database:

  Lane COMMUNITY (a valle)
    S1 Hubness            — Zipf structure of key_entities
    S2 Separazione        — silhouette(live community_id) vs k-means-on-embedding
    S3 Anisotropia        — silhouette delta after all-but-the-top whitening
    S4 Accordo            — co-association of the 4 shadow partitions (EAC consensus,
                            max-lifetime cut, cross-space ARI/NMI vs k-means)
  Lane STORYLINE (a monte, fix B)
    S5 Frammentazione     — singleton-ratio, size distribution
    S6 Match-replay       — best-match score distribution; dip test → KDE antimode τ*
    S7 Coerenza (triangolo) — non-circular merge-precision proxy in space C
                             (summary_vector) with a label-free reference set

Every number maps to a decision (design.md § Tabella di decisione). Validation is
proxy-only: silhouette is flagged in-space (circular); the honest guardrail is the
triangle stability / separation / fragmentation(recall+precision).

Usage (from repo root):
    python scripts/diagnose_clustering_signal.py                 # JSON + figures in artifacts/
    python scripts/diagnose_clustering_signal.py --no-viz        # JSON only
    python scripts/diagnose_clustering_signal.py --sample-size 8000
    # On PROD (numbers autoritativi):
    #   docker compose -p app exec backend python scripts/diagnose_clustering_signal.py --no-viz

Determinism: fixed seeds everywhere (Louvain random_state=42, Leiden seed=42,
KMeans/PCA random_state=42, sampling seeded). Two runs on the same snapshot →
same numbers. Optional deps degrade cleanly: `diptest` (S6 bimodality),
`matplotlib` (figures), `umap-learn` (2D projection → PCA fallback).
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone
from collections import defaultdict, Counter
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / '.env')

import numpy as np
from pydantic import BaseModel

from sklearn.metrics import (
    silhouette_score,
    normalized_mutual_info_score,
    adjusted_rand_score,
)
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA

from scipy.stats import gaussian_kde, pearsonr
from scipy.spatial.distance import squareform
from scipy.cluster.hierarchy import linkage, fcluster

import networkx as nx
import community as community_louvain  # python-louvain

from src.storage.database import DatabaseManager
from src.nlp.config import load_clustering_config
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---- optional dependencies (graceful degradation) ----
try:
    import igraph as ig
    import leidenalg
    LEIDEN_AVAILABLE = True
except ImportError:
    LEIDEN_AVAILABLE = False

try:
    import diptest
    DIPTEST_AVAILABLE = True
except ImportError:
    DIPTEST_AVAILABLE = False

try:
    import matplotlib
    matplotlib.use("Agg")  # headless / CPU-only
    import matplotlib.pyplot as plt
    MPL_AVAILABLE = True
except ImportError:
    MPL_AVAILABLE = False

ACTIVE_STATUSES = ('emerging', 'active', 'stabilized')
KMEANS_KS = (10, 18, 30, 50)
LIVE_THRESHOLD = None  # filled from config.matching.threshold
SEED = 42


# =============================================================================
# Pydantic report schema (spec § Schema esteso del report)
# =============================================================================
class HubnessStats(BaseModel):
    n_active_storylines: int
    n_distinct_entities: int
    pct_entities_df1: float
    median_doc_freq: float
    max_doc_freq: int
    top_hubs: list[tuple[str, int, float]]
    pct_edges_from_top20_entities: float


class SignalAlignment(BaseModel):
    silhouette_raw: float | None             # stored community_id (often NULL on prod)
    silhouette_graph_louvain: float | None   # fresh Louvain on the graph = space-A baseline
    silhouette_whitened: float | None
    silhouette_delta: float | None
    silhouette_kmeans: dict[int, float] | None
    kmeans_best_k: int | None
    jaccard_cosine_pearson: float | None
    n_pairs_sampled: int


class ConsensusAgreement(BaseModel):
    n_methods: int
    method_names: list[str]
    agreement_hist: dict[str, float]
    pct_pairs_fuzzy: float
    consensus_lifetime_k: int | None
    consensus_singleton_ratio: float | None
    ari_consensus_kmeans: float | None
    nmi_consensus_kmeans: float | None
    no_natural_scale: bool


class FragmentationStats(BaseModel):
    n_active_storylines: int
    singleton_ratio: float
    articles_per_storyline_median: float
    articles_per_storyline_p90: float
    pct_archived_single: float | None
    max_size_ratio: float


class MatchReplay(BaseModel):
    formula: str
    best_score_hist: dict[str, float]
    bimodality_method: str            # "hartigan_dip" | "kde_prominence" | "insufficient_n" …
    bimodality_stat: float | None     # p-value (hartigan) or antimode prominence (kde)
    is_bimodal: bool | None
    bimodal_boost: bool | None        # bimodality of the entity_boost sub-distribution
    bimodal_no_boost: bool | None
    bimodality_is_artifact: bool | None
    tau_star: float | None
    kde_bandwidth: float | None
    pct_band_tau_075: float | None
    n_confirmed_ge_075: int
    n_band: int


class CoherenceValidation(BaseModel):
    reference_set: str
    space_used: str
    coh_c_confirmed_median: float | None
    coh_c_confirmed_std: float | None
    coh_c_band_median: float | None
    merge_precision_proxy: float | None
    temporal_unimodal_fraction: float | None


class ClusteringDiagnosticsReport(BaseModel):
    snapshot_at: str
    run_id: str
    partition_source: str
    n_active_storylines: int
    hubness: HubnessStats | None = None
    alignment: SignalAlignment | None = None
    consensus: ConsensusAgreement | None = None
    fragmentation: FragmentationStats | None = None
    match_replay: MatchReplay | None = None
    coherence: CoherenceValidation | None = None
    interpretation: str = ""
    warnings: list[str] = []


# =============================================================================
# Low-level helpers
# =============================================================================
def _vec_to_array(value):
    """pgvector return value → float32 numpy array (handles str / list / iterable / None)."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().strip('[]')
        if not s:
            return None
        try:
            return np.asarray([float(x) for x in s.split(',')], dtype=np.float32)
        except ValueError:
            return None
    if hasattr(value, 'tolist'):
        value = value.tolist()
    elif not isinstance(value, list):
        try:
            value = list(value)
        except TypeError:
            return None
    return np.asarray(value, dtype=np.float32) if value else None


def _parse_entities(raw) -> set:
    """key_entities jsonb → set of lowercased entity strings (handles list[str]/list[dict]/dict)."""
    if raw is None:
        return set()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return set()
    out = []
    if isinstance(raw, dict):
        raw = raw.get('entities', list(raw.keys()))
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, str):
                out.append(e.strip().lower())
            elif isinstance(e, dict):
                v = e.get('name') or e.get('text') or e.get('entity')
                if v:
                    out.append(str(v).strip().lower())
    return {x for x in out if x}


def _l2norm(X):
    n = np.linalg.norm(X, axis=1, keepdims=True)
    n[n == 0] = 1e-10
    return X / n


def _jaccard_matrix(entity_sets: list[set]) -> np.ndarray:
    """Vectorised pairwise entity Jaccard via a binary incidence matrix."""
    vocab: dict = {}
    for ents in entity_sets:
        for e in ents:
            if e not in vocab:
                vocab[e] = len(vocab)
    n = len(entity_sets)
    if not vocab:
        return np.zeros((n, n), dtype=np.float32)
    E = np.zeros((n, len(vocab)), dtype=np.float32)
    for i, ents in enumerate(entity_sets):
        for e in ents:
            E[i, vocab[e]] = 1.0
    inter = E @ E.T
    sizes = E.sum(axis=1)
    union = sizes[:, None] + sizes[None, :] - inter
    with np.errstate(divide='ignore', invalid='ignore'):
        J = np.where(union > 0, inter / union, 0.0)
    return J.astype(np.float32)


# =============================================================================
# Partition helpers (S4) — self-contained, mirror compute_communities.py
# =============================================================================
def _disparity_backbone(edges, alpha=0.3, fallback=0.10):
    """Serrano-Boguñá-Vespignani (2009) disparity filter backbone.
    Keep edge (i,j) if a_ij=(1-w/s_i)^(k_i-1) < alpha from EITHER endpoint (union).
    Degree-1 nodes kept unconditionally. Falls back to weight>=fallback if empty."""
    incident = defaultdict(list)
    for s, t, w in edges:
        incident[s].append((t, w))
        incident[t].append((s, w))
    keep = set()
    for i, nbrs in incident.items():
        k = len(nbrs)
        s_i = sum(w for _, w in nbrs)
        if k == 1:
            j, _ = nbrs[0]
            keep.add(frozenset((i, j)))
            continue
        if s_i <= 0:
            continue
        for j, w in nbrs:
            p = w / s_i
            if (1.0 - p) ** (k - 1) < alpha:
                keep.add(frozenset((i, j)))
    backbone = [(s, t, w) for s, t, w in edges if frozenset((s, t)) in keep]
    if not backbone:
        backbone = [(s, t, w) for s, t, w in edges if w >= fallback]
    return backbone


def _louvain(all_ids, edges, resolution) -> dict:
    G = nx.Graph()
    G.add_nodes_from(all_ids)
    for s, t, w in edges:
        G.add_edge(s, t, weight=w)
    return community_louvain.best_partition(
        G, random_state=SEED, weight='weight', resolution=resolution
    )


def _leiden(all_ids, edges, gamma) -> dict:
    g = ig.Graph()
    g.add_vertices([str(x) for x in all_ids])
    idx = {str(x): i for i, x in enumerate(all_ids)}
    ig_edges, weights = [], []
    for s, t, w in edges:
        ss, ts = str(s), str(t)
        if ss in idx and ts in idx:
            ig_edges.append((idx[ss], idx[ts]))
            weights.append(w)
    if ig_edges:
        g.add_edges(ig_edges)
        g.es['weight'] = weights
    part = leidenalg.find_partition(
        g, leidenalg.CPMVertexPartition,
        weights=(weights or None), resolution_parameter=gamma, seed=SEED,
    )
    names = g.vs['name']
    return {int(names[i]): cid for i, cid in enumerate(part.membership)}


def _singleton_ratio(labels: np.ndarray) -> float:
    freq = Counter(labels.tolist())
    return sum(1 for c in freq.values() if c == 1) / max(len(labels), 1)


def _lifetime_cut(C: np.ndarray, k_min=5, k_max=100):
    """EAC consensus cut at maximum lifetime (Fred & Jain / MCP vertical gaps).
    Returns (labels, k, no_natural_scale). Metric-free (keeps stability leg
    independent of the separation/silhouette leg)."""
    N = C.shape[0]
    if N < k_min + 2:
        return None, None, True
    D = 1.0 - C
    np.fill_diagonal(D, 0.0)
    D = (D + D.T) / 2.0  # enforce symmetry for squareform
    Z = linkage(squareform(D, checks=False), method='average')
    heights = Z[:, 2]
    gaps = []
    for m in range(len(heights) - 1):
        clusters = N - (m + 1)
        if k_min <= clusters <= k_max:
            gaps.append((heights[m + 1] - heights[m], m, clusters))
    if not gaps:
        return None, None, True
    gaps.sort(reverse=True)
    max_gap, m_star, k_star = gaps[0]
    median_gap = float(np.median([g for g, _, _ in gaps]))
    no_natural = (max_gap < 2.0 * median_gap) if median_gap > 0 else True
    cut_h = (heights[m_star] + heights[m_star + 1]) / 2.0
    labels = fcluster(Z, t=cut_h, criterion='distance')
    return labels, int(k_star), bool(no_natural)


def _bimodality(x):
    """Return (is_bimodal, method, stat, tau_star, bandwidth).

    Prefers Hartigan dip (diptest) when installed; otherwise a pure-numpy KDE
    prominence test (Silverman bandwidth) — no C-extension / CMake needed, so it
    runs inside the prod backend container without installing anything. `stat` =
    dip p-value (hartigan) or antimode prominence ∈[0,1] (kde). tau_star = the
    antimode (natural merge threshold)."""
    x = np.asarray(x, dtype=float)
    if len(x) < 10:
        return None, "insufficient_n", None, None, None
    if DIPTEST_AVAILABLE:
        try:
            _, pval = diptest.diptest(x)
            is_bi = pval < 0.05
            tau, bw = (_kde_antimode(x, lo=float(x.min()), hi=float(x.max()))
                       if is_bi else (None, None))
            return bool(is_bi), "hartigan_dip", float(pval), tau, bw
        except Exception as e:
            logger.debug("diptest failed, falling back to KDE prominence: %s", e)
    try:
        kde = gaussian_kde(x, bw_method='silverman')
    except Exception:
        return None, "kde_failed", None, None, None
    lo, hi = float(x.min()), float(x.max())
    if hi <= lo:
        return None, "degenerate", None, None, None
    grid = np.linspace(lo, hi, 512)
    d = kde(grid)
    bandwidth = float(kde.factor * np.std(x))
    peaks = [i for i in range(1, len(d) - 1) if d[i] >= d[i - 1] and d[i] > d[i + 1]]
    if len(peaks) < 2:
        return False, "kde_prominence", 0.0, None, bandwidth
    a, b = sorted(sorted(peaks, key=lambda i: d[i], reverse=True)[:2])
    valley = a + int(np.argmin(d[a:b + 1]))
    peak_lo = float(min(d[a], d[b]))
    prominence = float((peak_lo - d[valley]) / peak_lo) if peak_lo > 0 else 0.0
    is_bi = prominence >= 0.10  # antimode ≥10% below the shallower of the two modes
    tau = float(grid[valley]) if is_bi else None
    return is_bi, "kde_prominence", round(prominence, 4), tau, bandwidth


def _kde_antimode(x, lo=None, hi=None):
    """Return (tau_star, bandwidth). tau_star = deepest antimode between the two
    dominant KDE modes (Silverman bandwidth). None if <2 modes."""
    x = np.asarray(x, dtype=float)
    if len(x) < 10:
        return None, None
    try:
        kde = gaussian_kde(x, bw_method='silverman')
    except Exception:
        return None, None
    lo = float(x.min()) if lo is None else lo
    hi = float(x.max()) if hi is None else hi
    if hi <= lo:
        return None, None
    grid = np.linspace(lo, hi, 512)
    d = kde(grid)
    peaks = [i for i in range(1, len(d) - 1) if d[i] >= d[i - 1] and d[i] > d[i + 1]]
    bandwidth = float(kde.factor * np.std(x))
    if len(peaks) < 2:
        return None, bandwidth
    a, b = sorted(sorted(peaks, key=lambda i: d[i], reverse=True)[:2])
    valley = a + int(np.argmin(d[a:b + 1]))
    return float(grid[valley]), bandwidth


# =============================================================================
# Snapshot
# =============================================================================
class Snapshot:
    def __init__(self, ids, cur_emb, sum_emb, entities, article_count,
                 created_at, last_update, community_id, edges):
        self.ids = ids                      # list[int]
        self.cur_emb = cur_emb              # dict id -> np.array (space B)
        self.sum_emb = sum_emb              # dict id -> np.array (space C)
        self.entities = entities            # dict id -> set[str]
        self.article_count = article_count  # dict id -> int
        self.created_at = created_at        # dict id -> datetime
        self.last_update = last_update      # dict id -> datetime
        self.community_id = community_id     # dict id -> int|None
        self.edges = edges                  # list[(s,t,w)]


def fetch_snapshot(db: DatabaseManager, min_weight: float) -> Snapshot:
    ids, cur_emb, sum_emb, entities = [], {}, {}, {}
    article_count, created_at, last_update, community_id = {}, {}, {}, {}
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, current_embedding, summary_vector, key_entities, "
                "article_count, created_at, last_update, community_id "
                "FROM storylines WHERE narrative_status IN %s",
                (ACTIVE_STATUSES,),
            )
            for row in cur.fetchall():
                sid, ce, se, ke, ac, ca, lu, cid = row
                ids.append(sid)
                ca_arr = _vec_to_array(ce)
                se_arr = _vec_to_array(se)
                if ca_arr is not None:
                    cur_emb[sid] = ca_arr
                if se_arr is not None:
                    sum_emb[sid] = se_arr
                entities[sid] = _parse_entities(ke)
                article_count[sid] = ac if ac is not None else 0
                created_at[sid] = ca
                last_update[sid] = lu
                community_id[sid] = cid
            cur.execute(
                "SELECT source_story_id, target_story_id, weight "
                "FROM storyline_edges WHERE weight >= %s",
                (min_weight,),
            )
            active = set(ids)
            edges = [(s, t, float(w)) for s, t, w in cur.fetchall()
                     if s in active and t in active]
    return Snapshot(ids, cur_emb, sum_emb, entities, article_count,
                    created_at, last_update, community_id, edges)


# =============================================================================
# S1 — Hubness
# =============================================================================
def s1_hubness(snap: Snapshot) -> HubnessStats:
    N = len(snap.ids)
    df = Counter()
    for sid in snap.ids:
        for e in snap.entities[sid]:
            df[e] += 1
    n_ent = len(df)
    dfs = np.array(list(df.values())) if df else np.array([0])
    pct_df1 = float(np.mean(dfs == 1)) if n_ent else 0.0
    top = df.most_common(20)
    top_hubs = [(e, c, float(np.log(N / c)) if c else 0.0) for e, c in top[:15]]
    top20 = {e for e, _ in top}
    # edge share attributable to top-20 hubs: fraction of edges whose endpoints
    # share ≥1 top-20 entity (proxy for the bipartite k-clique contribution).
    n_edges = len(snap.edges)
    from_top = 0
    for s, t, _ in snap.edges:
        if snap.entities[s] & snap.entities[t] & top20:
            from_top += 1
    pct_edges_top20 = (from_top / n_edges) if n_edges else 0.0
    return HubnessStats(
        n_active_storylines=N,
        n_distinct_entities=n_ent,
        pct_entities_df1=round(pct_df1, 4),
        median_doc_freq=float(np.median(dfs)),
        max_doc_freq=int(dfs.max()),
        top_hubs=top_hubs,
        pct_edges_from_top20_entities=round(pct_edges_top20, 4),
    )


# =============================================================================
# S2 + S3 — Separation (k-means vs live) + anisotropy (whitening)
# =============================================================================
def _silhouette(X, labels):
    if X.shape[0] < 3:
        return None
    distinct = set(labels)
    if not (2 <= len(distinct) < len(labels)):
        return None
    try:
        return float(silhouette_score(X, labels, metric='cosine'))
    except Exception as e:
        logger.debug("silhouette failed: %s", e)
        return None


def _all_but_the_top(X, k):
    """all-but-the-top whitening (Mu & Viswanath): mean-center + remove top-k PCs."""
    Xc = X - X.mean(axis=0, keepdims=True)
    k = min(k, min(Xc.shape) - 1)
    if k < 1:
        return Xc
    pca = PCA(n_components=k, random_state=SEED).fit(Xc)
    proj = pca.transform(Xc) @ pca.components_
    return Xc - proj


def s2_s3_alignment(snap: Snapshot, cfg, sample_size: int):
    emb_ids = [i for i in snap.ids if i in snap.cur_emb]
    if len(emb_ids) < 20:
        return None, {}, None
    X = np.stack([snap.cur_emb[i] for i in emb_ids])

    # ---- space-A partition: stored community_id if present, else a fresh Louvain ----
    # community_id is frequently NULL on prod between runs (nulled at the start of
    # compute_communities and only the applied partition is written back), so we
    # recompute a graph partition to have a space-A baseline regardless.
    live_labels = [snap.community_id[i] for i in emb_ids]
    have_live = all(l is not None for l in live_labels)
    sil_raw = _silhouette(X, live_labels) if have_live else None

    sil_graph, glabels = None, None
    active = set(emb_ids)
    g_edges = [(s, t, w) for s, t, w in snap.edges if s in active and t in active]
    if g_edges:
        gpart = _louvain(emb_ids, g_edges, cfg.community.resolution)
        glabels = [gpart.get(i, -1) for i in emb_ids]
        sil_graph = _silhouette(X, glabels)

    # ---- S2 k-means baseline (space B) ----
    sil_kmeans, best_k, best_sil, best_labels = {}, None, -2.0, None
    for k in KMEANS_KS:
        if k >= len(emb_ids):
            continue
        km = KMeans(n_clusters=k, random_state=SEED, n_init=10).fit(X)
        s = _silhouette(X, km.labels_)
        if s is not None:
            sil_kmeans[k] = round(s, 4)
            if s > best_sil:
                best_sil, best_k, best_labels = s, k, km.labels_

    # ---- S3 whitening (anisotropy check) on the space-A partition under test ----
    base_labels = live_labels if have_live else glabels
    base_sil = sil_raw if have_live else sil_graph
    sil_white = None
    if base_labels is not None:
        Xw = _all_but_the_top(X, k=10)
        sil_white = _silhouette(Xw, base_labels)
    delta = (sil_white - base_sil) if (sil_white is not None and base_sil is not None) else None

    # ---- Jaccard↔cosine correlation (stratified sample) ----
    pearson, n_pairs = _jaccard_cosine_corr(snap, emb_ids, X, sample_size)

    alignment = SignalAlignment(
        silhouette_raw=(round(sil_raw, 4) if sil_raw is not None else None),
        silhouette_graph_louvain=(round(sil_graph, 4) if sil_graph is not None else None),
        silhouette_whitened=(round(sil_white, 4) if sil_white is not None else None),
        silhouette_delta=(round(delta, 4) if delta is not None else None),
        silhouette_kmeans=(sil_kmeans or None),
        kmeans_best_k=best_k,
        jaccard_cosine_pearson=(round(pearson, 4) if pearson is not None else None),
        n_pairs_sampled=n_pairs,
    )
    kmeans_labels = ({emb_ids[i]: int(best_labels[i]) for i in range(len(emb_ids))}
                     if best_labels is not None else None)
    return alignment, {"emb_ids": emb_ids, "X": X}, kmeans_labels


def _jaccard_cosine_corr(snap, emb_ids, X, sample_size):
    """Pearson(edge-Jaccard weight, cosine embedding) over a stratified sample
    of with-edge and without-edge pairs."""
    rng = np.random.default_rng(SEED)
    id_to_row = {sid: r for r, sid in enumerate(emb_ids)}
    edge_w = {}
    for s, t, w in snap.edges:
        if s in id_to_row and t in id_to_row:
            edge_w[frozenset((s, t))] = w
    with_edge = list(edge_w.items())
    if not with_edge:
        return None, 0
    half = max(1, sample_size // 2)
    # with-edge sample
    idx = rng.choice(len(with_edge), size=min(half, len(with_edge)), replace=False)
    jacc, cos = [], []
    Xn = _l2norm(X)
    for k in idx:
        pair, w = with_edge[k]
        a, b = tuple(pair)
        jacc.append(w)
        cos.append(float(Xn[id_to_row[a]] @ Xn[id_to_row[b]]))
    # without-edge sample (random pairs not in edge set)
    n_no = min(half, len(emb_ids) * 2)
    tries = 0
    while len(jacc) < len(idx) + n_no and tries < n_no * 20:
        i, j = rng.integers(0, len(emb_ids), size=2)
        tries += 1
        if i == j:
            continue
        a, b = emb_ids[i], emb_ids[j]
        if frozenset((a, b)) in edge_w:
            continue
        jacc.append(0.0)
        cos.append(float(Xn[i] @ Xn[j]))
    if len(set(jacc)) < 2:
        return None, len(jacc)
    try:
        r, _ = pearsonr(jacc, cos)
        return float(r), len(jacc)
    except Exception:
        return None, len(jacc)


# =============================================================================
# S4 — Co-association / consensus
# =============================================================================
def s4_consensus(snap: Snapshot, cfg, kmeans_labels, max_n: int):
    if not snap.edges:
        return None
    ids = snap.ids
    if len(ids) > max_n:
        rng = np.random.default_rng(SEED)
        keep = set(rng.choice(ids, size=max_n, replace=False).tolist())
        ids = [i for i in ids if i in keep]
        edges = [(s, t, w) for s, t, w in snap.edges if s in keep and t in keep]
    else:
        edges = snap.edges

    resolution = cfg.community.resolution
    gamma = cfg.community.resolution_parameter
    alpha = getattr(getattr(cfg, 'sparsification', None), 'alpha', 0.3)
    fb = getattr(getattr(cfg, 'sparsification', None), 'fallback_threshold', 0.10)
    backbone = _disparity_backbone(edges, alpha=alpha, fallback=fb)

    partitions, names = [], []
    partitions.append(_louvain(ids, edges, resolution)); names.append("louvain_full")
    partitions.append(_louvain(ids, backbone, resolution)); names.append("louvain_backbone")
    if LEIDEN_AVAILABLE:
        try:
            partitions.append(_leiden(ids, edges, gamma)); names.append("leiden_full")
            partitions.append(_leiden(ids, backbone, gamma)); names.append("leiden_backbone")
        except Exception as e:
            logger.warning("Leiden shadow failed (%s) — Louvain-only co-association", e)

    N = len(ids)
    Ls = [np.array([p.get(i, -1) for i in ids]) for p in partitions]
    C = np.zeros((N, N), dtype=np.float32)
    valid = np.zeros((N, N), dtype=np.float32)
    for lab in Ls:
        same = (lab[:, None] == lab[None, :]).astype(np.float32)
        v = ((lab[:, None] >= 0) & (lab[None, :] >= 0)).astype(np.float32)
        C += same * v
        valid += v
    C = np.where(valid > 0, C / np.maximum(valid, 1.0), 0.0).astype(np.float32)

    iu = np.triu_indices(N, k=1)
    cv = C[iu]
    buckets = np.round(cv * len(partitions)) / len(partitions)
    hist = {f"{b:.2f}": round(float(np.mean(buckets == b)), 4)
            for b in sorted(set(np.round(np.arange(0, 1.0001, 1.0 / len(partitions)), 2)))}
    pct_fuzzy = round(float(np.mean((cv > 0) & (cv < 1))), 4)

    cons_labels, k_star, no_natural = _lifetime_cut(C)
    cons_singleton = _singleton_ratio(cons_labels) if cons_labels is not None else None

    ari = nmi = None
    if cons_labels is not None and kmeans_labels:
        common = [j for j, i in enumerate(ids) if i in kmeans_labels]
        if len(common) >= 3:
            cl = cons_labels[common]
            kl = np.array([kmeans_labels[ids[j]] for j in common])
            try:
                ari = float(adjusted_rand_score(cl, kl))
                nmi = float(normalized_mutual_info_score(cl, kl))
            except Exception:
                pass

    return ConsensusAgreement(
        n_methods=len(partitions),
        method_names=names,
        agreement_hist=hist,
        pct_pairs_fuzzy=pct_fuzzy,
        consensus_lifetime_k=k_star,
        consensus_singleton_ratio=(round(cons_singleton, 4) if cons_singleton is not None else None),
        ari_consensus_kmeans=(round(ari, 4) if ari is not None else None),
        nmi_consensus_kmeans=(round(nmi, 4) if nmi is not None else None),
        no_natural_scale=no_natural,
    )


# =============================================================================
# S5 — Fragmentation
# =============================================================================
def s5_fragmentation(db: DatabaseManager, snap: Snapshot) -> FragmentationStats:
    counts = np.array([snap.article_count[i] for i in snap.ids], dtype=float)
    N = len(counts)
    singleton_ratio = float(np.mean(counts <= 1)) if N else 0.0
    max_size_ratio = float(counts.max() / N) if N else 0.0

    pct_archived_single = None
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FILTER (WHERE article_count <= 1), count(*) "
                    "FROM storylines WHERE narrative_status = 'archived'"
                )
                single, total = cur.fetchone()
                if total:
                    pct_archived_single = round(single / total, 4)
    except Exception as e:
        logger.debug("archived fragmentation query skipped: %s", e)

    return FragmentationStats(
        n_active_storylines=N,
        singleton_ratio=round(singleton_ratio, 4),
        articles_per_storyline_median=float(np.median(counts)) if N else 0.0,
        articles_per_storyline_p90=float(np.percentile(counts, 90)) if N else 0.0,
        pct_archived_single=pct_archived_single,
        max_size_ratio=round(max_size_ratio, 4),
    )


# =============================================================================
# S6 + S7 — Match-replay (τ*) + coherence triangle (space C)
# =============================================================================
def s6_s7_match(snap: Snapshot, cfg):
    emb_ids = [i for i in snap.ids if i in snap.cur_emb]
    if len(emb_ids) < 20:
        return None, None

    thr = cfg.matching.threshold
    tdf = cfg.matching.time_decay_factor
    boost = cfg.matching.entity_boost
    jthr = cfg.matching.entity_jaccard_threshold

    X = _l2norm(np.stack([snap.cur_emb[i] for i in emb_ids]))
    cos = X @ X.T  # cosine (space B)
    J = _jaccard_matrix([snap.entities[i] for i in emb_ids])

    # temporal distance in days between storyline creation points
    def _days(sid):
        d = snap.created_at.get(sid)
        return d.timestamp() / 86400.0 if d else 0.0
    t = np.array([_days(i) for i in emb_ids])
    Dtime = np.abs(t[:, None] - t[None, :])

    boost_mat = (J >= jthr).astype(np.float32) * boost
    score = cos - tdf * Dtime + boost_mat
    np.fill_diagonal(score, -np.inf)

    best_j = np.argmax(score, axis=1)
    best_s = score[np.arange(len(emb_ids)), best_j]
    best_had_boost = (J[np.arange(len(emb_ids)), best_j] >= jthr)

    # ---- S6 auto-diagnostic cut (pure-numpy; diptest optional) ----
    valid = np.isfinite(best_s)
    bs = best_s[valid]
    is_bimodal, method, stat, tau_star, bw = _bimodality(bs)
    # artifact check: does bimodality survive decomposition by entity_boost?
    bimodal_b = bimodal_nb = None
    artifact = None
    if is_bimodal:
        bimodal_b = _bimodality(best_s[valid & best_had_boost])[0]
        bimodal_nb = _bimodality(best_s[valid & ~best_had_boost])[0]
        if bimodal_b is not None and bimodal_nb is not None:
            artifact = bool((not bimodal_b) and (not bimodal_nb))

    lo_band = tau_star if tau_star is not None else 0.65  # fallback band lower edge
    band_mask = valid & (best_s >= lo_band) & (best_s < thr)
    conf_mask = valid & (best_s >= thr)
    pct_band = round(float(np.mean(band_mask)), 4)

    hist_edges = np.linspace(0.0, 1.0, 21)
    counts, _ = np.histogram(bs, bins=hist_edges)
    hist = {f"{hist_edges[i]:.2f}": int(counts[i]) for i in range(len(counts))}

    match = MatchReplay(
        formula="cos(B) - {:.2f}*days + {:.2f}*[jaccard>={:.2f}]; live threshold={:.2f}".format(
            tdf, boost, jthr, thr),
        best_score_hist=hist,
        bimodality_method=method,
        bimodality_stat=(round(stat, 4) if stat is not None else None),
        is_bimodal=is_bimodal,
        bimodal_boost=bimodal_b,
        bimodal_no_boost=bimodal_nb,
        bimodality_is_artifact=artifact,
        tau_star=(round(tau_star, 4) if tau_star is not None else None),
        kde_bandwidth=(round(bw, 4) if bw is not None else None),
        pct_band_tau_075=pct_band,
        n_confirmed_ge_075=int(conf_mask.sum()),
        n_band=int(band_mask.sum()),
    )

    # ---- S7 coherence in space C (summary_vector), reference-set = confirmed ----
    coherence = _coherence_triangle(snap, cfg, emb_ids, best_j, band_mask, conf_mask)
    return match, coherence


def _coherence_triangle(snap, cfg, emb_ids, best_j, band_mask, conf_mask):
    have_c = [i for i in emb_ids if i in snap.sum_emb]
    if len(have_c) < 10:
        return None
    row = {sid: r for r, sid in enumerate(emb_ids)}
    Sc = {sid: snap.sum_emb[sid] / (np.linalg.norm(snap.sum_emb[sid]) or 1e-10)
          for sid in have_c}

    def coh_c(i_row):
        a = emb_ids[i_row]
        b = emb_ids[best_j[i_row]]
        if a in Sc and b in Sc:
            return float(Sc[a] @ Sc[b])
        return None

    conf_vals = [v for v in (coh_c(i) for i in np.where(conf_mask)[0]) if v is not None]
    band_vals = [v for v in (coh_c(i) for i in np.where(band_mask)[0]) if v is not None]

    conf_med = float(np.median(conf_vals)) if conf_vals else None
    conf_std = float(np.std(conf_vals)) if conf_vals else None
    band_med = float(np.median(band_vals)) if band_vals else None

    # merge-precision proxy: fraction of band merges whose space-C coherence falls
    # within the confirmed bulk (>= confirmed p25). Non-circular: C is absent from
    # the match formula (which uses B + entity + time).
    merge_prec = None
    if conf_vals and band_vals:
        p25 = float(np.percentile(conf_vals, 25))
        merge_prec = round(float(np.mean(np.array(band_vals) >= p25)), 4)

    # temporal cross-check (encoder-independent): band merges whose activity
    # windows [created_at, last_update] overlap → not two disjoint events.
    temporal_frac = None
    band_rows = np.where(band_mask)[0]
    if len(band_rows):
        ok = 0
        for i_row in band_rows:
            a, b = emb_ids[i_row], emb_ids[best_j[i_row]]
            ca, la = snap.created_at.get(a), snap.last_update.get(a)
            cb, lb = snap.created_at.get(b), snap.last_update.get(b)
            if ca and la and cb and lb:
                if ca <= lb and cb <= la:  # ranges overlap
                    ok += 1
        temporal_frac = round(ok / len(band_rows), 4)

    return CoherenceValidation(
        reference_set="best-match score >= {:.2f} (confident system merges)".format(
            cfg.matching.threshold),
        space_used="summary_vector (space C) — provably outside the match formula",
        coh_c_confirmed_median=(round(conf_med, 4) if conf_med is not None else None),
        coh_c_confirmed_std=(round(conf_std, 4) if conf_std is not None else None),
        coh_c_band_median=(round(band_med, 4) if band_med is not None else None),
        merge_precision_proxy=merge_prec,
        temporal_unimodal_fraction=temporal_frac,
    )


# =============================================================================
# Interpretation (design.md § Tabella di decisione)
# =============================================================================
def build_interpretation(rep: ClusteringDiagnosticsReport) -> str:
    lines = []
    if rep.hubness:
        h = rep.hubness
        lines.append(f"S1 hubness: {h.pct_entities_df1:.0%} entità df=1 (coda lunga Zipf), "
                     f"max doc_freq={h.max_doc_freq}, {h.pct_edges_from_top20_entities:.0%} edge dai top-20 hub.")
    a = rep.alignment
    if a and a.silhouette_kmeans:
        best = max(a.silhouette_kmeans.values())
        base = a.silhouette_raw if a.silhouette_raw is not None else a.silhouette_graph_louvain
        base_lbl = "live" if a.silhouette_raw is not None else "grafo (louvain fresh)"
        if base is not None:
            delta = best - base
            verdict = "struttura NELLO spazio-embedding" if delta > 0.15 else "vantaggio modesto"
            lines.append(f"S2 separazione: k-means best={best:+.3f} vs {base_lbl}={base:+.3f} "
                         f"(Δ={delta:+.3f}) → {verdict}. [silhouette in-space/circolare]")
        else:
            lines.append(f"S2 separazione: k-means best={best:+.3f} (k={a.kmeans_best_k}); "
                         f"baseline grafo non calcolabile su questo snapshot.")
    if a and a.jaccard_cosine_pearson is not None:
        lines.append(f"    Jaccard↔cosine Pearson={a.jaccard_cosine_pearson:+.3f} (n={a.n_pairs_sampled}).")
    if a and a.silhouette_delta is not None:
        kind = "anisotropia curabile" if a.silhouette_delta > 0.03 else "disallineamento STRUTTURALE"
        lines.append(f"S3 whitening delta={a.silhouette_delta:+.3f} → {kind}.")
    c = rep.consensus
    if c:
        lines.append(f"S4 accordo: {c.pct_pairs_fuzzy:.0%} coppie fuzzy; consensus k={c.consensus_lifetime_k} "
                     f"(no_natural_scale={c.no_natural_scale}); ARI(cons,kmeans)={c.ari_consensus_kmeans}.")
    f = rep.fragmentation
    if f:
        lines.append(f"S5 frammentazione: singleton-ratio={f.singleton_ratio:.0%}, "
                     f"mediana articoli/storyline={f.articles_per_storyline_median:.0f}.")
    m = rep.match_replay
    if m:
        meth = m.bimodality_method
        if m.is_bimodal is False:
            lines.append(f"S6 match-replay: distribuzione UNIMODALE ({meth}) → frammentazione continuum "
                         "→ soglia = trade-off, delega al triangolo.")
        elif m.is_bimodal and m.tau_star is not None:
            art = " (⚠ artefatto del boost)" if m.bimodality_is_artifact else ""
            lines.append(f"S6 match-replay: bimodale ({meth}, stat={m.bimodality_stat}), "
                         f"τ*={m.tau_star:.3f}{art}; % banda [τ*,0.75)={m.pct_band_tau_075:.1%} → merge recuperabili.")
        else:
            lines.append(f"S6 match-replay: bimodalità indeterminata ({meth}); banda fallback usata.")
    co = rep.coherence
    if co and co.merge_precision_proxy is not None:
        band_lbl = (f"τ*={rep.match_replay.tau_star:.3f}"
                    if (rep.match_replay and rep.match_replay.tau_star)
                    else "banda 0.65 (fallback, dist. unimodale)")
        safe = "abbassare la soglia è SICURO" if co.merge_precision_proxy >= 0.6 \
            else "banda ~rumore (abbassare diluirebbe la qualità)"
        lines.append(f"S7 coerenza-C [{band_lbl}]: merge_precision={co.merge_precision_proxy:.0%} "
                     f"(band_med={co.coh_c_band_median} vs conf_med={co.coh_c_confirmed_median}) → {safe}.")
    return "\n".join(lines)


# =============================================================================
# Visual report (optional — matplotlib)
# =============================================================================
def render_figures(rep: ClusteringDiagnosticsReport, out_dir: Path):
    if not MPL_AVAILABLE:
        return []
    saved = []
    tag = rep.run_id[:8]
    try:
        m = rep.match_replay
        if m and m.best_score_hist:
            fig, ax = plt.subplots(figsize=(7, 4))
            xs = [float(k) for k in m.best_score_hist.keys()]
            ys = list(m.best_score_hist.values())
            ax.bar(xs, ys, width=0.045, align='edge', color='#4477aa')
            ax.axvline(0.75, color='red', ls='--', label='soglia 0.75')
            if m.tau_star:
                ax.axvline(m.tau_star, color='green', ls='--', label=f'τ*={m.tau_star:.3f}')
            ax.set_title(f"S6 best-match score distribution [{tag}]")
            ax.set_xlabel("best-match score"); ax.set_ylabel("# storylines"); ax.legend()
            p = out_dir / f"s6_match_scores_{tag}.png"
            fig.tight_layout(); fig.savefig(p, dpi=110); plt.close(fig); saved.append(str(p))
    except Exception as e:
        logger.debug("figure S6 failed: %s", e)
    try:
        a = rep.alignment
        if a and a.silhouette_kmeans:
            fig, ax = plt.subplots(figsize=(7, 4))
            ks = sorted(a.silhouette_kmeans.keys())
            ax.plot(ks, [a.silhouette_kmeans[k] for k in ks], 'o-', label='k-means (space B)')
            if a.silhouette_raw is not None:
                ax.axhline(a.silhouette_raw, color='gray', ls='--',
                           label=f'live (graph A)={a.silhouette_raw:+.3f}')
            ax.axhline(0, color='black', lw=0.5)
            ax.set_title(f"S2 silhouette: embedding vs graph [{tag}]")
            ax.set_xlabel("k"); ax.set_ylabel("silhouette (cosine)"); ax.legend()
            p = out_dir / f"s2_silhouette_{tag}.png"
            fig.tight_layout(); fig.savefig(p, dpi=110); plt.close(fig); saved.append(str(p))
    except Exception as e:
        logger.debug("figure S2 failed: %s", e)
    return saved


# =============================================================================
# main
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Clustering signal diagnostics (read-only, S1–S7).")
    ap.add_argument("--sample-size", type=int,
                    default=int(os.getenv("DIAGNOSTICS_SAMPLE_SIZE", "5000")),
                    help="pairs sampled for Jaccard↔cosine correlation")
    ap.add_argument("--max-coassoc-n", type=int, default=6000,
                    help="cap on storylines for the NxN co-association (sampled above)")
    ap.add_argument("--output-dir", default="artifacts")
    ap.add_argument("--no-viz", action="store_true", help="skip figures (JSON only)")
    args = ap.parse_args()

    t0 = time.time()
    import uuid
    run_id = str(uuid.uuid4())
    snapshot_at = datetime.now(timezone.utc).isoformat()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    warnings = []
    if not LEIDEN_AVAILABLE:
        warnings.append("leidenalg/igraph missing → co-association on 2 Louvain partitions only")
    if not DIPTEST_AVAILABLE:
        warnings.append("diptest not installed → S6 bimodality via pure-numpy KDE-prominence "
                        "(Hartigan dip skipped; install diptest for the p-value cross-check)")
    if not MPL_AVAILABLE and not args.no_viz:
        warnings.append("matplotlib missing → figures skipped (JSON still produced)")

    cfg = load_clustering_config()
    global LIVE_THRESHOLD
    LIVE_THRESHOLD = cfg.matching.threshold
    db = DatabaseManager()

    logger.info("Fetching snapshot (read-only)…")
    snap = fetch_snapshot(db, cfg.community.min_weight)
    N = len(snap.ids)
    logger.info("Snapshot: %d active storylines, %d edges, %d with current_embedding",
                N, len(snap.edges), len(snap.cur_emb))

    rep = ClusteringDiagnosticsReport(
        snapshot_at=snapshot_at, run_id=run_id,
        partition_source="storylines.community_id (louvain live)",
        n_active_storylines=N, warnings=warnings,
    )

    try:
        rep.hubness = s1_hubness(snap)
    except Exception as e:
        warnings.append(f"S1 failed: {e}"); logger.exception("S1")

    kmeans_labels = None
    try:
        rep.alignment, _ctx, kmeans_labels = s2_s3_alignment(snap, cfg, args.sample_size)
    except Exception as e:
        warnings.append(f"S2/S3 failed: {e}"); logger.exception("S2/S3")

    try:
        rep.consensus = s4_consensus(snap, cfg, kmeans_labels, args.max_coassoc_n)
    except Exception as e:
        warnings.append(f"S4 failed: {e}"); logger.exception("S4")

    try:
        rep.fragmentation = s5_fragmentation(db, snap)
    except Exception as e:
        warnings.append(f"S5 failed: {e}"); logger.exception("S5")

    try:
        rep.match_replay, rep.coherence = s6_s7_match(snap, cfg)
    except Exception as e:
        warnings.append(f"S6/S7 failed: {e}"); logger.exception("S6/S7")

    rep.warnings = warnings
    rep.interpretation = build_interpretation(rep)

    figs = []
    if not args.no_viz:
        figs = render_figures(rep, out_dir)

    json_path = out_dir / f"clustering_diagnostics_{run_id[:8]}.json"
    json_path.write_text(rep.model_dump_json(indent=2))

    print("\n" + "=" * 70)
    print(f"CLUSTERING SIGNAL DIAGNOSTICS — {snapshot_at}  (run {run_id[:8]})")
    print("=" * 70)
    print(rep.interpretation or "(no stations produced output)")
    if warnings:
        print("\n⚠ warnings:")
        for w in warnings:
            print("  -", w)
    print(f"\nJSON  → {json_path}")
    for f in figs:
        print(f"figure → {f}")
    print(f"\nDone in {time.time() - t0:.1f}s (read-only, {N} storylines).")


if __name__ == "__main__":
    main()
