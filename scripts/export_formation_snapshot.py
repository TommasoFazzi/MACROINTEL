#!/usr/bin/env python3
"""
Read-only export for the level-1 storyline formation replay.

Freezes what the chronological replay needs to re-run articles → storylines
offline: the articles of the window (embeddings and the entity fields the
narrative engine reads), prod's article → storyline links with each storyline's
status and final title/summary, the narrative_processing run times, and the
orphan pool as it is now.

Runs inside the prod backend container, piped over stdin (from the server's
repo checkout), like export_clustering_snapshot.py:

    docker compose -p app exec -T backend python - --probe < scripts/export_formation_snapshot.py
    docker compose -p app exec -T backend python - --git-sha "$(git rev-parse HEAD)" \
        < scripts/export_formation_snapshot.py
    docker compose -p app cp backend:/app/artifacts/formation_snapshot_<date> ./artifacts/

Issues SELECT statements only. Spec reference:
openspec/changes/storyline-formation-causal-review/ design.md § D3 and
specs/storyline-formation-review/spec.md ("Read-only formation export").
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Piped over stdin there is no __file__: resolve the repo root from cwd (/app).
_root = Path(__file__).resolve().parent.parent if "__file__" in globals() else Path.cwd()
sys.path.insert(0, str(_root))

import numpy as np  # noqa: E402

from scripts.export_clustering_snapshot import _execute, _sha256, _write_csv  # noqa: E402

EMBEDDING_DIM = 384

# Every article created in the window, embedded or not: the replay applies prod's
# loader predicate itself (embedding present, published_date within the run's
# window), so undated and late articles must be kept. Only the entity fields that
# NarrativeProcessor._extract_entity_list reads are exported.
ARTICLES_SQL = """
    SELECT a.id, a.title, a.published_date, a.created_at, a.full_text_embedding,
           jsonb_strip_nulls(jsonb_build_object(
               'clean', jsonb_build_object('all', a.entities->'clean'->'all'),
               'by_type', jsonb_build_object(
                   'GPE', a.entities->'by_type'->'GPE',
                   'ORG', a.entities->'by_type'->'ORG',
                   'PERSON', a.entities->'by_type'->'PERSON'))),
           a.category, a.source
    FROM articles a
    WHERE a.created_at > NOW() - make_interval(days => %s)
    ORDER BY a.created_at, a.id
"""

LINKS_SQL = """
    SELECT als.article_id, als.storyline_id, als.is_origin, als.added_at,
           s.created_at, s.narrative_status, s.article_count
    FROM article_storylines als
    JOIN articles a ON a.id = als.article_id
    JOIN storylines s ON s.id = als.storyline_id
    WHERE a.created_at > NOW() - make_interval(days => %s)
    ORDER BY als.added_at, als.article_id
"""

# Final title and summary of every storyline linked to a window article. Archival
# freezes them, so for an archived storyline they are the text the relevance
# filter last saw: the fidelity run re-applies the predicate to tell off-topic
# archival apart from decay (status columns cannot: the sync trigger sets
# status='ARCHIVED' for every archival).
STORYLINES_SQL = """
    SELECT s.id, s.title, s.summary, s.narrative_status, s.created_at, s.last_update,
           s.article_count
    FROM storylines s
    WHERE s.id IN (
        SELECT als.storyline_id
        FROM article_storylines als JOIN articles a ON a.id = als.article_id
        WHERE a.created_at > NOW() - make_interval(days => %s)
    )
    ORDER BY s.id
"""

RUNS_SQL = """
    SELECT ts, run_id::text, runtime_seconds, match_stats::text, decay_stats::text
    FROM narrative_run_metrics
    WHERE pipeline_step = 'narrative_processing'
      AND ts > NOW() - make_interval(days => %s)
    ORDER BY ts
"""

ORPHANS_SQL = """
    SELECT id, article_ids, created_at, retry_count, last_retry
    FROM orphan_events
    ORDER BY created_at, id
