# Oracle 2.0 — Agentic Engine Architecture

`src/llm/oracle_orchestrator.py` — singleton via `get_oracle_orchestrator_singleton()`

Oracle 2.0 is a native Gemini function-calling agentic engine with iterative tool use, session memory, time-weighted RAG, and Chain-of-Verification (CoVe) synthesis.

## Agentic Loop — Sequence Diagram

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant PROXY as Next.js Proxy
    participant API as oracle.py (FastAPI)
    participant OO as OracleOrchestrator
    participant QR as QueryRouter
    participant MEM as ConversationMemory
    participant Gemini as Gemini 2.5-flash
    participant Tools as Tool Registry (9 tools)
    participant DB as PostgreSQL + pgvector

    U->>PROXY: POST /api/proxy/oracle/chat
    Note over PROXY: Adds X-API-Key header
    PROXY->>API: POST /api/v1/oracle/chat
    Note over API: Rate limit: 3/min per IP
    API->>OO: process_query(query, session_id, filters, gemini_key)

    OO->>QR: classify intent (Gemini 2.5-flash)
    QR-->>OO: intent + QueryPlan + tool sequence

    OO->>MEM: load conversation history (TTL 2h)

    loop Agentic loop — max 4 iterations
        OO->>Gemini: [system_prompt + history + tool_definitions]
        Gemini-->>OO: tool_call(name, params) OR final_text

        alt tool_call
            OO->>Tools: execute(tool_name, params)
            Tools->>DB: semantic search / SQL / graph / spatial query
            DB-->>Tools: results + metadata
            Tools-->>OO: ToolResult (content + sources + citations)
            OO->>MEM: append tool_call + result
        else final_text
            Note over OO: Exit loop
        end
    end

    OO->>Gemini: synthesize(results, CoVe instructions)
    Note over OO,Gemini: CoVe: when structured data (macro_forecasts, country_profiles)\nand RAG disagree on quantitative KPIs:\nannotate both: "Dato strutturato [fonte]: X | Contesto narrativo [fonte]: Y"
    Gemini-->>OO: final_answer + inline citations [N]

    OO->>MEM: save exchange
    OO->>DB: log_oracle_query() → oracle_query_log
    OO-->>API: {answer, sources, query_plan, execution_steps, intent}
    API-->>PROXY: OracleResponse JSON
    PROXY-->>U: rendered response + citations
```

---

## Intent Classification → Tool Routing

`src/llm/query_router.py` classifies into 6 intent types, each with a different tool sequence and RAG decay constant:

```mermaid
flowchart TD
    Q[User Query] --> QR[QueryRouter\nGemini 2.5-flash intent classification]

    QR --> FACT[FACTUAL\nk=0.03 decay]
    QR --> ANAL[ANALYTICAL\nk=0.015 decay]
    QR --> NAR[NARRATIVE\nk=0.02 decay]
    QR --> MKT[MARKET\nk=0.04 decay]
    QR --> COMP[COMPARATIVE\nk=0.025 decay]
    QR --> OVW[OVERVIEW\nk=0.005 decay\nvector-only search]

    FACT --> T1[RAGTool + ReferenceTool]
    ANAL --> T2[RAGTool + SQLTool + AggregationTool]
    NAR --> T3[GraphTool + RAGTool]
    MKT --> T4[MarketTool + TickerThemesTool + SQLTool]
    COMP --> T5[ReportCompareTool + RAGTool]
    OVW --> T6[RAGTool]
```

---

## Tool Registry — 9 Tools

```mermaid
flowchart LR
    subgraph Tools["Tool Registry (src/llm/tools/)"]
        RAG["**RAGTool**
        Hybrid vector+FTS search
        Over-fetch 3× top-K (anti-bias)
        Time-weighted: score × exp(-k × days)
        Min floor: 0.15
        Authority reranking (intelligence_sources.authority_score)
        Historical query: reference_date = end_date"]

        SQL["**SQLTool**
        5-layer safety:
        1. ALLOWED_TABLES whitelist
        2. Forbidden keywords
        3. Max 3 JOINs
        4. LIMIT enforcement
        5. EXPLAIN cost ≤ 10000
        Timeout: 5s statement_timeout
        Uses v_sanctions_public (not raw table)"]

        AGG["**AggregationTool**
        Macro-level trend summarization
        Time-series aggregation
        Delta computation"]

        GRAPH["**GraphTool**
        Narrative graph queries
        Storyline neighbors + communities
        Ego network traversal"]

        MKT["**MarketTool**
        Ticker OHLCV prices
        Fundamentals (PE, sector)
        Market sentiment from articles"]

        TICK["**TickerThemesTool**
        Theme clustering per ticker
        Sentiment from recent articles
        Whitelisted tickers only"]

        RPT["**ReportCompareTool**
        Delta analysis between 2 reports
        Gemini 2.5-flash synthesis
        4 sections: new, resolved, shifted, persistent"]

        REF["**ReferenceTool**
        Cite articles/reports with URL + metadata
        Safe queries (pre-approved SQL patterns)
        Uses v_sanctions_public"]

        SPAT["**SpatialTool**
        PostGIS queries on entities
        Conflict events (UCDP GED)
        ST_DWithin, ST_Intersects"]
    end
```

---

## Time-Weighted Decay (RAGTool)

```mermaid
flowchart LR
    RAW["Raw relevance score\n(cosine similarity)"] --> DECAY

    DECAY["score_final = score × exp(-k × days_ago)
    
    k values by intent:
    FACTUAL    k=0.03
    ANALYTICAL k=0.015
    NARRATIVE  k=0.02
    MARKET     k=0.04
    COMPARATIVE k=0.025
    OVERVIEW   k=0.005"]

    DECAY --> FLOOR["min floor: 0.15\n(ensures old docs still surfaced)"]
    FLOOR --> RANK[Final ranked results]
```

---

## Singleton & Session Management

```mermaid
flowchart TD
    FIRST[First request] --> CHECK{Singleton\nexists?}
    CHECK -- No --> INIT["Initialize OracleOrchestrator
    Load 400MB embedding model
    Connect to DB pool
    Register 9 tools
    Thread-safe double-checked locking"]
    INIT --> SING[(Singleton instance)]
    CHECK -- Yes --> SING

    SING --> SESSION{Session\nexists?}
    SESSION -- No --> NEW[Create ConversationMemory\nTTL: 2 hours]
    SESSION -- Yes --> LOAD[Load existing history]

    NEW & LOAD --> PROC[Process query]
    PROC --> CLEANUP[Background daemon:\nexpire sessions > TTL]
```
