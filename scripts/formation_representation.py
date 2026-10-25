#!/usr/bin/env python3
"""
Candidate level-1 formation vectors (storyline-representation-track). Local only:

    # R1: the user runs the generated read-only query in prod (output stays gitignored)
    python scripts/formation_representation.py chunks-query artifacts/formation_snapshot_<date>
    python scripts/formation_representation.py build-r1 artifacts/formation_snapshot_<date> \
        data/formation_reference/chunk_means_<date>.csv.gz
    # R2 (R3: --name R3 --model <hf id>): article text, local only, never committed
    python scripts/formation_representation.py text-query artifacts/formation_snapshot_<date>
    python scripts/formation_representation.py build-text artifacts/formation_snapshot_<date> \
        data/formation_reference/text_<date>.csv.gz
    # D3 gate against the baseline embeddings, on the rich gold
    python scripts/formation_representation.py gate artifacts/formation_snapshot_<date> R0|R1|R2

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


def _write_query(snapshot: Path, out: str, comment: str, select: str) -> int:
    """A read-only query over the snapshot's article ids, run in prod by the user; its
    gzipped CSV output lands in data/formation_reference/ (gitignored)."""
    ids, _ = _snapshot(snapshot)
    sql = (f"-- Read-only: {comment}\n"
           "-- Run from the Mac (repo root):\n"
           "-- ssh deploy@IP 'cd /opt/intelligence-ita/repo && docker compose -p app exec -T postgres "
           "psql -U intelligence_user -d intelligence_ita -q --csv' "
           f"< data/formation_reference/{out}_query.sql | gzip > data/formation_reference/{out}.csv.gz\n"
           "BEGIN TRANSACTION READ ONLY;\n"
           + select.format(ids=", ".join(map(str, ids))) + "\nROLLBACK;\n")
    path = REF / f"{out}_query.sql"
    path.write_text(sql)
    print(f"{path} ({len(ids)} articles)")
    return 0


def cmd_chunks_query(args) -> int:
    return _write_query(
        args.snapshot, f"chunk_means_{_stamp(args.snapshot)}",
        "per-article mean of the L2-normalised RAG chunk embeddings (vectors only, no text).",
        "SELECT article_id, COUNT(*) AS n_chunks, AVG(l2_normalize(embedding)) AS embedding\n"
        "FROM chunks WHERE embedding IS NOT NULL AND article_id IN ({ids})\n"
        "GROUP BY article_id ORDER BY article_id;")


def cmd_text_query(args) -> int:
    return _write_query(
        args.snapshot, f"text_{_stamp(args.snapshot)}",
        "full article text for R2/R3. ARTICLE TEXT: the output stays local and gitignored (public repo).",
        "SELECT id, full_text FROM articles WHERE id IN ({ids}) ORDER BY id;")


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


def cmd_build_text(args) -> int:
    """R2/R3: the model on the whole cleaned text, in sentence-packed windows, mean-pooled.
    Also re-embeds each cleaned text the way prod does (truncated) and records its cosine
    with the snapshot embedding, which checks that the exported text is the one prod used."""
    from sentence_transformers import SentenceTransformer

    from src.nlp import formation_representation as fr
    from src.nlp.processing import NLPProcessor

    ids, base = _snapshot(args.snapshot)
    clean = object.__new__(NLPProcessor).clean_text          # stateless; skips loading spaCy and the model
    csv.field_size_limit(sys.maxsize)
    with gzip.open(args.text, "rt") as f:
        texts = {int(r["id"]): clean(r["full_text"] or "") for r in csv.DictReader(f)}

    import torch
    torch.set_num_threads(args.threads)                   # an 8 GB laptop: bounded CPU, no MPS

    model = SentenceTransformer(args.model, device="cpu")
    limit = model.max_seq_length - 2                      # [CLS] and [SEP]
    limit -= len(model.tokenizer.tokenize(args.prefix)) if args.prefix else 0   # e5 needs "query: "
    count = lambda t: len(model.tokenizer.tokenize(t))    # noqa: E731
    order = [a for a in ids if texts.get(a)]
    is_baseline = args.model == fr.BASELINE_MODEL
    # Blocks of articles, so memory stays flat: only one block's windows are held at a time
    means, n_windows, check = {}, {}, {}
    # Checkpoint every few blocks; a rerun with the same settings resumes from it
    partial = args.snapshot / f"formation_{args.name}.partial.npz"
    settings = json.dumps({"model": args.model, "prefix": args.prefix, "limit": limit, "text": str(args.text)})
    if partial.exists():
        z = np.load(partial, allow_pickle=False)
        if str(z["settings"]) == settings:
            for i, aid in enumerate(z["ids"].tolist()):
                means[aid], n_windows[aid] = z["means"][i].astype(np.float64), int(z["n_windows"][i])
            check = dict(zip(z["check_ids"].tolist(), z["check"].tolist()))
            print(f"resuming from {partial.name}: {len(means)} articles done", flush=True)

    def save_partial():
        done_ids = list(means)
        np.savez(partial, settings=np.array(settings), ids=np.array(done_ids, dtype=np.int64),
                 means=np.array([means[a] for a in done_ids]).reshape(len(done_ids), -1),
                 n_windows=np.array([n_windows[a] for a in done_ids], dtype=np.int64),
                 check_ids=np.array(list(check), dtype=np.int64), check=np.array(list(check.values())))

    todo = [a for a in order if a not in means]
    started = datetime.now(timezone.utc)
    for n_block, start in enumerate(range(0, len(todo), args.block), 1):
        block = todo[start:start + args.block]
        windows, owner = [], []
        for aid in block:
            ws = fr.sentence_windows(texts[aid], count, limit)
            windows += ws
            owner += [aid] * len(ws)
        w_emb = model.encode([args.prefix + w for w in windows], batch_size=args.batch, convert_to_numpy=True)
        for aid in block:
            vs = [fr.unit(v.astype(np.float64)) for a, v in zip(owner, w_emb) if a == aid]
            if vs:
                means[aid], n_windows[aid] = np.mean(vs, axis=0), len(vs)
        if is_baseline:
            probe = [a for a in block if a in base]
            for a, v in zip(probe, model.encode([texts[a] for a in probe], batch_size=args.batch,
                                                convert_to_numpy=True)):
                check[a] = float(fr.unit(v.astype(np.float64)) @ fr.unit(base[a]))
        if n_block % 5 == 0:
            save_partial()
        done = start + len(block)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        print(f"{len(order) - len(todo) + done}/{len(order)} articles, {elapsed / 60:.1f} min, "
              f"~{elapsed / done * (len(todo) - done) / 60:.1f} min left", flush=True)
    seconds = (datetime.now(timezone.utc) - started).total_seconds()

    emb, fallback = fr.build_from_means(ids, means, base if is_baseline else {})
    out = args.snapshot / f"formation_{args.name}.npz"
    np.savez_compressed(out, ids=np.array(ids), embedding=emb, fallback=fallback)
    per_article = sorted(n_windows.values())
    cos = np.array(sorted(check.values())) if check else None
    manifest = {"candidate": args.name, "built_at": started.isoformat(), "model": args.model,
                "window": f"sentence-packed, <= {limit} tokens, mean of L2-normalised windows",
                "prefix": args.prefix, "batch": args.batch,
                "articles": len(ids), "fallback": int(fallback.sum()),
                "windows": sum(per_article), "windows_per_article": {"median": per_article[len(per_article) // 2],
                                                                 "p90": per_article[int(0.9 * len(per_article))]},
                "encode_seconds": round(seconds, 1), "threads": args.threads,  # this run only (after a resume)
                "note": "encode time includes the prod-like check re-embedding for MiniLM",
                "seconds_per_1000_articles": round(1000 * seconds / max(len(todo), 1), 1),
                "text_check_cosine_vs_snapshot": None if cos is None else {
                    "n": len(cos), "median": round(float(np.median(cos)), 4),
                    "share_ge_0.99": round(float(np.mean(cos >= 0.99)), 4)},
                "sha256": hashlib.sha256(out.read_bytes()).hexdigest()}
    (args.snapshot / f"formation_{args.name}.json").write_text(json.dumps(manifest, indent=2))
    partial.unlink(missing_ok=True)
    print(json.dumps(manifest, indent=2))
    return 0


NEW_JOIN_SEED = 1022
RICH_ARTICLES, ARTICLE_LEAD, MEMBER_LEAD = 8, 400, 250    # as the causal review's rich round
NEW_JOIN_STRATUM_KEY = "J-new-R"                          # = formation_representation.NEW_JOIN_STRATUM


def cmd_new_joins_sample(args) -> int:
    """D8: a random sample of the arms' new joins (pooled, deduplicated), written as a J set
    for the AI judges. Ids go to `key_J_<stamp>.csv`; the rich judge input holds article
    text, so it goes to the gitignored `judges/rich_input_J_<stamp>.csv`."""
    import random

    pool = json.loads((args.run_dir / "new_joins.json").read_text())
    unique = {}
    for item in pool:
        unique.setdefault((item["article_id"], tuple(sorted(item["storyline_members"]))), item)
    items = sorted(unique.values(), key=lambda x: (x["article_id"], x["run_ts"], x["arm"], x["variant"]))
    random.Random(NEW_JOIN_SEED).shuffle(items)
    sample = items[:args.n]

    meta = {json.loads(line)["id"]: json.loads(line)
            for line in (args.snapshot / "articles.jsonl").read_text().splitlines()}
    csv.field_size_limit(sys.maxsize)
    need = {x["article_id"] for x in sample} | {a for x in sample for a in x["storyline_members"][:RICH_ARTICLES]}
    with gzip.open(args.text, "rt") as f:
        lead = {int(r["id"]): " ".join((r["full_text"] or "").split())[:ARTICLE_LEAD]
                for r in csv.DictReader(f) if int(r["id"]) in need}
    title = lambda a: meta.get(a, {}).get("title") or ""  # noqa: E731

    key_rows, judge_rows = [], []
    for i, x in enumerate(sample):
        a = x["article_id"]
        members = x["storyline_members"][:RICH_ARTICLES]
        key_rows.append([i, NEW_JOIN_STRATUM_KEY, x["arm"], x["variant"], x["run_ts"], a, len(x["storyline_members"]),
                         " ".join(map(str, members))])
        judge_rows.append([i, title(a), meta.get(a, {}).get("source") or "", lead.get(a, ""), x["storyline_title"],
                           "\n".join(f"- {title(m)} — {lead.get(m, '')[:MEMBER_LEAD]}" for m in members)])
    with open(REF / f"key_J_{args.stamp}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["row_id", "stratum", "arm", "variant", "run_ts", "article_id", "n_members", "shown_member_ids"])
        w.writerows(key_rows)
    with open(REF / "judges" / f"rich_input_J_{args.stamp}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["row_id", "article_title", "article_source", "article_lead", "storyline_title",
                    "storyline_articles_at_decision_time"])
        w.writerows(judge_rows)
    arms = {}
    for x in sample:
        arms[f"{x['arm']}/{x['variant']}"] = arms.get(f"{x['arm']}/{x['variant']}", 0) + 1
    print(json.dumps({"pool": len(pool), "unique": len(unique), "sampled": len(sample), "by_arm": arms,
                      "missing_leads": sum(1 for x in sample if x["article_id"] not in lead)}, indent=2))
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
    p = sub.add_parser("text-query")
    p.add_argument("snapshot", type=Path)
    p = sub.add_parser("build-text")
    p.add_argument("snapshot", type=Path)
    p.add_argument("text", type=Path)
    p.add_argument("--name", default="R2")
    p.add_argument("--model", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    p.add_argument("--block", type=int, default=200)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--batch", type=int, default=32)
    p.add_argument("--prefix", default="", help='e.g. "query: " for intfloat/multilingual-e5-*')
    p = sub.add_parser("new-joins-sample")
    p.add_argument("snapshot", type=Path)
    p.add_argument("run_dir", type=Path, help="artifacts/storyline_replay/<run> of `storyline_replay.py representations`")
    p.add_argument("text", type=Path)
    p.add_argument("--n", type=int, default=300)
    p.add_argument("--stamp", default="20261022")
    p = sub.add_parser("gate")
    p.add_argument("snapshot", type=Path)
    p.add_argument("candidate")
    args = parser.parse_args(argv)
    return {"chunks-query": cmd_chunks_query, "build-r1": cmd_build_r1, "text-query": cmd_text_query,
            "build-text": cmd_build_text, "new-joins-sample": cmd_new_joins_sample, "gate": cmd_gate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
