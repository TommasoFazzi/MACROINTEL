"""
P2 — Relevance Filter Eval

FAST EVAL (eval_fast):
    Testa la logica di grading e il parsing dell'output testuale.
    LLM non viene chiamato.
    Eseguito su ogni PR.

SLOW EVAL (eval_slow):
    Chiama il vero RelevanceFilter (Gemini 2.0-flash) su tutti i casi split=eval.
    Misura accuracy, precision, recall, F1.
    Target: accuracy >= 90%, recall >= 0.95.
    Il recall è la metrica critica: i falsi negativi (articoli rilevanti scartati)
    degradano silenziosamente il corpus.
    Eseguito nightly con GEMINI_API_KEY reale.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from tests.evals.graders import aggregate_relevance_metrics, grade_relevance

# ---------------------------------------------------------------------------
# Caricamento dataset
# ---------------------------------------------------------------------------

DATASET_PATH = Path(__file__).parent / "datasets" / "relevance_filter.yaml"


def _load_cases(split: str | None = None) -> list[dict]:
    cases = yaml.safe_load(DATASET_PATH.read_text())
    if split:
        cases = [c for c in cases if c.get("split") == split]
    return cases


def _case_to_article(case: dict) -> dict:
    return {
        "title": case["title"],
        "source": case["source"],
        "full_text": case["text_preview"],
    }


# ---------------------------------------------------------------------------
# FAST EVALS — grader logic, LLM non chiamato
# ---------------------------------------------------------------------------

class TestRelevanceGraderLogic:
    """
    Fast eval: testa grade_relevance() con output LLM simulati.
    Non chiama il modello reale.
    """

    @pytest.mark.eval_fast
    def test_relevant_detected_correctly(self):
        result = grade_relevance("RELEVANT", "RELEVANT")
        assert result["pass"] is True
        assert result["score"] == 1.0

    @pytest.mark.eval_fast
    def test_not_relevant_detected_correctly(self):
        result = grade_relevance("NOT_RELEVANT", "NOT_RELEVANT")
        assert result["pass"] is True
        assert result["score"] == 1.0

    @pytest.mark.eval_fast
    def test_false_positive(self):
        """LLM dice RELEVANT ma doveva essere NOT_RELEVANT."""
        result = grade_relevance("RELEVANT", "NOT_RELEVANT")
        assert result["pass"] is False
        assert result["score"] == 0.0

    @pytest.mark.eval_fast
    def test_false_negative(self):
        """LLM dice NOT_RELEVANT ma doveva essere RELEVANT — caso critico."""
        result = grade_relevance("NOT_RELEVANT", "RELEVANT")
        assert result["pass"] is False
        assert result["score"] == 0.0

    @pytest.mark.eval_fast
    def test_llm_output_with_extra_text(self):
        """LLM spesso aggiunge testo prima/dopo — deve comunque parsare correttamente."""
        result = grade_relevance("This article is NOT_RELEVANT to the platform scope.", "NOT_RELEVANT")
        assert result["pass"] is True

    @pytest.mark.eval_fast
    def test_llm_output_lowercase(self):
        """Output in lowercase — deve comunque parsare."""
        result = grade_relevance("not_relevant", "NOT_RELEVANT")
        assert result["pass"] is True

    @pytest.mark.eval_fast
    def test_empty_output_defaults_to_relevant(self):
        """Output vuoto → default RELEVANT (comportamento production del filtro)."""
        result = grade_relevance("", "RELEVANT")
        assert result["pass"] is True  # Empty → predicted RELEVANT → matches expected RELEVANT

    @pytest.mark.eval_fast
    def test_aggregate_metrics_perfect(self):
        results = [
            {"predicted": "RELEVANT", "expected": "RELEVANT"},
            {"predicted": "RELEVANT", "expected": "RELEVANT"},
            {"predicted": "NOT_RELEVANT", "expected": "NOT_RELEVANT"},
            {"predicted": "NOT_RELEVANT", "expected": "NOT_RELEVANT"},
        ]
        metrics = aggregate_relevance_metrics(results)
        assert metrics["accuracy"] == pytest.approx(1.0)
        assert metrics["recall"] == pytest.approx(1.0)
        assert metrics["precision"] == pytest.approx(1.0)
        assert metrics["f1"] == pytest.approx(1.0)

    @pytest.mark.eval_fast
    def test_aggregate_metrics_with_false_negative(self):
        """1 falso negativo → recall < 1.0."""
        results = [
            {"predicted": "RELEVANT", "expected": "RELEVANT"},
            {"predicted": "NOT_RELEVANT", "expected": "RELEVANT"},  # FN
            {"predicted": "NOT_RELEVANT", "expected": "NOT_RELEVANT"},
        ]
        metrics = aggregate_relevance_metrics(results)
        assert metrics["recall"] == pytest.approx(0.5)
        assert metrics["fn"] == 1

    @pytest.mark.eval_fast
    def test_all_eval_cases_have_required_fields(self):
        cases = _load_cases()
        for case in cases:
            assert "id" in case
            assert "title" in case
            assert "source" in case
            assert "text_preview" in case
            assert case.get("expected") in ("RELEVANT", "NOT_RELEVANT"), (
                f"Invalid expected value in case {case.get('id')}: {case.get('expected')}"
            )
            assert case.get("split") in ("eval", "dev"), (
                f"Invalid split in case {case.get('id')}: {case.get('split')}"
            )

    @pytest.mark.eval_fast
    def test_dataset_balanced(self):
        """Verifica che l'eval set sia bilanciato (≥ 40% per classe)."""
        eval_cases = _load_cases(split="eval")
        relevant = sum(1 for c in eval_cases if c["expected"] == "RELEVANT")
        not_relevant = sum(1 for c in eval_cases if c["expected"] == "NOT_RELEVANT")
        total = len(eval_cases)
        assert relevant / total >= 0.4, f"Too few RELEVANT cases: {relevant}/{total}"
        assert not_relevant / total >= 0.4, f"Too few NOT_RELEVANT cases: {not_relevant}/{total}"


