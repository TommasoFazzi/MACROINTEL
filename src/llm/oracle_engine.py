"""
The Oracle - RAG Chat Engine

Hybrid RAG system that searches both:
- Articles (chunks) - for factual/specific queries
- Reports - for strategic/macro queries

Features:
- UI-driven search mode (Hybrid/Investigative/Strategic)
- Context injection with XML-like delimiters (anti-hallucination)
- Freshness indicators for source dating
- Gemini LLM for grounded responses
"""

import os
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Literal
from pathlib import Path

# Load .env file
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path)

import google.generativeai as genai

from ..storage.database import DatabaseManager
from ..utils.logger import get_logger
from ..utils.stopwords import clean_query
from .query_analyzer import get_query_analyzer, merge_filters

logger = get_logger(__name__)

# Search mode types
SearchMode = Literal["both", "factual", "strategic"]


class OracleEngine:
    """
    Hybrid RAG engine for querying intelligence database.

    Supports three search modes:
    - both: Search chunks AND reports (default)
    - factual: Search only chunks (articles)
    - strategic: Search only reports
    """

    # Default configuration
    DEFAULT_CHUNK_TOP_K = 10
    DEFAULT_REPORT_TOP_K = 8
    DEFAULT_MIN_SIMILARITY = 0.30
    DEFAULT_CONTEXT_MAX_CHARS = 60000  # Full reports (~30k each) + articles

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        embedding_model=None,
        gemini_api_key: Optional[str] = None,
        gemini_model_name: str = "gemini-2.5-flash"
    ):
        """
        Initialize Oracle engine.

        Args:
            db_manager: Database manager (uses singleton if None)
            embedding_model: SentenceTransformer model (uses cached singleton if None)
            gemini_api_key: Gemini API key (reads from env if None)
            gemini_model_name: Gemini model to use
        """
        # Database
        if db_manager is None:
            self.db = DatabaseManager()
        else:
            self.db = db_manager

        # Embedding model (lazy load from cached singleton)
        if embedding_model is None:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = None  # Lazy loaded
        else:
            self._embedding_model = embedding_model

        # Configure Gemini
        api_key = (gemini_api_key or os.getenv('GEMINI_API_KEY', '')).strip()
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in environment or parameters")

        genai.configure(api_key=api_key, transport='rest')
        self.llm = genai.GenerativeModel(gemini_model_name)

        logger.info(f"Oracle Engine initialized with {gemini_model_name}")

    @property
    def embedding_model(self):
        """Lazy load embedding model."""
        if self._embedding_model is None:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
            logger.info("Loaded embedding model: paraphrase-multilingual-MiniLM-L12-v2")
        return self._embedding_model

    def search_chunks(
        self,
        query_embedding: List[float],
        top_k: int = DEFAULT_CHUNK_TOP_K,
        min_similarity: float = DEFAULT_MIN_SIMILARITY
    ) -> List[Dict[str, Any]]:
        """
        Search article chunks by semantic similarity.

        Args:
            query_embedding: Query embedding vector
            top_k: Maximum results to return
            min_similarity: Minimum similarity threshold

        Returns:
            List of matching chunks with metadata
        """
        results = self.db.semantic_search(
            query_embedding=query_embedding,
            top_k=top_k
        )

        # Filter by similarity threshold
        filtered = [r for r in results if r.get('similarity', 0) >= min_similarity]
        logger.debug(f"Chunk search: {len(filtered)}/{len(results)} results above threshold")

        return filtered

    def search_reports(
        self,
        query_embedding: List[float],
        top_k: int = DEFAULT_REPORT_TOP_K,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        start_date: Optional[datetime] = None,
        end_date: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        """
        Search reports by semantic similarity with date filtering.
        """
        results = self.db.semantic_search_reports(
            query_embedding=query_embedding,
            top_k=top_k,
            min_similarity=min_similarity,
            # AGGIUNTA: Passiamo i filtri al database
            start_date=start_date,
            end_date=end_date
        )

        logger.debug(f"Report search: {len(results)} results")
        return results

    def format_context_for_llm(
        self,
        reports: List[Dict],
        chunks: List[Dict],
        max_chars: int = DEFAULT_CONTEXT_MAX_CHARS
    ) -> str:
        """
        Format context with XML-like delimiters for anti-hallucination.

        Each document is clearly delimited so the LLM can cite sources accurately.

        Args:
            reports: List of report dictionaries
            chunks: List of chunk dictionaries
            max_chars: Maximum context length

        Returns:
            Formatted context string
        """
        context_parts = []
        current_length = 0

        # Format reports first (higher priority for strategic context)
        for i, report in enumerate(reports, 1):
            report_date = report.get('report_date')
            if hasattr(report_date, 'strftime'):
                date_str = report_date.strftime('%d/%m/%Y')
            else:
                date_str = str(report_date)[:10] if report_date else 'N/A'

            content = report.get('final_content') or report.get('draft_content', '')
            # Pass full report - Investment Implications starts at ~55% and Level 3 at ~85%
            # Gemini handles 30k chars easily, no need to truncate reports
            content = content[:28000] if len(content) > 28000 else content

            doc_text = f"""
<DOCUMENTO_{i}>
Tipo: REPORT
ID: {report.get('id', 'N/A')}
Data: {date_str}
Status: {report.get('status', 'draft')}
Similarity: {report.get('similarity', 0):.2f}

[INIZIO_TESTO]
{content}
[FINE_TESTO]
</DOCUMENTO_{i}>
"""
            if current_length + len(doc_text) > max_chars:
                break
            context_parts.append(doc_text)
            current_length += len(doc_text)

        # Format chunks
        for j, chunk in enumerate(chunks, len(reports) + 1):
            pub_date = chunk.get('published_date')
            if hasattr(pub_date, 'strftime'):
                date_str = pub_date.strftime('%d/%m/%Y')
            else:
                date_str = str(pub_date)[:10] if pub_date else 'N/A'

            doc_text = f"""
<DOCUMENTO_{j}>
Tipo: ARTICOLO
Titolo: {chunk.get('title', 'N/A')}
Fonte: {chunk.get('source', 'Unknown')}
Data: {date_str}
Similarity: {chunk.get('similarity', 0):.2f}

[INIZIO_TESTO]
{chunk.get('content', '')}
[FINE_TESTO]
</DOCUMENTO_{j}>
"""
            if current_length + len(doc_text) > max_chars:
                break
            context_parts.append(doc_text)
            current_length += len(doc_text)

        return "\n".join(context_parts)

    def build_prompt(self, query: str, context: str) -> str:
        """
        Build the prompt for Gemini with 'Sandwich' structure to fix Recency Bias.
        """
        current_date = datetime.now().strftime("%d/%m/%Y")

        prompt = f"""Sei The Oracle, un analista senior di intelligence finanziaria e geopolitica.
DATA ODIERNA: {current_date}

OBIETTIVO:
Fornire un'analisi strategica, approfondita e basata rigorosamente sui fatti recuperati.
Non inventare nulla. Se il contesto non basta, dichiaralo.

CONTESTO DOCUMENTALE:
{context}

---
DOMANDA DELL'UTENTE:
{query}

---
ISTRUZIONI PER LA RISPOSTA (DA SEGUIRE RIGOROSAMENTE):
Agisci come un analista esperto. Non essere sbrigativo.
1. **Analisi Profonda**: Scrivi  paragrafi densi di contenuto.
2. **Citazioni**: Ogni affermazione deve avere la fonte, es: [Report #ID - data] o [Articolo: Titolo].
3. **Freshness Check**: Confronta la data dei documenti con la DATA ODIERNA ({current_date}). Se un report è vecchio (>30gg), segnalalo come 'Dato Storico'.
4. **Struttura Obbligatoria**:
   - **Sintesi Esecutiva**: Il succo in poche righe.
   - **Analisi Dettagliata**: Punti elenco esplosi con dati specifici. Esplicita gli attori, le motivazioni, dinamiche, aree geografiche coinvolte precise.
   - **Implicazioni Strategiche**: Cosa significa questo per il futuro? Impatti potenziali, scenari, rischi, oppoortunità.
5. **Linguaggio**: Formale, professionale, analitico.

RISPOSTA DETTAGLIATA:"""

        return prompt

    def chat(
        self,
        query: str,
        mode: SearchMode = "both",
        chunk_top_k: int = DEFAULT_CHUNK_TOP_K,
        report_top_k: int = DEFAULT_REPORT_TOP_K,
        # NEW PARAMETERS for enhanced search
        search_type: str = "hybrid",  # "vector", "keyword", "hybrid"
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        categories: Optional[List[str]] = None,
        gpe_filter: Optional[List[str]] = None,
        sources: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Main entrypoint for Oracle chat.
        Includes optimized GenerationConfig to fix short/cut-off responses.

        New in FASE 3:
        - Stopword filtering for semantic disambiguation
        - Hybrid search (vector + keyword)
        - Filtering by date, category, geography

        New in FASE 4 (Query Analysis):
        - Pre-search query analysis with Gemini
        - Automatic extraction of temporal/categorical/geographic filters
        - Merge extracted filters with UI filters (UI takes precedence)
        """
        try:
            # ===========================================
            # STEP 0: Query Analysis Layer (NEW)
            # Extract structured filters from natural language
            # ===========================================
            extracted_filters = {}
            query_for_search = query

            try:
                analyzer = get_query_analyzer()
                analysis_result = analyzer.analyze(query)

                if analysis_result['success']:
                    extracted_filters = analysis_result['filters']
                    # Use optimized semantic query for embedding if available
                    semantic_query = extracted_filters.get('semantic_query', '')
                    if semantic_query and len(semantic_query) > 3:
                        query_for_search = semantic_query

                    logger.info(
                        f"Query analysis: extracted filters "
                        f"(dates={extracted_filters.get('start_date')}->{extracted_filters.get('end_date')}, "
                        f"gpe={extracted_filters.get('gpe_filter')}, "
                        f"categories={extracted_filters.get('categories')}, "
                        f"confidence={extracted_filters.get('extraction_confidence', 0):.0%})"
                    )
                else:
                    logger.debug(f"Query analysis returned no filters: {analysis_result.get('error', 'unknown')}")

            except Exception as e:
                logger.warning(f"Query analyzer error (continuing with standard search): {e}")

            # Merge extracted filters with UI filters (UI takes precedence)
            merged = merge_filters(
                extracted_filters,
                ui_start_date=start_date,
                ui_end_date=end_date,
                ui_categories=categories,
                ui_gpe_filter=gpe_filter,
                ui_sources=sources
            )

            # Use merged filters for all subsequent operations
            final_start_date = merged['start_date']
            final_end_date = merged['end_date']
            final_categories = merged['categories']
            final_gpe_filter = merged['gpe_filter']
            final_sources = merged['sources']

            # ===========================================
            # STEP 1: Clean query
            # ===========================================
            cleaned_query = clean_query(query_for_search)
            logger.debug(f"Original: '{query}' -> Semantic: '{query_for_search}' -> Cleaned: '{cleaned_query}'")

            # ===========================================
            # STEP 2: Generate query embedding
            # ===========================================
            query_embedding = self.embedding_model.encode(cleaned_query).tolist()

            # ===========================================
            # STEP 3: Search with MERGED filters
            # ===========================================
            chunks = []
            reports = []

            if mode in ("both", "factual"):
                # Route to appropriate search method based on search_type
                if search_type == "vector":
                    chunks = self.db.semantic_search(
                        query_embedding=query_embedding,
                        top_k=chunk_top_k,
                        category=final_categories[0] if final_categories else None,
                        start_date=final_start_date,
                        end_date=final_end_date,
                        sources=final_sources,
                        gpe_entities=final_gpe_filter
                    )
                elif search_type == "keyword":
                    chunks = self.db.full_text_search(
                        query=cleaned_query,
                        top_k=chunk_top_k,
                        category=final_categories[0] if final_categories else None,
                        start_date=final_start_date,
                        end_date=final_end_date,
                        sources=final_sources,
                        gpe_entities=final_gpe_filter
                    )
                else:  # hybrid (default)
                    chunks = self.db.hybrid_search(
                        query=cleaned_query,
                        query_embedding=query_embedding,
                        top_k=chunk_top_k,
                        category=final_categories[0] if final_categories else None,
                        start_date=final_start_date,
                        end_date=final_end_date,
                        sources=final_sources,
                        gpe_entities=final_gpe_filter
                    )

            if mode in ("both", "strategic"):
                reports = self.search_reports(
                    query_embedding,
                    top_k=report_top_k,
                    start_date=final_start_date,
                    end_date=final_end_date
                )

            # 3. Check if we have any results
            if not chunks and not reports:
                return {
                    "answer": "Non ho trovato informazioni rilevanti nel database. Prova a riformulare la domanda o a cambiare la modalita di ricerca.",
                    "sources": [],
                    "mode": mode,
                    "metadata": {
                        "query": query,
                        "chunks_found": 0,
                        "reports_found": 0,
                        "timestamp": datetime.now().isoformat()
                    }
                }

            # 4. Build context
            context = self.format_context_for_llm(reports, chunks)

            # 5. Generate response with SPECIFIC CONFIG
            prompt = self.build_prompt(query, context)
            
            # CONFIGURAZIONE CRITICA AGGIUNTA
            # Questa configurazione impedisce le risposte mozze (max_output_tokens)
            # e riduce le allucinazioni (temperature 0.4)
            gen_config = genai.types.GenerationConfig(
                max_output_tokens=4096,  # Aumentato per consentire risposte piu lunghe
                temperature=0.6,         # Basso per rigore analitico
                top_p=0.95
            )

            response = self.llm.generate_content(
                prompt,
                generation_config=gen_config,
                request_options={"timeout": 60}
            )
            answer = response.text

            # 6. Prepare sources for UI
            sources = self._prepare_sources(reports, chunks)

            return {
                "answer": answer,
                "sources": sources,
                "mode": mode,
                "metadata": {
                    "query": query,
                    "semantic_query": query_for_search,  # Optimized query used for embedding
                    "extracted_filters": {
                        "start_date": str(extracted_filters.get('start_date')) if extracted_filters.get('start_date') else None,
                        "end_date": str(extracted_filters.get('end_date')) if extracted_filters.get('end_date') else None,
                        "categories": extracted_filters.get('categories'),
                        "gpe_filter": extracted_filters.get('gpe_filter'),
                        "sources": extracted_filters.get('sources'),
                        "confidence": extracted_filters.get('extraction_confidence', 0)
                    },
                    "applied_filters": {  # Final filters after merge (what was actually used)
                        "start_date": str(final_start_date) if final_start_date else None,
                        "end_date": str(final_end_date) if final_end_date else None,
                        "categories": final_categories,
                        "gpe_filter": final_gpe_filter,
                        "sources": final_sources
                    },
                    "chunks_found": len(chunks),
                    "reports_found": len(reports),
                    "context_length": len(context),
                    "timestamp": datetime.now().isoformat()
                }
            }

        except Exception as e:
            logger.error(f"Oracle chat error: {e}")
            return {
                "answer": f"Si e verificato un errore durante l'elaborazione: {str(e)}",
                "sources": [],
                "mode": mode,
                "metadata": {
                    "query": query,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                }
            }
            
    def _prepare_sources(
        self,
        reports: List[Dict],
        chunks: List[Dict]
    ) -> List[Dict[str, Any]]:
        """
        Prepare sources list for UI display.

        Args:
            reports: List of report results
            chunks: List of chunk results

        Returns:
            List of source dictionaries with freshness info
        """
        sources = []

        for report in reports:
            report_date = report.get('report_date')
            if hasattr(report_date, 'strftime'):
                date_str = report_date.strftime('%d/%m/%Y')
            else:
                date_str = str(report_date)[:10] if report_date else 'N/A'

            sources.append({
                "type": "REPORT",
                "id": report.get('id'),
                "title": f"Report #{report.get('id')} - {date_str}",
                "date": report_date,
                "date_str": date_str,
                "similarity": report.get('similarity', 0),
                "status": report.get('status', 'draft'),
                "preview": (report.get('final_content') or report.get('draft_content', ''))[:200]
            })

        for chunk in chunks:
            pub_date = chunk.get('published_date')
            if hasattr(pub_date, 'strftime'):
                date_str = pub_date.strftime('%d/%m/%Y')
            else:
                date_str = str(pub_date)[:10] if pub_date else 'N/A'

            sources.append({
                "type": "ARTICOLO",
                "id": chunk.get('article_id'),
                "title": chunk.get('title', 'Articolo senza titolo'),
                "date": pub_date,
                "date_str": date_str,
                "source": chunk.get('source', 'Unknown'),
                "similarity": chunk.get('similarity', 0),
                "preview": chunk.get('content', '')[:200],
                "link": chunk.get('link')
            })

        # Sort by similarity descending
        sources.sort(key=lambda x: x.get('similarity', 0), reverse=True)

        return sources


# Factory function for Streamlit integration
def get_oracle_engine(
    db_manager: Optional[DatabaseManager] = None,
    embedding_model=None
) -> OracleEngine:
    """
    Factory function to create OracleEngine with proper caching.

    For Streamlit, use with @st.cache_resource on the embedding model.

    Args:
        db_manager: Database manager (optional)
        embedding_model: Pre-loaded embedding model (optional)

    Returns:
        OracleEngine instance
    """
    return OracleEngine(
        db_manager=db_manager,
        embedding_model=embedding_model
    )
