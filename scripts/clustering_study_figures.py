"""
Figures and noise pages for scripts/clustering_validation_study.py
(clustering-offline-validation-study, design.md D8 and D10).

Local only: matplotlib comes from requirements-dev.txt and is never in the
prod image. Every figure is a PNG in the run directory.
"""

from __future__ import annotations

import csv
import html
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from src.nlp import clustering_validation as cv  # noqa: E402

GREY = "#9AA0A6"
# tab20 without its two greys, so no theme can be mistaken for noise
CLUSTER_COLOURS = [c for i, c in enumerate(plt.get_cmap("tab20").colors) if i not in (14, 15)]
THEME_NAME_MIN_COS = 0.8
PROJECTION_CAPTION = "2D view only — not the clustering space"


def render_all(out: Path, snap, configs, results, louvain, report, view) -> None:
    names = theme_names(snap, view["kmeans"].full)
    fig_sweep(out / "fig1_sweep.png", configs, results)
    fig_sizes(out / "fig2_sizes.png", view, results, louvain)
    fig_coassignment(out / "fig3_coassignment.png", snap, view)
    fig_projection(out / "fig4_projection.png", snap, view)
    fig_refit_series(out / "fig5_refit_series.png", snap.path / "run_metrics.csv")
    fig_decision(out / "fig6_decision.png", report)
    fig_noise(out / "fig7_noise.png", snap, configs, view["kmeans"].full, names)
    for c in configs:
        if c.algorithm == "hdbscan_umap":
            noise_page(out / f"noise_{c.key.replace(':', '_').replace('=', '')}.html",
                       snap, c, view["kmeans"].full, names)
    fig_level1(out / "fig8_level1.png", snap.path / "l1_baseline.json")
    plt.close("all")


# ---------------------------------------------------------------------------
# Theme names for study clusters (D8 figure 7)
# ---------------------------------------------------------------------------

def theme_names(snap, kmeans_partition: dict) -> dict:
    """Name each study cluster after the nearest prod theme centroid when the
    cosine similarity is >= THEME_NAME_MIN_COS, otherwise after its 3 most
    frequent key_entities."""
    members = defaultdict(list)
    for sid, lab in kmeans_partition.items():
        members[lab].append(sid)
    themes = [t for t in snap.themes if t.get("centroid")]
    C = np.array([t["centroid"] for t in themes], dtype=float) if themes else np.zeros((0, 1))
    if len(C):
        C /= np.maximum(np.linalg.norm(C, axis=1, keepdims=True), 1e-10)
    names = {}
    for lab, sids in members.items():
        centroid = snap.X(sids).mean(axis=0)
        centroid /= max(np.linalg.norm(centroid), 1e-10)
        if len(C):
            sims = C @ centroid
            best = int(np.argmax(sims))
            if sims[best] >= THEME_NAME_MIN_COS and themes[best].get("label"):
                names[lab] = themes[best]["label"]
                continue
        ents = Counter(e.lower() for s in sids for e in snap.rows[s]["key_entities"] or [])
        names[lab] = ", ".join(e for e, _ in ents.most_common(3)) or f"cluster {lab}"
    return names


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def fig_sweep(path: Path, configs, results) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for row, algo, param, native in (
        (0, "kmeans", "k", "silhouette_cosine"),
        (1, "hdbscan_umap", "min_cluster_size", "dbcv"),
    ):
        cs = [c for c in configs if c.algorithm == algo]
        xs = [c.params[param] for c in cs]
        rs = [results[c.key] for c in cs]
        ax = axes[row][0]
        for attr, label in (("seed_stability", "seed ARI"), ("data_stability", "data ARI")):
            means = [getattr(r, attr).mean_ari for r in rs]
            p10s = [getattr(r, attr).p10_ari for r in rs]
            ax.plot(xs, means, marker="o", label=f"{label} (mean)")
            ax.plot(xs, p10s, linestyle=":", marker=".", label=f"{label} (p10)")
        ax.axhline(cv.ARI_FLOOR, color="red", linewidth=0.8, label="ARI_FLOOR (D7 bar, seed and data)")
        for x, r in zip(xs, rs):
            if not r.eligible:
                ax.annotate("✗", (x, r.data_stability.mean_ari or 0), color="red", ha="center")
        ax.set_title(f"{algo}: stability vs {param}")
        ax.set_xlabel(param)
        ax.legend(fontsize=7)
        ax = axes[row][1]
        ax.plot(xs, [getattr(r, native) for r in rs], marker="o", label=native)
        ax.plot(xs, [r.coherence_med for r in rs], marker="s", label="coherence_med (context only)")
        if algo == "hdbscan_umap":
            ax.plot(xs, [r.noise_ratio for r in rs], marker="^", label="noise ratio")
            ax.axhline(cv.MAX_NOISE_RATIO, color="red", linewidth=0.8, label="MAX_NOISE_RATIO")
        ax.set_title(f"{algo}: native metric vs {param}")
        ax.set_xlabel(param)
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=120)


