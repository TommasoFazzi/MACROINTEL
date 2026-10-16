#!/usr/bin/env python3
"""
Blind labelling files for the level-1 formation review (J and S sets).

`storyline-formation-causal-review` design.md D5, tasks.md 6.3. Local only:

    # 1. sample (runs the replay at both bracket ends, ~10 minutes)
    python scripts/formation_labels.py sample artifacts/formation_snapshot_<date>
    # 2. the user runs the generated read-only leads query in prod and copies the CSV back
    # 3. add the article leads to the J file
    python scripts/formation_labels.py merge-leads data/formation_reference/leads_<date>.csv

Writes to data/formation_reference/: `labels_J_<date>.csv` and `labels_S_<date>.csv`
(what the labeller sees), `key_J_<date>.csv` and `key_S_<date>.csv` (strata, ids:
never opened while labelling), `leads_query_<date>.sql` and `labels_manifest_<date>.json`.

PYTHONHASHSEED is pinned like scripts/storyline_replay.py, so the replay and the
sample are reproducible.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

OUT = project_root / "data" / "formation_reference"
J_SEED, S_SEED, J_ORDER_SEED, S_ORDER_SEED = 1016, 1017, 1018, 1019
BRACKET = {"spacy_boost": None, "no_boost": 0.0}


def _pin_hash_seed() -> None:
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable] + sys.argv)


def _write(path: Path, header: list, rows: list) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def cmd_sample(args) -> int:
    from src.nlp import formation_labels as fl
    from src.nlp import storyline_attribution as at
    from src.nlp import storyline_replay as rp
    from src.nlp.config import load_clustering_config

    snap = rp.load_formation_snapshot(args.snapshot)
    meta = {json.loads(line)["id"]: json.loads(line)
            for line in (Path(args.snapshot) / "articles.jsonl").read_text().splitlines()}
    titles = {aid: m["title"] or "" for aid, m in meta.items()}

    j_pool, s_pool, since = [], [], None
    for variant, boost in BRACKET.items():
        cfg = load_clustering_config().model_copy(deep=True)
        if boost is not None:
            cfg.matching.entity_boost = boost
        result = rp.StorylineReplay(snap.articles, cfg, off_topic=rp.prod_off_topic_hook(snap)).run(snap.run_starts)
        at.attribute(result, cfg.matching.threshold)
        j_pool += fl.j_pool(result, variant, titles)
        s_pool += fl.s_new_pool(result, variant, titles)
        since = result.scored_since
        print(f"[{variant}] replayed", flush=True)
    s_pool += fl.s_prod_pool(snap, since)

    j_items = fl.sample_strata(j_pool, fl.J_STRATUM_SIZES, J_SEED)
    s_items = fl.sample_strata(s_pool, fl.S_STRATUM_SIZES, S_SEED)
    j_rows = fl.labelling_order(j_items, J_ORDER_SEED, fl.J_REPEATS, fl.J_MIN_REPEAT_GAP)
    s_rows = fl.labelling_order(s_items, S_ORDER_SEED, fl.S_REPEATS, fl.S_MIN_REPEAT_GAP)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    OUT.mkdir(parents=True, exist_ok=True)
    recent = lambda r, k: r["recent_titles"][k] if len(r["recent_titles"]) > k else ""  # noqa: E731
    _write(OUT / f"labels_J_{stamp}.csv",
           ["row_id", "article_title", "article_source", "article_lead", "storyline_title",
            "recent_article_1", "recent_article_2", "recent_article_3", "label"],
           [[r["row_id"], titles.get(r["article_id"], ""), meta.get(r["article_id"], {}).get("source") or "",
             "", r["storyline_title"], recent(r, 0), recent(r, 1), recent(r, 2), ""] for r in j_rows])
    _write(OUT / f"key_J_{stamp}.csv",
           ["row_id", "stratum", "variant", "run_ts", "article_id", "n_event_articles", "storyline_id",
            "score", "repeat_of"],
           [[r["row_id"], r["stratum"], r["variant"], r["run_ts"], r["article_id"], r["n_event_articles"],
             r["storyline_id"], r["score"], r["repeat_of"]] for r in j_rows])

    def side(r, which):
        detail = r.get(f"{which}_summary") or " | ".join(t for t in r[f"{which}_titles"] if t)
        return r[f"{which}_title"], detail

    s_out, s_key = [], []
    for r in s_rows:
        left, right = ("right", "left") if r["swap"] else ("left", "right")
        s_out.append([r["row_id"], *side(r, left), *side(r, right), ""])
        s_key.append([r["row_id"], r["stratum"], r["variant"], r["left_id"], r["right_id"], r["cosine"],
                      r["swap"], r["repeat_of"]])
    _write(OUT / f"labels_S_{stamp}.csv",
           ["row_id", "left_title", "left_detail", "right_title", "right_detail", "label"], s_out)
    _write(OUT / f"key_S_{stamp}.csv",
           ["row_id", "stratum", "variant", "left_id", "right_id", "cosine", "swap", "repeat_of"], s_key)

    ids = sorted({r["article_id"] for r in j_rows})
    (OUT / f"leads_query_{stamp}.sql").write_text(
        "-- Read-only: first 400 characters of the J articles' text.\n"
        "-- Run from the Mac (repo root): ssh deploy@IP 'cd /opt/intelligence-ita/repo && docker compose -p app "
        "exec -T postgres psql -U intelligence_user -d intelligence_ita -q --csv' "
        f"< data/formation_reference/leads_query_{stamp}.sql > data/formation_reference/leads_{stamp}.csv\n"
        "BEGIN TRANSACTION READ ONLY;\n"
        "SELECT id, LEFT(regexp_replace(COALESCE(full_text, ''), '\\s+', ' ', 'g'), 400) AS lead\n"
        f"FROM articles WHERE id IN ({', '.join(map(str, ids))})\nORDER BY id;\nROLLBACK;\n")

    counts = lambda rows: {s: sum(1 for r in rows if r["stratum"] == s and r["repeat_of"] is None)  # noqa: E731
                           for s in sorted({r["stratum"] for r in rows})}
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(), "snapshot": str(args.snapshot),
        "snapshot_git_sha": snap.manifest.get("git_sha"), "bracket": list(BRACKET),
        "seeds": {"J": J_SEED, "S": S_SEED, "J_order": J_ORDER_SEED, "S_order": S_ORDER_SEED},
        "pool_sizes": {"J": {s: sum(1 for x in j_pool if x["stratum"] == s) for s in fl.J_STRATUM_SIZES},
                       "S": {s: sum(1 for x in s_pool if x["stratum"] == s) for s in fl.S_STRATUM_SIZES}},
        "sampled": {"J": counts(j_rows), "S": counts(s_rows)},
        "rows": {"J": len(j_rows), "S": len(s_rows)},
        "repeats": {"J": sum(r["repeat_of"] is not None for r in j_rows),
                    "S": sum(r["repeat_of"] is not None for r in s_rows)},
    }
    (OUT / f"labels_manifest_{stamp}.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))
    return 0


def cmd_merge_leads(args) -> int:
    leads = {}
    with open(args.leads) as f:
        for r in csv.DictReader(f):
            leads[int(r["id"])] = r["lead"]
    stamp = args.leads.stem.split("_")[-1]
    keys = {int(r["row_id"]): int(r["article_id"]) for r in csv.DictReader(open(OUT / f"key_J_{stamp}.csv"))}
    path = OUT / f"labels_J_{stamp}.csv"
    rows = list(csv.DictReader(open(path)))
    missing = 0
    for r in rows:
        lead = leads.get(keys[int(r["row_id"])])
        missing += lead is None
        r["article_lead"] = lead or ""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"leads merged into {path}: {len(rows) - missing} rows, {missing} without a lead")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample")
    p.add_argument("snapshot", type=Path)
    p = sub.add_parser("merge-leads")
    p.add_argument("leads", type=Path)
    args = parser.parse_args(argv)
    return {"sample": cmd_sample, "merge-leads": cmd_merge_leads}[args.cmd](args)


if __name__ == "__main__":
    _pin_hash_seed()
    sys.exit(main())
