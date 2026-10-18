#!/usr/bin/env python3
"""
AI judges for the level-1 labelled sets (J and S), and their aggregation.

`storyline-formation-causal-review` design.md D5, amendment of 2026-10-18: the
sets are labelled by independent AI judges instead of the product owner, and the
reliability gate is agreement between judges (pairwise Cohen's κ ≥ 0.6) instead of
within-labeller repeats, which a model answers identically by construction.

Judges:
- `sonnet46_chat`: the labels already in labels_{J,S}_<date>.csv, given by Claude
  Sonnet 4.6 in a chat session with the guideline (user, 2026-10-18);
- `gemini31pro`: LLMFactory tier t1, one call per row, rows in a judge-specific
  random order, repeats skipped;
- `sonnet46_api`: LLMFactory tier t2, same protocol, a clean session per row
  (needs ANTHROPIC_API_KEY; not used: the user has no API budget for it);
- chat judges: any `judges/<name>_{J,S}_<date>.csv` with `row_id,label`, labelled in a
  new conversation from `judges/opus_input_{J,S}_<date>.csv` (first occurrences only,
  own order, no previous labels) with `judges/OPUS_PROMPT.md`.

Two rounds (tasks.md 6.4c). `plain` shows what the labeller saw: three storyline titles.
`rich` adds, for every storyline side, up to 8 of its articles at decision time as
`title — lead` (`judges/rich_input_{J,S}_<date>.csv`, from `formation_labels.py
rich-inputs`). The rich round tests whether the judges disagreed for lack of context:
if κ rises, it was the context; if not, the granularity is the product owner's call.
Rich judges are named `<name>_rich`; the plain chat labels have no rich counterpart.

    python scripts/formation_judges.py run gemini31pro   [--set J|S|both] [--context rich]
    python scripts/formation_judges.py run sonnet46_api
    python scripts/formation_judges.py aggregate [--context rich]   # κ per pair, majority-vote gold files
    python scripts/formation_judges.py merge-agents claude_opus_agent  # agent chunks -> one rich judge file
    python scripts/formation_judges.py report      # tasks.md 6.5: per-stratum J precision, S duplicate rate, H6 AUC

Agent judges (rich round only): a fresh Claude Code subagent per chunk of
`judges/agent_chunks/<judge>/{J,S}_NN.md` (rows in a judge-specific order), prompted with
`judges/AGENT_PROMPT.md`, reads only the guideline and its chunk and writes `out_<chunk>.csv`.

Answers are cached in data/formation_reference/judges/<judge>_<set>_<date>.jsonl,
so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

REF = project_root / "data" / "formation_reference"
JUDGES_DIR = REF / "judges"
STAMP = "20261016"
JUDGE_TIERS = {"gemini31pro": "t1", "sonnet46_api": "t2"}
EXISTING_JUDGE = "sonnet46_chat"
ALLOWED = {"J": ("yes", "no", "unsure"), "S": ("same", "different", "unsure")}
ORDER_SEED = {"gemini31pro": 2101, "sonnet46_api": 2102}
WORKERS = 6
MIN_PAIRWISE_KAPPA = 0.6


def _suffix(context: str) -> str:
    return "_rich" if context == "rich" else ""


def _rows(set_: str, context: str = "plain") -> list[dict]:
    """First occurrences only: a judge never sees a repeat."""
    if context == "rich":
        return list(csv.DictReader(open(JUDGES_DIR / f"rich_input_{set_}_{STAMP}.csv")))
    labels = list(csv.DictReader(open(REF / f"labels_{set_}_{STAMP}.csv")))
    key = {r["row_id"]: r for r in csv.DictReader(open(REF / f"key_{set_}_{STAMP}.csv"))}
    return [r for r in labels if key[r["row_id"]]["repeat_of"] in ("", "None")]


def _prompt(set_: str, r: dict) -> str:
    if set_ == "J" and "storyline_articles_at_decision_time" in r:
        body = (f"ARTICLE\nTitle: {r['article_title']}\nSource: {r['article_source']}\n"
                f"First lines: {r['article_lead']}\n\nSTORYLINE\nTitle: {r['storyline_title']}\n"
                f"Its most recent articles at decision time (title — first lines):\n"
                f"{r['storyline_articles_at_decision_time']}\n\n"
                "Question: does this article belong to this storyline?")
    elif set_ == "S" and "left_articles" in r:
        body = (f"STORYLINE A\nTitle: {r['left_title']}\nArticles (title — first lines):\n{r['left_articles']}\n\n"
                f"STORYLINE B\nTitle: {r['right_title']}\nArticles (title — first lines):\n{r['right_articles']}\n\n"
                "Question: are these two storylines the same story?")
    elif set_ == "J":
        recent = "\n".join(f"  - {r[f'recent_article_{i}']}" for i in (1, 2, 3) if r[f"recent_article_{i}"])
        body = (f"ARTICLE\nTitle: {r['article_title']}\nSource: {r['article_source']}\n"
                f"First lines: {r['article_lead']}\n\nSTORYLINE\nTitle: {r['storyline_title']}\n"
                f"Most recent articles at decision time:\n{recent}\n\n"
                "Question: does this article belong to this storyline?")
    else:
        body = (f"STORYLINE A\nTitle: {r['left_title']}\nDetail: {r['left_detail']}\n\n"
                f"STORYLINE B\nTitle: {r['right_title']}\nDetail: {r['right_detail']}\n\n"
                "Question: are these two storylines the same story?")
    labels = " | ".join(ALLOWED[set_])
    return (f"{body}\n\nApply the guideline strictly. Titles may be in any language; judge the story, "
            f'not the wording.\nAnswer with JSON only: {{"label": "{labels}", "reason": "<one short sentence>"}}')


def _parse(set_: str, text: str) -> str:
    try:
        label = json.loads(text.strip().strip("`").removeprefix("json").strip())["label"].strip().lower()
    except (ValueError, KeyError, AttributeError):
        return "invalid"
    return label if label in ALLOWED[set_] else "invalid"


def cmd_run(args) -> int:
    from src.llm.llm_factory import LLMFactory

    client = LLMFactory.get(JUDGE_TIERS[args.judge])
    JUDGES_DIR.mkdir(parents=True, exist_ok=True)
    for set_ in (("J", "S") if args.set == "both" else (args.set,)):
        system = (REF / f"GUIDELINE_{set_}.md").read_text()
        cache = JUDGES_DIR / f"{args.judge}{_suffix(args.context)}_{set_}_{STAMP}.jsonl"
        done = {json.loads(line)["row_id"] for line in cache.read_text().splitlines()} if cache.exists() else set()
        rows = [r for r in _rows(set_, args.context) if r["row_id"] not in done]
        random.Random(ORDER_SEED[args.judge]).shuffle(rows)
        lock = threading.Lock()

        def judge(r):
            for _ in range(3):
                try:
                    text = client.generate(_prompt(set_, r), system=system, temperature=0.0,
                                           max_tokens=4000, json_mode=True)
                    label = _parse(set_, text)
                    if label != "invalid":
                        return r["row_id"], label, text
                except Exception as e:  # noqa: BLE001 — retried, then recorded as invalid
                    text = f"error: {e}"
            return r["row_id"], "invalid", text

        with ThreadPoolExecutor(WORKERS) as pool, open(cache, "a") as out:
            for n, fut in enumerate(as_completed([pool.submit(judge, r) for r in rows]), 1):
                row_id, label, raw = fut.result()
                with lock:
                    out.write(json.dumps({"row_id": row_id, "label": label, "raw": raw}, ensure_ascii=False) + "\n")
                    out.flush()
                if n % 25 == 0:
                    print(f"[{args.judge} {set_}] {n}/{len(rows)}", flush=True)
        print(f"[{args.judge} {set_}] done ({len(rows)} new rows)")
    return 0


def _load_judges(set_: str, context: str = "plain") -> dict[str, dict[str, str]]:
    first = {r["row_id"] for r in _rows(set_)}
    sfx = _suffix(context)
    judges = {}
    if context == "plain":
        judges[EXISTING_JUDGE] = {r["row_id"]: r["label"].strip().lower()
                                  for r in csv.DictReader(open(REF / f"labels_{set_}_{STAMP}.csv"))
                                  if r["row_id"] in first}
    for name in JUDGE_TIERS:
        path = JUDGES_DIR / f"{name}{sfx}_{set_}_{STAMP}.jsonl"
        if path.exists():
            judges[name + sfx] = {json.loads(line)["row_id"]: json.loads(line)["label"]
                                  for line in path.read_text().splitlines()}
    # judges labelled in a chat or agent session: judges/<name>_{set}_<date>.csv with row_id,label
    for path in sorted(JUDGES_DIR.glob(f"*{sfx}_{set_}_{STAMP}.csv")):
        name = path.name.removesuffix(f"_{set_}_{STAMP}.csv")
        if name.endswith("_input") or (context == "plain") == name.endswith("_rich"):
            continue
        labels = {r["row_id"].strip(): r["label"].strip().lower() for r in csv.DictReader(open(path))}
        missing = first - set(labels)
        if missing:
            raise ValueError(f"{path.name}: {len(missing)} rows missing, e.g. {sorted(missing, key=int)[:5]}")
        judges[name] = {r: labels[r] for r in first}
    return judges


def cmd_aggregate(args) -> int:
    from collections import Counter

    from src.nlp.clustering_validation import rater_agreement

    report = {}
    for set_ in ("J", "S"):
        judges = _load_judges(set_, args.context)
        names = list(judges)
        rows = sorted(set.intersection(*(set(v) for v in judges.values())), key=int)
        pairs = {}
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a = [judges[names[i]][r] for r in rows]
                b = [judges[names[j]][r] for r in rows]
                k, agree = rater_agreement(a, b)
                pairs[f"{names[i]} vs {names[j]}"] = {"kappa": round(k, 3), "agreement": round(agree, 3)}
        gold, unanimous = {}, 0
        for r in rows:
            votes = Counter(judges[n][r] for n in names if judges[n][r] in ALLOWED[set_][:2])
            top = votes.most_common()
            if top and (len(top) == 1 or top[0][1] > top[1][1]) and top[0][1] * 2 > len(names):
                gold[r] = top[0][0]
                unanimous += top[0][1] == len(names)
            else:
                gold[r] = "unsure"
        with open(REF / f"gold_{set_}{_suffix(args.context)}_{STAMP}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["row_id", "label", "unanimous"] + names)
            for r in rows:
                w.writerow([r, gold[r], all(judges[n][r] == gold[r] for n in names)] + [judges[n][r] for n in names])
        report[set_] = {"judges": names, "n_rows": len(rows), "pairwise": pairs,
                        "gate_passed": all(p["kappa"] >= MIN_PAIRWISE_KAPPA for p in pairs.values()),
                        "gold_counts": dict(Counter(gold.values())), "unanimous": unanimous}
    (REF / f"judges_report{_suffix(args.context)}_{STAMP}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0


def cmd_merge_agents(args) -> int:
    """Join an agent judge's chunk outputs into judges/<judge>_rich_{J,S}_<date>.csv.
    Tolerates `row_id label` lines (the first pilot chunk used a space)."""
    import re

    d = JUDGES_DIR / "agent_chunks" / args.judge
    for set_ in ("J", "S"):
        labels = {}
        for chunk in sorted(d.glob(f"{set_}_*.md")):
            expected = set(re.findall(r"^## row_id (\d+)", chunk.read_text(), re.M))
            out = d / f"out_{chunk.name.removesuffix('.md')}.csv"
            got = {}
            for line in out.read_text().splitlines()[1:]:
                parts = re.split(r"[,\s]+", line.strip())
                if len(parts) >= 2:
                    got[parts[0]] = parts[1].lower()
            if set(got) != expected or not set(got.values()) <= set(ALLOWED[set_]):
                raise ValueError(f"{out.name}: rows or labels do not match the chunk")
            labels.update(got)
        with open(JUDGES_DIR / f"{args.judge}_rich_{set_}_{STAMP}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["row_id", "label"])
            w.writerows(sorted(labels.items(), key=lambda kv: int(kv[0])))
        print(f"{args.judge} {set_}: {len(labels)} rows")
    return 0


SENSITIVITY_JUDGE = "claude_sonnet_agent_rich"   # broadest judge, closest to the user's anchor (6.4c)


def cmd_report(args) -> int:
    """Labelled baselines from the rich gold labels (tasks.md 6.5). Per-stratum rates are
    descriptive: J strata are oversampled on purpose, so they are never pooled here;
    lever comparisons go through `stratified_precision` with replay counts (group 7).
    Every J rate is also given under the broadest judge, as a granularity sensitivity."""
    from src.nlp import formation_labels as fl

    sfx = _suffix(args.context)
    out = {"context": args.context}
    for set_, positive in (("J", "yes"), ("S", "same")):
        gold = list(csv.DictReader(open(REF / f"gold_{set_}{sfx}_{STAMP}.csv")))
        key = {r["row_id"]: r for r in csv.DictReader(open(REF / f"key_{set_}_{STAMP}.csv"))}
        strata = {r: k["stratum"] for r, k in key.items()}
        sources = {"gold": {r["row_id"]: r["label"] for r in gold}}
        if set_ == "J" and SENSITIVITY_JUDGE in gold[0]:
            sources[SENSITIVITY_JUDGE] = {r["row_id"]: r[SENSITIVITY_JUDGE] for r in gold}
        block = {}
        for name, labels in sources.items():
            per = {}
            for stratum, v in sorted(fl.stratum_labels(labels, strata, positive).by_stratum.items()):
                k, n = int(v.sum()), len(v)
                per[stratum] = {"n": n, "rate": round(k / n, 3), "ci95": [round(x, 3) for x in fl.wilson_ci(k, n)]}
            block[name] = per
        if set_ == "S":
            rows = [r for r in gold if r["label"] != "unsure"]
            cos = [float(key[r["row_id"]]["cosine"]) for r in rows]
            same = [r["label"] == "same" for r in rows]
            block["h6_auc_cosine"] = {"all": round(fl.auc(cos, same), 3)}
            for stratum in sorted({key[r["row_id"]]["stratum"] for r in rows}):
                idx = [i for i, r in enumerate(rows) if key[r["row_id"]]["stratum"] == stratum]
                block["h6_auc_cosine"][stratum] = round(fl.auc([cos[i] for i in idx], [same[i] for i in idx]), 3)
        out[set_] = block
    path = REF / f"report_6_5{sfx}_{STAMP}.json"
    path.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run")
    p.add_argument("judge", choices=list(JUDGE_TIERS))
    p.add_argument("--set", choices=["J", "S", "both"], default="both")
    p.add_argument("--context", choices=["plain", "rich"], default="plain")
    p = sub.add_parser("aggregate")
    p.add_argument("--context", choices=["plain", "rich"], default="plain")
    p = sub.add_parser("merge-agents")
    p.add_argument("judge")
    p = sub.add_parser("report")
    p.add_argument("--context", choices=["plain", "rich"], default="rich")
    args = parser.parse_args(argv)
    return {"run": cmd_run, "aggregate": cmd_aggregate, "merge-agents": cmd_merge_agents,
            "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
