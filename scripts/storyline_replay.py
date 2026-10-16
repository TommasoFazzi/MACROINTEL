#!/usr/bin/env python3
"""
Offline replay of level-1 storyline formation on a formation export.

`storyline-formation-causal-review` design.md D3. Local only, never in prod:

    python scripts/storyline_replay.py fidelity artifacts/formation_snapshot_<date>

`attribution` runs the same replay at both ends of the entity-boost bracket
(design.md Context, fidelity run: spaCy-entity boost / no boost), attributes every
fragmentation event and lost article to its first cause (D4), and writes
`attribution.json` and `attribution.png` to artifacts/storyline_replay/<run_id>/:

    python scripts/storyline_replay.py attribution artifacts/formation_snapshot_<date>

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

import numpy as np

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


BRACKET = {"spacy_boost": None, "no_boost": 0.0}   # matching.entity_boost override


def _attribution_figure(results: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from src.nlp.storyline_attribution import CAUSE_ORDER

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), sharey=True)
    for ax, view, title in ((axes[0], "fragmentation", "Fragmentation events (new storyline or orphan)"),
                            (axes[1], "lost", "Lost articles (never considered or expired)")):
        y = np.arange(len(CAUSE_ORDER))
        for k, (variant, w) in enumerate(results.items()):
            unit = "events" if view == "fragmentation" else "articles"
            total = max(sum(v[unit] for v in w[view]["by_cause"].values()), 1)
            share = [w[view]["by_cause"][c][unit] / total for c in CAUSE_ORDER]
            ax.barh(y + (k - 0.5) * 0.38, share, height=0.38, label=variant)
        ax.set_yticks(y, CAUSE_ORDER)
        ax.invert_yaxis()
        ax.set_xlabel("share (primary cause, first in D4 order)")
        ax.set_title(title, fontsize=10)
    axes[1].legend(title="entity boost")
    fig.suptitle("Level-1 attribution waterfall — scored window after burn-in", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def cmd_attribution(args) -> int:
    from src.nlp import storyline_attribution as at
    from src.nlp import storyline_replay as rp
    from src.nlp.config import load_clustering_config

    snap = rp.load_formation_snapshot(args.snapshot)
    results = {}
    for variant, boost in BRACKET.items():
        cfg = load_clustering_config().model_copy(deep=True)
        if boost is not None:
            cfg.matching.entity_boost = boost
        result = rp.StorylineReplay(snap.articles, cfg, off_topic=rp.prod_off_topic_hook(snap),
                                    burn_in_days=args.burn_in).run(snap.run_starts)
        at.attribute(result, cfg.matching.threshold)
        results[variant] = at.waterfall(result) | {
            "entity_boost": cfg.matching.entity_boost,
            "fidelity": rp.fidelity_report(result, snap).model_dump(mode="json")}
        print(f"[{variant}] done", flush=True)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "attribution.json").write_text(json.dumps({
        "snapshot": str(args.snapshot), "burn_in_days": args.burn_in, "variants": results}, indent=2))
    _attribution_figure(results, out / "attribution.png")

    print(f"Attribution written to {out}")
    for view, unit in (("fragmentation", "events"), ("lost", "articles")):
        print(f"\n{view} ({unit}, share by primary cause)")
        print(f"  {'cause':22s}" + "".join(f"{v:>14s}" for v in results))
        for cause in at.CAUSE_ORDER:
            row = []
            for w in results.values():
                total = max(sum(x[unit] for x in w[view]["by_cause"].values()), 1)
                row.append(f"{w[view]['by_cause'][cause][unit] / total:14.1%}")
            print(f"  {cause:22s}" + "".join(row))
    print("\nfragmentation flags (non-exclusive, share of events)")
    flags = sorted({f for w in results.values() for f in w["fragmentation"]["flags_events"]})
    for flag in flags:
        print(f"  {flag:22s}" + "".join(
            f"{w['fragmentation']['flags_events'].get(flag, 0) / max(w['fragmentation']['n_events'], 1):14.1%}"
            for w in results.values()))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fidelity", help="replay today's behaviour and compare with prod")
    p.add_argument("snapshot", type=Path)
    p.add_argument("--burn-in", type=int, default=30)
    p.add_argument("--out", type=Path, default=project_root / "artifacts" / "storyline_replay")
    p = sub.add_parser("attribution", help="causal attribution waterfall at both bracket ends")
    p.add_argument("snapshot", type=Path)
    p.add_argument("--burn-in", type=int, default=30)
    p.add_argument("--out", type=Path, default=project_root / "artifacts" / "storyline_replay")
    args = parser.parse_args(argv)
    return {"fidelity": cmd_fidelity, "attribution": cmd_attribution}[args.cmd](args)


if __name__ == "__main__":
    _pin_hash_seed()
    sys.exit(main())
