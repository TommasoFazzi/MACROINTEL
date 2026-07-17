"""
Clustering Shadow Comparison — Phase 1E Decision-22 dashboard.

Consumes ``narrative_run_metrics.shadow_partitions`` (migration 046) to answer:
"which of the 4 shadow partitions (louvain_full / louvain_backbone /
leiden_full / leiden_backbone) should we promote to ``storylines.community_id``
— and if none pass the composite gate, why?"

Data source: shadow_partitions JSONB (one array per pipeline run, one object per
partition). Reads ``coherence_med_k5`` and ``fallback_path`` inline from JSONB
because the legacy view ``v_shadow_partitions_unnested`` predates Fix 2 and
does not expose them.

Read-only page — no writes, no LLM calls.
"""

import sys
from pathlib import Path

# Setup path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import streamlit as st

from src.hitl.streamlit_utils import (
    get_db_manager,
    inject_custom_css,
    init_session_state,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

st.set_page_config(
    page_title="Clustering Shadow Comparison | INTELLIGENCE_ITA",
    page_icon="🧪",
    layout="wide",
)

inject_custom_css()
init_session_state()
db = get_db_manager()

# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

# The composite gate hardcoded in _run_leiden_cpm_adaptive_sweep. Duplicated
# here (not imported) so this page stays independent of compute_communities.
GATE_AVG_SIZE_MIN = 80
GATE_AVG_SIZE_MAX = 240
GATE_MAX_RATIO = 0.20
GATE_COHERENCE_FLOOR = 0.45  # cfg.community.quality_gate.coherence_median_min

PARTITION_NAMES = [
    "louvain_full",
    "louvain_backbone",
    "leiden_full",
    "leiden_backbone",
]
PARTITION_COLORS = {
    "louvain_full": "#1f77b4",       # blue — the live partition
    "louvain_backbone": "#aec7e8",
    "leiden_full": "#d62728",        # red — the candidate
    "leiden_backbone": "#ff9896",
}


@st.cache_data(ttl=300)
def fetch_shadow_history(days: int) -> pd.DataFrame:
    """One row per (run, partition). Reads coherence_med_k5 and fallback_path
    directly from the JSONB array (they postdate the v_shadow_partitions_unnested
    view schema in migration 046)."""
    sql = """
        SELECT
            m.run_id,
            m.ts,
            m.n_storylines_active,
            (p.value ->> 'name')                              AS name,
            (p.value ->> 'n_edges')::INT                      AS n_edges,
            (p.value ->> 'n_communities')::INT                AS n_communities,
            (p.value ->> 'n_singletons')::INT                 AS n_singletons,
            (p.value ->> 'max_community_size')::INT           AS max_community_size,
            (p.value ->> 'avg_community_size')::FLOAT         AS avg_community_size,
            (p.value ->> 'modularity')::FLOAT                 AS modularity,
            (p.value ->> 'silhouette')::FLOAT                 AS silhouette,
            (p.value ->> 'coherence_med')::FLOAT              AS coherence_med,
            (p.value ->> 'coherence_med_k5')::FLOAT           AS coherence_med_k5,
            (p.value ->> 'runtime_ms')::INT                   AS runtime_ms,
            (p.value ->> 'gamma_used')::FLOAT                 AS gamma_used,
             p.value -> 'gamma_sweep_range'                   AS gamma_sweep_range,
            (p.value ->> 'fallback_path')                     AS fallback_path
        FROM narrative_run_metrics m
        CROSS JOIN LATERAL jsonb_array_elements(m.shadow_partitions) AS p(value)
        WHERE m.shadow_partitions IS NOT NULL
          AND m.ts >= NOW() - (%s || ' days')::INTERVAL
        ORDER BY m.ts DESC, name
    """
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (str(days),))
                cols = [c[0] for c in cur.description]
                rows = cur.fetchall()
    except Exception as e:
        logger.error("shadow_partitions query failed: %s", e)
        st.error(f"Query fallita: {e}")
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df

    df["ts"] = pd.to_datetime(df["ts"])
    # Derived: singleton_ratio and max_community_ratio (not stored, cheap to derive).
    df["singleton_ratio"] = df["n_singletons"] / df["n_storylines_active"].replace(0, pd.NA)
    df["max_community_ratio"] = df["max_community_size"] / df["n_storylines_active"].replace(0, pd.NA)
    return df


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _series_chart(df: pd.DataFrame, metric: str, title: str) -> None:
    """One line per partition. Streamlit's built-in line_chart to avoid a
    matplotlib/plotly dependency on this page."""
    if df.empty or metric not in df.columns:
        st.info(f"Nessun dato per '{metric}'.")
        return
    pivot = (
        df[["ts", "name", metric]]
        .pivot_table(index="ts", columns="name", values=metric, aggfunc="last")
        .sort_index()
    )
    # Preserve canonical ordering + color mapping.
    cols_present = [n for n in PARTITION_NAMES if n in pivot.columns]
    if not cols_present:
        st.info(f"'{metric}': nessuna partition disponibile nel range selezionato.")
        return
    pivot = pivot[cols_present]
    st.markdown(f"**{title}**")
    st.line_chart(
        pivot,
        color=[PARTITION_COLORS[c] for c in cols_present],
        height=260,
    )


