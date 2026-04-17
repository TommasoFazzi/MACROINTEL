"""
LLM-based relevance filter for intelligence articles.

Uses T5 (Gemini 2.5 Flash-Lite) via LLMFactory to classify articles as relevant
or not relevant to the platform's scope: geopolitics, defense, cyber security,
energy, finance/macro, space, supply chain (strategic), politics.

Articles marked as not relevant are tagged but NOT deleted — they are
excluded from further processing (clustering, storylines, reports).
"""

import json
import time
from typing import Dict, List, Tuple

from ..utils.logger import get_logger

logger = get_logger(__name__)

# Domains that define the platform's scope
SCOPE_DESCRIPTION = """geopolitica, politica internazionale e domestica, difesa e militare, \
cyber security, intelligence, spazio (strategico/militare), energia (strategica), \
economia e finanza macro/strategica, supply chain strategica, semiconduttori, \
minerali critici, sanzioni, commercio internazionale, terrorismo, crimine organizzato \
transnazionale, diritti umani (in contesto geopolitico), migrazioni (in contesto politico)."""

# What is OUT of scope
OUT_OF_SCOPE = """sport (calcio, cricket, tennis, basket, etc.), \
intrattenimento (film, musica, streaming, celebrity), \
salute/medicina (a meno che non sia bio-sicurezza o arma biologica), \
cronaca locale (incidenti, omicidi, meteo locale), \
business consumer (prodotti alimentari, moda, turismo), \
archeologia, lifestyle, gossip."""

CLASSIFICATION_PROMPT = (
    "Sei un analista di intelligence. Classifica questo articolo.\n\n"
    f"AMBITO DELLA PIATTAFORMA: {SCOPE_DESCRIPTION}\n\n"
    f"FUORI AMBITO: {OUT_OF_SCOPE}\n\n"
    "REGOLE:\n"
    "- Se l'articolo è chiaramente dentro l'ambito → relevant: true\n"
    "- Se l'articolo è chiaramente fuori ambito → relevant: false\n"
    "- Se è borderline (es. sport usato come leva geopolitica, salute pubblica come arma strategica) → relevant: true\n"
    "- Se hai dubbi, preferisci relevant: true (meglio un falso positivo che perdere intelligence)\n\n"
    'Rispondi SOLO con JSON: {"relevant": true} oppure {"relevant": false}\n\n'
    "TITOLO: {title}\n"
    "FONTE: {source}\n"
    "TESTO (primi 300 caratteri): {snippet}"
)

# Rate limit between LLM calls (seconds)
RATE_LIMIT_SECONDS = 0.15  # Flash-Lite is fast and has high quotas


class RelevanceFilter:
    """Classifies articles as relevant or not using T5 (Gemini 2.5 Flash-Lite)."""

    def __init__(self):
        from ..llm.llm_factory import LLMFactory
        self._llm = LLMFactory.get("t5")
        logger.info("RelevanceFilter: T5 (Gemini 2.5 Flash-Lite) initialized")

    def classify_article(self, article: Dict) -> bool:
        """
        Classify a single article as relevant or not.

        Returns:
            True if relevant, False if not relevant
        """
        title = article.get('title', '')
        source = article.get('source', '')
        full_text = article.get('full_text', '') or article.get('summary', '') or ''
        snippet = full_text[:300]

        prompt = CLASSIFICATION_PROMPT.format(title=title, source=source, snippet=snippet)

        try:
            response = self._llm.generate(
                prompt,
                max_tokens=20,
                temperature=0.1,
                json_mode=True,
            )
            data = json.loads(response.strip())
            return bool(data.get("relevant", True))
        except Exception as e:
            logger.warning(f"LLM classification failed for '{title[:50]}': {e}. Defaulting to RELEVANT.")
            return True  # On error, keep the article

    def filter_batch(self, articles: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """
        Classify a batch of articles.

        Returns:
            Tuple of (relevant_articles, filtered_out_articles)
        """
        if not articles:
            return [], []

        relevant = []
        filtered_out = []

        for i, article in enumerate(articles):
            is_relevant = self.classify_article(article)

            if is_relevant:
                article['relevance_label'] = 'relevant'
                relevant.append(article)
            else:
                article['relevance_label'] = 'not_relevant'
                filtered_out.append(article)
                logger.debug(
                    f"Filtered (not relevant): {article.get('title', 'N/A')[:60]}... "
                    f"[{article.get('source', '?')}]"
                )

            # Rate limiting
            if i < len(articles) - 1:
                time.sleep(RATE_LIMIT_SECONDS)

            # Progress logging
            if (i + 1) % 50 == 0:
                logger.info(
                    f"  Relevance check: {i + 1}/{len(articles)} "
                    f"({len(relevant)} relevant, {len(filtered_out)} filtered)"
                )

        logger.info(
            f"✓ LLM relevance filter: {len(articles)} → {len(relevant)} relevant "
            f"({len(filtered_out)} not relevant, "
            f"{len(filtered_out)/len(articles)*100:.1f}% filtered)"
        )

        return relevant, filtered_out
