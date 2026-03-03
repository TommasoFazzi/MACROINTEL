"""
Oracle 2.0 Admin Dashboard

Monitoring for Oracle 2.0: active sessions, tool usage, latency percentiles,
cost estimates, and raw query log.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import streamlit as st
import requests

# ── Config ────────────────────────────────────────────────────────────────────
API_BASE = os.getenv("INTELLIGENCE_API_URL", "http://localhost:8000")
API_KEY = os.getenv("INTELLIGENCE_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

st.set_page_config(
    page_title="Oracle Admin",
    page_icon="🔮",
    layout="wide",
)

st.title("🔮 Oracle 2.0 — Admin Dashboard")
st.caption(f"API: {API_BASE}")

# ── Health check ──────────────────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("Health")
    try:
        resp = requests.get(f"{API_BASE}/api/v1/oracle/health", headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            health = resp.json()
            if health.get("healthy"):
                st.success("✅ Oracle healthy")
                checks = health.get("checks", {})
                st.metric("Active Sessions", checks.get("active_sessions", "N/A"))
                tools = checks.get("registry_tools", [])
                st.write("**Registered tools:**")
                for t in tools:
                    st.write(f"  • `{t}`")
            else:
                st.error(f"❌ Unhealthy: {health.get('error', 'unknown')}")
        else:
            st.warning(f"HTTP {resp.status_code}")
    except Exception as e:
        st.error(f"Connection failed: {e}")

# ── Query log stats ───────────────────────────────────────────────────────────
with col2:
    st.subheader("24h Stats")
    try:
        from src.storage.database import DatabaseManager
        db = DatabaseManager()
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) AS total_queries,
                        COUNT(*) FILTER (WHERE success) AS successful,
                        ROUND(AVG(execution_time)::numeric, 2) AS avg_time,
                        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY execution_time)::numeric, 2) AS p50,
                        ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY execution_time)::numeric, 2) AS p95,
                        COUNT(DISTINCT session_id) AS unique_sessions
                    FROM oracle_query_log
                    WHERE created_at >= NOW() - INTERVAL '24 hours'
                """)
                row = cur.fetchone()
                conn.rollback()

        if row:
            st.metric("Queries (24h)", row[0])
            st.metric("Success Rate", f"{row[1]/row[0]*100:.0f}%" if row[0] else "N/A")
            st.metric("P50 Latency", f"{row[3]}s")
            st.metric("P95 Latency", f"{row[4]}s")
            st.metric("Unique Sessions", row[5])
    except Exception as e:
        st.info(f"Query log unavailable: {e}")

with col3:
    st.subheader("Cost Estimate")
    try:
        from src.storage.database import DatabaseManager
        db = DatabaseManager()
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM oracle_query_log
                    WHERE created_at >= NOW() - INTERVAL '24 hours'
                """)
                total_24h = cur.fetchone()[0]
                conn.rollback()

        # Rough estimate: ~2000 tokens/query × $0.00015/1k tokens Gemini 2.5 Flash
        cost_24h = total_24h * 2000 * 0.00015 / 1000
        cost_30d = cost_24h * 30
        st.metric("Queries (24h)", total_24h)
        st.metric("Est. Cost (24h)", f"${cost_24h:.4f}")
        st.metric("Est. Cost (30d)", f"${cost_30d:.2f}")
        st.caption("Based on ~2k tokens/query @ Gemini 2.5 Flash pricing")
    except Exception as e:
        st.info(f"Cost estimate unavailable: {e}")

# ── Tool usage chart ──────────────────────────────────────────────────────────
st.divider()
st.subheader("Tool Usage (24h)")

try:
    from src.storage.database import DatabaseManager
    import json

    db = DatabaseManager()
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT unnest(tools_used) AS tool, COUNT(*) AS uses
                FROM oracle_query_log
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY tool
                ORDER BY uses DESC
            """)
            tool_rows = cur.fetchall()
            conn.rollback()

    if tool_rows:
        import pandas as pd
        df_tools = pd.DataFrame(tool_rows, columns=["Tool", "Uses"])
        st.bar_chart(df_tools.set_index("Tool"))
    else:
        st.info("No tool usage data yet.")
except Exception as e:
    st.info(f"Tool usage unavailable: {e}")

# ── Latency by intent ─────────────────────────────────────────────────────────
st.subheader("Latency by Intent (P50 / P95)")

try:
    from src.storage.database import DatabaseManager
    db = DatabaseManager()
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    intent,
                    COUNT(*) AS count,
                    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY execution_time)::numeric, 2) AS p50,
                    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY execution_time)::numeric, 2) AS p95
                FROM oracle_query_log
                WHERE created_at >= NOW() - INTERVAL '7 days'
                GROUP BY intent
                ORDER BY p95 DESC
            """)
            latency_rows = cur.fetchall()
            conn.rollback()

    if latency_rows:
        import pandas as pd
        df_lat = pd.DataFrame(latency_rows, columns=["Intent", "Count", "P50 (s)", "P95 (s)"])
        st.dataframe(df_lat, use_container_width=True)
    else:
        st.info("No latency data yet.")
except Exception as e:
    st.info(f"Latency data unavailable: {e}")

# ── Recent queries ────────────────────────────────────────────────────────────
st.subheader("Recent Queries")

try:
    from src.storage.database import DatabaseManager
    db = DatabaseManager()
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT session_id, query, intent, complexity, tools_used,
                       ROUND(execution_time::numeric, 2) AS exec_time, success, created_at
                FROM oracle_query_log
                ORDER BY created_at DESC
                LIMIT 20
            """)
            recent_rows = cur.fetchall()
            conn.rollback()

    if recent_rows:
        import pandas as pd
        df_recent = pd.DataFrame(recent_rows, columns=[
            "Session", "Query", "Intent", "Complexity", "Tools", "Time(s)", "Success", "Created At"
        ])
        df_recent["Session"] = df_recent["Session"].str[:8] + "..."
        df_recent["Query"] = df_recent["Query"].str[:80]
        st.dataframe(df_recent, use_container_width=True)
    else:
        st.info("No queries logged yet.")
except Exception as e:
    st.info(f"Recent queries unavailable: {e}")