def _passes_gate(row: pd.Series) -> bool:
    """Decision-22 composite gate applied to any shadow partition (not just
    Leiden). Mirrors the hardcoded gate in _run_leiden_cpm_adaptive_sweep."""
    avg = row.get("avg_community_size")
    max_ratio = row.get("max_community_ratio")
    coh = row.get("coherence_med")
    if pd.isna(avg) or pd.isna(max_ratio) or pd.isna(coh):
        return False
    return (
        GATE_AVG_SIZE_MIN <= avg <= GATE_AVG_SIZE_MAX
        and max_ratio <= GATE_MAX_RATIO
        and coh >= GATE_COHERENCE_FLOOR
    )


def _emoji(ok: bool | None) -> str:
    if ok is None:
        return "—"
    return "✅" if ok else "❌"


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("🧪 Clustering Shadow Comparison")
st.caption(
    "Phase 1E Decision-22 · Data source: `narrative_run_metrics.shadow_partitions` "
    "(migration 046). Live in prod = `louvain_full` (scritto in `storylines.community_id`)."
)

with st.sidebar:
    st.subheader("Filtri")
    days = st.slider("Finestra temporale (giorni)", min_value=7, max_value=90, value=14, step=1)
    st.caption(
        "Il piano OpenSpec (task 1.22) prevede 14–28 giorni di osservazione shadow "
        "prima di decidere se promuovere Leiden a live."
    )

df = fetch_shadow_history(days)

if df.empty:
    st.warning(
        "Nessun run shadow raccolto nella finestra selezionata. "
        "Verifica che migration 046 sia applicata e che la pipeline nightly abbia girato."
    )
    st.stop()

runs_count = df["run_id"].nunique()
partitions_count = len(df)
last_run_ts = df["ts"].max()
n_active_range = f"{int(df['n_storylines_active'].min())} – {int(df['n_storylines_active'].max())}"

c1, c2, c3, c4 = st.columns(4)
c1.metric("Run raccolti", runs_count)
c2.metric("Righe partition", partitions_count)
c3.metric("Ultimo run", last_run_ts.strftime("%Y-%m-%d %H:%M UTC") if pd.notna(last_run_ts) else "—")
c4.metric("n_active range", n_active_range)

readiness = "🟢 Pronto per Decision-22" if runs_count >= 14 else f"🟡 Servono ≥14 run (mancano {max(0, 14 - runs_count)})"
st.caption(readiness)

st.divider()

# ---------------------------------------------------------------------------
# Section 1 — time series
# ---------------------------------------------------------------------------

st.header("1. Serie temporali per partition")
st.caption("Ogni linea è una delle 4 partition; il colore blu è la live (`louvain_full`).")

col_l, col_r = st.columns(2)
with col_l:
    _series_chart(df, "n_communities", "n_communities")
    _series_chart(df, "avg_community_size", "avg_community_size")
    _series_chart(df, "singleton_ratio", "singleton_ratio (n_singletons / n_active)")
with col_r:
    _series_chart(df, "coherence_med", "coherence_med")
    _series_chart(df, "modularity", "modularity")
    _series_chart(df, "silhouette", "silhouette")

st.divider()

# ---------------------------------------------------------------------------
# Section 2 — trade-off scatter
# ---------------------------------------------------------------------------

