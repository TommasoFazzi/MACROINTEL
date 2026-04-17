# ARCHIVED — superseded by Oracle 2.0 (oracle_orchestrator.py)
"""
The Oracle - RAG Chat Interface

Hybrid RAG chat interface for querying the intelligence database.
Features:
- Search mode toggle (Hybrid/Investigative/Strategic)
- Context-aware responses from Gemini LLM
- Source citations with freshness indicators
- Mobile-friendly expander for sources
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# Setup path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import streamlit as st

from src.hitl.streamlit_utils import (
    get_db_manager,
    get_embedding_model,
    inject_custom_css,
    init_session_state,
    get_freshness_badge,
    get_freshness_label
)
from src.llm.oracle_engine import OracleEngine
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Page Configuration
st.set_page_config(
    page_title="The Oracle | INTELLIGENCE_ITA",
    page_icon="🧠",
    layout="wide"
)

# Initialize
inject_custom_css()
init_session_state()
db = get_db_manager()

# Oracle-specific session state
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'search_mode' not in st.session_state:
    st.session_state.search_mode = "both"
if 'oracle_engine' not in st.session_state:
    st.session_state.oracle_engine = None


# =============================================================================
# ORACLE ENGINE INITIALIZATION
# =============================================================================

@st.cache_resource
def get_oracle():
    """Get cached Oracle engine instance."""
    try:
        embedding_model = get_embedding_model()
        engine = OracleEngine(
            db_manager=db,
            embedding_model=embedding_model
        )
        return engine
    except Exception as e:
        logger.error(f"Failed to initialize Oracle: {e}")
        return None


# =============================================================================
# SEARCH MODE SELECTOR
# =============================================================================

def render_search_mode_selector():
    """Render the search mode toggle."""
    st.markdown("#### Modalita Ricerca")

    mode_options = {
        "both": "🌐 Ibrida (Tutto)",
        "factual": "🔍 Investigativa (Articoli)",
        "strategic": "🧠 Strategica (Report)"
    }

    mode_descriptions = {
        "both": "Cerca in articoli E report per risposte complete",
        "factual": "Cerca solo negli articoli per informazioni specifiche",
        "strategic": "Cerca solo nei report per analisi macro"
    }

    selected = st.radio(
        "Seleziona modalita",
        options=list(mode_options.keys()),
        format_func=lambda x: mode_options[x],
        horizontal=True,
        index=list(mode_options.keys()).index(st.session_state.search_mode),
        label_visibility="collapsed"
    )

    st.caption(mode_descriptions[selected])
    st.session_state.search_mode = selected

    return selected


# =============================================================================
# SOURCES EXPANDER
# =============================================================================

def render_sources_expander(sources: list):
    """Render sources in an expander with freshness badges."""
    if not sources:
        return

    # Count by type
    reports = [s for s in sources if s.get('type') == 'REPORT']
    articles = [s for s in sources if s.get('type') == 'ARTICOLO']

    with st.expander(f"📚 Fonti utilizzate ({len(reports)} report, {len(articles)} articoli)"):
        # Freshness legend
        st.markdown("""
        <small>
        🟢 Intelligence Fresca (&lt;7gg) |
        🟡 Intelligence Recente (&lt;30gg) |
        🔴 Old Intelligence (&gt;30gg)
        </small>
        """, unsafe_allow_html=True)

        st.divider()

        for source in sources:
            badge = get_freshness_badge(source.get('date'))
            freshness_label = get_freshness_label(source.get('date'))
            similarity_pct = int(source.get('similarity', 0) * 100)

            if source.get('type') == 'REPORT':
                st.markdown(
                    f"{badge} **Report #{source.get('id')}** - "
                    f"{source.get('date_str', 'N/A')} ({freshness_label}) | "
                    f"Match: {similarity_pct}%"
                )
                if source.get('preview'):
                    st.caption(source['preview'][:150] + "...")

            else:  # ARTICOLO
                st.markdown(
                    f"{badge} **{source.get('title', 'Articolo')[:50]}** - "
                    f"{source.get('source', 'Unknown')} | "
                    f"{source.get('date_str', 'N/A')} ({freshness_label}) | "
                    f"Match: {similarity_pct}%"
                )
                if source.get('link'):
                    st.caption(f"[Link]({source['link']})")

            st.markdown("---")


# =============================================================================
# CHAT INTERFACE
# =============================================================================

def render_chat_messages():
    """Render chat history."""
    for message in st.session_state.chat_history:
        role = message.get('role', 'user')
        content = message.get('content', '')
        sources = message.get('sources', [])

        with st.chat_message(role):
            st.markdown(content)

            # Render sources for assistant messages
            if role == 'assistant' and sources:
                render_sources_expander(sources)


def process_query(
    query: str,
    oracle: OracleEngine,
    mode: str,
    search_type: str = "hybrid",
    start_date=None,
    end_date=None,
    categories=None,
    gpe_filter=None
):
    """
    Process user query and get response from Oracle.

    New in FASE 3: Supports filtering and hybrid search.
    """
    with st.spinner("🔮 Consulto le fonti di intelligence..."):
        response = oracle.chat(
            query=query,
            mode=mode,
            search_type=search_type,
            chunk_top_k=7,
            report_top_k=5,
            start_date=start_date,
            end_date=end_date,
            categories=categories,
            gpe_filter=gpe_filter
        )

    return response


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main entry point."""
    st.title("🧠 The Oracle")
    st.markdown("### Interroga il Database di Intelligence")

    # Check if Oracle is available
    oracle = get_oracle()

    if oracle is None:
        st.error("""
        **Oracle non disponibile**

        Possibili cause:
        - GEMINI_API_KEY non configurata
        - Errore di connessione al database

        Verifica il file .env e riprova.
        """)
        return

    # Sidebar with controls
    with st.sidebar:
        st.title("🎛️ Controlli")

        # Search mode
        render_search_mode_selector()

        st.divider()

        # Search type (NUOVO - FASE 3)
        st.subheader("🔍 Tipo di Ricerca")
        search_type = st.radio(
            "Modalità",
            ["hybrid", "vector", "keyword"],
            format_func=lambda x: {
                "hybrid": "🌐 Ibrida (Vector + Keyword)",
                "vector": "🧠 Solo Semantica (Vector)",
                "keyword": "📝 Solo Parole Chiave"
            }[x],
            index=0,
            horizontal=False,
            key="search_type_radio"
        )

        if search_type == "hybrid":
            st.caption("✨ Combina ricerca semantica e keyword per risultati ottimali")
        elif search_type == "vector":
            st.caption("🎯 Ricerca semantica (concetti simili)")
        else:
            st.caption("🔤 Ricerca esatta per parole chiave")

        st.divider()

        # Date range filter (NUOVO - FASE 3)
        st.subheader("📅 Filtro Temporale")
        use_date_filter = st.checkbox("Filtra per data", value=False, key="use_date_filter_cb")

        start_date = None
        end_date = None
        if use_date_filter:
            col1, col2 = st.columns(2)
            with col1:
                start_date = st.date_input(
                    "Da",
                    value=datetime.now() - timedelta(days=30),
                    max_value=datetime.now(),
                    key="start_date_input"
                )
            with col2:
                end_date = st.date_input(
                    "A",
                    value=datetime.now(),
                    max_value=datetime.now(),
                    key="end_date_input"
                )

        st.divider()

        # Category filter (NUOVO - FASE 3)
        st.subheader("🏷️ Categorie")
        categories = st.multiselect(
            "Seleziona categorie",
            options=["GEOPOLITICS", "DEFENSE", "ECONOMY", "CYBER", "ENERGY"],
            default=[],
            help="Filtra per categoria di intelligence",
            key="categories_multiselect"
        )

        st.divider()

        # Geographic filter (NUOVO - FASE 3)
        st.subheader("🌍 Aree Geografiche")
        gpe_options = [
            "China", "Taiwan", "Russia", "Ukraine", "USA", "Iran",
            "Israel", "Gaza", "North Korea", "Japan", "India",
            "Europe", "Middle East", "Asia", "Africa"
        ]
        gpe_filter = st.multiselect(
            "Seleziona regioni/paesi",
            options=gpe_options,
            default=[],
            help="Filtra per menzioni geografiche (GPE entities)",
            key="gpe_filter_multiselect"
        )

        st.divider()

        # Database stats
        st.subheader("📊 Database Stats")
        try:
            stats = db.get_statistics()
            st.metric("Articoli", stats.get('total_articles', 0))
            st.metric("Chunks RAG", stats.get('total_chunks', 0))
            st.metric("Recenti (7gg)", stats.get('recent_articles', 0))
        except Exception as e:
            st.warning(f"Stats non disponibili: {e}")

        st.divider()

        # Clear chat button
        if st.button("🗑️ Pulisci Chat", width="stretch"):
            st.session_state.chat_history = []
            st.rerun()

        st.divider()

        # Navigation
        if st.button("🏠 Torna alla War Room", width="stretch"):
            st.switch_page("Home.py")

        if st.button("📝 Vai al Daily Briefing", width="stretch"):
            st.switch_page("pages/1_Daily_Briefing.py")

    # Main chat area
    st.divider()

    # Render existing messages
    render_chat_messages()

    # Chat input
    if prompt := st.chat_input("Chiedi qualcosa al database di intelligence..."):
        # Add user message
        st.session_state.chat_history.append({
            "role": "user",
            "content": prompt
        })

        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)

        # Process and get response (with FASE 3 filters)
        response = process_query(
            prompt,
            oracle,
            st.session_state.search_mode,
            search_type=search_type,
            start_date=start_date,
            end_date=end_date,
            categories=categories,
            gpe_filter=gpe_filter
        )

        # Add assistant message
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": response.get('answer', 'Nessuna risposta'),
            "sources": response.get('sources', [])
        })

        # Display assistant response
        with st.chat_message("assistant"):
            st.markdown(response.get('answer', 'Nessuna risposta'))
            render_sources_expander(response.get('sources', []))

        # Show metadata in expander
        metadata = response.get('metadata', {})
        if metadata:
            with st.expander("📈 Metadata query"):
                col1, col2, col3 = st.columns(3)
                col1.metric("Chunks trovati", metadata.get('chunks_found', 0))
                col2.metric("Report trovati", metadata.get('reports_found', 0))
                col3.metric("Contesto (chars)", metadata.get('context_length', 0))

    # Welcome message if no history
    if not st.session_state.chat_history:
        st.info("""
        👋 **Benvenuto su The Oracle**

        Questo e il sistema RAG per interrogare il database di intelligence.
        Puoi fare domande come:

        - "Quali sono le principali minacce cyber degli ultimi 7 giorni?"
        - "Cosa dice l'ultimo report sulle tensioni geopolitiche?"
        - "Quali aziende del settore difesa sono state menzionate recentemente?"

        **Suggerimento:** Usa la modalita di ricerca nella sidebar per affinare i risultati:
        - **Ibrida**: Per risposte complete da tutte le fonti
        - **Investigativa**: Per dettagli specifici dagli articoli
        - **Strategica**: Per analisi macro dai report

        *Inizia a scrivere nel campo sottostante!*
        """)


if __name__ == "__main__":
    main()
