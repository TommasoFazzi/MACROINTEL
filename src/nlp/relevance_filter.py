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
    'Rispondi SOLO con JSON: {{"relevant": true}} oppure {{"relevant": false}}\n\n'
    "TITOLO: {title}\n"
    "FONTE: {source}\n"
    "TESTO (primi 300 caratteri): {snippet}"
)

_CESEO_SCOPE_PROMPT = (
    "Sei un analista economico specializzato in Romania e relazioni economiche Italia-Romania.\n\n"
    "Classifica questo articolo come RILEVANTE se riguarda uno dei seguenti ambiti:\n"
    "- Macroeconomia e finanza rumena: inflazione, PIL, disoccupazione, bilancio, debito, deficit, pensioni, salari, investimenti\n"
    "- Banca Nazionale della Romania (BNR): politica monetaria, tassi, riserve, regolamentazione bancaria\n"
    "- Energia e utilities Romania: elettricità, gas, petrolio, nucleare, rinnovabili, ANRE, Transelectrica, Transgaz\n"
    "- Infrastrutture Romania: autostrade, ferrovie, porti, aeroporti, fondi UE, PNRR Romania\n"
    "- Banking e mercati finanziari Romania: banche commerciali, credito, Borsa di Bucarest, dividendi, rating\n"
    "- Politica economica e legislazione Romania: governo, parlamento, leggi fiscali, regolamentazione business\n"
    "- Relazioni economiche UE-Romania: sorveglianza macroeconomica, MIP, coesione, aiuti di Stato\n"
    "- Aziende italiane in Romania: investimenti, manufacturing, supply chain, operazioni\n"
    "- Mar Nero e Caspio con impatto su Romania: gas, grano, corridoi commerciali, Costanza port\n"
    "- Outlook economico CEE con impatto diretto su Romania: Moldova, Bulgaria, Ungheria, Serbia\n\n"
    "Classifica come NON RILEVANTE se riguarda:\n"
    "- Sport, intrattenimento, lifestyle, cronaca nera, gossip\n"
    "- Articoli senza connessione a economia, business o politica economica rumena\n"
    "- Notizie internazionali senza impatto su Romania o interessi italiani in Romania\n\n"
    "REGOLE:\n"
    "- Se il tema ha anche un impatto indiretto sull'economia rumena o sulle aziende italiane → relevant: true\n"
    "- Se hai dubbi, preferisci relevant: true\n\n"
    'Rispondi SOLO con JSON: {{"relevant": true}} oppure {{"relevant": false}}\n\n'
    "TITOLO: {title}\n"
    "FONTE: {source}\n"
    "TESTO (primi 300 caratteri): {snippet}"
)

# Rate limit between LLM calls (seconds)
RATE_LIMIT_SECONDS = 0.15  # Flash-Lite is fast and has high quotas

_SCOPE_PROMPTS = {
    "global": CLASSIFICATION_PROMPT,
    "ceseo": _CESEO_SCOPE_PROMPT,
}


class RelevanceFilter:
    """Classifies articles as relevant or not using T5 (Gemini 2.5 Flash-Lite).

    Args:
        scope: Classification scope — "global" (default geopolitics) or
               "ceseo" (Romania economic/business focus for the CESEO vertical).
    """

    def __init__(self, scope: str = "global"):
        from ..llm.llm_factory import LLMFactory
        self._llm = LLMFactory.get("t5")
        self._scope = scope
        self._prompt_template = _SCOPE_PROMPTS.get(scope, CLASSIFICATION_PROMPT)
        logger.info(f"RelevanceFilter: T5 (Gemini 2.5 Flash-Lite) initialized [scope={scope}]")

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

        prompt = self._prompt_template.format(title=title, source=source, snippet=snippet)

        try:
            response = self._llm.generate(
                prompt,
                max_tokens=50,
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
            f"✓ LLM relevance filter [{self._scope}]: {len(articles)} → {len(relevant)} relevant "
            f"({len(filtered_out)} not relevant, "
            f"{len(filtered_out)/len(articles)*100:.1f}% filtered)"
        )

        return relevant, filtered_out