st.header("2. Trade-off avg_community_size vs coherence_med")
st.caption(
    f"Zona target Decision-22: avg ∈ [{GATE_AVG_SIZE_MIN}, {GATE_AVG_SIZE_MAX}], "
    f"coherence ≥ {GATE_COHERENCE_FLOOR}. Se nessun punto è nel rettangolo → gate fallito."
)

scatter_df = df[["name", "avg_community_size", "coherence_med"]].dropna()
if scatter_df.empty:
    st.info("Nessun punto valido per lo scatter.")
else:
    st.scatter_chart(
        scatter_df,
        x="avg_community_size",
        y="coherence_med",
        color="name",
        height=380,
    )
    passed = df.apply(_passes_gate, axis=1).sum()
    st.caption(f"Righe che passano il gate nel periodo: **{passed} / {len(df)}**.")

st.divider()

# ---------------------------------------------------------------------------
# Section 3 — summary stats per partition
# ---------------------------------------------------------------------------

st.header("3. Statistiche riassuntive per partition")
st.caption("p50, p95 e coefficient of variation (σ/μ) sulle metriche core.")

metrics_for_summary = [
    "n_communities",
    "avg_community_size",
    "coherence_med",
    "modularity",
    "silhouette",
    "singleton_ratio",
]

summary_rows = []
for name, sub in df.groupby("name"):
    row = {"partition": name, "n_runs": len(sub)}
    for m in metrics_for_summary:
        col = sub[m].dropna()
        if col.empty:
            row[f"{m}_p50"] = None
            row[f"{m}_p95"] = None
            row[f"{m}_cv"] = None
        else:
            p50 = float(col.median())
            p95 = float(col.quantile(0.95))
            mean = float(col.mean())
            std = float(col.std(ddof=0)) if len(col) > 1 else 0.0
            cv = (std / mean) if mean not in (0, None) and not pd.isna(mean) else None
            row[f"{m}_p50"] = round(p50, 4)
            row[f"{m}_p95"] = round(p95, 4)
            row[f"{m}_cv"] = round(cv, 3) if cv is not None else None
    summary_rows.append(row)

