"""
P3 — Query Analyzer Eval

FAST EVAL (eval_fast):
    Testa la logica di grading field-level con output JSON pre-definiti.
    LLM non viene chiamato.
    Eseguito su ogni PR.

SLOW EVAL (eval_slow):
    Chiama il vero QueryAnalyzer (Gemini 2.5-flash) su tutti i casi split=eval,
    iniettando reference_date dal dataset per rendere i test deterministici.
    Misura precision per campo: start_date, end_date, categories, gpe_filter.
    Target: field precision >= 90%.
    Eseguito nightly con GEMINI_API_KEY reale.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from tests.evals.graders import grade_query_analyzer

# ---------------------------------------------------------------------------
# Caricamento dataset
# ---------------------------------------------------------------------------

DATASET_PATH = Path(__file__).parent / "datasets" / "query_analyzer.yaml"


def _load_cases(split: str | None = None) -> list[dict]:
    cases = yaml.safe_load(DATASET_PATH.read_text())
    if split:
        cases = [c for c in cases if c.get("split") == split]
    return cases


def _normalize_output_dates(filters_dict: dict) -> dict:
    """
    Converte eventuali datetime object in stringhe ISO per il confronto.
    QueryAnalyzer._post_process_dates() trasforma le stringhe in datetime objects,
    ma il grader confronta stringhe ISO — serve il back-conversion.
    """
    out = dict(filters_dict)
    for field in ("start_date", "end_date"):
        val = out.get(field)
        if isinstance(val, datetime):
            out[field] = val.strftime("%Y-%m-%d")
    return out


# ---------------------------------------------------------------------------
# FAST EVALS — grader logic, LLM non chiamato
# ---------------------------------------------------------------------------

class TestQueryAnalyzerGraderLogic:
    """
    Fast eval: testa grade_query_analyzer() con output pre-definiti.
    Non chiama il modello reale.
    """

    @pytest.mark.eval_fast
    def test_exact_dates_pass(self):
        output = {"start_date": "2026-03-20", "end_date": "2026-03-27"}
        expected = {"start_date": "2026-03-20", "end_date": "2026-03-27"}
        result = grade_query_analyzer(output, expected)
        assert result["fields"]["start_date"] is True
        assert result["fields"]["end_date"] is True

    @pytest.mark.eval_fast
    def test_wrong_start_date_fails(self):
        output = {"start_date": "2026-03-21", "end_date": "2026-03-27"}
        expected = {"start_date": "2026-03-20", "end_date": "2026-03-27"}
        result = grade_query_analyzer(output, expected)
        assert result["fields"]["start_date"] is False

    @pytest.mark.eval_fast
    def test_null_date_when_expected_null(self):
        output = {"start_date": None, "end_date": None}
        expected = {"start_date": None, "end_date": None}
        result = grade_query_analyzer(output, expected)
        assert result["fields"]["start_date"] is True
        assert result["fields"]["end_date"] is True

    @pytest.mark.eval_fast
    def test_non_null_when_expected_null_fails(self):
        output = {"start_date": "2026-03-01", "end_date": None}
        expected = {"start_date": None, "end_date": None}
        result = grade_query_analyzer(output, expected)
        assert result["fields"]["start_date"] is False

    @pytest.mark.eval_fast
    def test_category_subset_match(self):
        output = {"categories": ["DEFENSE", "CYBER"]}
        expected = {"categories": ["DEFENSE"]}
        result = grade_query_analyzer(output, expected)
        # L'expected è trovato nel subset dell'output → precision = 1.0
        assert result["fields"]["categories"] == pytest.approx(1.0)

    @pytest.mark.eval_fast
    def test_category_case_insensitive(self):
        output = {"categories": ["defense"]}
        expected = {"categories": ["DEFENSE"]}
        result = grade_query_analyzer(output, expected)
        assert result["fields"]["categories"] == pytest.approx(1.0)

    @pytest.mark.eval_fast
    def test_category_missing_fails(self):
        output = {"categories": ["CYBER"]}
        expected = {"categories": ["DEFENSE"]}
        result = grade_query_analyzer(output, expected)
        assert result["fields"]["categories"] == pytest.approx(0.0)

    @pytest.mark.eval_fast
    def test_gpe_filter_contains(self):
        output = {"gpe_filter": ["Iran", "United States"]}
        expected = {"gpe_filter_contains": ["Iran"]}
        result = grade_query_analyzer(output, expected)
        assert result["fields"]["gpe_filter"] is True

    @pytest.mark.eval_fast
    def test_gpe_filter_contains_missing_fails(self):
        output = {"gpe_filter": ["Russia"]}
        expected = {"gpe_filter_contains": ["Iran"]}
        result = grade_query_analyzer(output, expected)
        assert result["fields"]["gpe_filter"] is False

    @pytest.mark.eval_fast
    def test_aggregate_score_high_when_all_pass(self):
        output = {
            "start_date": "2026-03-20",
            "end_date": "2026-03-27",
            "categories": ["DEFENSE"],
            "gpe_filter": ["Iran"],
        }
        expected = {
            "start_date": "2026-03-20",
            "end_date": "2026-03-27",
            "categories": ["DEFENSE"],
            "gpe_filter_contains": ["Iran"],
        }
        result = grade_query_analyzer(output, expected)
        assert result["score"] == pytest.approx(1.0)
        assert result["pass"] is True

    @pytest.mark.eval_fast
    def test_all_eval_cases_have_required_fields(self):
        cases = _load_cases()
        for case in cases:
            assert "id" in case
            assert "query" in case
            assert "current_date" in case, f"Missing current_date in {case.get('id')} — needed for deterministic eval"
            assert "expected" in case
            assert "split" in case
            assert case["split"] in ("eval", "dev")

    @pytest.mark.eval_fast
    def test_reference_date_injected_correctly(self):
        """
        Verifica che analyze() accetti reference_date come parametro.
        Mock del modello per evitare la chiamata reale.
        """
        from unittest.mock import MagicMock, patch

        mock_resp = MagicMock()
        mock_resp.text = json.dumps({
            "start_date": "2026-03-20",
            "end_date": "2026-03-27",
            "categories": None,
            "gpe_filter": ["Iran"],
            "sources": None,
            "semantic_query": "Iran news",
            "extraction_confidence": 0.9,
        })

        with patch("google.generativeai.GenerativeModel.generate_content", return_value=mock_resp):
            import google.generativeai as genai
            genai.configure(api_key="ci-fake-key-for-unit-tests", transport="rest")

            from src.llm.query_analyzer import QueryAnalyzer
            analyzer = QueryAnalyzer.__new__(QueryAnalyzer)
            analyzer.model = genai.GenerativeModel("gemini-2.5-flash")
            analyzer.model_name = "gemini-2.5-flash"

            result = analyzer.analyze("Articoli Iran ultimi 7 giorni", reference_date="2026-03-27")

        assert result["success"] is True
        # Verifica che la reference_date sia stata effettivamente usata nel prompt
        # (non testiamo il contenuto del prompt direttamente, ma che il metodo non crashi)


# ---------------------------------------------------------------------------
# SLOW EVALS — LLM reale, field precision
# ---------------------------------------------------------------------------

class TestQueryAnalyzerSlow:
    """
    Slow eval: chiama il vero QueryAnalyzer sui casi eval con date fisse.
    Target: field precision media >= 90%.
    """

    @pytest.mark.eval_slow
    def test_field_precision_eval_set(self):
        """
        Field precision media >= 90% su tutti i campi valutati nell'eval set.
        Richiede GEMINI_API_KEY reale.
        NOTA: reference_date è sempre iniettato dal YAML per determinismo.
        """
        if not os.environ.get("GEMINI_API_KEY") or \
                os.environ.get("GEMINI_API_KEY") == "ci-fake-key-for-unit-tests":
            pytest.skip("GEMINI_API_KEY reale non disponibile")

        from src.llm.query_analyzer import QueryAnalyzer

        analyzer = QueryAnalyzer()
        eval_cases = _load_cases(split="eval")

        all_results = []
        field_totals: dict[str, list[float]] = {}

        for case in eval_cases:
            result = analyzer.analyze(
                query=case["query"],
                reference_date=case["current_date"],  # deterministico
            )

            if not result.get("success"):
                pytest.fail(
                    f"QueryAnalyzer failed on case {case['id']}: {result.get('error')}"
                )

            # Converti datetime → ISO string per il grader
            filters = _normalize_output_dates(result["filters"])
            graded = grade_query_analyzer(filters, case["expected"])
            all_results.append(graded)

            for field, score in graded["fields"].items():
                field_totals.setdefault(field, [])
                field_totals[field].append(1.0 if score is True else (0.0 if score is False else float(score)))

        # Calcola precision media per campo
        field_precisions = {
            field: round(sum(vals) / len(vals), 3)
            for field, vals in field_totals.items()
            if vals
        }
        overall = round(
            sum(field_precisions.values()) / len(field_precisions), 3
        ) if field_precisions else 0.0

        # Salva metriche per baseline check
        results_path = Path("eval_results.json")
        existing = json.loads(results_path.read_text()) if results_path.exists() else {}
        existing["query_analyzer_date_precision"] = field_precisions.get("start_date", 0)
        existing["query_analyzer_category_precision"] = field_precisions.get("categories", 0)
        existing["query_analyzer_gpe_precision"] = field_precisions.get("gpe_filter", 0)
        results_path.write_text(json.dumps(existing, indent=2))

        print(f"\nQuery Analyzer Results (eval set, n={len(eval_cases)}):")
        for field, prec in field_precisions.items():
            print(f"  {field:20s}: {prec:.1%}")
        print(f"  {'Overall':20s}: {overall:.1%}")

        assert overall >= 0.90, (
            f"Query analyzer overall field precision {overall:.1%} < 90% target. "
            f"Per field: {field_precisions}"
        )
