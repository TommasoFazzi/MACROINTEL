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
RICH_ARTICLES = 8          # rich-context relabelling (6.4c): article ids kept per storyline side
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
        before = [aid for t, aid in idx.get(sid, []) if t < rec.run_ts]
        recent = [titles.get(aid, "") for aid in before[:RECENT_TITLES]]
        return {"stratum": stratum, "variant": variant, "run_ts": rec.run_ts.isoformat(),
                "article_id": rec.event_article_ids[0], "n_event_articles": len(rec.event_article_ids),
                "storyline_id": sid, "storyline_title": result.storylines[sid].title,
                "recent_titles": recent, "recent_ids": before[:RICH_ARTICLES], "score": score}

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
                     "left_ids": [a for _, a in idx.get(sid, [])][:RICH_ARTICLES],
                     "right_title": result.storylines[b["other"]].title,
                     "right_titles": [titles.get(a, "") for _, a in idx.get(b["other"], [])][:5],
                     "right_ids": [a for _, a in idx.get(b["other"], [])][:RICH_ARTICLES],
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
                             "left_ids": sorted(members[ids[i]])[::-1][:RICH_ARTICLES],
                             "right_title": b["title"], "right_summary": (b["summary"] or "")[:300],
                             "right_titles": [titles.get(x, "") for x in sorted(members[ids[j]])[-5:]],
                             "right_ids": sorted(members[ids[j]])[::-1][:RICH_ARTICLES],
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


def stratum_labels(labels: Dict[str, str], strata: Dict[str, str], positive: str) -> StratumLabels:
    """0/1 arrays per stratum from row labels (`row_id -> label`) and the key's strata
    (`row_id -> stratum`). Rows labelled `unsure` are dropped, as D5 prescribes."""
    out: Dict[str, list] = {}
    for row_id, label in labels.items():
        if label == "unsure":
            continue
        out.setdefault(strata[row_id], []).append(1.0 if label == positive else 0.0)
    return StratumLabels({s: np.array(v) for s, v in out.items()})


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple:
    """95% Wilson score interval for k successes out of n."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (float(centre - half), float(centre + half))


def auc(scores: Sequence[float], positive: Sequence[bool]) -> float:
    """ROC AUC as the Mann–Whitney probability that a positive outscores a negative
    (ties count half). H6: does article-centroid cosine separate same from different?"""
    pos = np.array([x for x, y in zip(scores, positive) if y])
    neg = np.array([x for x, y in zip(scores, positive) if not y])
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    greater = (pos[:, None] > neg[None, :]).sum()
    ties = (pos[:, None] == neg[None, :]).sum()
    return float((greater + 0.5 * ties) / (len(pos) * len(neg)))


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


# ---------------------------------------------------------------------------
# Lever evaluation (D7, tasks.md 7.2)
# ---------------------------------------------------------------------------

COVERAGE_GAIN_PP = 2.0             # D7: coverage gain of at least 2 percentage points ...
DUPLICATE_DROP_REL = 0.10          # ... or the duplicate-story rate down by at least 10% relative


def join_counts(result: ReplayResult, base_threshold: float, base_decay: float) -> Dict[str, int]:
    """A configuration's scored-window joins per J stratum.

    A join to a stabilized storyline (L2) stands for `J-H2`, one to an archived
    storyline revived within L8's grace period for `J-H8`. Any other join that would
    also clear under the baseline's linear penalty is `J-base`-like; one that clears
    only because the lever changed the penalty is `J-H1`-like. The baseline replay is
    all J-base.
    """
    counts = {"J-base": 0, "J-H1": 0, "J-H2": 0, "J-H8": 0}
    for rec in result.records:
        if rec.run_ts < result.scored_since or rec.outcome not in ("joined", "orphan_recovered"):
            continue
        if rec.target_status == "stabilized":
            counts["J-H2"] += 1
            continue
        if rec.target_status == "archived":
            counts["J-H8"] += 1
            continue
        c = rec.best_active
        base_score = c["cosine"] - base_decay * c["days_inactive"] + c["boost"]
        counts["J-base" if base_score >= base_threshold else "J-H1"] += 1
    return counts


def coverage(result: ReplayResult, articles) -> float:
    """Share of scored-window articles (with an embedding) linked to a storyline."""
    end = result.run_times[-1]
    ids = [a.id for a in articles
           if a.embedding is not None and result.scored_since <= a.created_at <= end]
    return sum(1 for a in ids if a in result.links) / max(len(ids), 1)


def close_duplicate_share(result: ReplayResult) -> float:
    """New storylines in the scored window that have a close existing candidate
    (the S-new pool), over all new storylines. The duplicate-story rate is
    p(S-new) × this share; p cancels in the relative change D7 tests, as long as a
    stratum's precision is taken as fixed (the same assumption as J)."""
    n_new = sum(1 for s in result.storylines.values() if s.created_at >= result.scored_since)
    return len(s_new_pool(result, "", {})) / max(n_new, 1)


def lever_result(base: ReplayResult, lever: ReplayResult, articles, labels: Dict[str, StratumLabels],
                 base_threshold: float, base_decay: float) -> dict:
    """D7 verdict of one lever against the baseline replay, under each label source."""
    cov_b, cov_l = coverage(base, articles), coverage(lever, articles)
    dup_b, dup_l = close_duplicate_share(base), close_duplicate_share(lever)
    counts_b = join_counts(base, base_threshold, base_decay)
    counts_l = join_counts(lever, base_threshold, base_decay)
    gain_pp = 100 * (cov_l - cov_b)
    dup_rel = (dup_l - dup_b) / dup_b if dup_b else 0.0
    benefit = gain_pp >= COVERAGE_GAIN_PP or dup_rel <= -DUPLICATE_DROP_REL
    out = {"coverage": [round(cov_b, 4), round(cov_l, 4)], "coverage_gain_pp": round(gain_pp, 2),
           "close_duplicate_share": [round(dup_b, 4), round(dup_l, 4)], "duplicate_change_rel": round(dup_rel, 3),
           "benefit": benefit, "join_counts": [counts_b, counts_l], "j_precision": {}}
    for name, sl in labels.items():
        d = paired_delta(counts_l, counts_b, sl)
        out["j_precision"][name] = {"delta": round(d["delta"], 4), "ci95": [round(x, 4) for x in d["ci95"]],
                                    "passes_bound": d["passes_bound"], "accepted": benefit and d["passes_bound"]}
    return out