def fig_sizes(path: Path, view, results, louvain) -> None:
    panels = [(c.key, results[c.key].size_distribution) for c in view.values()]
    panels.append(("louvain_live", louvain.size_distribution))
    fig, axes = plt.subplots(1, len(panels), figsize=(5 * len(panels), 4))
    for ax, (title, sizes) in zip(axes, panels):
        ax.bar(range(len(sizes)), sizes, color=["C0" if i < 15 else GREY for i in range(len(sizes))])
        ax.set_title(f"{title} ({len(sizes)} clusters)")
        ax.set_xlabel("cluster rank (grey = beyond the 15 graph colours)")
        ax.set_ylabel("storylines")
    fig.tight_layout()
    fig.savefig(path, dpi=120)


def coassignment(ids: list, runs: list) -> np.ndarray:
    """C[i, j] = share of the runs containing both i and j that put them in the
    same (non-noise) cluster."""
    index = {s: k for k, s in enumerate(ids)}
    n = len(ids)
    together = np.zeros((n, n), dtype=np.float32)
    present = np.zeros((n, n), dtype=np.float32)
    for run in runs:
        pos = np.array([index[s] for s in run if s in index])
        labs = np.array([run[s] for s in run if s in index])
        mask = np.zeros(n, dtype=np.float32)
        mask[pos] = 1
        present += np.outer(mask, mask)
        for lab in set(labs.tolist()) - {cv.NOISE}:
            m = np.zeros(n, dtype=np.float32)
            m[pos[labs == lab]] = 1
            together += np.outer(m, m)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(present > 0, together / present, np.nan)


def fig_coassignment(path: Path, snap, view) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 6.5))
    for ax, c in zip(axes, view.values()):
        order = sorted(snap.ids, key=lambda s: (c.full.get(s, cv.NOISE) == cv.NOISE, c.full.get(s, cv.NOISE)))
        im = ax.imshow(coassignment(order, c.data_runs), vmin=0, vmax=1, cmap="viridis",
                       interpolation="nearest")
        ax.set_title(f"{c.key}: co-assignment over {len(c.data_runs)} subsamples\n"
                     "(ordered by full-run cluster; noise last)")
        ax.set_xticks([])
        ax.set_yticks([])
    fig.colorbar(im, ax=axes, shrink=0.8)
    fig.savefig(path, dpi=110)


