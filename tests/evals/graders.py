"""
Funzioni di grading per gli LLM evals.

Indice:
  - grade_intent / aggregate_intent_metrics     → P1 Intent Classification
  - grade_relevance / aggregate_relevance_metrics → P2 Relevance Filter
  - grade_query_analyzer                         → P3 Query Analyzer
  - grade_sql / aggregate_sql_metrics            → P4 SQL Generation (deterministico)

Ogni grader prende l'output del modello e il caso di test atteso,
e restituisce un dict con almeno: pass (bool) e score (float 0-1).

Nota: confidence LLM auto-reportata è esclusa dal grading
(mal calibrata per definizione — Kadavath et al. 2022).
"""

from __future__ import annotations
from collections import defaultdict
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Normalizza stringhe LLM: strip + lowercase per evitare falsi negativi."""
    return s.strip().lower() if isinstance(s, str) else ""


# ---------------------------------------------------------------------------
# P1 — Intent Classification
# ---------------------------------------------------------------------------

def grade_intent(output: dict, expected: dict) -> dict:
    """
    Exact match sul campo intent (case-insensitive, strip).
    NON verifica confidence: gli LLM non sono calibrati su quel campo.

    Fast eval docstring: testa il parsing JSON e il confronto intent.
    La qualità reale del modello è misurata dagli slow evals aggregati.
    """
    got = _normalize(output.get("intent", ""))
    exp = _normalize(expected["expected_intent"])
    correct = got == exp
    return {
        "pass": correct,
        "score": 1.0 if correct else 0.0,
        "predicted": got,
        "expected": exp,
    }


def aggregate_intent_metrics(results: list[dict]) -> dict:
    """
    Calcola accuracy aggregata + precision/recall per classe.
    Input: lista di dict da grade_intent (con campi predicted, expected).
    """
    per_class: dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})

    for r in results:
        pred, truth = r["predicted"], r["expected"]
        if pred == truth:
            per_class[truth]["tp"] += 1
        else:
            per_class[pred]["fp"] += 1
            per_class[truth]["fn"] += 1

    metrics: dict[str, Any] = {}
    for cls, counts in per_class.items():
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        metrics[cls] = {"precision": round(precision, 3), "recall": round(recall, 3)}

    total = len(results)
    correct = sum(1 for r in results if r["predicted"] == r["expected"])
    metrics["_accuracy"] = round(correct / total, 3) if total > 0 else 0.0
    metrics["_total"] = total
    metrics["_correct"] = correct
    return metrics


# ---------------------------------------------------------------------------
# P2 — Relevance Filter
# ---------------------------------------------------------------------------

def grade_relevance(output: str, expected: str) -> dict:
    """
    Binary classification: cerca 'NOT_RELEVANT' nel testo libero dell'LLM.
    Default a RELEVANT se non trovato (comportamento del filtro in produzione).
    """
    predicted = "NOT_RELEVANT" if "NOT_RELEVANT" in output.upper().strip() else "RELEVANT"
    correct = predicted == expected.upper()
    return {
        "pass": correct,
        "score": 1.0 if correct else 0.0,
        "predicted": predicted,
        "expected": expected.upper(),
    }


def aggregate_relevance_metrics(results: list[dict]) -> dict:
    """
    Accuracy, precision, recall, F1 per la classe RELEVANT.
    Il recall è la metrica più critica: i falsi negativi (articoli rilevanti
    scartati) degradano il corpus e sono difficili da recuperare.
    """
    tp = sum(1 for r in results if r["predicted"] == "RELEVANT" and r["expected"] == "RELEVANT")
    fp = sum(1 for r in results if r["predicted"] == "RELEVANT" and r["expected"] == "NOT_RELEVANT")
    fn = sum(1 for r in results if r["predicted"] == "NOT_RELEVANT" and r["expected"] == "RELEVANT")
    tn = sum(1 for r in results if r["predicted"] == "NOT_RELEVANT" and r["expected"] == "NOT_RELEVANT")

    total = len(results)
    accuracy = (tp + tn) / total if total > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "accuracy": round(accuracy, 3),
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "_total": total,
    }


# ---------------------------------------------------------------------------
# P3 — Query Analyzer (field-level precision)
# ---------------------------------------------------------------------------

def grade_query_analyzer(output: dict, expected: dict) -> dict:
    """
    Field-level precision per i filtri estratti:
    - start_date / end_date: exact match ISO string
    - categories: precision del subset (case-insensitive)
    - gpe_filter: verifica che i valori attesi siano contenuti nell'output
    """
    field_scores: dict[str, Any] = {}

    # Date: confronto ISO string (già normalizzato da _post_process_dates)
    # Il test deve passare l'output come stringhe ISO prima di questa funzione
    for date_field in ("start_date", "end_date"):
        exp_val = expected.get(date_field)
        got_val = output.get(date_field)
        if exp_val is None:
            # Se l'atteso è null, ci aspettiamo null nell'output
            field_scores[date_field] = got_val is None
        else:
            field_scores[date_field] = got_val == exp_val

    # Categories: subset precision (case-insensitive)
    if expected.get("categories") is not None:
        exp_cats = {c.strip().upper() for c in expected["categories"]}
        got_cats = {c.strip().upper() for c in (output.get("categories") or [])}
        if exp_cats:
            field_scores["categories"] = round(len(exp_cats & got_cats) / len(exp_cats), 3)
        else:
            field_scores["categories"] = 1.0

    # GPE filter: verifica che i valori attesi siano nel risultato
    if expected.get("gpe_filter_contains") is not None:
        got_gpe = {g.strip().upper() for g in (output.get("gpe_filter") or [])}
        all_found = all(
            g.strip().upper() in got_gpe
            for g in expected["gpe_filter_contains"]
        )
        field_scores["gpe_filter"] = all_found
    elif expected.get("gpe_filter") is not None:
        exp_gpe = {g.strip().upper() for g in expected["gpe_filter"]}
        got_gpe = {g.strip().upper() for g in (output.get("gpe_filter") or [])}
        field_scores["gpe_filter"] = exp_gpe == got_gpe

    # Score aggregato: media dei campi valutati
    numeric = [v for v in field_scores.values() if isinstance(v, (int, float))]
    bool_as_num = [1.0 if v else 0.0 for v in field_scores.values() if isinstance(v, bool)]
    all_scores = numeric + bool_as_num
    # Ricalcola uniformemente
    all_scores = []
    for v in field_scores.values():
        if isinstance(v, bool):
            all_scores.append(1.0 if v else 0.0)
        elif isinstance(v, float):
            all_scores.append(v)

    aggregate = round(sum(all_scores) / len(all_scores), 3) if all_scores else 0.0

    return {
        "pass": aggregate >= 0.8,
        "score": aggregate,
        "fields": field_scores,
    }


# ---------------------------------------------------------------------------
# P4 — SQL Generation (grading deterministico, nessun LLM-as-judge)
# ---------------------------------------------------------------------------

# Tabelle consentite nel prompt di _generate_sql
SQL_ALLOWED_TABLES = {
    "articles", "chunks", "reports", "storylines", "entities",
    "entity_mentions", "trade_signals", "macro_indicators", "market_data",
    "article_storylines", "storyline_edges", "v_active_storylines", "v_storyline_graph",
}

# Keyword DML/DDL che non devono mai comparire nell'SQL generato
SQL_FORBIDDEN_KEYWORDS = {
    "DROP", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER", "CREATE",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "COPY", "VACUUM",
}


def _extract_table_references(sql: str) -> set[str]:
    """
    Estrae i nomi di tabella referenziati in FROM e JOIN.
    Parsing semplice ma sufficiente per SQL generati da LLM (non annidati).
    """
    import re
    # Cerca pattern: FROM table_name e JOIN table_name (con alias opzionale)
    pattern = r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
    matches = re.findall(pattern, sql, flags=re.IGNORECASE)
    return {m.lower() for m in matches}


def grade_sql(sql: str | None, expected: dict) -> dict:
    """
    Grader deterministico per SQL generato da LLM. Tre dimensioni:

    1. Structural validity: inizia con SELECT (dopo strip di markdown ```sql)
    2. Safety: nessun keyword DML/DDL distruttivo
    3. Schema adherence:
       - Tutte le tabelle referenziate sono nella allowed list
       - Le tabelle attese (expected_tables) sono presenti nell'SQL
       - Le colonne attese (expected_columns) sono presenti come token
       - Le tabelle proibite (forbidden_tables) sono assenti

    Returns dict con: pass (bool), score (float 0-1), dimensions (dict).
    """
    if sql is None:
        return {
            "pass": False,
            "score": 0.0,
            "dimensions": {
                "structural": False,
                "safety": False,
                "schema": False,
            },
            "error": "SQL is None — generation failed or returned None",
        }

    # Strip markdown code fences che l'LLM può aggiungere nonostante le istruzioni
    clean_sql = sql.strip().strip("```sql").strip("```").strip()
    sql_upper = clean_sql.upper()

    # ── Dimensione 1: Structural validity ────────────────────────────────────
    structural_ok = sql_upper.startswith("SELECT")

    # ── Dimensione 2: Safety ─────────────────────────────────────────────────
    import re
    safety_violations = [
        kw for kw in SQL_FORBIDDEN_KEYWORDS
        if re.search(rf"\b{kw}\b", sql_upper)
    ]
    safety_ok = len(safety_violations) == 0

    # ── Dimensione 3: Schema adherence ───────────────────────────────────────
    schema_checks: dict[str, Any] = {}
    referenced_tables = _extract_table_references(clean_sql)

    # 3a. Tutte le tabelle referenziate sono nella allowed list
    unknown_tables = referenced_tables - SQL_ALLOWED_TABLES
    schema_checks["no_unknown_tables"] = len(unknown_tables) == 0

    # 3b. Tabelle attese presenti
    if expected.get("expected_tables"):
        for tbl in expected["expected_tables"]:
            schema_checks[f"has_table_{tbl}"] = tbl.lower() in sql_upper.lower()

    # 3c. Colonne attese presenti come token
    if expected.get("expected_columns"):
        for col in expected["expected_columns"]:
            schema_checks[f"has_column_{col}"] = (
                re.search(rf"\b{re.escape(col)}\b", clean_sql, re.IGNORECASE) is not None
            )

    # 3d. Tabelle proibite assenti
    if expected.get("forbidden_tables"):
        for tbl in expected["forbidden_tables"]:
            schema_checks[f"no_forbidden_table_{tbl}"] = (
                tbl.lower() not in sql_upper.lower()
            )

    # 3e. LIMIT presente (se richiesto)
    if expected.get("must_have_limit"):
        schema_checks["has_limit"] = "LIMIT" in sql_upper

    # 3f. MAX JOINs rispettato
    if expected.get("max_joins") is not None:
        join_count = len(re.findall(r'\bJOIN\b', sql_upper))
        schema_checks["max_joins_respected"] = join_count <= expected["max_joins"]

    schema_bool_scores = [1.0 if v else 0.0 for v in schema_checks.values()]
    schema_score = round(
        sum(schema_bool_scores) / len(schema_bool_scores), 3
    ) if schema_bool_scores else 1.0
    schema_ok = schema_score >= 0.8

    # ── Score finale ─────────────────────────────────────────────────────────
    # Safety è gate assoluto: un SQL non sicuro fallisce sempre, indipendentemente
    # da structural e schema.
    passed = structural_ok and safety_ok and schema_ok

    dim_scores = [1.0 if structural_ok else 0.0, 1.0 if safety_ok else 0.0, schema_score]
    overall_score = round(sum(dim_scores) / 3, 3)

    return {
        "pass": passed,
        "score": overall_score,
        "dimensions": {
            "structural": structural_ok,
            "safety": safety_ok,
            "safety_violations": safety_violations,
            "schema": schema_ok,
            "schema_score": schema_score,
            "schema_checks": schema_checks,
            "referenced_tables": list(referenced_tables),
            "unknown_tables": list(unknown_tables),
        },
    }


def aggregate_sql_metrics(results: list[dict]) -> dict:
    """Aggregato di pass-rate e score medio per tutti i casi SQL."""
    if not results:
        return {}
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    structural_ok = sum(1 for r in results if r["dimensions"]["structural"])
    safety_ok = sum(1 for r in results if r["dimensions"]["safety"])
    schema_ok = sum(1 for r in results if r["dimensions"]["schema"])
    avg_score = round(sum(r["score"] for r in results) / total, 3)

    return {
        "pass_rate": round(passed / total, 3),
        "structural_pass_rate": round(structural_ok / total, 3),
        "safety_pass_rate": round(safety_ok / total, 3),
        "schema_pass_rate": round(schema_ok / total, 3),
        "avg_score": avg_score,
        "_total": total,
        "_passed": passed,
    }
