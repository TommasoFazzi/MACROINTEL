"""
LLM-as-Judge usando gpt-4o-mini (OpenAI).

Scelta del modello:
- Vendor diverso da Gemini → elimina self-serving bias
- 6x più economico di claude-haiku-4-5 (~$0.45/mese per 50 call/notte)
- Sufficiente per output JSON strutturati con Chain-of-Thought

Scala a 3 valori (1/3/5): più stabile di 1-5.
Gli LLM non distinguono affidabilmente 3 da 4 (Zheng et al. 2023).

Chain-of-Thought prima del verdetto: il campo `analisi_ragionata` forza
il ragionamento esplicito prima dei punteggi, riducendo la varianza ~30%.
"""

from __future__ import annotations
import json
import os
from typing import Optional

JUDGE_PROMPT = """\
Sei un valutatore esperto di sistemi di intelligence geopolitica. Il tuo compito \
è valutare la qualità di un output LLM rispetto al contesto e alla domanda che lo ha generato.

CONTESTO FORNITO ALL'LLM:
{context}

TASK/DOMANDA:
{query}

OUTPUT DA VALUTARE:
{output}

Prima di assegnare i punteggi, ragiona ad alta voce su ciascuna dimensione.
Poi assegna un punteggio usando SOLO i valori: 1 (Fail), 3 (Passable), 5 (Perfect).

FAITHFULNESS — L'output afferma solo cose verificabili nel contesto fornito?
- 5 (Perfect): Tutte le affermazioni sono direttamente tracciabili al contesto
- 3 (Passable): Qualche inferenza ragionevole ma non esplicitamente nel contesto
- 1 (Fail): Contiene affermazioni non supportate o inventate (hallucinations)

RELEVANCE — L'output risponde direttamente al task/domanda?
- 5 (Perfect): Risponde in modo diretto, completo e focalizzato
- 3 (Passable): Risponde parzialmente o con divagazioni significative
- 1 (Fail): Off-topic o non risponde alla domanda

COHERENCE — L'output è internamente coerente e ben strutturato?
- 5 (Perfect): Logico, chiaro, nessuna contraddizione interna
- 3 (Passable): Qualche incongruenza minore o struttura debole
- 1 (Fail): Contraddizioni evidenti o struttura incomprensibile

Rispondi SOLO in JSON con questo schema esatto:
{{
  "analisi_ragionata": "Ragionamento passo-passo su faithfulness, relevance e coherence prima del verdetto",
  "faithfulness": 1,
  "relevance": 1,
  "coherence": 1
}}
"""

VALID_SCORES = {1, 3, 5}
PASS_THRESHOLD = 3


def run_judge(
    context: str,
    query: str,
    output: str,
    n_runs: int = 1,
    api_key: Optional[str] = None,
) -> dict:
    """
    Esegue il judge n_runs volte e restituisce la mediana dei punteggi.

    n_runs=1 per Fase 1 (P5) — bilanciamento costo/stabilità.
    n_runs=3 per Fasi 2-3 dove la varianza è più alta (output lunghi).

    Args:
        context: Il contesto passato al modello valutato.
        query: La domanda/task originale.
        output: L'output del modello da valutare.
        n_runs: Numero di run del judge (mediana usata per aggregare).
        api_key: OpenAI API key. Se None, usa OPENAI_API_KEY dall'ambiente.

    Returns:
        dict con: faithfulness, relevance, coherence (mediana), reasoning, pass, n_runs.

    Raises:
        ImportError: se il pacchetto openai non è installato.
        ValueError: se l'output del judge non è JSON valido o manca campi obbligatori.
    """
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ImportError(
            "Pacchetto 'openai' non installato. "
            "Aggiungilo a requirements-dev.txt: openai>=1.30.0"
        ) from e

    client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
    prompt = JUDGE_PROMPT.format(context=context, query=query, output=output)

    raw_results = []
    for _ in range(n_runs):
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        raw = json.loads(resp.choices[0].message.content)
        raw_results.append(raw)

    def _median_score(key: str) -> int:
        vals = sorted(r[key] for r in raw_results)
        return vals[len(vals) // 2]

    def _validate_score(v: int, field: str) -> int:
        if v not in VALID_SCORES:
            # Snap al valore valido più vicino
            return min(VALID_SCORES, key=lambda x: abs(x - v))
        return v

    faithfulness = _validate_score(_median_score("faithfulness"), "faithfulness")
    relevance = _validate_score(_median_score("relevance"), "relevance")
    coherence = _validate_score(_median_score("coherence"), "coherence")

    return {
        "faithfulness": faithfulness,
        "relevance": relevance,
        "coherence": coherence,
        "reasoning": raw_results[0].get("analisi_ragionata", ""),
        "pass": all(s >= PASS_THRESHOLD for s in [faithfulness, relevance, coherence]),
        "n_runs": n_runs,
    }
