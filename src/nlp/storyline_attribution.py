"""
Causal attribution waterfall for the level-1 replay.

`storyline-formation-causal-review` design.md D4. Every fragmentation event of a
replay (an event that became a new storyline or went to the orphan pool) and
every lost article (never considered, or expired in the orphan pool) gets the
first cause that applies, in a fixed order:

    1. never_considered      H7  the loader never read the article
    2. archived_candidate    H8  best candidate of any status, prod scoring with the
                                 time penalty, is a storyline archived by rule_4,
                                 rule_5 or the relevance filter, and clears the threshold
    3. stabilized_candidate  H2  the same, but the candidate is stabilized
    4. time_penalty          H1  the best emerging/active candidate clears without the penalty
    5. boost_lost            H4  it clears with the boost from the storyline's creation entities
    6. orphan_twin           H3  an orphan with a later orphan at cosine >= 0.75 before it ends
    7. residual                  none of the above: a new story, or H6

Non-exclusive flags record every cause that applies. An expired orphan inherits
the cause of the record that pooled it (the candidate context at pooling time);
retries in between are not re-scored. The waterfall says what *would have
joined*, not whether that join would have been right: correctness comes from the
labelled sets (D5).

H8 and H2 as primary causes use the single best candidate of any status under
prod scoring, penalty included, so they count only cases that L8 or L2 alone
would fix. The overlap flags `H8xH1` and `H2xH1` mark events whose best
H8-archived (or stabilized) storyline clears only without the time penalty: those
need L1 together with L8 (or L2).
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

import numpy as np

from .storyline_replay import H8_ARCHIVAL_RULES, DecisionRecord, ReplayResult

CAUSE_ORDER = ["never_considered", "archived_candidate", "stabilized_candidate",
               "time_penalty", "boost_lost", "orphan_twin", "residual"]
FLAG_OF = {"never_considered": "H7", "archived_candidate": "H8", "stabilized_candidate": "H2",
           "time_penalty": "H1", "boost_lost": "H4", "orphan_twin": "H3"}
H8_RULES = set(H8_ARCHIVAL_RULES)
ORPHAN_TWIN_COSINE = 0.75
FRAGMENTATION = ("new_storyline", "orphan")
LOST = ("never_considered", "orphan_expired")


def _clears(candidate: Optional[dict], threshold: float) -> bool:
    return candidate is not None and candidate["score"] >= threshold


def orphans_with_twin(result: ReplayResult, cosine: float = ORPHAN_TWIN_COSINE) -> set:
    """Ids of orphans that, before they were recovered or expired (or the replay
    ended), were followed into the pool by another orphan at cosine >= `cosine`."""
    log = sorted(result.orphan_log.values(), key=lambda o: (o.created_at, o.id))
    if not log:
        return set()
    end_of_replay = result.run_times[-1]
    created = np.array([o.created_at.timestamp() for o in log])
    ends = np.array([(o.ended_at or end_of_replay).timestamp() for o in log])
    emb = np.stack([o.embedding / max(np.linalg.norm(o.embedding), 1e-10) for o in log])
    twins = set()
    for start in range(0, len(log), 1000):
        block = emb[start:start + 1000] @ emb.T
        for k in range(block.shape[0]):
            i = start + k
            later = (created > created[i]) & (created <= ends[i])
            if np.any(block[k][later] >= cosine):
                twins.add(log[i].id)
    return twins


def _flags(rec: DecisionRecord, threshold: float, twins: set) -> List[str]:
    flags = []
    if rec.outcome == "never_considered":
        return ["H7"]
    best_any = rec.best_any_status
    if _clears(best_any, threshold) and best_any.get("status") == "archived" \
            and best_any.get("archived_by") in H8_RULES:
        flags.append("H8")
    if _clears(best_any, threshold) and best_any.get("status") == "stabilized":
        flags.append("H2")
    if _clears(rec.best_active_no_penalty, threshold):
        flags.append("H1")
    if _clears(rec.best_active_creation_boost, threshold):
        flags.append("H4")
    if rec.orphan_id is not None and rec.orphan_id in twins:
        flags.append("H3")
    for flag, cand in (("H8xH1", rec.best_archived_h8), ("H2xH1", rec.best_stabilized)):
        if cand is not None and cand["score"] < threshold <= cand["score_no_penalty"]:
            flags.append(flag)
    return flags


def _primary(flags: List[str]) -> str:
    for cause in CAUSE_ORDER[:-1]:
        if FLAG_OF[cause] in flags:
            return cause
    return "residual"


def attribute(result: ReplayResult, threshold: float) -> None:
    """Set `primary_cause` and `flags` on every fragmentation and loss record."""
    twins = orphans_with_twin(result)
    pooled_by: Dict[int, DecisionRecord] = {}
    for rec in result.records:
        if rec.outcome in FRAGMENTATION or rec.outcome == "never_considered":
            rec.flags = _flags(rec, threshold, twins)
            rec.primary_cause = _primary(rec.flags)
            if rec.outcome == "orphan":
                pooled_by[rec.orphan_id] = rec
    for rec in result.records:
        if rec.outcome == "orphan_expired":
            origin = pooled_by.get(rec.orphan_id)
            rec.flags = list(origin.flags) if origin else []
            rec.primary_cause = origin.primary_cause if origin else "residual"


def waterfall(result: ReplayResult) -> dict:
    """Counts by primary cause and by flag over the scored window (after burn-in).

    `fragmentation` counts events and their articles (new storylines + orphans at
    pooling time); `lost` counts articles never linked (never considered + expired).
    """
    scored = [r for r in result.records if r.run_ts >= result.scored_since]

    def tally(records):
        by_cause = {c: {"events": 0, "articles": 0} for c in CAUSE_ORDER}
        flags = Counter()
        for r in records:
            by_cause[r.primary_cause]["events"] += 1
            by_cause[r.primary_cause]["articles"] += len(r.event_article_ids)
            flags.update(r.flags)
        n = sum(v["articles"] for v in by_cause.values())
        return {"by_cause": by_cause, "flags_events": dict(flags), "n_articles": n,
                "n_events": len(records)}

    return {
        "scored_since": result.scored_since.isoformat(),
        "fragmentation": tally([r for r in scored if r.outcome in FRAGMENTATION]),
        "lost": tally([r for r in scored if r.outcome in LOST]),
        "outcomes_events": dict(Counter(r.outcome for r in scored)),
    }