def fig_projection(path: Path, snap, view) -> None:
    import umap

    xy = umap.UMAP(n_components=2, n_neighbors=15, min_dist=0.1, random_state=42).fit_transform(snap.X())
    panels = [(c.key, c.full) for c in view.values()] + [("louvain_live", snap.louvain)]
    fig, axes = plt.subplots(1, 3, figsize=(18, 6), sharex=True, sharey=True)
    for ax, (title, part) in zip(axes, panels):
        labs = np.array([part.get(s, cv.NOISE) for s in snap.ids])
        noise = labs == cv.NOISE
        ax.scatter(xy[noise, 0], xy[noise, 1], s=3, c=GREY, label="noise / no theme")
        ax.scatter(xy[~noise, 0], xy[~noise, 1], s=3, c=[CLUSTER_COLOURS[v % len(CLUSTER_COLOURS)] for v in labs[~noise]])
        ax.set_title(title)
        ax.set_xticks([])
        ax.set_yticks([])
    axes[0].legend(fontsize=7, loc="lower left")
    fig.text(0.5, 0.01, PROJECTION_CAPTION, ha="center", fontsize=11, style="italic")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=120)


def fig_refit_series(path: Path, run_metrics_csv: Path) -> None:
    series = defaultdict(lambda: defaultdict(list))
    with open(run_metrics_csv) as f:
        for r in csv.DictReader(f):
            if r["entry"] not in ("kmeans_embedding", "hdbscan_shadow"):
                continue
            try:
                value = float(r["value"])
            except (TypeError, ValueError):
                continue
            series[(r["entry"], r["key"])]["ts"].append(datetime.fromisoformat(r["ts"]))
            series[(r["entry"], r["key"])]["v"].append(value)
    keys = ("n_communities", "silhouette", "ari_vs_previous_refit", "n_noise")
    fig, axes = plt.subplots(len(keys), 1, figsize=(12, 10), sharex=True)
    for ax, key in zip(axes, keys):
        for entry in ("kmeans_embedding", "hdbscan_shadow"):
            s = series.get((entry, key))
            if s:
                ax.plot(s["ts"], s["v"], marker="o", markersize=3, label=entry)
        ax.set_ylabel(key)
        ax.legend(fontsize=7)
    axes[0].set_title("Prod refit history (shadow_partitions) — gaps between points are refit cadence")
    fig.tight_layout()
    fig.savefig(path, dpi=120)


def fig_decision(path: Path, report) -> None:
    fig, ax = plt.subplots(figsize=(10, max(2.5, 0.5 * len(report.deltas) + 1.5)))
    if not report.deltas:
        ax.text(0.5, 0.5, "No paired comparisons (labels missing or reliability gate failed)",
                ha="center", va="center")
        ax.axis("off")
    else:
        for y, d in enumerate(report.deltas):
            ax.errorbar(d.delta_macro_f1, y, xerr=[[d.delta_macro_f1 - d.ci95[0]], [d.ci95[1] - d.delta_macro_f1]],
                        fmt="o", color="C1" if d.purpose == "cross_algorithm" else "C0", capsize=3)
        ax.set_yticks(range(len(report.deltas)))
        ax.set_yticklabels([f"{d.a} − {d.b}" for d in report.deltas], fontsize=8)
        ax.axvline(0, color="black", linewidth=0.8)
        ax.axvline(cv.MIN_DELTA_F1, color="red", linestyle="--", linewidth=0.8, label="MIN_DELTA_F1")
        ax.set_xlabel("paired Δ macro-F1 (95% interval)")
        ax.legend(fontsize=8)
    ax.set_title(f"Decision — verdict: {report.verdict}")
    fig.tight_layout()
    fig.savefig(path, dpi=120)


