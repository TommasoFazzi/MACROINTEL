"""Tests for RAGTool time-weighted decay."""

import math
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.llm.tools.rag_tool import (
    DEFAULT_DECAY_K,
    MIN_DECAYED_SCORE,
    OVER_FETCH_MULTIPLIER,
    SEARCH_TYPE_SCORE_FIELD,
    apply_time_decay,
    RAGTool,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_chunk(similarity: float, days_ago: int, ref: date = None) -> dict:
    """Create a fake chunk result dict."""
    ref = ref or date.today()
    pub = ref - timedelta(days=days_ago)
    return {
        "chunk_id": hash((similarity, days_ago)),
        "content": f"chunk days_ago={days_ago}",
        "title": f"Article {days_ago}d ago",
        "source": "TestSource",
        "published_date": datetime(pub.year, pub.month, pub.day),
        "similarity": similarity,
    }


def _make_report(similarity: float, days_ago: int, ref: date = None) -> dict:
    ref = ref or date.today()
    pub = ref - timedelta(days=days_ago)
    return {
        "id": hash((similarity, days_ago)),
        "report_date": datetime(pub.year, pub.month, pub.day),
        "similarity": similarity,
        "status": "final",
        "final_content": "report content",
    }


# ── Test apply_time_decay ─────────────────────────────────────────────────────

class TestApplyTimeDecay:
    """Unit tests for the apply_time_decay function."""

    def test_basic_math(self):
        """Verify formula: score = raw * exp(-k * days)."""
        ref = datetime(2026, 3, 16, tzinfo=timezone.utc)
        results = [_make_chunk(0.90, 30, ref.date())]

        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity", reference_date=ref,
        )

        expected = 0.90 * math.exp(-0.025 * 30)
        assert abs(decayed[0]["similarity"] - expected) < 1e-6
        assert decayed[0]["similarity_raw"] == 0.90
        assert decayed[0]["days_old"] == 30
        assert abs(decayed[0]["time_decay_factor"] - math.exp(-0.025 * 30)) < 1e-4

    def test_today_articles_unaffected(self):
        """Articles published today should have decay_factor ~1.0."""
        ref = datetime(2026, 3, 16, tzinfo=timezone.utc)
        results = [_make_chunk(0.85, 0, ref.date())]

        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity", reference_date=ref,
        )

        assert decayed[0]["time_decay_factor"] == 1.0
        assert decayed[0]["similarity"] == 0.85
        assert decayed[0]["days_old"] == 0

    def test_no_date_field_no_penalty(self):
        """Results without a date get decay_factor=1.0, no crash."""
        results = [{"similarity": 0.80, "content": "no date here"}]
        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity",
        )
        assert decayed[0]["time_decay_factor"] == 1.0
        assert decayed[0]["similarity"] == 0.80
        assert decayed[0]["days_old"] == 0

    def test_resorting_recent_beats_old(self):
        """A recent article with moderate similarity should beat an old high-similarity one."""
        ref = datetime(2026, 3, 16, tzinfo=timezone.utc)
        old_high = _make_chunk(0.90, 60, ref.date())   # old but very similar
        new_moderate = _make_chunk(0.70, 1, ref.date())  # fresh, moderate similarity

        results = [old_high, new_moderate]
        decayed = apply_time_decay(
            results, decay_k=0.03, date_field="published_date",
            score_field="similarity", reference_date=ref,
        )

        # After decay: old = 0.90 * exp(-0.03*60) ≈ 0.149, new = 0.70 * exp(-0.03*1) ≈ 0.679
        assert decayed[0]["content"] == new_moderate["content"]
        assert decayed[0]["similarity"] > decayed[1]["similarity"]

    def test_timezone_aware_naive_mix(self):
        """Mix of timezone-aware ref and naive published_date should not crash."""
        ref_aware = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        results = [{
            "similarity": 0.80,
            "published_date": datetime(2026, 3, 10),  # naive
        }]
        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity", reference_date=ref_aware,
        )
        assert decayed[0]["days_old"] == 6
        assert decayed[0]["time_decay_factor"] > 0

    def test_timezone_aware_published_date(self):
        """Published date with timezone info should work correctly."""
        ref = datetime(2026, 3, 16, tzinfo=timezone.utc)
        results = [{
            "similarity": 0.80,
            "published_date": datetime(2026, 3, 9, 15, 30, tzinfo=timezone.utc),
        }]
        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity", reference_date=ref,
        )
        assert decayed[0]["days_old"] == 7

    def test_string_date(self):
        """Date as ISO string should be parsed correctly."""
        ref = datetime(2026, 3, 16, tzinfo=timezone.utc)
        results = [{"similarity": 0.80, "published_date": "2026-03-06T10:00:00"}]
        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity", reference_date=ref,
        )
        assert decayed[0]["days_old"] == 10

    def test_different_score_fields(self):
        """Works with fts_score, fusion_score, not just similarity."""
        ref = datetime(2026, 3, 16, tzinfo=timezone.utc)

        for field in ("fts_score", "fusion_score", "similarity"):
            results = [{field: 0.80, "published_date": datetime(2026, 3, 6)}]
            decayed = apply_time_decay(
                results, decay_k=0.025, date_field="published_date",
                score_field=field, reference_date=ref,
            )
            assert f"{field}_raw" in decayed[0]
            assert decayed[0][f"{field}_raw"] == 0.80
            assert decayed[0][field] < 0.80  # decayed

    def test_days_old_field_present(self):
        """Every result should have days_old field."""
        ref = datetime(2026, 3, 16, tzinfo=timezone.utc)
        results = [
            _make_chunk(0.90, 0, ref.date()),
            _make_chunk(0.80, 15, ref.date()),
            {"similarity": 0.70},  # no date
        ]
        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity", reference_date=ref,
        )
        for r in decayed:
            assert "days_old" in r

    def test_sort_score_field_present(self):
        """Every result with a date should have _sort_score."""
        ref = datetime(2026, 3, 16, tzinfo=timezone.utc)
        results = [_make_chunk(0.85, 5, ref.date())]
        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity", reference_date=ref,
        )
        assert "_sort_score" in decayed[0]
        assert decayed[0]["_sort_score"] == decayed[0]["similarity"]


