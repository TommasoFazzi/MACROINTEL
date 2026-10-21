#!/usr/bin/env python3
"""
Candidate level-1 formation vectors (storyline-representation-track). Local only:

    # R1: the user runs the generated read-only query in prod (output stays gitignored)
    python scripts/formation_representation.py chunks-query artifacts/formation_snapshot_<date>
    python scripts/formation_representation.py build-r1 artifacts/formation_snapshot_<date> \
        data/formation_reference/chunk_means_<date>.csv.gz
    # D3 gate against the baseline embeddings, on the rich gold
    python scripts/formation_representation.py gate artifacts/formation_snapshot_<date> R0|R1

Candidate files go to the snapshot folder: `formation_<name>.npz` + `formation_<name>.json`.
`R0` is the snapshot's own embeddings (the gate's self-check: ΔAUC 0). Gate results go to
`data/formation_reference/gate_<name>_<date>.json`.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

REF = project_root / "data" / "formation_reference"


def _snapshot(path: Path):
    z = np.load(path / "articles.npz")
    ids = [int(a) for a in z["ids"]]
    emb, has = z["embedding"], z["has_embedding"]   # read each array once (tasks 4.7 of the causal review)
    base = {ids[i]: emb[i].astype(np.float64) for i in range(len(ids)) if has[i]}
    return ids, base


def _stamp(snapshot: Path) -> str:
    return snapshot.name.rsplit("_", 1)[-1]


def cmd_chunks_query(args) -> int:
    ids, _ = _snapshot(args.snapshot)
    stamp = _stamp(args.snapshot)
    out = f"chunk_means_{stamp}"
    sql = ("-- Read-only: per-article mean of the L2-normalised RAG chunk embeddings (vectors only, no text).\n"
           "-- Run from the Mac (repo root):\n"
           "-- ssh deploy@IP 'cd /opt/intelligence-ita/repo && docker compose -p app exec -T postgres "
           "psql -U intelligence_user -d intelligence_ita -q --csv' "
           f"< data/formation_reference/{out}_query.sql | gzip > data/formation_reference/{out}.csv.gz\n"
           "BEGIN TRANSACTION READ ONLY;\n"
           "SELECT article_id, COUNT(*) AS n_chunks, AVG(l2_normalize(embedding)) AS embedding\n"
           f"FROM chunks WHERE embedding IS NOT NULL AND article_id IN ({', '.join(map(str, ids))})\n"
           "GROUP BY article_id ORDER BY article_id;\nROLLBACK;\n")
    path = REF / f"{out}_query.sql"
    path.write_text(sql)
    print(f"{path} ({len(ids)} articles)")
    return 0


def cmd_build_r1(args) -> int:
    from src.nlp import formation_representation as fr

    ids, base = _snapshot(args.snapshot)
    opener = gzip.open if str(args.chunk_means).endswith(".gz") else open
    means, n_chunks = {}, {}
    with opener(args.chunk_means, "rt") as f:
        for r in csv.DictReader(f):
            aid = int(r["article_id"])
            means[aid] = np.array(json.loads(r["embedding"]), dtype=np.float64)
            n_chunks[aid] = int(r["n_chunks"])
    emb, fallback = fr.build_from_means(ids, means, base)
    out = args.snapshot / "formation_R1.npz"
    np.savez_compressed(out, ids=np.array(ids), embedding=emb, fallback=fallback)
    counts = sorted(n_chunks[a] for a in ids if a in n_chunks)
    manifest = {"candidate": "R1", "built_at": datetime.now(timezone.utc).isoformat(),
                "source": "mean of AVG(l2_normalize(chunks.embedding)) per article",
                "model": "paraphrase-multilingual-MiniLM-L12-v2 (each ~500-word chunk truncated at 128 tokens)",
                "articles": len(ids), "fallback": int(fallback.sum()),
                "chunks_per_article": {"median": counts[len(counts) // 2] if counts else 0,
                                       "p90": counts[int(0.9 * len(counts))] if counts else 0},
                "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}
    (args.snapshot / "formation_R1.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))
    return 0


def cmd_gate(args) -> int:
    from src.nlp import formation_representation as fr

    ids, base = _snapshot(args.snapshot)
    stamp = _stamp(args.snapshot)
    cand = base if args.candidate == "R0" else fr.load_vectors(args.snapshot / f"formation_{args.candidate}.npz", ids)
    members = json.loads((REF / f"members_{stamp}.json").read_text())
    gold = {s: {r["row_id"]: r["label"] for r in csv.DictReader(open(REF / f"gold_{s}_rich_{stamp}.csv"))}
            for s in ("J", "S")}
    result = fr.auc_gate(gold, members, base, cand)
    result.update(candidate=args.candidate, gold=f"gold_{{J,S}}_rich_{stamp}.csv",
                  rule={"S_min_delta": fr.GATE_S_MIN_DELTA, "S_ci95_low": "> 0", "J_min_delta": fr.GATE_J_MIN_DELTA})
    out = REF / f"gate_{args.candidate}_{stamp}.json"
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("chunks-query")
    p.add_argument("snapshot", type=Path)
    p = sub.add_parser("build-r1")
    p.add_argument("snapshot", type=Path)
    p.add_argument("chunk_means", type=Path)
    p = sub.add_parser("gate")
    p.add_argument("snapshot", type=Path)
    p.add_argument("candidate")
    args = parser.parse_args(argv)
    return {"chunks-query": cmd_chunks_query, "build-r1": cmd_build_r1, "gate": cmd_gate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
