#!/usr/bin/env python3
"""
seed_sources.py — Popola la tabella intelligence_sources con la matrice
delle fonti autorevoli e fa backfill di articles.source_id / articles.domain.

Idempotente: usa INSERT ... ON CONFLICT (name) DO UPDATE.

Uso:
    python scripts/seed_sources.py
"""

import os
import sys
import logging
from pathlib import Path

# Aggiungi la root al path
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ============================================================
# MATRICE INTELLIGENCE SOURCES
# Campi: (name, domain, source_type, authority_score, llm_context, feed_names, has_rss)
# ============================================================
SOURCES = [
    # ── CYBER ───────────────────────────────────────────────
    (
        "Darktrace",
        "cyber", "Cyber Intel", 4.5,
        "Reportistica tecnica su minacce cyber avanzate e IA offensiva. Focus su attacchi state-sponsored e anomalie di rete.",
        [], False,
    ),
    (
        "Krebs on Security",
        "cyber", "Investigativo", 4.5,
        "Giornalismo investigativo di altissimo livello sul cybercrime. Fonte primaria per breach, fraud e attori criminali.",
        ["Krebs on Security"], True,
    ),
    (
        "CyberScoop",
        "cyber", "Specializzato", 4.0,
        "Ottimo per policy governative USA sul cyber e sicurezza nazionale. Copertura CISA, NSA, Cyber Command.",
        ["CyberScoop"], True,
    ),
    (
        "BleepingComputer",
        "cyber", "Tecnico", 4.0,
        "Cronaca tattica e tempestiva su vulnerabilità, ransomware e CVE quotidiani. Utile per IoC e patch tracking.",
        ["BleepingComputer"], True,
    ),

    # ── TECH ────────────────────────────────────────────────
    (
        "SpaceNews",
        "tech", "Specializzato", 4.5,
        "Riferimento assoluto per economia spaziale, costellazioni satellitari e militarizzazione dell'orbita.",
        ["SpaceNews"], True,
    ),
    (
        "Ars Technica",
        "tech", "Specializzato", 4.0,
        "Analisi profonda delle regolamentazioni tecnologiche, AI policy e tech-war USA-Cina.",
        ["Policy - Ars Technica"], True,
    ),

    # ── SUPPLY CHAIN ────────────────────────────────────────
    (
        "Semiconductor Engineering",
        "supply_chain", "Tecnico", 4.5,
        "Dati fondamentali e tecnici sulla guerra dei chip, nodi di processo, supply chain semiconduttori e ASML/TSMC.",
        ["Semiconductor Engineering"], True,
    ),
    (
        "Supply Chain Dive",
        "supply_chain", "Specializzato", 4.0,
        "Monitoraggio dei blocchi logistici marittimi, terrestri, dazi e shock di fornitura globali.",
        ["Supply Chain Dive"], True,
    ),

    # ── ECONOMICS & FINANCE ─────────────────────────────────
    (
        "ECB",
        "economics", "Ufficiale/Gov", 5.0,
        "Verità di base assoluta. Comunicati ufficiali della Banca Centrale Europea: tassi, QE, proiezioni macro.",
        ["ECB - European Central Bank Press Releases", "ECB - Publications"], True,
    ),
    (
        "The Economist",
        "economics", "Macro", 4.0,
        "Analisi economica globale di altissimo rigore con prospettiva occidentale/liberale. Copertura macro e geopolitica.",
        [], False,
    ),
    (
        "OilPrice",
        "economics", "Specializzato", 4.0,
        "Riferimento per i mercati dell'energia, OPEC+, geopolitica del petrolio e del gas naturale.",
        ["OilPrice"], True,
    ),
    (
        "Il Sole 24 ORE",
        "economics", "Nazionale", 3.5,
        "Prospettiva fondamentale sui mercati italiani ed europei. Utile per contesto domestico e dati BTP/spread.",
        ["Il Sole 24 ORE - Finanza"], True,
    ),
    (
        "Euronews Business",
        "economics", "Regionale", 3.5,
        "Cronaca macro-economica generale del blocco UE. Utile per sintesi rapide su policy europea.",
        ["Business | Euronews RSS"], True,
    ),
    (
        "Kommersant",
        "economics", "Nazionale", 3.0,
        "Quotidiano economico russo. Fondamentale per leggere l'economia russa e le sanzioni al netto della censura di stato.",
        [], False,
    ),

    # ── DEFENSE ─────────────────────────────────────────────
    (
        "Janes Defence Weekly",
        "defense", "Tecnico", 4.5,
        "Gold standard globale per specifiche militari, contratti di procurement e capacità degli armamenti.",
        ["Janes Defence Weekly"], True,
    ),
    (
        "War on the Rocks",
        "defense", "Think Tank/Media", 4.5,
        "Scritto da militari e strateghi. Altissimo valore per tattica, dottrina operativa e teoria della guerra.",
        ["War on the Rocks"], True,
    ),
    (
        "Breaking Defense",
        "defense", "Specializzato", 4.0,
        "Ottima copertura tempestiva sulle tecnologie militari emergenti e sulle decisioni del Pentagono.",
        ["Breaking Defense"], True,
    ),
    (
        "Defense News",
        "defense", "Specializzato", 4.0,
        "Cronaca dell'industria bellica globale, fiere della difesa (DSEI, Eurosatory) e contratti internazionali.",
        [], False,
    ),
    (
        "The War Zone",
        "defense", "Investigativo", 4.0,
        "Giornalismo iper-dettagliato su aviazione militare, droni, tecnologie classificate e avvistamenti UAP.",
        ["The War Zone"], True,
    ),
    (
        "Defense One",
        "defense", "Specializzato", 4.0,
        "Analisi del business e della politica dietro la difesa USA: budget, contractor e strategia del Pentagono.",
        ["Defense One - All Content"], True,
    ),

    # ── GEOPOLITICS & INTERNATIONAL RELATIONS ───────────────
    (
        "POLITICO Europe",
        "geopolitics", "Media", 4.5,
        "Dinamiche politiche europee, istituzioni UE, diplomazia transatlantica e negoziati NATO-UE.",
        ["Foreign Affairs - POLITICO"], True,
    ),
    (
        "The Diplomat",
        "geopolitics", "Regionale", 4.0,
        "Copertura essenziale dell'Indo-Pacifico, ASEAN, competizione USA-Cina e sicurezza regionale asiatica.",
        ["ASEAN Beat - The Diplomat", "China Power - The Diplomat", "Security - The Diplomat"], True,
    ),
    (
        "Asia Times",
        "geopolitics", "Regionale", 4.0,
        "Analisi e cronaca geopolitica dal punto di vista asiatico. Utile per prospettive non occidentali su Taiwan/Corea.",
        ["Asia Times"], True,
    ),
    (
        "Americas Quarterly",
        "geopolitics", "Regionale", 3.5,
        "Analisi politica e sicurezza in America Latina: elezioni, crimine organizzato e relazioni USA-LatAm.",
        ["Americas Quarterly"], True,
    ),
    (
        "Diálogo Américas",
        "geopolitics", "Regionale", 3.5,
        "Copertura militare e sicurezza nell'emisfero occidentale. Focus su cooperazione difesa USA-LatAm.",
        ["Diálogo Américas"], True,
    ),
    (
        "Al Jazeera",
        "geopolitics", "Regionale", 3.5,
        "Prospettiva araba sulle crisi mediorientali e africane. Dati cinetici da incrociare con fonti occidentali.",
        ["Al Jazeera English"], True,
    ),
    (
        "Middle East Eye",
        "geopolitics", "Regionale", 3.5,
        "Approfondimento sulle dinamiche politiche interne di Iran, Turchia, Arabia Saudita e Paesi del Golfo.",
        ["Middle East Eye"], True,
    ),
    (
        "The Jerusalem Post",
        "geopolitics", "Nazionale", 3.5,
        "Prospettiva israeliana sulle dinamiche regionali. Fondamentale per comunicati ufficiali IDF e intelligence interna.",
        ["The Jerusalem Post"], True,
    ),
    (
        "Times of Israel",
        "geopolitics", "Nazionale", 3.5,
        "Copertura israeliana indipendente. Utile per notizie non filtrate dal governo su conflitti Gaza/Libano.",
        ["Times of Israel"], True,
    ),

    # ── INTELLIGENCE & GEOSTRATEGY ───────────────────────────
    (
        "RAND Corporation",
        "intelligence", "Think Tank", 5.0,
        "Simulazioni di guerra, policy USA e strategie di deterrenza di altissimo livello. Fonti primarie per dottrina NATO.",
        ["RAND Corporation - Research Reports", "RAND Corporation - Commentary"], True,
    ),
    (
        "CSIS",
        "intelligence", "Think Tank", 5.0,
        "Bipartisan USA. Mappe satellitari, analisi conflitti e geoeconomia. Fondamentale per Indo-Pacifico e Medio Oriente.",
        ["CSIS - Center for Strategic and International Studies"], True,
    ),
    (
        "RUSI",
        "intelligence", "Think Tank", 5.0,
        "Istituto britannico. Dottrina NATO, sicurezza europea, guerre ibride e analisi Russia-Ucraina di prima qualità.",
        [], False,
    ),
    (
        "Chatham House",
        "intelligence", "Think Tank", 5.0,
        "Istituto britannico. Analisi strategica su Europa, Africa, energia e sicurezza internazionale.",
        ["Chatham House"], True,
    ),
    (
        "Council on Foreign Relations",
        "intelligence", "Think Tank", 5.0,
        "Il cuore della riflessione strategica sulla politica estera americana. CFR Backgrounders e War Reports.",
        ["Council on Foreign Relations"], True,
    ),
    (
        "GAO / CRS",
        "intelligence", "Ufficiale/Gov", 5.0,
        "Enti USA neutrali (Government Accountability Office / Congressional Research Service). Audit, dati ufficiali e brief per decisori politici.",
        ["EveryCRSReport - Congressional Research Service"], True,
    ),
    (
        "ECFR",
        "intelligence", "Think Tank", 4.5,
        "European Council on Foreign Relations. Analisi strategica europea, policy UE e rapporti est-ovest.",
        ["European Council on Foreign Relations"], True,
    ),
    (
        "ISW",
        "intelligence", "Tattico", 4.5,
        "Institute for the Study of War. Mappatura quotidiana e chirurgica dell'avanzamento dei conflitti attivi (Ucraina, Medio Oriente).",
        [], False,
    ),
    (
        "Bellingcat",
        "intelligence", "OSINT", 4.5,
        "Investigazione open-source pura. Prove visive, geolocalizzazione forense e debunking di propaganda.",
        ["Bellingcat"], True,
    ),
    (
        "King's College War Studies",
        "intelligence", "Accademia", 4.5,
        "Analisi accademica di lungo periodo su strategia militare, conflitti e teoria delle relazioni internazionali.",
        [], False,
    ),
]


