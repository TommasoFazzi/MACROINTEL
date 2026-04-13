"""
macro_regime_persistence.py
===========================
Persistence layer per macro_regime_history.

Responsabilità:
  - Salvare il JSON di output della Macro Analysis (Layer 2) ogni giorno
  - Esporre query strutturate usate da:
      · Narrative Engine  (regime_context_for_date, streak, sc_signal_streak)
      · Oracle 2.0        (current_regime, regime_history_summary)
      · Strategic Layer   (regime_trend, scenario_context)

Non fa parsing dei dati grezzi — lavora sul JSON già prodotto
da macro_analysis_prompt (LLM call #1).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures per le query
# ---------------------------------------------------------------------------

@dataclass
class RegimeStreak:
    """Quanti giorni consecutivi siamo nello stesso regime."""
    regime: str
    days: int
    since: date
    confidence_avg: float


@dataclass
class SCSignalStreak:
    """Quanti giorni consecutivi un settore SC è nei segnali attivi."""
    sector: str
    days: int
    since: date


@dataclass
class RegimeContext:
    """
    Contesto regime per una data specifica.
    Usato dal Narrative Engine per calibrare i boost.
    """
    date: date
    risk_regime: str
    regime_confidence: float
    active_convergences: list[str]
    active_sc_sectors: list[str]
    macro_narrative: str
    streak: Optional[RegimeStreak]


# ---------------------------------------------------------------------------
# Persistence class
# ---------------------------------------------------------------------------

class MacroRegimePersistence:
    """
    Singleton — condivide la connessione DB con il resto del sistema.
    Inizializzato con DatabaseManager al primo accesso via get_macro_regime_persistence_singleton().
    """

    def __init__(self, db):
        """
        Parameters
        ----------
        db : DatabaseManager
            Stesso db passato agli altri servizi (openbb_service, etc.)
        """
        self.db = db

    # -------------------------------------------------------------------------
    # WRITE
    # -------------------------------------------------------------------------

    def save(
        self,
        analysis_date: date,
        analysis_json: dict,
        freshness_gap_days: int = 0,
    ) -> bool:
        """
        Persiste il JSON di output della Macro Analysis.
        Usa INSERT ... ON CONFLICT DO UPDATE per idempotenza
        (se il job gira due volte nella stessa giornata, aggiorna).

        Parameters
        ----------
        analysis_date : date
            Data di riferimento dell'analisi (non necessariamente oggi).
        analysis_json : dict
            Output completo di macro_analysis_prompt (LLM call #1).
        freshness_gap_days : int
            0 = dati del giorno, 1 = weekend (dati venerdì), etc.
        """
        try:
            regime_data = analysis_json.get("risk_regime", {})
            risk_regime = regime_data.get("label", "neutral")
            regime_confidence = regime_data.get("confidence", 0.0)

            active_convergences = [
                c["id"]
                for c in analysis_json.get("active_convergences", [])
            ]

            active_sc_sectors = [
                s["sector"]
                for s in analysis_json.get("supply_chain_signals", [])
                if s.get("confidence") in ("medium", "high")
            ]

            macro_narrative = analysis_json.get("macro_narrative", "")

            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO macro_regime_history (
                            date,
                            risk_regime,
                            regime_confidence,
                            active_convergence_ids,
                            active_sc_sectors,
                            macro_narrative,
                            analysis_json,
                            data_freshness_gap_days
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (date) DO UPDATE SET
                            risk_regime             = EXCLUDED.risk_regime,
                            regime_confidence       = EXCLUDED.regime_confidence,
                            active_convergence_ids  = EXCLUDED.active_convergence_ids,
                            active_sc_sectors       = EXCLUDED.active_sc_sectors,
                            macro_narrative         = EXCLUDED.macro_narrative,
                            analysis_json           = EXCLUDED.analysis_json,
                            data_freshness_gap_days = EXCLUDED.data_freshness_gap_days,
                            created_at              = NOW()
                    """, (
                        analysis_date,
                        risk_regime,
                        regime_confidence,
                        active_convergences,
                        active_sc_sectors,
                        macro_narrative,
                        json.dumps(analysis_json),
                        freshness_gap_days,
                    ))
                conn.commit()

            logger.info(
                f"macro_regime_history saved: {analysis_date} "
                f"regime={risk_regime} ({regime_confidence:.2f}) "
                f"convergences={active_convergences} "
                f"sc_sectors={active_sc_sectors}"
            )
            return True

        except Exception as e:
            logger.error(f"macro_regime_history save failed: {e}")
            return False

    # -------------------------------------------------------------------------
    # READ — query usate dal Narrative Engine
    # -------------------------------------------------------------------------

    def get_regime_context(self, target_date: date) -> Optional[RegimeContext]:
        """
        Contesto regime per una data specifica.
        Usato dal Narrative Engine per calibrare momentum boost.
        """
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            date,
                            risk_regime,
                            regime_confidence,
                            active_convergence_ids,
                            active_sc_sectors,
                            macro_narrative
                        FROM macro_regime_history
                        WHERE date <= %s
                        ORDER BY date DESC
                        LIMIT 1
                    """, (target_date,))
                    row = cur.fetchone()

            if not row:
                return None

            streak = self.get_regime_streak(target_date)

            return RegimeContext(
                date=row[0],
                risk_regime=row[1],
                regime_confidence=float(row[2] or 0),
                active_convergences=row[3] or [],
                active_sc_sectors=row[4] or [],
                macro_narrative=row[5] or "",
                streak=streak,
            )

        except Exception as e:
            logger.error(f"get_regime_context failed: {e}")
            return None

    def get_regime_streak(self, as_of: date) -> Optional[RegimeStreak]:
        """
        Quanti giorni consecutivi siamo nello stesso regime fino a `as_of`.
        Usato da: Narrative Engine (boost intensity), Oracle, Strategic Layer.

        Logica: parte dalla riga più recente e risale finché il regime
        non cambia. Tolera gap di weekend (data_freshness_gap_days > 0).
        """
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT date, risk_regime, regime_confidence
                        FROM macro_regime_history
                        WHERE date <= %s
                        ORDER BY date DESC
                        LIMIT 60
                    """, (as_of,))
                    rows = cur.fetchall()

            if not rows:
                return None

            current_regime = rows[0][1]
            streak_start = rows[0][0]
            confidences = [float(rows[0][2] or 0)]

            for row in rows[1:]:
                if row[1] == current_regime:
                    streak_start = row[0]
                    confidences.append(float(row[2] or 0))
                else:
                    break

            streak_days = (rows[0][0] - streak_start).days + 1

            return RegimeStreak(
                regime=current_regime,
                days=streak_days,
                since=streak_start,
                confidence_avg=round(sum(confidences) / len(confidences), 3),
            )

        except Exception as e:
            logger.error(f"get_regime_streak failed: {e}")
            return None

    def get_sc_signal_streaks(
        self,
        as_of: date,
        min_days: int = 2,
    ) -> list[SCSignalStreak]:
        """
        Settori SC che appaiono nei segnali attivi per almeno `min_days`
        giorni consecutivi fino a `as_of`.

        Un segnale SC persistente per 3+ giorni ha alta probabilità
        di riflettere un cambiamento strutturale, non rumore giornaliero.
        Usato dal Narrative Engine per boost storylines SC-related.
        """
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT date, active_sc_sectors
                        FROM macro_regime_history
                        WHERE date <= %s
                        ORDER BY date DESC
                        LIMIT 30
                    """, (as_of,))
                    rows = cur.fetchall()

            if not rows:
                return []

            # Per ogni settore, conta i giorni consecutivi partendo da oggi
            sector_streaks: dict[str, dict] = {}

            for row in rows:
                row_date, sectors = row[0], row[1] or []
                for sector in sectors:
                    if sector not in sector_streaks:
                        # Prima apparizione (= giorno più recente)
                        sector_streaks[sector] = {
                            "days": 1,
                            "since": row_date,
                            "active": True,
                        }
                    elif sector_streaks[sector]["active"]:
                        sector_streaks[sector]["days"] += 1
                        sector_streaks[sector]["since"] = row_date

                # Marca come non-attivi i settori assenti in questo giorno
                for sector in list(sector_streaks.keys()):
                    if sector not in sectors:
                        sector_streaks[sector]["active"] = False

            return [
                SCSignalStreak(
                    sector=sector,
                    days=data["days"],
                    since=data["since"],
                )
                for sector, data in sector_streaks.items()
                if data["days"] >= min_days
            ]

        except Exception as e:
            logger.error(f"get_sc_signal_streaks failed: {e}")
            return []

    # -------------------------------------------------------------------------
    # READ — query usate da Oracle 2.0 e Strategic Layer
    # -------------------------------------------------------------------------

    def get_regime_history_summary(
        self,
        days: int = 30,
        as_of: Optional[date] = None,
    ) -> list[dict]:
        """
        Ultimi N giorni di storia regime in formato compatto.
        Usato da Oracle per rispondere a domande tipo
        "com'era il regime macro nelle ultime settimane?"
        e dallo Strategic Layer per costruire il contesto scenari.

        Returns list of dicts (JSON-serializable).
        """
        as_of = as_of or date.today()
        since = as_of - timedelta(days=days)

        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            date,
                            risk_regime,
                            regime_confidence,
                            active_convergence_ids,
                            active_sc_sectors,
                            macro_narrative
                        FROM macro_regime_history
                        WHERE date BETWEEN %s AND %s
                        ORDER BY date ASC
                    """, (since, as_of))
                    rows = cur.fetchall()

            return [
                {
                    "date": str(row[0]),
                    "regime": row[1],
                    "confidence": float(row[2] or 0),
                    "convergences": row[3] or [],
                    "sc_sectors": row[4] or [],
                    "narrative": row[5] or "",
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"get_regime_history_summary failed: {e}")
            return []

    def get_scenario_context(self, as_of: Optional[date] = None) -> dict:
        """
        Contesto strutturato per lo Strategic Layer (Scenario Analysis).
        Aggrega: regime attuale + streak + SC signals persistenti +
        transizioni di regime negli ultimi 60 giorni.

        Questo è l'input che permette all'LLM di ragionare su scenari
        con base storica, non solo sul giorno corrente.
        """
        as_of = as_of or date.today()

        current = self.get_regime_context(as_of)
        streak = self.get_regime_streak(as_of)
        sc_streaks = self.get_sc_signal_streaks(as_of, min_days=2)
        history = self.get_regime_history_summary(days=60, as_of=as_of)

        # Conta le transizioni di regime negli ultimi 60 giorni
        regime_transitions = []
        prev_regime = None
        for entry in history:
            if prev_regime and entry["regime"] != prev_regime:
                regime_transitions.append({
                    "date": entry["date"],
                    "from": prev_regime,
                    "to": entry["regime"],
                })
            prev_regime = entry["regime"]

        return {
            "as_of": str(as_of),
            "current_regime": {
                "label": current.risk_regime if current else "unknown",
                "confidence": current.regime_confidence if current else 0,
                "narrative": current.macro_narrative if current else "",
                "active_convergences": current.active_convergences if current else [],
            },
            "streak": {
                "regime": streak.regime if streak else None,
                "days": streak.days if streak else 0,
                "since": str(streak.since) if streak else None,
                "confidence_avg": streak.confidence_avg if streak else 0,
            },
            "persistent_sc_signals": [
                {
                    "sector": s.sector,
                    "days": s.days,
                    "since": str(s.since),
                }
                for s in sc_streaks
            ],
            "regime_transitions_60d": regime_transitions,
            "regime_distribution_60d": _count_regime_days(history),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_regime_days(history: list[dict]) -> dict[str, int]:
    """Conta i giorni per regime negli ultimi N giorni."""
    counts: dict[str, int] = {}
    for entry in history:
        r = entry["regime"]
        counts[r] = counts.get(r, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Narrative Engine integration helper
# ---------------------------------------------------------------------------

def compute_regime_momentum_boost(
    storyline_topics: list[str],
    regime_context: RegimeContext,
    sc_streaks: list[SCSignalStreak],
) -> float:
    """
    Calcola il moltiplicatore di momentum per una storyline
    basandosi sul regime macro attivo e sui SC signals persistenti.

    Usato nel Narrative Engine durante il calcolo del momentum decay.
    Restituisce un float moltiplicatore (1.0 = nessun boost, 1.3 = boost max).

    Parameters
    ----------
    storyline_topics : list[str]
        Topic / label della storyline (usati per match con SC sectors).
    regime_context : RegimeContext
        Output di get_regime_context() per la data corrente.
    sc_streaks : list[SCSignalStreak]
        Output di get_sc_signal_streaks() per la data corrente.

    Logica di boost:
      - Base boost da streak regime: più lungo lo streak, più il regime
        è consolidato e le storylines correlate sono rilevanti.
      - SC boost: se la storyline menziona un settore con SC signal
        persistente (>= 3 giorni), boost aggiuntivo.
      - Cap a 1.3 per evitare dominanza eccessiva di pochi temi.
    """
    boost = 1.0

    # Boost da streak regime (lineare, cap a _REGIME_STREAK_BOOST_MAX)
    if regime_context.streak:
        streak_days = regime_context.streak.days
        regime_boost = min(_REGIME_STREAK_BOOST_MAX, streak_days * _REGIME_STREAK_BOOST_PER_DAY)
        boost += regime_boost

    # Boost da SC signal persistente (lineare, cap a _SC_SIGNAL_BOOST_MAX)
    topics_lower = [t.lower() for t in storyline_topics]
    for sc in sc_streaks:
        sector_lower = sc.sector.lower().replace("_", " ")
        if any(sector_lower in topic or topic in sector_lower
               for topic in topics_lower):
            sc_boost = min(_SC_SIGNAL_BOOST_MAX, sc.days * _SC_SIGNAL_BOOST_PER_DAY)
            boost += sc_boost
            break  # un solo SC boost per storyline

    return min(_BOOST_CAP, round(boost, 3))


# ---------------------------------------------------------------------------
# Regime momentum boost constants
# ---------------------------------------------------------------------------
# Max boost from regime streak: +0.15 after 7 consecutive days in same regime.
# Rationale: 7 days = two full trading weeks — at that point the regime is
# structurally confirmed, not just noise. Linear ramp (0.02/day) to avoid
# step-function artifacts on day 6→7 boundaries.
_REGIME_STREAK_BOOST_PER_DAY: float = 0.02
_REGIME_STREAK_BOOST_MAX: float = 0.15   # 0.15 / 0.02 = 7.5 days to max

# Max boost from persistent SC signal: +0.15 after 5 consecutive days.
# Rationale: SC signals are noisy — a sector only earns a storyline boost
# after 5 days of persistence (structural shift, not daily jitter).
# Cap matches regime boost so neither dominates over the other.
_SC_SIGNAL_BOOST_PER_DAY: float = 0.03
_SC_SIGNAL_BOOST_MAX: float = 0.15      # 0.15 / 0.03 = 5 days to max

# Combined boost cap: 1.0 (base) + 0.15 (regime) + 0.15 (SC) = 1.3 max.
# Rationale: prevents a single strongly-aligned storyline from monopolising
# the narrative feed; other storylines remain discoverable.
_BOOST_CAP: float = 1.3

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class _SingletonHolder:
    """
    Encapsulates singleton state to avoid mutable module-level globals.

    The outer `if instance is None` check (double-checked locking) is safe in
    CPython because attribute reads on a class object are GIL-protected. The
    inner check inside `_lock` guarantees correctness on non-CPython runtimes
    and when the GIL is released by C extensions.
    """
    instance: Optional['MacroRegimePersistence'] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> 'MacroRegimePersistence':
        if cls.instance is None:
            with cls._lock:
                if cls.instance is None:
                    from ..storage.database import DatabaseManager
                    cls.instance = MacroRegimePersistence(DatabaseManager())
        return cls.instance


def get_macro_regime_persistence_singleton() -> 'MacroRegimePersistence':
    """
    Thread-safe singleton getter for MacroRegimePersistence.
    Instantiated with a new DatabaseManager on first access (lazy init).
    """
    return _SingletonHolder.get()
