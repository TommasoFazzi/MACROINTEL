"""
Fixtures comuni per gli LLM evals.

IMPORTANTE: i fast evals (eval_fast) usano il mock LLM e testano
solo la logica di parsing/grading, NON la qualità del modello.
La qualità del modello è testata dai slow evals (eval_slow) nightly.
"""

import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixture: skip se la chiave Gemini reale non è disponibile
# ---------------------------------------------------------------------------

def _has_real_gemini_key() -> bool:
    key = os.environ.get("GEMINI_API_KEY", "")
    return bool(key) and key != "ci-fake-key-for-unit-tests"


def _has_real_openai_key() -> bool:
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("ci-fake")


skip_if_no_gemini_key = pytest.mark.skipif(
    not _has_real_gemini_key(),
    reason="GEMINI_API_KEY reale non disponibile — slow eval skipped"
)

skip_if_no_openai_key = pytest.mark.skipif(
    not _has_real_openai_key(),
    reason="OPENAI_API_KEY non disponibile — judge LLM skipped"
)


# ---------------------------------------------------------------------------
# Fixture: mock risposta Gemini generica
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_gemini_response():
    """
    Restituisce una factory per creare mock di generate_content().
    Uso: mock_gemini_response(text='{"intent": "factual", "confidence": 0.9}')
    """
    def _factory(text: str):
        mock_resp = MagicMock()
        mock_resp.text = text
        return mock_resp
    return _factory


@pytest.fixture
def patch_gemini():
    """
    Patch genai.GenerativeModel.generate_content per l'intera durata del test.
    Restituisce il mock object per configurare side_effect o return_value.

    Uso:
        def test_something(patch_gemini):
            patch_gemini.return_value.text = '{"intent": "factual", ...}'
    """
    with patch("google.generativeai.GenerativeModel.generate_content") as mock_gc:
        yield mock_gc