summary_df = pd.DataFrame(summary_rows)
# Order rows canonically.
summary_df["_order"] = summary_df["partition"].map({n: i for i, n in enumerate(PARTITION_NAMES)})
summary_df = summary_df.sort_values("_order").drop(columns="_order").reset_index(drop=True)
st.dataframe(summary_df, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# Section 4 — Decision-22 pass/fail matrix
# ---------------------------------------------------------------------------

st.header("4. Decision-22 matrix (ultimo run)")
st.caption(
    "Applica il gate composito hardcoded in `_run_leiden_cpm_adaptive_sweep` "
    "all'ultima run raccolta, per ciascuna delle 4 partizioni."
)

last_run_id = df.loc[df["ts"].idxmax(), "run_id"]
last_df = df[df["run_id"] == last_run_id].copy()

matrix_rows = []
for name in PARTITION_NAMES:
    sub = last_df[last_df["name"] == name]
    if sub.empty:
        matrix_rows.append({
            "partition": name,
            "avg_community_size": None,
            "in_[80,240]": _emoji(None),
            "max_community_ratio": None,
            "≤0.20": _emoji(None),
            "coherence_med": None,
            f"≥{GATE_COHERENCE_FLOOR}": _emoji(None),
            "OVERALL": _emoji(None),
        })
        continue
    row = sub.iloc[0]
    avg = row.get("avg_community_size")
    mr = row.get("max_community_ratio")
    coh = row.get("coherence_med")
    ok_avg = None if pd.isna(avg) else GATE_AVG_SIZE_MIN <= avg <= GATE_AVG_SIZE_MAX
    ok_mr = None if pd.isna(mr) else mr <= GATE_MAX_RATIO
    ok_coh = None if pd.isna(coh) else coh >= GATE_COHERENCE_FLOOR
    overall = None if None in (ok_avg, ok_mr, ok_coh) else (ok_avg and ok_mr and ok_coh)
    matrix_rows.append({
        "partition": name,
        "avg_community_size": round(avg, 2) if pd.notna(avg) else None,
        "in_[80,240]": _emoji(ok_avg),
        "max_community_ratio": round(mr, 3) if pd.notna(mr) else None,
        "≤0.20": _emoji(ok_mr),
        "coherence_med": round(coh, 4) if pd.notna(coh) else None,
        f"≥{GATE_COHERENCE_FLOOR}": _emoji(ok_coh),
        "OVERALL": _emoji(overall),
    })

st.dataframe(pd.DataFrame(matrix_rows), use_container_width=True, hide_index=True)

if all(r["OVERALL"] == "❌" for r in matrix_rows if r["OVERALL"] != "—"):
    st.warning(
        "**Nessuna partizione passa il gate nell'ultimo run.** "
        "Se il pattern regge per 14+ giorni: (a) NON promuovere Leiden ciecamente, "
        "(b) valutare relaxing del gate (task 1.22b) o design review "
        "(silhouette negativo persistente = geometria del grafo non supporta macro-community)."
    )

st.divider()

# ---------------------------------------------------------------------------
# Section 5 — γ-sweep + fallback_path panel
# ---------------------------------------------------------------------------

st.header("5. γ-sweep Leiden — gamma_used e fallback_path")
st.caption(
    "Solo Leiden esegue lo sweep γ. `fallback_path` (Fix 2) documenta come è "
    "stata scelta la γ vincente: gate passato / debiased coh_med_k5 / modularity tertiary."
)

leiden_df = df[df["name"].isin(["leiden_full", "leiden_backbone"])].copy()

col_a, col_b = st.columns(2)

with col_a:
    st.markdown("**gamma_used nel tempo**")
    if leiden_df.empty:
        st.info("Nessun dato Leiden nel range.")
    else:
        pivot_g = (
            leiden_df[["ts", "name", "gamma_used"]]
            .pivot_table(index="ts", columns="name", values="gamma_used", aggfunc="last")
            .sort_index()
        )
        cols_present = [c for c in ["leiden_full", "leiden_backbone"] if c in pivot_g.columns]
        if cols_present:
            st.line_chart(
                pivot_g[cols_present],
                color=[PARTITION_COLORS[c] for c in cols_present],
                height=280,
            )
        else:
            st.info("gamma_used non disponibile.")

with col_b:
    st.markdown("**Distribuzione fallback_path**")
    if leiden_df.empty or leiden_df["fallback_path"].dropna().empty:
        st.info("Nessun fallback_path registrato (Fix 2 non ancora attivo su questi run).")
    else:
        counts = (
            leiden_df.groupby(["name", "fallback_path"])
            .size()
            .reset_index(name="count")
            .pivot_table(index="fallback_path", columns="name", values="count", aggfunc="sum")
            .fillna(0)
            .astype(int)
        )
        st.dataframe(counts, use_container_width=True)
        st.caption(
            "`gate_passed` = gate composito soddisfatto | "
            "`coh_med_k5` = fallback debiased (Fix 2) | "
            "`modularity_tertiary` = nessun cluster ≥5 membri."
        )

st.divider()

# ---------------------------------------------------------------------------
# Section 6 — coherence_med vs coherence_med_k5 (Fix 2 audit)
# ---------------------------------------------------------------------------

st.header("6. Audit Fix 2 — coherence_med vs coherence_med_k5")
st.caption(
    "Se `coh_med_k5 << coh_med` → c'era bias micro-cluster e Fix 2 sposta la scelta. "
    "Se sono vicini → bias marginale (osservato in prod post-deploy)."
)

audit_df = df.dropna(subset=["coherence_med", "coherence_med_k5"]).copy()
if audit_df.empty:
    st.info("Nessun run ha entrambe le colonne popolate.")
else:
    audit_df["delta_k5"] = audit_df["coherence_med"] - audit_df["coherence_med_k5"]
    audit_summary = (
        audit_df.groupby("name")
        .agg(
            runs=("delta_k5", "size"),
            coh_med_p50=("coherence_med", "median"),
            coh_med_k5_p50=("coherence_med_k5", "median"),
            delta_p50=("delta_k5", "median"),
            delta_p95=("delta_k5", lambda s: s.quantile(0.95)),
        )
        .round(4)
        .reset_index()
    )
    audit_summary["_order"] = audit_summary["name"].map({n: i for i, n in enumerate(PARTITION_NAMES)})
    audit_summary = audit_summary.sort_values("_order").drop(columns="_order").reset_index(drop=True)
    st.dataframe(audit_summary, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# Raw data expander
# ---------------------------------------------------------------------------

with st.expander("Raw data — righe utilizzate"):
    st.dataframe(df, use_container_width=True, hide_index=True)