# ── Test time-shifting ────────────────────────────────────────────────────────

class TestTimeShifting:
    """Test time-shifting for historical queries."""

    def test_historical_query_penalizes_out_of_window(self):
        """Query for Jan 2024 should penalize articles from 2021."""
        ref_historical = datetime(2024, 1, 15, tzinfo=timezone.utc)

        jan_2024_article = {
            "similarity": 0.75,
            "published_date": datetime(2024, 1, 10),
        }
        old_2021_article = {
            "similarity": 0.90,  # higher raw similarity
            "published_date": datetime(2021, 6, 1),
        }

        results = [old_2021_article, jan_2024_article]
        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity", reference_date=ref_historical,
        )

        # Jan 2024 article is 5 days from ref → mild decay
        # 2021 article is ~940 days from ref → massive decay
        assert decayed[0]["published_date"] == jan_2024_article["published_date"]
        assert decayed[1]["similarity"] < 0.01  # 2021 article nearly zeroed

    def test_recent_query_no_shift(self):
        """Without reference_date, decay is relative to now."""
        results = [_make_chunk(0.80, 0)]
        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity",
        )
        assert decayed[0]["days_old"] == 0
        assert decayed[0]["time_decay_factor"] == 1.0


# ── Test min floor ────────────────────────────────────────────────────────────

class TestMinFloor:
    """Test that MIN_DECAYED_SCORE filters out noise."""

    def test_min_floor_value(self):
        """MIN_DECAYED_SCORE should be 0.15."""
        assert MIN_DECAYED_SCORE == 0.15

    def test_floor_filters_low_scores(self):
        """Demonstrate that post-decay filtering works with the floor constant."""
        ref = datetime(2026, 3, 16, tzinfo=timezone.utc)
        # Very old article: 0.30 * exp(-0.025 * 200) ≈ 0.002 → below floor
        results = [_make_chunk(0.30, 200, ref.date())]
        decayed = apply_time_decay(
            results, decay_k=0.025, date_field="published_date",
            score_field="similarity", reference_date=ref,
        )
        # The function itself doesn't filter — _execute() does
        assert decayed[0]["similarity"] < MIN_DECAYED_SCORE


# ── Test over-fetch multiplier ────────────────────────────────────────────────

class TestOverFetch:
    """Test that over-fetch is applied when decay is active."""

    def test_multiplier_value(self):
        assert OVER_FETCH_MULTIPLIER == 3

    @patch("src.llm.tools.rag_tool._get_embedding_model")
    def test_execute_overfetches_with_decay(self, mock_embed):
        """_execute should pass top_k * 3 to DB when decay is active."""
        mock_model = MagicMock()
        mock_model.encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
        mock_embed.return_value = mock_model

        mock_db = MagicMock()
        mock_db.semantic_search.return_value = []
        mock_db.semantic_search_reports.return_value = []

        tool = RAGTool(db=mock_db, llm=MagicMock())
        tool._execute(query="test", top_k=10, mode="both",
                      filters={"search_type": "vector", "time_decay_k": 0.025})

        # Should have called semantic_search with top_k=30 (10 * 3)
        call_args = mock_db.semantic_search.call_args
        assert call_args.kwargs.get("top_k", call_args[1].get("top_k")) == 30

    @patch("src.llm.tools.rag_tool._get_embedding_model")
    def test_execute_no_overfetch_without_decay(self, mock_embed):
        """_execute should pass original top_k when decay is disabled."""
        mock_model = MagicMock()
        mock_model.encode.return_value = MagicMock(tolist=lambda: [0.1] * 384)
        mock_embed.return_value = mock_model

        mock_db = MagicMock()
        mock_db.semantic_search.return_value = []
        mock_db.semantic_search_reports.return_value = []

        tool = RAGTool(db=mock_db, llm=MagicMock())
        tool._execute(query="test", top_k=10, mode="both",
                      filters={"search_type": "vector", "time_decay_k": 0})

        call_args = mock_db.semantic_search.call_args
        assert call_args.kwargs.get("top_k", call_args[1].get("top_k")) == 10


# ── Test intent-based K ──────────────────────────────────────────────────────

class TestIntentDecayK:
    """Test that intent-based K values are correctly mapped."""

    def test_score_field_mapping(self):
        """SEARCH_TYPE_SCORE_FIELD should map all search types."""
        assert SEARCH_TYPE_SCORE_FIELD["vector"] == "similarity"
        assert SEARCH_TYPE_SCORE_FIELD["keyword"] == "fts_score"
        assert SEARCH_TYPE_SCORE_FIELD["hybrid"] == "fusion_score"

    def test_default_decay_k(self):
        """DEFAULT_DECAY_K should be 0.025."""
        assert DEFAULT_DECAY_K == 0.025
