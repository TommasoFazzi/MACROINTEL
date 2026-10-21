"""
Offline chronological replay of level 1 (articles → storylines).

`storyline-formation-causal-review` design.md D3. Re-runs the narrative engine's
formation steps in memory at prod's recorded run times, from a cold start, on a
read-only export (`scripts/export_formation_snapshot.py`). Decisions come from the
same pure functions prod calls (`storyline_decisions`); this module only holds the
state that prod keeps in Postgres and follows `NarrativeProcessor.process_daily_batch`
step by step:

    load unassigned articles → micro-clusters → load emerging/active storylines →
    orphan-pool retry (expiry first) → match → assign → residual HDBSCAN → orphan
    pool → relevance filter → decay

Skipped, because they do not decide membership: LLM summaries, NIS, graph edges,
summary backfill. Without summaries the relevance filter needs a hook (D3): the
fidelity run applies prod's actual off-topic outcomes, lever runs a title-only
filter on both sides.

Known differences from prod, documented rather than corrected:
- prod evaluates `NOW()` per statement; the replay uses the run's start time for
  the whole run (decay in prod runs a few minutes later);
- DB row order without ORDER BY ties is arbitrary in prod; the replay breaks ties
  by id (loader: published_date DESC, id; candidates: momentum DESC, id);
- `merge_entities` truncates a set-ordered list, so results depend on string
  hashing: the CLI pins PYTHONHASHSEED, prod does not.

Every non-join decision and every lost article gets a `DecisionRecord` for the
attribution waterfall (D4, task 5.1 fills `primary_cause`).
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Sequence, Set
from zoneinfo import ZoneInfo

import numpy as np
from pydantic import BaseModel

from . import storyline_decisions as sd
from .config import FormationLevers, NarrativeClusteringConfig

# ---------------------------------------------------------------------------
# Lifecycle predicates. Prod runs these as SQL (`narrative_processor.DECAY_RULE_*_SQL`,
# `ORPHAN_EXPIRY_SQL`); `test_storyline_replay.py` pins each constant to that text.
# ---------------------------------------------------------------------------
RULE_1_STALE_DAYS = 7
MOMENTUM_DECAY_FACTOR = 0.7          # NarrativeProcessor.MOMENTUM_DECAY_FACTOR
RULE_2_MOMENTUM_BELOW = 0.3
RULE_3_STABILIZED_DAYS = 30
RULE_4_MIN_ARTICLES = 3
RULE_4_DAYS = 5
ORPHAN_TTL_DAYS = 14
INITIAL_MOMENTUM = 0.5               # _create_storyline_from_events
LOADER_DAYS = 1                      # daily_pipeline: process_narratives.py --days 1
H8_ARCHIVAL_RULES = ("rule_4", "rule_5", "off_topic")   # archivals hypothesis H8 is about

Outcome = Literal["joined", "new_storyline", "orphan", "orphan_recovered",
                  "orphan_expired", "never_considered"]


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class ReplayArticle:
    id: int
    title: str
    published_date: Optional[datetime]
    created_at: datetime
    embedding: Optional[np.ndarray]
    entities: dict
    category: Optional[str]

    def as_loader_row(self) -> dict:
        """The dict `DatabaseManager.get_all_article_embeddings` returns."""
        return {'id': self.id, 'title': self.title, 'embedding': self.embedding.tolist(),
                'entities': self.entities or {}, 'category': self.category,
                'published_date': self.published_date}


# ---------------------------------------------------------------------------
# State (what prod keeps in `storylines`, `article_storylines`, `orphan_events`)
# ---------------------------------------------------------------------------

@dataclass
class ReplayStoryline:
    id: int
    title: str
    original_embedding: np.ndarray
    current_embedding: np.ndarray
    key_entities: List[str]
    initial_entities: List[str]
    category: Optional[str]
    article_count: int
    momentum: float
    status: str
    created_at: datetime
    last_update: datetime
    archived_by: Optional[str] = None
    archived_at: Optional[datetime] = None


@dataclass
class OrphanEvent:
    id: int
    article_ids: List[int]
    title: str
    embedding: np.ndarray
    entities: List[str]
    category: Optional[str]
    created_at: datetime
    retry_count: int = 0
    ended_at: Optional[datetime] = None
    end: Optional[Literal["recovered", "expired"]] = None


class DecisionRecord(BaseModel):
    run_ts: datetime
    event_article_ids: List[int]
    outcome: Outcome
    storyline_id: Optional[int] = None
    best_active: Optional[dict] = None          # best emerging/active candidate, threshold ignored
    best_active_no_penalty: Optional[dict] = None
    best_any_status: Optional[dict] = None      # same scoring, stabilized/archived included
    best_active_creation_boost: Optional[dict] = None  # boost from each storyline's initial entities (H4)
    # best storyline archived by an H8 rule / best stabilized one, ranked WITHOUT the time
    # penalty (`score_no_penalty`); flags the H8×H1 and H2×H1 overlaps
    best_archived_h8: Optional[dict] = None
    best_stabilized: Optional[dict] = None
    target_status: Optional[str] = None         # status of the joined storyline just before the join
    orphan_id: Optional[int] = None
    primary_cause: Optional[str] = None
    flags: List[str] = []


@dataclass
class ReplayResult:
    records: List[DecisionRecord]
    storylines: Dict[int, ReplayStoryline]
    links: Dict[int, Set[int]]                   # article_id -> replay storyline ids
    members: Dict[int, Set[int]]                 # replay storyline id -> article ids
    link_time: Dict[tuple, datetime]             # (article_id, storyline_id) -> run that linked it
    orphans: List[OrphanEvent]
    orphan_log: Dict[int, OrphanEvent]           # every orphan ever pooled, by id
    run_times: List[datetime]
    scored_since: datetime


OffTopicHook = Callable[[ReplayStoryline, Set[int]], bool]


def title_only_off_topic(storyline: ReplayStoryline, article_ids: Set[int]) -> bool:
    """Lever runs (D3): the relevance predicate on the title alone, no summary."""
    return sd.is_off_topic(storyline.title, None)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

IMPLEMENTED_LEVERS = {"link_count_fix", "time_penalty", "time_penalty_cap",         # L0, L1
                      "match_stabilized",                                             # L2
                      "small_storyline_ttl_from", "small_storyline_ttl_days", "archived_grace_days"}  # L8
L8_REVIVABLE_RULES = ("rule_4", "rule_5")     # small/stale emerging storylines; never off-topic ones


def _require_implemented(levers: FormationLevers) -> None:
    changed = {k for k, v in levers.model_dump().items() if v != FormationLevers.model_fields[k].default}
    missing = changed - IMPLEMENTED_LEVERS
    if missing:
        raise NotImplementedError(f"levers not implemented in the replay yet (tasks.md 7.1): {sorted(missing)}")


class StorylineReplay:
    def __init__(self, articles: Sequence[ReplayArticle], cfg: NarrativeClusteringConfig,
                 levers: Optional[FormationLevers] = None,
                 off_topic: OffTopicHook = title_only_off_topic,
                 burn_in_days: int = 30):
        from .narrative_processor import NarrativeProcessor  # entity helpers only, no DB

        self.levers = levers or cfg.formation_levers
        _require_implemented(self.levers)
        self.cfg = cfg
        self.params = sd.MatchParams(
            threshold=cfg.matching.threshold,
            time_decay_factor=cfg.matching.time_decay_factor,
            entity_boost=cfg.matching.entity_boost,
            entity_jaccard_threshold=cfg.matching.entity_jaccard_threshold,
            time_penalty_mode=self.levers.time_penalty,
            time_penalty_cap=self.levers.time_penalty_cap,
        )
        self.off_topic = off_topic
        self.burn_in = timedelta(days=burn_in_days)
        self._extract = NarrativeProcessor._extract_entity_list
        self._clean = NarrativeProcessor._clean_entity
        self._garbage = NarrativeProcessor._is_garbage_entity

        self.articles = {a.id: a for a in articles}
        self.storylines: Dict[int, ReplayStoryline] = {}
        self.storyline_articles: Dict[int, Set[int]] = {}
        self.links: Dict[int, Set[int]] = {}
        self.link_time: Dict[tuple, datetime] = {}
        self.orphans: List[OrphanEvent] = []
        self.orphan_log: Dict[int, OrphanEvent] = {}
        self.records: List[DecisionRecord] = []
        self.loaded: Set[int] = set()
        self._next_storyline = 1
        self._next_orphan = 1

    # -- prod: DatabaseManager.get_all_article_embeddings(days, exclude_assigned=True)
    def _load_articles(self, now: datetime) -> List[dict]:
        since = now - timedelta(days=LOADER_DAYS)
        # L0: prod re-reads articles waiting in the orphan pool (task 1.4); the fix skips them
        pooled = {aid for o in self.orphans for aid in o.article_ids} if self.levers.link_count_fix else set()
        rows = [a for a in self.articles.values()
                if a.embedding is not None and a.created_at <= now
                and a.published_date is not None and a.published_date > since
                and a.id not in self.links and a.id not in pooled]
        rows.sort(key=lambda a: (-a.published_date.timestamp(), a.id))
        return [a.as_loader_row() for a in rows]

    # -- prod: NarrativeProcessor._load_active_storylines
    def _active_storylines(self, now: Optional[datetime] = None) -> List[dict]:
        active = [s for s in self.storylines.values() if self._is_candidate(s, now)]
        active.sort(key=lambda s: (-s.momentum, s.id))
        return [self._as_candidate(s) for s in active]

    def _is_candidate(self, s: ReplayStoryline, now: Optional[datetime]) -> bool:
        if s.status in ('emerging', 'active'):
            return True
        if s.status == 'stabilized':
            return self.levers.match_stabilized                                  # L2
        grace = self.levers.archived_grace_days                                  # L8
        return (s.status == 'archived' and grace > 0 and now is not None
                and s.archived_by in L8_REVIVABLE_RULES and s.archived_at >= now - timedelta(days=grace))

    @staticmethod
    def _as_candidate(s: ReplayStoryline) -> dict:
        return {'storyline_id': s.id, 'title': s.title, 'current_embedding': s.current_embedding,
                'key_entities': set(e.lower() for e in s.key_entities), 'last_update': s.last_update,
                'narrative_status': s.status, 'article_count': s.article_count,
                'momentum_score': s.momentum}

    def _link(self, article_ids: Sequence[int], storyline_id: int, now: datetime) -> int:
        """INSERT ... ON CONFLICT (article_id, storyline_id) DO NOTHING; returns the rowcount."""
        members = self.storyline_articles.setdefault(storyline_id, set())
        inserted = 0
        for aid in article_ids:
            inserted += aid not in members
            members.add(aid)
            self.links.setdefault(aid, set()).add(storyline_id)
            self.link_time.setdefault((aid, storyline_id), now)
        return inserted

    # -- prod: NarrativeProcessor._assign_event_to_storyline
    def _assign(self, event: dict, storyline_id: int, now: datetime) -> None:
        inserted = self._link(event['article_ids'], storyline_id, now)
        s = self.storylines[storyline_id]
        current_emb = s.current_embedding if s.current_embedding is not None else event['embedding']
        s.current_embedding = sd.drift_embedding(
            current_emb, event['embedding'],
            self.cfg.evolution.drift_weight_old, self.cfg.evolution.drift_weight_new)
        new_clean = [self._clean(e) for e in event['entities'] if not self._garbage(self._clean(e))]
        s.key_entities = sd.merge_entities(set(s.key_entities), new_clean)
        # L0: count the links actually inserted, as prod's fix will (rowcount)
        s.article_count += inserted if self.levers.link_count_fix else len(event['article_ids'])
        s.momentum = min(1.0, s.momentum + sd.momentum_bump(len(event['article_ids'])))
        if s.status == 'stabilized':                  # L2: a join reactivates it
            s.status = 'active'
        elif s.status == 'archived':                  # L8: a join within the grace period revives it
            s.status, s.archived_by, s.archived_at = 'emerging', None, None
        s.status = sd.promote_status(s.status, s.article_count)
        s.last_update = now

    # -- prod: NarrativeProcessor._create_storyline_from_events
    def _create_storyline(self, events: Sequence[dict], now: datetime) -> int:
        seed = sd.storyline_seed(events)
        sid = self._next_storyline
        self._next_storyline += 1
        self.storylines[sid] = ReplayStoryline(
            id=sid, title=seed['title'], original_embedding=seed['centroid'],
            current_embedding=seed['centroid'], key_entities=list(seed['top_entities']),
            initial_entities=list(seed['top_entities']), category=seed['category'],
            article_count=len(seed['article_ids']), momentum=INITIAL_MOMENTUM, status='emerging',
            created_at=now, last_update=now)
        self._link(seed['article_ids'], sid, now)
        return sid

    # -- prod: NarrativeProcessor._store_orphan_events
    def _store_orphans(self, events: Sequence[dict], now: datetime) -> List[int]:
        ids = []
        for event in events:
            entities = [self._clean(e) for e in event.get('entities', [])
                        if not self._garbage(self._clean(e))]
            o = OrphanEvent(
                id=self._next_orphan, article_ids=list(event['article_ids']),
                title=event.get('representative_title', '')[:200],
                embedding=sd.orphan_embedding(event), entities=entities[:15],
                category=event.get('category'), created_at=now)
            self.orphans.append(o)
            self.orphan_log[o.id] = o
            ids.append(o.id)
            self._next_orphan += 1
        return ids

    def _best_creation_boost(self, event: dict, scores) -> Optional[dict]:
        """Best active candidate if each storyline still had its creation entities (H4)."""
        event_entities = set(e.lower() for e in event['entities'])
        best, best_score = None, None
        for c in scores:
            initial = set(e.lower() for e in self.storylines[c.storyline_id].initial_entities)
            boost = 0.0
            if event_entities and initial:
                jaccard = len(event_entities & initial) / len(event_entities | initial)
                if jaccard >= self.params.entity_jaccard_threshold:
                    boost = self.params.entity_boost
            score = c.cosine_sim - c.time_penalty + boost
            if best_score is None or score > best_score:
                best, best_score = c, score
        if best is None:
            return None
        return _candidate_dict(best) | {'boost': float(
            best_score - best.cosine_sim + best.time_penalty), 'score': float(best_score)}

    # -- attribution context (D4): not used by any decision
    def _best_other_status(self, event: dict, now: datetime):
        """(best stabilized/archived by prod score, best H8-archived and best stabilized by
        score without penalty). Attribution context only, never used by a decision."""
        others = [s for s in self.storylines.values() if s.status not in ('emerging', 'active')]
        if not others:
            return None, None, None
        scores = sd.score_candidates(event, [self._as_candidate(s) for s in others], now, self.params)
        if not scores:
            return None, None, None

        def describe(c):
            s = self.storylines[c.storyline_id]
            return _candidate_dict(c) | {'status': s.status, 'archived_by': s.archived_by,
                                         'score_no_penalty': float(c.score + c.time_penalty)}

        best = describe(max(scores, key=lambda c: c.score))
        groups = {'h8': [c for c in scores if self.storylines[c.storyline_id].archived_by in H8_ARCHIVAL_RULES],
                  'stabilized': [c for c in scores if self.storylines[c.storyline_id].status == 'stabilized']}
        picked = {k: describe(max(v, key=lambda c: c.score + c.time_penalty)) if v else None
                  for k, v in groups.items()}
        return best, picked['h8'], picked['stabilized']

    def _record(self, now, event, outcome, storyline_id=None, scores=None, orphan_id=None,
                target_status=None) -> None:
        best_active = best_np = best_cb = None
        if scores and outcome != 'joined':
            best_cb = self._best_creation_boost(event, scores)
        if scores:
            best_active = _candidate_dict(max(scores, key=lambda c: c.score))
            c = max(scores, key=lambda c: c.score + c.time_penalty)
            best_np = _candidate_dict(c) | {'time_penalty': 0.0, 'score': float(c.score + c.time_penalty)}
        best_any = best_h8 = best_stab = None
        if outcome != 'joined':
            best_any, best_h8, best_stab = self._best_other_status(event, now)
        if best_any is not None and best_active is not None and best_active['score'] >= best_any['score']:
            best_any = best_active | {'status': self.storylines[best_active['storyline_id']].status,
                                      'archived_by': None}
        self.records.append(DecisionRecord(
            run_ts=now, event_article_ids=list(event['article_ids']), outcome=outcome,
            storyline_id=storyline_id, best_active=best_active,
            best_active_no_penalty=best_np, best_any_status=best_any,
            best_active_creation_boost=best_cb, best_archived_h8=best_h8,
            best_stabilized=best_stab, target_status=target_status, orphan_id=orphan_id))

    # -- prod: NarrativeProcessor._retry_orphan_pool
    def _retry_orphans(self, active: List[dict], now: datetime) -> int:
        if not active:
            return 0
        cutoff = now - timedelta(days=ORPHAN_TTL_DAYS)
        expired = [o for o in self.orphans if o.created_at < cutoff]
        for o in expired:
            o.ended_at, o.end = now, 'expired'
            self.records.append(DecisionRecord(run_ts=now, event_article_ids=o.article_ids,
                                               outcome='orphan_expired', orphan_id=o.id))
        self.orphans = sorted((o for o in self.orphans if o.created_at >= cutoff),
                              key=lambda o: (o.created_at, o.id))
        recovered = set()
        for o in self.orphans:
            pseudo = {'article_ids': o.article_ids, 'representative_title': o.title,
                      'embedding': o.embedding, 'entities': o.entities or [], 'category': o.category}
            scores = sd.score_candidates(pseudo, active, now, self.params)
            match = sd.select_match(scores, self.params.threshold)
            if match:
                status = self.storylines[match.storyline_id].status
                self._assign(pseudo, match.storyline_id, now)
                recovered.add(o.id)
                o.ended_at, o.end = now, 'recovered'
                self._record(now, pseudo, 'orphan_recovered', match.storyline_id, scores, orphan_id=o.id,
                             target_status=status)
        for o in self.orphans:
            if o.id not in recovered:
                o.retry_count += 1
        self.orphans = [o for o in self.orphans if o.id not in recovered]
        return len(recovered)

    # -- prod: NarrativeProcessor._apply_decay (SQL predicates in Python)
    def _decay(self, now: datetime) -> None:
        ttl = self.cfg.decay.emerging_ttl_days
        for s in self.storylines.values():                                   # rule_1
            if s.status in ('emerging', 'active') and s.last_update < now - timedelta(days=RULE_1_STALE_DAYS):
                s.momentum = s.momentum * MOMENTUM_DECAY_FACTOR
        for s in self.storylines.values():                                   # rule_2
            if s.status == 'active' and s.momentum < RULE_2_MOMENTUM_BELOW:
                s.status = 'stabilized'
        rules = [
            ('rule_3', lambda s: s.status == 'stabilized'
             and s.last_update < now - timedelta(days=RULE_3_STABILIZED_DAYS)),
            ('rule_4', lambda s: s.status == 'emerging' and s.article_count < RULE_4_MIN_ARTICLES
             and self._rule_4_clock(s) < now - timedelta(days=self.levers.small_storyline_ttl_days)),
            ('rule_5', lambda s: s.status == 'emerging' and s.last_update < now - timedelta(days=ttl)),
        ]
        for name, predicate in rules:
            for s in self.storylines.values():
                if predicate(s):
                    self._archive(s, name, now)

    def _rule_4_clock(self, s: ReplayStoryline) -> datetime:
        """rule_4 counts from creation (prod) or, under L8, from the last join."""
        return s.last_update if self.levers.small_storyline_ttl_from == 'last_join' else s.created_at

    def _archive(self, s: ReplayStoryline, by: str, now: datetime) -> None:
        s.status, s.archived_by, s.archived_at = 'archived', by, now

    # -- prod: NarrativeProcessor.process_daily_batch (non-dry-run)
    def run_once(self, now: datetime) -> None:
        articles = self._load_articles(now)
        if not articles:
            return                       # prod returns before retry and decay
        self.loaded.update(a['id'] for a in articles)

        events = sd.create_micro_clusters(articles, self.cfg.micro_cluster.threshold, self._extract)
        active = self._active_storylines(now)
        if self._retry_orphans(active, now) > 0:
            active = self._active_storylines(now)

        matched, orphaned = [], []
        for event in events:
            scores = sd.score_candidates(event, active, now, self.params)
            match = sd.select_match(scores, self.params.threshold)
            if match:
                matched.append((event, match.storyline_id, scores))
            else:
                orphaned.append((event, scores))

        updated = set()
        for event, sid, scores in matched:
            status = self.storylines[sid].status
            self._assign(event, sid, now)
            updated.add(sid)
            self._record(now, event, 'joined', sid, scores, target_status=status)

        mcs, ms = self.cfg.hdbscan.min_cluster_size_base, self.cfg.hdbscan.min_samples
        if len(orphaned) < mcs:
            noise = orphaned
        else:
            labels = sd.residual_labels(np.array([e['embedding'] for e, _ in orphaned]), mcs, ms)
            clusters: Dict[int, list] = {}
            noise = []
            for (event, scores), label in zip(orphaned, labels):
                if label == -1:
                    noise.append((event, scores))
                else:
                    clusters.setdefault(int(label), []).append((event, scores))
            for label in sorted(clusters):
                sid = self._create_storyline([e for e, _ in clusters[label]], now)
                updated.add(sid)
                for event, scores in clusters[label]:
                    self._record(now, event, 'new_storyline', sid, scores)
        orphan_ids = self._store_orphans([e for e, _ in noise], now)
        for (event, scores), oid in zip(noise, orphan_ids):
            self._record(now, event, 'orphan', None, scores, orphan_id=oid)

        for sid in sorted(updated):
            s = self.storylines[sid]
            if s.status != 'archived' and self.off_topic(s, self.storyline_articles[sid]):
                self._archive(s, 'off_topic', now)

        self._decay(now)

    def run(self, run_times: Sequence[datetime]) -> ReplayResult:
        run_times = sorted(run_times)
        for now in run_times:
            self.run_once(now)
        end = run_times[-1] if run_times else None
        for a in sorted(self.articles.values(), key=lambda a: a.id):
            if a.id not in self.loaded and end is not None and a.created_at <= end:
                self.records.append(DecisionRecord(run_ts=end, event_article_ids=[a.id],
                                                   outcome='never_considered'))
        return ReplayResult(
            records=self.records, storylines=self.storylines, links=self.links,
            members=self.storyline_articles, link_time=self.link_time,
            orphans=self.orphans, orphan_log=self.orphan_log, run_times=list(run_times),
            scored_since=(run_times[0] + self.burn_in) if run_times else None)


def _candidate_dict(c: sd.CandidateScore) -> dict:
    return {'storyline_id': c.storyline_id, 'cosine': float(c.cosine_sim),
            'time_penalty': float(c.time_penalty), 'boost': float(c.entity_boost),
            'score': float(c.score), 'days_inactive': float(c.days_inactive)}


# ---------------------------------------------------------------------------
# Export loading (scripts/export_formation_snapshot.py)
# ---------------------------------------------------------------------------

@dataclass
class FormationSnapshot:
    articles: List[ReplayArticle]
    prod_links: Dict[int, Set[int]]             # article_id -> prod storyline ids
    prod_storylines: Dict[int, dict]
    run_starts: List[datetime]
    manifest: dict = field(default_factory=dict)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _aware(ts: Optional[str], tz: ZoneInfo) -> Optional[datetime]:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def load_formation_snapshot(path: Path) -> FormationSnapshot:
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text())
    for name, digest in manifest["file_sha256"].items():
        if _sha256(path / name) != digest:
            raise ValueError(f"{name}: SHA-256 does not match the manifest")
    tz = ZoneInfo(manifest["db_timezone"])

    z = np.load(path / "articles.npz")
    # NpzFile decompresses an array on every key access: read each one once
    npz_ids, embedding, has_embedding = z["ids"], z["embedding"], z["has_embedding"]
    rows = [json.loads(line) for line in (path / "articles.jsonl").read_text().splitlines()]
    articles = [
        ReplayArticle(
            id=r["id"], title=r["title"] or "", published_date=_aware(r["published_date"], tz),
            created_at=_aware(r["created_at"], tz),
            embedding=embedding[i].astype(np.float64) if has_embedding[i] else None,
            entities=r["entities"] or {}, category=r["category"])
        for i, r in enumerate(rows)
    ]
    if [a.id for a in articles] != npz_ids.tolist():
        raise ValueError("articles.npz and articles.jsonl are not aligned")

    prod_links: Dict[int, Set[int]] = {}
    with open(path / "links.csv") as f:
        for r in csv.DictReader(f):
            prod_links.setdefault(int(r["article_id"]), set()).add(int(r["storyline_id"]))
    prod_storylines = {}
    for line in (path / "storylines.jsonl").read_text().splitlines():
        r = json.loads(line)
        r["created_at"] = _aware(r["created_at"], tz)
        prod_storylines[r["id"]] = r
    with open(path / "runs.csv") as f:
        # narrative_run_metrics.ts is written at the end of a run; the loader's NOW() is at its start
        run_starts = [_aware(r["ts"], tz) - timedelta(seconds=float(r["runtime_seconds"] or 0))
                      for r in csv.DictReader(f)]
    return FormationSnapshot(articles, prod_links, prod_storylines, run_starts, manifest)


def prod_off_topic_hook(snapshot: FormationSnapshot) -> OffTopicHook:
    """Fidelity run (D3): archive a replay storyline when the prod storyline sharing
    most of its articles was archived as off-topic. Prod does not record the reason
    for an archival, so off-topic is the relevance predicate on the final title and
    summary of an archived prod storyline (archival freezes them)."""
    off_topic_ids = {sid for sid, s in snapshot.prod_storylines.items()
                     if s["narrative_status"] == "archived" and sd.is_off_topic(s["title"], s["summary"])}

    def hook(storyline: ReplayStoryline, article_ids: Set[int]) -> bool:
        votes: Dict[int, int] = {}
        for aid in article_ids:
            for psid in snapshot.prod_links.get(aid, ()):
                votes[psid] = votes.get(psid, 0) + 1
        if not votes:
            return False
        majority = min(votes, key=lambda k: (-votes[k], k))
        return majority in off_topic_ids
    return hook


# ---------------------------------------------------------------------------
# Fidelity report (D3)
# ---------------------------------------------------------------------------

class FidelityReport(BaseModel):
    scored_since: datetime
    n_scored_articles: int
    ari_co_membership: Optional[float]
    n_articles_linked_both: int
    coverage_replay: float
    coverage_prod: float
    new_storylines_per_day_replay: float
    new_storylines_per_day_prod: float
    off_topic_divergence: int


def fidelity_report(result: ReplayResult, snapshot: FormationSnapshot) -> FidelityReport:
    from sklearn.metrics import adjusted_rand_score

    since = result.scored_since
    scored = [a for a in snapshot.articles
              if a.embedding is not None and a.created_at >= since and a.created_at <= result.run_times[-1]]
    ids = [a.id for a in scored]
    both = [aid for aid in ids if aid in result.links and aid in snapshot.prod_links]
    ari = None
    if len(both) >= 2:
        ari = float(adjusted_rand_score([min(snapshot.prod_links[a]) for a in both],
                                        [min(result.links[a]) for a in both]))
    days = max((result.run_times[-1] - since).total_seconds() / 86400, 1e-9)
    new_replay = sum(1 for s in result.storylines.values() if s.created_at >= since)
    new_prod = sum(1 for s in snapshot.prod_storylines.values()
                   if s["created_at"] is not None and since <= s["created_at"] <= result.run_times[-1])

    hook = prod_off_topic_hook(snapshot)
    divergence = sum(1 for sid, s in result.storylines.items()
                     if title_only_off_topic(s, set()) != hook(s, result.members.get(sid, set())))
    n = max(len(ids), 1)
    return FidelityReport(
        scored_since=since, n_scored_articles=len(ids), ari_co_membership=ari,
        n_articles_linked_both=len(both),
        coverage_replay=sum(1 for a in ids if a in result.links) / n,
        coverage_prod=sum(1 for a in ids if a in snapshot.prod_links) / n,
        new_storylines_per_day_replay=new_replay / days,
        new_storylines_per_day_prod=new_prod / days,
        off_topic_divergence=divergence)
