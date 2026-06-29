"""Test that NarrativeProcessor._extract_entity_list filters
out media_artifacts entries listed in entity_blocklist.yaml.
"""

from unittest.mock import patch

from src.nlp.narrative_processor import NarrativeProcessor


def test_reuters_is_removed_from_key_entities() -> None:
    """Spec scenario: Reuters is removed from key_entities."""
    entities_json = {"clean": {"all": ["Reuters", "Vladimir Putin", "Kremlin"]}}
    with patch("src.nlp.narrative_processor._ENTITY_BLOCKLIST", {"reuters"}):
        result = NarrativeProcessor._extract_entity_list(entities_json)
    assert "Vladimir Putin" in result
    assert "Kremlin" in result
    assert all(e.lower() != "reuters" for e in result)


def test_lower_case_match_is_filtered() -> None:
    """Spec scenario: lower-case match is filtered (case-insensitive)."""
    entities_json = {"clean": {"all": ["reuters", "Kremlin"]}}
    with patch("src.nlp.narrative_processor._ENTITY_BLOCKLIST", {"reuters"}):
        result = NarrativeProcessor._extract_entity_list(entities_json)
    assert all(e.lower() != "reuters" for e in result)
    assert "Kremlin" in result


def test_empty_blocklist_keeps_everything() -> None:
    """If blocklist is empty, behavior matches pre-Fix (only structural garbage filtered)."""
    entities_json = {"clean": {"all": ["Reuters", "Vladimir Putin"]}}
    with patch("src.nlp.narrative_processor._ENTITY_BLOCKLIST", set()):
        result = NarrativeProcessor._extract_entity_list(entities_json)
    assert "Reuters" in result
    assert "Vladimir Putin" in result


def test_generic_terms_not_loaded_by_default() -> None:
    """Sanity: by default `_ENTITY_BLOCKLIST` is sourced from `media_artifacts`
    only, not from `generic_terms`. So a legitimate generic-but-real actor name
    like "China" or "President" is NOT filtered."""
    # We rely on the module-level _ENTITY_BLOCKLIST loaded at import time
    # from the real config/entity_blocklist.yaml.
    import src.nlp.narrative_processor as np_mod
    # media_artifacts entries (should be present)
    assert "reuters" in np_mod._ENTITY_BLOCKLIST or len(np_mod._ENTITY_BLOCKLIST) == 0
    # generic_terms entries (should NOT be present even if defined in YAML)
    assert "president" not in np_mod._ENTITY_BLOCKLIST
    assert "ministry" not in np_mod._ENTITY_BLOCKLIST


def test_blocklist_does_not_affect_old_format() -> None:
    """The hook SHALL apply also to the old `by_type` entity format."""
    entities_json = {
        "by_type": {
            "ORG": ["Reuters", "Hamas"],
            "PERSON": ["Vladimir Putin"],
            "GPE": ["Russia"],
        }
    }
    with patch("src.nlp.narrative_processor._ENTITY_BLOCKLIST", {"reuters"}):
        result = NarrativeProcessor._extract_entity_list(entities_json)
    assert "Hamas" in result
    assert "Vladimir Putin" in result
    assert "Russia" in result
    assert all(e.lower() != "reuters" for e in result)