"""

SETTINGS_SQL = "SELECT current_setting('TimeZone'), NOW()"

DATA_FILES = ["articles.npz", "articles.jsonl", "links.csv", "storylines.jsonl",
              "runs.csv", "orphans.csv"]


def _iso(x):
    return x.isoformat() if x is not None else None


def export(db, out: Path, git_sha: str, days: int) -> dict:
    from scripts.compute_communities import _vec_to_array

    out.mkdir(parents=True, exist_ok=True)
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            db_timezone, db_now = _execute(cur, SETTINGS_SQL)[0]
            articles = _execute(cur, ARTICLES_SQL, (days,))
            links = _execute(cur, LINKS_SQL, (days,))
            storylines = _execute(cur, STORYLINES_SQL, (days,))
            runs = _execute(cur, RUNS_SQL, (days,))
            orphans = _execute(cur, ORPHANS_SQL)

    # --- articles.npz + articles.jsonl --------------------------------------
    n = len(articles)
    ids = np.array([r[0] for r in articles], dtype=np.int64)
    emb = np.zeros((n, EMBEDDING_DIM), dtype=np.float32)
    has_emb = np.zeros(n, dtype=bool)
    with open(out / "articles.jsonl", "w") as f:
        for i, (aid, title, published, created, vec, entities, category, source) in enumerate(articles):
            arr = _vec_to_array(vec)
            if arr is not None:
                emb[i], has_emb[i] = arr, True
            f.write(json.dumps({
                "id": aid, "title": title, "published_date": _iso(published),
                "created_at": _iso(created), "entities": entities or {},
                "category": category, "source": source,
            }, ensure_ascii=False) + "\n")
    np.savez_compressed(out / "articles.npz", ids=ids, embedding=emb, has_embedding=has_emb)

    # --- links, storylines, runs, orphans -------------------------------------
    _write_csv(out / "links.csv",
               ["article_id", "storyline_id", "is_origin", "added_at",
                "storyline_created_at", "narrative_status", "article_count"], links)
    with open(out / "storylines.jsonl", "w") as f:
        for sid, title, summary, status, created, last_update, count in storylines:
            f.write(json.dumps({
                "id": sid, "title": title, "summary": summary, "narrative_status": status,
                "created_at": _iso(created), "last_update": _iso(last_update),
                "article_count": count,
            }, ensure_ascii=False) + "\n")
    _write_csv(out / "runs.csv",
               ["ts", "run_id", "runtime_seconds", "match_stats", "decay_stats"], runs)
    _write_csv(out / "orphans.csv",
               ["id", "article_ids", "created_at", "retry_count", "last_retry"],
               [(oid, " ".join(str(a) for a in aids), c, r, lr) for oid, aids, c, r, lr in orphans])

    published = [r[2] for r in articles]
    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "db_now": _iso(db_now),
        # storylines.created_at/last_update and article_storylines.added_at are
        # TIMESTAMP without time zone: they are in this zone.
        "db_timezone": db_timezone,
        "git_sha": git_sha,
        "window_days": days,
        "n_articles": n,
        "n_articles_embedded": int(has_emb.sum()),
        "n_articles_undated": sum(p is None for p in published),
        "n_links": len(links),
        "n_storylines": len(storylines),
        "n_runs": len(runs),
        "n_orphan_events": len(orphans),
        "file_sha256": {name: _sha256(out / name) for name in DATA_FILES},
    }
    with open(out / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def probe(out_parent: Path) -> int:
    print(f"cwd: {os.getcwd()}")
    import scripts.compute_communities  # noqa: F401
    import scripts.export_clustering_snapshot  # noqa: F401
    print("imports: ok")
    out_parent.mkdir(parents=True, exist_ok=True)
    test = out_parent / ".probe"
    test.write_text("ok")
    test.unlink()
    print(f"write {out_parent}: ok")
    return 0


def main(argv=None) -> int:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=Path(f"/app/artifacts/formation_snapshot_{today}"))
    parser.add_argument("--days", type=int, default=90, help="window on articles.created_at")
    parser.add_argument("--git-sha", default="unknown",
                        help="commit of the checkout the script was piped from")
    parser.add_argument("--probe", action="store_true",
                        help="only check cwd, imports and that the output parent is writable")
    args = parser.parse_args(argv)

    if args.probe:
        return probe(args.out.parent)

    from src.storage.database import DatabaseManager
    m = export(DatabaseManager(), args.out, args.git_sha, args.days)
    print(f"Formation snapshot written to {args.out}")
    print(f"  articles: {m['n_articles']} (embedded {m['n_articles_embedded']}, undated {m['n_articles_undated']})")
    print(f"  links: {m['n_links']}, storylines: {m['n_storylines']}, runs: {m['n_runs']}, "
          f"orphan events: {m['n_orphan_events']}")
    print(f"  db timezone: {m['db_timezone']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
