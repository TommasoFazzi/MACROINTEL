"""
Candidate level-1 formation vectors and their pre-replay gate.

`storyline-representation-track` design.md D1–D3. A formation vector replaces the
article embedding in level-1 decisions only; `current_embedding` (level 2) and
`chunks.embedding` (RAG) are never touched.

- R1: mean of the article's L2-normalised RAG chunk embeddings (`chunks.embedding`).
- R2/R3: windows over the whole cleaned text, mean-pooled (built later).

Every candidate file is `formation_<name>.npz` (`ids`, `embedding`, `fallback`) next to a
`formation_<name>.json` manifest, aligned to the snapshot's `articles.npz` ids. An
article with no candidate input falls back to its baseline embedding.

The gate (D3) compares the candidate's and the baseline's AUC of cosine on the gold
labels, with storyline vectors taken as the mean of all their articles, and a paired
bootstrap over the labelled rows.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

from .formation_labels import auc

GATE_S_MIN_DELTA = 0.05     # S: ΔAUC point estimate at least +0.05 ...
GATE_J_MIN_DELTA = -0.02    # ... and J ΔAUC no worse than −0.02
N_BOOT = 2000
POSITIVE = {"J": "yes", "S": "same"}


def unit(v: np.ndarray) -> np.ndarray:
    return v / max(float(np.linalg.norm(v)), 1e-10)


def build_from_means(article_ids: Sequence[int], means: Dict[int, np.ndarray],
                     baseline: Dict[int, np.ndarray]) -> tuple:
    """Aligned (embedding, fallback) arrays: the candidate's per-article vector, or the
    baseline embedding where the candidate has none. Articles with neither stay zero
    and are flagged as fallback."""
    dim = len(next(iter(means.values()))) if means else len(next(iter(baseline.values())))
    emb = np.zeros((len(article_ids), dim), dtype=np.float32)
    fallback = np.zeros(len(article_ids), dtype=bool)
    for i, aid in enumerate(article_ids):
        if aid in means:
            emb[i] = unit(np.asarray(means[aid], dtype=np.float64))
        else:
            fallback[i] = True
            if aid in baseline:
                if len(baseline[aid]) != dim:
                    raise ValueError("a fallback needs the candidate and the baseline in the same space")
                emb[i] = unit(np.asarray(baseline[aid], dtype=np.float64))
    return emb, fallback


def load_vectors(path, article_ids: Sequence[int]) -> Dict[int, np.ndarray]:
    """Candidate vectors by article id; raises if the file is not aligned to the snapshot."""
    z = np.load(path)
    ids, emb = z["ids"], z["embedding"]      # one decompression per array, not per article
    if ids.tolist() != list(article_ids):
        raise ValueError(f"{path} is not aligned to the snapshot's article ids")
    return {int(a): emb[i].astype(np.float64) for i, a in enumerate(ids) if emb[i].any()}


def with_formation_vectors(articles: Sequence, vectors: Dict[int, np.ndarray]) -> list:
    """D1 in the replay: the same articles with the candidate vector as `embedding`.

    Every level-1 decision of the replay (micro-clusters, matching, residual HDBSCAN,
    drift) reads only `ReplayArticle.embedding`, so substituting it is the whole hook.
    An article the candidate has no vector for keeps no embedding, exactly as the
    baseline treats an unembedded article. Level 2 is not replayed; keeping
    `current_embedding` on the old vectors is a prod requirement (tasks.md 6.2)."""
    return [replace(a, embedding=vectors.get(a.id) if a.embedding is not None else None) for a in articles]


def centroid(ids: Iterable[int], vectors: Dict[int, np.ndarray]) -> Optional[np.ndarray]:
    vs = [unit(vectors[a]) for a in ids if a in vectors]
    return unit(np.mean(vs, axis=0)) if vs else None


def row_cosines(members: dict, vectors: Dict[int, np.ndarray]) -> Dict[str, Dict[str, float]]:
    """Cosine per labelled row. J: article vs the mean of the storyline's articles at
    decision time; S: mean of one storyline's articles vs the other's."""
    out: Dict[str, Dict[str, float]] = {"J": {}, "S": {}}
    for row, m in members["J"].items():
        c = centroid(m["storyline"], vectors)
        if c is not None and m["article"] in vectors:
            out["J"][row] = float(unit(vectors[m["article"]]) @ c)
    for row, m in members["S"].items():
        left, right = centroid(m["left"], vectors), centroid(m["right"], vectors)
        if left is not None and right is not None:
            out["S"][row] = float(left @ right)
    return out


def _paired_auc(rows: List[str], gold: Dict[str, str], positive: str,
                base: Dict[str, float], cand: Dict[str, float], n_boot: int, seed: int) -> dict:
    y = np.array([gold[r] == positive for r in rows])
    b, c = np.array([base[r] for r in rows]), np.array([cand[r] for r in rows])
    pos, neg = np.flatnonzero(y), np.flatnonzero(~y)
    rng = np.random.default_rng(seed)
    deltas = np.empty(n_boot)
    for k in range(n_boot):
        # resample within each class so every replicate has both
        idx = np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])
        deltas[k] = auc(c[idx], y[idx]) - auc(b[idx], y[idx])
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    auc_b, auc_c = auc(b, y), auc(c, y)
    return {"n": len(rows), "n_positive": int(y.sum()), "auc_base": round(auc_b, 4), "auc_candidate": round(auc_c, 4),
            "delta": round(auc_c - auc_b, 4), "ci95": [round(float(lo), 4), round(float(hi), 4)]}


def auc_gate(gold: Dict[str, Dict[str, str]], members: dict, base_vectors: Dict[int, np.ndarray],
             cand_vectors: Dict[int, np.ndarray], n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """D3: does the candidate separate the gold better than the baseline?

    `gold` is `{"J": {row_id: label}, "S": {...}}`; `unsure` rows and rows either
    representation cannot score are dropped, so both are compared on the same rows."""
    base, cand = row_cosines(members, base_vectors), row_cosines(members, cand_vectors)
    out = {}
    for set_ in ("J", "S"):
        rows = sorted((r for r, lab in gold[set_].items()
                       if lab != "unsure" and r in base[set_] and r in cand[set_]), key=int)
        out[set_] = _paired_auc(rows, gold[set_], POSITIVE[set_], base[set_], cand[set_], n_boot, seed)
    out["passes"] = bool(out["S"]["delta"] >= GATE_S_MIN_DELTA and out["S"]["ci95"][0] > 0
                         and out["J"]["delta"] >= GATE_J_MIN_DELTA)
    return out


# ---------------------------------------------------------------------------
# D4: thresholds on a new similarity scale (quantile matching, no labels)
# ---------------------------------------------------------------------------

CALIBRATION_SENSITIVITY = (0.75, 1.25)    # the share above threshold ×0.75 and ×1.25, reported only
LOADED_OUTCOMES = ("joined", "new_storyline", "orphan")


def matched_threshold(cos_base: np.ndarray, cos_cand: np.ndarray, threshold: float, scale: float = 1.0) -> float:
    """The candidate threshold that leaves the same share of pairs at or above it as
    `threshold` leaves in the baseline (times `scale`, for the sensitivity band)."""
    share = float(np.mean(cos_base >= threshold)) * scale
    return float(np.quantile(cos_cand, 1.0 - min(max(share, 0.0), 1.0)))


def calibration_pairs(result, base_vectors: Dict[int, np.ndarray], cand_vectors: Dict[int, np.ndarray]) -> dict:
    """Cosines of the same pairs in both spaces, over the baseline replay's scored runs.

    - `micro`: every pair of articles loaded in the same run (what micro-clustering compares).
    - `match`: every loaded event vs the storyline that was its best candidate, as the
      mean of that storyline's articles linked before the run (what matching compares).
    """
    both = lambda ids: [a for a in ids if a in base_vectors and a in cand_vectors]  # noqa: E731
    by_run: Dict = {}
    for rec in result.records:
        if rec.run_ts >= result.scored_since and rec.outcome in LOADED_OUTCOMES:
            by_run.setdefault(rec.run_ts, []).append(rec)

    micro = {"base": [], "cand": []}
    for recs in by_run.values():
        ids = both(a for r in recs for a in r.event_article_ids)
        if len(ids) < 2:
            continue
        iu = np.triu_indices(len(ids), 1)
        for key, vecs in (("base", base_vectors), ("cand", cand_vectors)):
            m = np.stack([unit(vecs[a]) for a in ids])
            micro[key].append((m @ m.T)[iu])

    linked: Dict[int, list] = {}
    for (aid, sid), t in result.link_time.items():
        linked.setdefault(sid, []).append((t, aid))
    match = {"base": [], "cand": []}
    for run_ts, recs in by_run.items():
        for r in recs:
            if r.best_active is None:
                continue
            event = both(r.event_article_ids)
            members = both(a for t, a in linked.get(r.best_active["storyline_id"], []) if t < run_ts)
            if not event or not members:
                continue
            for key, vecs in (("base", base_vectors), ("cand", cand_vectors)):
                match[key].append(float(centroid(event, vecs) @ centroid(members, vecs)))

    flat = lambda xs: np.concatenate(xs) if xs and isinstance(xs[0], np.ndarray) else np.array(xs)  # noqa: E731
    return {"micro": {k: flat(v) for k, v in micro.items()}, "match": {k: flat(v) for k, v in match.items()}}


def calibrate(pairs: dict, micro_threshold: float, match_threshold: float) -> dict:
    """D4 thresholds for the candidate, plus the sensitivity band."""
    out = {"n_pairs": {k: int(len(v["base"])) for k, v in pairs.items()}}
    for name, scale in (("matched", 1.0), *((f"share_x{s}", s) for s in CALIBRATION_SENSITIVITY)):
        out[name] = {"micro": round(matched_threshold(pairs["micro"]["base"], pairs["micro"]["cand"],
                                                      micro_threshold, scale), 4),
                     "match": round(matched_threshold(pairs["match"]["base"], pairs["match"]["cand"],
                                                      match_threshold, scale), 4)}
    return out


# ---------------------------------------------------------------------------
# D5: duplicates as co-assignment of S gold pairs
# ---------------------------------------------------------------------------

CO_SAME_GAIN_REL = 0.10      # `same` pairs: co-assignment up by at least 10% relative ...
CO_DIFF_MAX_RISE = 0.05      # ... `different` pairs: up by at most 5 pp (95% upper bound)


def co_assignment(links: Dict[int, set], left: Sequence[int], right: Sequence[int]) -> Optional[float]:
    """Share of cross article pairs (one from each storyline) that end in a common
    storyline. None when the pair has no cross pair of distinct articles."""
    pairs = [(a, b) for a in left for b in right if a != b]
    if not pairs:
        return None
    return sum(bool(links.get(a, set()) & links.get(b, set())) for a, b in pairs) / len(pairs)


def co_assignment_result(gold_s: Dict[str, str], members_s: dict, base_links: Dict[int, set],
                         cand_links: Dict[int, set], n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """D5 benefit and guard, with a paired bootstrap over the S gold pairs (within class)."""
    rates = {}
    for row, lab in gold_s.items():
        if lab == "unsure" or row not in members_s:
            continue
        m = members_s[row]
        b, c = co_assignment(base_links, m["left"], m["right"]), co_assignment(cand_links, m["left"], m["right"])
        if b is not None:
            rates.setdefault(lab, []).append((b, c))
    rng = np.random.default_rng(seed)
    out = {}
    for lab in ("same", "different"):
        arr = np.array(rates.get(lab, []), dtype=float).reshape(-1, 2)
        boots = np.array([arr[rng.integers(0, len(arr), len(arr))].mean(axis=0) for _ in range(n_boot)]) \
            if len(arr) else np.full((1, 2), np.nan)
        base_m, cand_m = arr.mean(axis=0) if len(arr) else (np.nan, np.nan)
        d = boots[:, 1] - boots[:, 0]
        out[lab] = {"n": int(len(arr)), "base": round(float(base_m), 4), "candidate": round(float(cand_m), 4),
                    "delta": round(float(cand_m - base_m), 4),
                    "ci95": [round(float(x), 4) for x in np.percentile(d, [2.5, 97.5])]}
    same, diff = out["same"], out["different"]
    gain_rel = (same["candidate"] - same["base"]) / same["base"] if same["base"] else float("nan")
    out["same_gain_rel"] = round(gain_rel, 4)
    out["benefit"] = bool(gain_rel >= CO_SAME_GAIN_REL and diff["ci95"][1] <= CO_DIFF_MAX_RISE)
    return out