def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL non trovata nelle variabili d'ambiente")
    return psycopg2.connect(database_url)


def seed_sources(conn) -> int:
    """Inserisce/aggiorna tutte le fonti. Ritorna il numero di righe upsertate."""
    sql = """
        INSERT INTO intelligence_sources
            (name, domain, source_type, authority_score, llm_context, feed_names, has_rss)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            domain          = EXCLUDED.domain,
            source_type     = EXCLUDED.source_type,
            authority_score = EXCLUDED.authority_score,
            llm_context     = EXCLUDED.llm_context,
            feed_names      = EXCLUDED.feed_names,
            has_rss         = EXCLUDED.has_rss
    """
    rows = [
        (name, domain, source_type, score, ctx, feed_names, has_rss)
        for name, domain, source_type, score, ctx, feed_names, has_rss in SOURCES
    ]
    with conn.cursor() as cur:
        execute_batch(cur, sql, rows)
    conn.commit()
    return len(rows)


def backfill_articles(conn) -> dict:
    """
    Aggiorna articles.source_id e articles.domain per gli articoli esistenti
    matchando articles.source con intelligence_sources.feed_names.
    Idempotente: WHERE source_id IS NULL.
    """
    sql = """
        UPDATE articles a
        SET
            source_id = s.id,
            domain    = s.domain
        FROM intelligence_sources s
        WHERE a.source = ANY(s.feed_names)
          AND a.source_id IS NULL
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        updated = cur.rowcount

        # Distribuzione per dominio
        cur.execute("""
            SELECT domain, COUNT(*) AS cnt
            FROM articles
            WHERE source_id IS NOT NULL
            GROUP BY domain
            ORDER BY cnt DESC
        """)
        by_domain = {row[0]: row[1] for row in cur.fetchall()}

        # Articoli ancora senza source_id (fonti non in matrice)
        cur.execute("SELECT COUNT(*) FROM articles WHERE source_id IS NULL AND source IS NOT NULL AND source != ''")
        unmatched = cur.fetchone()[0]

    conn.commit()
    return {"updated": updated, "by_domain": by_domain, "unmatched": unmatched}


def main():
    logger.info("Connessione al database...")
    conn = get_db_connection()

    try:
        # 1. Seed fonti
        logger.info("Inserimento/aggiornamento fonti in intelligence_sources...")
        n = seed_sources(conn)
        logger.info(f"  ✓ {n} fonti inserite/aggiornate")

        # 2. Backfill articoli
        logger.info("Backfill articles.source_id e articles.domain...")
        result = backfill_articles(conn)
        logger.info(f"  ✓ {result['updated']} articoli aggiornati")
        logger.info(f"  ✓ Distribuzione per dominio:")
        for domain, cnt in result["by_domain"].items():
            logger.info(f"      {domain:20s}: {cnt:6,} articoli")
        if result["unmatched"] > 0:
            logger.info(
                f"  ⚠ {result['unmatched']:,} articoli senza source_id "
                "(fonti non presenti in matrice o feed rimossi — normale)"
            )

    finally:
        conn.close()

    logger.info("Done.")


if __name__ == "__main__":
    main()
