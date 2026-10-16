#!/usr/bin/env python3
"""
Offline replay of level-1 storyline formation on a formation export.

`storyline-formation-causal-review` design.md D3. Local only, never in prod:

    python scripts/storyline_replay.py fidelity artifacts/formation_snapshot_<date>

`fidelity` replays today's behaviour (default levers) at prod's run times, applies
prod's actual off-topic outcomes, and writes to
artifacts/storyline_replay/<run_id>/: `records.jsonl` (one DecisionRecord per
decision), `storylines.jsonl` (final replay state) and `fidelity.json`
(FidelityReport). Per D3, no lever result is read before this report is checked.

PYTHONHASHSEED is pinned (the process re-executes itself if it is not 0):
`merge_entities` truncates a set-ordered list, so string hashing would otherwise
make two runs on the same export differ.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


def _pin_hash_seed() -> None:
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable] + sys.argv)


def cmd_fidelity(args) -> int:
    from src.nlp import storyline_replay as rp
    from src.nlp.config import load_clustering_config

    snap = rp.load_formation_snapshot(args.snapshot)
    replay = rp.StorylineReplay(snap.articles, load_clustering_config(),
                                off_topic=rp.prod_off_topic_hook(snap), burn_in_days=args.burn_in)
    result = replay.run(snap.run_starts)
    report = rp.fidelity_report(result, snap)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / run_id
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "records.jsonl", "w") as f:
        for r in result.records:
            f.write(r.model_dump_json() + "\n")
    with open(out / "storylines.jsonl", "w") as f:
        for sid, s in sorted(result.storylines.items()):
            f.write(json.dumps({
                "id": sid, "title": s.title, "status": s.status, "archived_by": s.archived_by,
                "article_count": s.article_count, "n_links": len(result.members.get(sid, ())),
                "created_at": s.created_at.isoformat(), "last_update": s.last_update.isoformat(),
            }, ensure_ascii=False) + "\n")
    (out / "fidelity.json").write_text(json.dumps({
        "snapshot": str(args.snapshot), "snapshot_git_sha": snap.manifest.get("git_sha"),
        "n_runs": len(snap.run_starts), "burn_in_days": args.burn_in,
        "report": report.model_dump(mode="json"),
    }, indent=2))

    print(f"Replay written to {out}")
    for k, v in report.model_dump().items():
        print(f"  {k}: {v}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fidelity", help="replay today's behaviour and compare with prod")
    p.add_argument("snapshot", type=Path)
    p.add_argument("--burn-in", type=int, default=30)
    p.add_argument("--out", type=Path, default=project_root / "artifacts" / "storyline_replay")
    args = parser.parse_args(argv)
    return {"fidelity": cmd_fidelity}[args.cmd](args)


if __name__ == "__main__":
    _pin_hash_seed()
    sys.exit(main())
