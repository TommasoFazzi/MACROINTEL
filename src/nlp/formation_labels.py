"""
Blind labelled sets for the level-1 formation review, and their estimator.

`storyline-formation-causal-review` design.md D5 and the D7 amendment of
2026-10-16. Two sets in this round:

- **J (join precision)**: "Does this article belong to this storyline?" An event's
  representative article against a storyline as it was at decision time (title and
  its 3 most recent article titles). Strata:
    J-base  joins the baseline replay made (the decisions every lever shares or removes)
    J-H1    events a lever on the time penalty would add: best emerging/active
            candidate that clears without the penalty
    J-H8    best rule_4/rule_5/off-topic archived storyline that clears without the penalty
    J-H2    best stabilized storyline that clears without the penalty
    J-near  best emerging/active candidate scoring in [0.65, 0.75) with the penalty (L7)
- **S (same story)**: "Are these two storylines the same story?" Event-level, narrower
  than level 2's "same theme". Strata:
    S-new   a replay storyline created after burn-in vs its best existing candidate
    S-prod  two prod storylines created after burn-in whose article centroids have
            cosine >= 0.80

C (cohesion) waits for the second round: its strata exist only once L3, L6 or L8
are implemented.

Precision is never a plain average over labelled items: J oversamples the joins a
lever changes on purpose. `stratified_precision` weighs each stratum's labelled
precision by the number of decisions a configuration makes in that stratum, and
`paired_delta` resamples labelled items within each stratum once and applies the
lever's and the baseline's weights to the same resample.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import numpy as np

from .clustering_validation import labelling_order, rater_agreement  # noqa: F401  (re-exported)
from .storyline_replay import FormationSnapshot, ReplayResult

J_STRATUM_SIZES = {"J-base": 150, "J-H1": 110, "J-H8": 45, "J-H2": 30, "J-near": 15}
S_STRATUM_SIZES = {"S-new": 70, "S-prod": 30}
J_REPEATS, S_REPEATS = 30, 20
J_MIN_REPEAT_GAP, S_MIN_REPEAT_GAP = 60, 30
J_PRECISION_BOUND = -0.05          # D7 as amended 2026-10-16
NEAR_MISS = (0.65, 0.75)
S_NEW_MIN_COSINE = 0.60
S_PROD_MIN_COSINE = 0.80
RECENT_TITLES = 3
N_BOOT = 2000


# ---------------------------------------------------------------------------
# Candidate pools
# ---------------------------------------------------------------------------

def _link_index(result: ReplayResult) -> Dict[int, list]:
    idx: Dict[int, list] = {}
    for (aid, sid), t in result.link_time.items():
        idx.setdefault(sid, []).append((t, aid))
    for v in idx.values():
        v.sort(reverse=True)
    return idx


def j_pool(result: ReplayResult, variant: str, titles: Dict[int, str]) -> List[dict]:
    """Every J candidate of one replay, after burn-in, tagged with its stratum."""
    idx = _link_index(result)

    def item(rec, stratum, sid, score):
        recent = [titles.get(aid, "") for t, aid in idx.get(sid, []) if t < rec.run_ts][:RECENT_TITLES]
        return {"stratum": stratum, "variant": variant, "run_ts": rec.run_ts.isoformat(),
                "article_id": rec.event_article_ids[0], "n_event_articles": len(rec.event_article_ids),
                "storyline_id": sid, "storyline_title": result.storylines[sid].title,
                "recent_titles": recent, "score": score}

    pool = []
    for rec in result.records:
        if rec.run_ts < result.scored_since:
            continue
        if rec.outcome in ("joined", "orphan_recovered"):
            pool.append(item(rec, "J-base", rec.storyline_id, rec.best_active["score"] if rec.best_active else None))
            continue
        if rec.outcome not in ("new_storyline", "orphan"):
            continue
        flags = set(rec.flags)
        if "H1" in flags:
            c = rec.best_active_no_penalty
            pool.append(item(rec, "J-H1", c["storyline_id"], c["score"]))
        if "H8xH1" in flags:
            c = rec.best_archived_h8
            pool.append(item(rec, "J-H8", c["storyline_id"], c["score_no_penalty"]))
        if "H2xH1" in flags:
            c = rec.best_stabilized
            pool.append(item(rec, "J-H2", c["storyline_id"], c["score_no_penalty"]))
        c = rec.best_active
        if c is not None and NEAR_MISS[0] <= c["score"] < NEAR_MISS[1]:
            pool.append(item(rec, "J-near", c["storyline_id"], c["score"]))
    return pool


def s_new_pool(result: ReplayResult, variant: str, titles: Dict[int, str]) -> List[dict]:
    """Each replay storyline created after burn-in vs its best existing candidate
    (any status, ranked without the time penalty) over its creation events."""
    best: Dict[int, dict] = {}
    for rec in result.records:
        if rec.outcome != "new_storyline" or rec.run_ts < result.scored_since:
            continue
        for c in (rec.best_active_no_penalty, rec.best_archived_h8, rec.best_stabilized):
            if c is None:
                continue
            score = c.get("score_no_penalty", c["score"])
            cur = best.get(rec.storyline_id)
            if cur is None or score > cur["score"]:
                best[rec.storyline_id] = {"other": c["storyline_id"], "score": score, "cosine": c["cosine"]}
    idx = _link_index(result)
    pool = []
    for sid, b in best.items():
        if b["cosine"] < S_NEW_MIN_COSINE:
            continue
        pool.append({"stratum": "S-new", "variant": variant, "left_id": sid, "right_id": b["other"],
                     "left_title": result.storylines[sid].title,
                     "left_titles": [titles.get(a, "") for _, a in idx.get(sid, [])][:5],
                     "right_title": result.storylines[b["other"]].title,
                     "right_titles": [titles.get(a, "") for _, a in idx.get(b["other"], [])][:5],
                     "cosine": b["cosine"]})
    return pool


def s_prod_pool(snap: FormationSnapshot, since: datetime) -> List[dict]:
    """Pairs of prod storylines created after burn-in whose article centroids are close."""
    emb = {a.id: a.embedding for a in snap.articles if a.embedding is not None}
    titles = {a.id: a.title for a in snap.articles}
    members: Dict[int, List[int]] = {}
    for aid, sids in snap.prod_links.items():
        for sid in sids:
            members.setdefault(sid, []).append(aid)
    ids, cents = [], []
    for sid, s in sorted(snap.prod_storylines.items()):
        arts = [a for a in members.get(sid, []) if a in emb]
        if s["created_at"] is None or s["created_at"] < since or not arts:
            continue
        c = np.mean([emb[a] for a in arts], axis=0)
        ids.append(sid)
        cents.append(c / max(np.linalg.norm(c), 1e-10))
    if len(ids) < 2:
        return []
    m = np.stack(cents) @ np.stack(cents).T
    pool = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if m[i, j] >= S_PROD_MIN_COSINE:
                a, b = snap.prod_storylines[ids[i]], snap.prod_storylines[ids[j]]
                pool.append({"stratum": "S-prod", "variant": "prod", "left_id": ids[i], "right_id": ids[j],
                             "left_title": a["title"], "left_summary": (a["summary"] or "")[:300],
                             "left_titles": [titles.get(x, "") for x in sorted(members[ids[i]])[-5:]],
                             "right_title": b["title"], "right_summary": (b["summary"] or "")[:300],
                             "right_titles": [titles.get(x, "") for x in sorted(members[ids[j]])[-5:]],
                             "cosine": float(m[i, j])})
    return pool


def sample_strata(pool: Sequence[dict], sizes: Dict[str, int], seed: int,
                  key=lambda x: (x.get("article_id"), x.get("storyline_title"), x.get("left_id"), x.get("right_id"))
                  ) -> List[dict]:
    """Draw `sizes[stratum]` items per stratum without replacement, never the same
    item twice across strata (first stratum in `sizes` order wins)."""
    rng = np.random.default_rng(seed)
    chosen, seen = [], set()
    for stratum, n in sizes.items():
        cands = [x for x in pool if x["stratum"] == stratum]
        taken = 0
        for i in rng.permutation(len(cands)):
            if taken == n:
                break
            if key(cands[i]) in seen:
                continue
            chosen.append(cands[i])
            seen.add(key(cands[i]))
            taken += 1
    return chosen


# ---------------------------------------------------------------------------
# Stratified estimator (D5)
# ---------------------------------------------------------------------------

@dataclass
class StratumLabels:
    """0/1 correctness labels per stratum (`unsure` and repeats removed)."""
    by_stratum: Dict[str, np.ndarray]


def stratified_precision(counts: Dict[str, int], labels: StratumLabels,
                         means: Optional[Dict[str, float]] = None) -> float:
    """P(k) = Σ_s N_s(k)·p_s / Σ_s N_s(k), with p_s the labelled precision of stratum s.
    `counts` are a configuration's decisions per stratum (from its replay)."""
    p = means if means is not None else {s: float(v.mean()) for s, v in labels.by_stratum.items() if len(v)}
    total = sum(n for s, n in counts.items() if n and s in p)
    if total == 0:
        raise ValueError("no decision falls in a labelled stratum")
    missing = [s for s, n in counts.items() if n and s not in p]
    if missing:
        raise ValueError(f"strata with decisions but no labels: {missing}")
    return sum(n * p[s] for s, n in counts.items() if n) / total


def paired_delta(counts_lever: Dict[str, int], counts_base: Dict[str, int], labels: StratumLabels,
                 n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """P(lever) − P(baseline) with a stratified paired bootstrap: one resample of the
    labelled items within each stratum, both formulas applied to it."""
    rng = np.random.default_rng(seed)
    point = stratified_precision(counts_lever, labels) - stratified_precision(counts_base, labels)
    deltas = np.empty(n_boot)
    for b in range(n_boot):
        means = {s: float(v[rng.integers(0, len(v), len(v))].mean())
                 for s, v in labels.by_stratum.items() if len(v)}
        deltas[b] = (stratified_precision(counts_lever, labels, means)
                     - stratified_precision(counts_base, labels, means))
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    return {"delta": float(point), "ci95": (float(lo), float(hi)),
            "passes_bound": bool(lo >= J_PRECISION_BOUND)}