def fig_noise(path: Path, snap, configs, kmeans_partition: dict, names: dict) -> None:
    hd = [c for c in configs if c.algorithm == "hdbscan_umap"]
    fig, axes = plt.subplots(len(hd), 2, figsize=(14, 3.6 * len(hd)), squeeze=False)
    clusters = sorted(set(kmeans_partition.values()), key=lambda lab: names.get(lab, ""))
    for row, c in zip(axes, hd):
        noise_ids = [s for s in snap.ids if c.full.get(s, cv.NOISE) == cv.NOISE]
        per = Counter(kmeans_partition[s] for s in noise_ids if s in kmeans_partition)
        row[0].bar(range(len(clusters)), [per.get(lab, 0) for lab in clusters], color="C3")
        row[0].set_xticks(range(len(clusters)))
        row[0].set_xticklabels([names.get(lab, str(lab))[:28] for lab in clusters], rotation=60,
                               ha="right", fontsize=7)
        row[0].set_title(f"{c.key}: {len(noise_ids)} unthemed storylines by k-means theme")
        counts_noise = [snap.rows[s]["article_count"] or 0 for s in noise_ids]
        counts_themed = [snap.rows[s]["article_count"] or 0 for s in snap.ids if s not in set(noise_ids)]
        bins = np.logspace(0, np.log10(max(counts_noise + counts_themed + [2])), 25)
        row[1].hist([counts_themed, counts_noise], bins=bins, label=["themed", "unthemed"],
                    color=["C0", "C3"], density=True)
        row[1].set_xscale("log")
        row[1].set_xlabel("article_count")
        row[1].legend(fontsize=7)
        row[1].set_title("article_count: unthemed vs themed (density)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)


def noise_page(path: Path, snap, c, kmeans_partition: dict, names: dict) -> None:
    noise_ids = [s for s in snap.ids if c.full.get(s, cv.NOISE) == cv.NOISE]
    noise_ids.sort(key=lambda s: -(snap.rows[s]["article_count"] or 0))
    rows = "\n".join(
        "<tr><td>{n}</td><td>{t}</td><td>{s}</td><td>{k}</td></tr>".format(
            n=snap.rows[s]["article_count"],
            t=html.escape(snap.rows[s]["title"] or ""),
            s=html.escape((snap.rows[s].get("summary") or "")[:200]),
            k=html.escape(names.get(kmeans_partition.get(s), "—")),
        )
        for s in noise_ids
    )
    path.write_text(f"""<!doctype html><meta charset="utf-8">
<title>{html.escape(c.key)} — unthemed storylines</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px}}table{{border-collapse:collapse}}
td,th{{border-bottom:1px solid #ddd;padding:6px 8px;vertical-align:top;font-size:14px}}</style>
<h1>{html.escape(c.key)}: {len(noise_ids)} of {len(snap.ids)} storylines left without a theme</h1>
<p>Sorted by article count, largest first. Would leaving these unthemed be acceptable in the graph?</p>
<table><tr><th>articles</th><th>title</th><th>summary (200 chars)</th><th>nearest k-means theme</th></tr>
{rows}
</table>""")


def fig_level1(path: Path, baseline_json: Path) -> None:
    data = json.loads(baseline_json.read_text())
    fig, axes = plt.subplots(2, 1, figsize=(12, 9))
    weeks = data["weekly_coverage"]
    if weeks:
        x = [w["week"] for w in weeks]
        keys = ["linked", "in_orphan_pool", "neither_lag_le_1d", "neither_lag_gt_1d", "neither_null_date"]
        bottom = np.zeros(len(weeks))
        for k in keys:
            vals = np.array([w[k] / w["total"] if w["total"] else 0 for w in weeks])
            axes[0].bar(x, vals, bottom=bottom, label=k)
            bottom += vals
        axes[0].set_ylabel("share of embedded articles")
        axes[0].tick_params(axis="x", rotation=60, labelsize=7)
        axes[0].legend(fontsize=7)
    axes[0].set_title("Level 1: where each week's articles ended up (last 90 days)")
    buckets = ["1", "2", "3-5", "6-20", ">20"]
    by_month = defaultdict(Counter)
    for r in data["size_buckets"]:
        by_month[r["month"]][r["bucket"]] += r["count"]
    months = sorted(m for m in by_month if m)
    bottom = np.zeros(len(months))
    for b in buckets:
        vals = np.array([by_month[m][b] for m in months], dtype=float)
        axes[1].bar(months, vals, bottom=bottom, label=f"{b} articles")
        bottom += vals
    axes[1].set_title(f"Storylines by creation month and size (all statuses) — "
                      f"article_count mismatches: {data['article_count_mismatches']}")
    axes[1].tick_params(axis="x", rotation=60, labelsize=7)
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