# ---------------------------------------------------------------------------
# SLOW EVALS — LLM reale, metriche aggregate
# ---------------------------------------------------------------------------

class TestRelevanceFilterSlow:
    """
    Slow eval: chiama il vero RelevanceFilter (Gemini 2.0-flash) sui casi eval.
    Target: accuracy >= 90%, recall >= 0.95.
    """

    @pytest.mark.eval_slow
    def test_relevance_accuracy_and_recall_eval_set(self):
        """
        Accuracy >= 90% e recall >= 0.95 sull'eval set.
        Richiede GEMINI_API_KEY reale.
        """
        if not os.environ.get("GEMINI_API_KEY") or \
                os.environ.get("GEMINI_API_KEY") == "ci-fake-key-for-unit-tests":
            pytest.skip("GEMINI_API_KEY reale non disponibile")

        from src.nlp.relevance_filter import RelevanceFilter

        rf = RelevanceFilter()
        eval_cases = _load_cases(split="eval")
        results = []

        for case in eval_cases:
            article = _case_to_article(case)
            is_relevant: bool = rf.classify_article(article)
            predicted = "RELEVANT" if is_relevant else "NOT_RELEVANT"
            graded = grade_relevance(predicted, case["expected"])
            results.append(graded)

        metrics = aggregate_relevance_metrics(results)

        # Salva metriche per il baseline check
        results_path = Path("eval_results.json")
        existing = json.loads(results_path.read_text()) if results_path.exists() else {}
        existing["relevance_filter_accuracy"] = metrics["accuracy"]
        existing["relevance_filter_recall"] = metrics["recall"]
        existing["relevance_filter_f1"] = metrics["f1"]
        results_path.write_text(json.dumps(existing, indent=2))

        print(f"\nRelevance Filter Results (eval set, n={metrics['_total']}):")
        print(f"  Accuracy:  {metrics['accuracy']:.1%}")
        print(f"  Precision: {metrics['precision']:.1%}")
        print(f"  Recall:    {metrics['recall']:.1%}  (target: >= 95%)")
        print(f"  F1:        {metrics['f1']:.1%}")
        print(f"  TP={metrics['tp']} FP={metrics['fp']} FN={metrics['fn']} TN={metrics['tn']}")

        assert metrics["accuracy"] >= 0.90, (
            f"Accuracy {metrics['accuracy']:.1%} < 90% target"
        )
        assert metrics["recall"] >= 0.95, (
            f"Recall {metrics['recall']:.1%} < 95% target — "
            f"{metrics['fn']} articoli rilevanti scartati erroneamente"
        )
