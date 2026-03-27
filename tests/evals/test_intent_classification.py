"""
P1 — Intent Classification Eval

FAST EVAL (eval_fast):
    Testa la logica di grading con output pre-definiti (LLM mockato).
    NON misura la qualità del modello — verifica che il grader funzioni correttamente.
    Eseguito su ogni PR.

SLOW EVAL (eval_slow):
    Chiama il vero LLM (Gemini) su tutti i casi split=eval.
    Misura accuracy aggregata e precision/recall per classe.
    Target: accuracy >= 85%.
    Eseguito nightly con GEMINI_API_KEY reale.
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from tests.evals.graders import aggregate_intent_metrics, grade_intent

# ---------------------------------------------------------------------------
# Caricamento dataset
# ---------------------------------------------------------------------------

DATASET_PATH = Path(__file__).parent / "datasets" / "intent_classification.yaml"


def _load_cases(split: str | None = None) -> list[dict]:
    cases = yaml.safe_load(DATASET_PATH.read_text())
    if split:
        cases = [c for c in cases if c.get("split") == split]
    return cases


# ---------------------------------------------------------------------------
# FAST EVALS — grader logic, LLM mockato
# ---------------------------------------------------------------------------

class TestIntentGraderLogic:
    """
    Fast eval: testa grade_intent() con output pre-definiti.
    LLM non viene chiamato — si usa generate_content mockato.
    """

    @pytest.mark.eval_fast
    def test_exact_match_passes(self):
        output = {"intent": "factual", "confidence": 0.9, "key_entities": ["Ukraine"]}
        expected = {"expected_intent": "factual"}
        result = grade_intent(output, expected)
        assert result["pass"] is True
        assert result["score"] == 1.0

    @pytest.mark.eval_fast
    def test_wrong_intent_fails(self):
        output = {"intent": "analytical", "confidence": 0.8, "key_entities": []}
        expected = {"expected_intent": "factual"}
        result = grade_intent(output, expected)
        assert result["pass"] is False
        assert result["score"] == 0.0

    @pytest.mark.eval_fast
    def test_case_insensitive_match(self):
        """LLM a volte restituisce 'Factual' con maiuscola — deve comunque passare."""
        output = {"intent": "Factual", "confidence": 0.85, "key_entities": []}
        expected = {"expected_intent": "factual"}
        result = grade_intent(output, expected)
        assert result["pass"] is True

    @pytest.mark.eval_fast
    def test_whitespace_stripped(self):
        """LLM a volte aggiunge spazi — deve comunque passare."""
        output = {"intent": " factual ", "confidence": 0.85, "key_entities": []}
        expected = {"expected_intent": "factual"}
        result = grade_intent(output, expected)
        assert result["pass"] is True

    @pytest.mark.eval_fast
    def test_missing_intent_field_fails(self):
        output = {"confidence": 0.9}  # campo intent assente
        expected = {"expected_intent": "factual"}
        result = grade_intent(output, expected)
        assert result["pass"] is False

    @pytest.mark.eval_fast
    def test_confidence_not_part_of_grading(self):
        """confidence=0.0 NON deve causare fallimento — non è nel grading logic."""
        output = {"intent": "factual", "confidence": 0.0, "key_entities": []}
        expected = {"expected_intent": "factual"}
        result = grade_intent(output, expected)
        assert result["pass"] is True

    @pytest.mark.eval_fast
    def test_aggregate_metrics_accuracy(self):
        results = [
            {"predicted": "factual", "expected": "factual"},
            {"predicted": "factual", "expected": "factual"},
            {"predicted": "analytical", "expected": "factual"},  # errore
            {"predicted": "analytical", "expected": "analytical"},
        ]
        metrics = aggregate_intent_metrics(results)
        assert metrics["_accuracy"] == pytest.approx(0.75)
        assert metrics["_correct"] == 3
        assert metrics["_total"] == 4

    @pytest.mark.eval_fast
    def test_aggregate_metrics_per_class_precision(self):
        results = [
            {"predicted": "factual", "expected": "factual"},   # TP factual
            {"predicted": "factual", "expected": "analytical"}, # FP factual / FN analytical
            {"predicted": "analytical", "expected": "analytical"},  # TP analytical
        ]
        metrics = aggregate_intent_metrics(results)
        # factual: precision = 1/(1+1) = 0.5, recall = 1/(1+0) = 1.0
        assert metrics["factual"]["precision"] == pytest.approx(0.5)
        assert metrics["factual"]["recall"] == pytest.approx(1.0)

    @pytest.mark.eval_fast
    def test_all_eval_cases_have_required_fields(self):
        """Validità strutturale del dataset: ogni caso deve avere id, query, expected_intent, split."""
        cases = _load_cases()
        for case in cases:
            assert "id" in case, f"Missing 'id': {case}"
            assert "query" in case, f"Missing 'query': {case.get('id')}"
            assert "expected_intent" in case, f"Missing 'expected_intent': {case.get('id')}"
            assert "split" in case, f"Missing 'split': {case.get('id')}"
            assert case["split"] in ("eval", "dev"), f"Invalid split value: {case}"

    @pytest.mark.eval_fast
    def test_dataset_has_correct_class_distribution(self):
        """35 casi totali: 5 per classe, 3 eval + 2 dev per classe."""
        cases = _load_cases()
        assert len(cases) == 35, f"Expected 35 cases, got {len(cases)}"

        eval_cases = [c for c in cases if c["split"] == "eval"]
        dev_cases = [c for c in cases if c["split"] == "dev"]
        assert len(eval_cases) == 21, f"Expected 21 eval cases, got {len(eval_cases)}"
        assert len(dev_cases) == 14, f"Expected 14 dev cases, got {len(dev_cases)}"

        # Verifica 3 eval per ogni intent
        intents = ["factual", "analytical", "narrative", "market", "comparative", "ticker", "overview"]
        for intent in intents:
            count = sum(1 for c in eval_cases if c["expected_intent"] == intent)
            assert count == 3, f"Expected 3 eval cases for '{intent}', got {count}"


# ---------------------------------------------------------------------------
# SLOW EVALS — LLM reale, metriche aggregate
# ---------------------------------------------------------------------------

class TestIntentClassificationSlow:
    """
    Slow eval: chiama il vero QueryRouter con Gemini su tutti i casi split=eval.
    Target: accuracy >= 85% sull'eval set.
    """

    @pytest.mark.eval_slow
    def test_intent_accuracy_eval_set(self):
        """
        Accuracy aggregata >= 85% sull'eval set (21 casi).
        Richiede GEMINI_API_KEY reale.
        """
        if not os.environ.get("GEMINI_API_KEY") or \
                os.environ.get("GEMINI_API_KEY") == "ci-fake-key-for-unit-tests":
            pytest.skip("GEMINI_API_KEY reale non disponibile")

        import google.generativeai as genai
        from src.llm.query_router import QueryRouter

        genai.configure(api_key=os.environ["GEMINI_API_KEY"], transport="rest")
        llm = genai.GenerativeModel("gemini-2.5-flash")
        router = QueryRouter(llm=llm)

        eval_cases = _load_cases(split="eval")
        results = []

        for case in eval_cases:
            intent_obj, _ = router._classify_intent(case["query"])
            graded = grade_intent(
                output={"intent": intent_obj.value},
                expected={"expected_intent": case["expected_intent"]},
            )
            results.append(graded)

        metrics = aggregate_intent_metrics(results)

        # Salva metriche per il baseline check
        results_path = Path("eval_results.json")
        existing = json.loads(results_path.read_text()) if results_path.exists() else {}
        existing["intent_classification_accuracy"] = metrics["_accuracy"]
        existing["intent_classification_per_class"] = {
            k: v for k, v in metrics.items() if not k.startswith("_")
        }
        results_path.write_text(json.dumps(existing, indent=2))

        # Report per-class
        print(f"\nIntent Classification Results (eval set, n={metrics['_total']}):")
        print(f"  Overall accuracy: {metrics['_accuracy']:.1%}")
        for intent in ["factual", "analytical", "narrative", "market", "comparative", "ticker", "overview"]:
            cls_metrics = metrics.get(intent, {})
            print(f"  {intent:12s}: precision={cls_metrics.get('precision', 'N/A'):.2f}, "
                  f"recall={cls_metrics.get('recall', 'N/A'):.2f}")

        assert metrics["_accuracy"] >= 0.85, (
            f"Intent classification accuracy {metrics['_accuracy']:.1%} < 85% target. "
            f"Correct: {metrics['_correct']}/{metrics['_total']}"
        )
