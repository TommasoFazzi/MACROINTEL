# HITL Feedback Loop - Come il Sistema Migliora

## Panoramica

Il sistema **Human-in-the-Loop (HITL)** raccoglie feedback dalle revisioni umane e lo usa per migliorare continuamente la qualità dei report generati dall'LLM.

```
┌─────────────────────────────────────────────────────────────────┐
│                     FEEDBACK LOOP CYCLE                          │
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌─────────────────┐ │
│  │     LLM      │────▶│    Human     │────▶│    Database     │ │
│  │  Generates   │     │   Reviews    │     │  (Feedback)     │ │
│  │    Draft     │     │  & Edits     │     │                 │ │
│  └──────────────┘     └──────────────┘     └─────────────────┘ │
│         ▲                                            │          │
│         │                                            │          │
│         │              ┌──────────────┐              │          │
│         └──────────────│   Analyze    │◀─────────────┘          │
│                        │   Feedback   │                         │
│                        │  & Improve   │                         │
│                        └──────────────┘                         │
│                                                                  │
│  Cycle repeats daily → Prompt migliora → Qualità aumenta       │
└─────────────────────────────────────────────────────────────────┘
```

## Tipi di Feedback Raccolti

### 1. Corrections (Correzioni)

**Cosa traccia:**
- Testo originale LLM
- Testo corretto dall'umano
- Sezione modificata
- Note sull'errore

**Esempio:**
```
Section: "Executive Summary"
Original: "Russia announced military exercises near Ukraine border"
Corrected: "Russia announced military exercises near Ukraine border, involving 50,000 troops and nuclear-capable missiles"
Comment: "LLM omesso dettagli critici su scala e capacità nucleare"
```

**Come viene usato:**
- Identifica dove l'LLM tende a omettere dettagli
- Migliora prompt per enfatizzare completezza
- Crea esempi di "good output" per few-shot learning

### 2. Additions (Aggiunte)

**Cosa traccia:**
- Sezione dove è stata fatta aggiunta
- Testo aggiunto
- Perché era necessario

**Esempio:**
```
Section: "Cybersecurity"
Added: "IMPORTANTE: Il breach ha esposto 2.3M record di utenti inclusi dati biometrici"
Comment: "LLM non ha menzionato gravità del breach (dati biometrici)"
```

**Come viene usato:**
- Identifica topic/dettagli che LLM trascura
- Aggiorna prompt con esempi di cosa includere
- Migliora RAG queries per recuperare context più rilevante

### 3. Removals (Rimozioni)

**Cosa traccia:**
- Testo rimosso
- Perché era irrilevante

**Esempio:**
```
Section: "Technology Trends"
Removed: "Paragrafo completo su nuove funzionalità iOS 18"
Comment: "Troppo consumer-focused, non rilevante per intelligence geopolitica"
```

**Come viene usato:**
- Identifica quando LLM include materiale off-topic
- Raffina focus areas nei prompt
- Migliora filtri nelle RAG queries

### 4. Ratings (Valutazioni)

**Cosa traccia:**
- Rating 1-5 per ogni report
- Commenti generali sulla qualità

**Esempio:**
```
Rating: 4/5
Comment: "Buona sintesi ma executive summary troppo lungo (3 paragrafi invece di 2)"
```

**Come viene usato:**
- Trend di qualità nel tempo
- Correlazione tra rating e modifiche
- Identificazione pattern di successo

## Database Schema per Feedback

```sql
-- Ogni modifica viene salvata con dettaglio completo
INSERT INTO report_feedback (
    report_id,
    section_name,        -- "Executive Summary", "Cybersecurity", etc.
    feedback_type,       -- 'correction', 'addition', 'removal', 'rating'
    original_text,       -- Testo LLM (prima della modifica)
    corrected_text,      -- Testo corretto (dopo modifica)
    comment,             -- Note umane
    rating,              -- 1-5 stelle
    created_at           -- Timestamp
) VALUES (...);
```

## Analisi del Feedback

### Query 1: Sezioni con Più Correzioni

Identifica quali sezioni del report richiedono più intervento umano:

```sql
SELECT 
    section_name,
    COUNT(*) as correction_count,
    AVG(CASE WHEN rating IS NOT NULL THEN rating END) as avg_rating
FROM report_feedback
WHERE feedback_type = 'correction'
GROUP BY section_name
ORDER BY correction_count DESC;
```

**Output esempio:**
```
section_name          | correction_count | avg_rating
----------------------|------------------|------------
Executive Summary     |       15         |    3.8
Geopolitical Events   |       12         |    4.1
Cybersecurity         |        8         |    4.3
Economic Trends       |        5         |    4.5
```

**Azione:**
- Focus sui prompt per Executive Summary (più correzioni)
- Aggiungi esempi specifici per quella sezione

### Query 2: Pattern di Errori Comuni

Trova errori ricorrenti:

```sql
SELECT 
    original_text,
    corrected_text,
    COUNT(*) as occurrences,
    STRING_AGG(comment, ' | ') as all_comments
FROM report_feedback
WHERE feedback_type = 'correction'
  AND original_text IS NOT NULL
GROUP BY original_text, corrected_text
HAVING COUNT(*) > 2
ORDER BY occurrences DESC;
```

**Identifica pattern tipo:**
- LLM omette sempre numeri/statistiche
- LLM usa linguaggio troppo generico
- LLM non cita fonti specifiche

### Query 3: Trend Qualità nel Tempo

Verifica se prompt improvements funzionano:

```sql
SELECT 
    DATE(r.report_date) as date,
    AVG(f.rating) as avg_rating,
    COUNT(CASE WHEN f.feedback_type = 'correction' THEN 1 END) as corrections,
    COUNT(r.id) as total_reports
FROM reports r
LEFT JOIN report_feedback f ON r.id = f.report_id
GROUP BY DATE(r.report_date)
ORDER BY date DESC
LIMIT 30;
```

**Aspettativa:**
- Rating medio aumenta nel tempo
- Numero correzioni diminuisce
- Indica miglioramento continuo

### Query 4: Best Performing Reports

Trova report con rating alto e poche modifiche (esempi di "gold standard"):

```sql
SELECT 
    r.id,
    r.report_date,
    AVG(f.rating) as avg_rating,
    COUNT(CASE WHEN f.feedback_type = 'correction' THEN 1 END) as corrections
FROM reports r
LEFT JOIN report_feedback f ON r.id = f.report_id
WHERE r.status = 'approved'
GROUP BY r.id, r.report_date
HAVING AVG(f.rating) >= 4.5 AND COUNT(CASE WHEN f.feedback_type = 'correction' THEN 1 END) <= 2
ORDER BY avg_rating DESC;
```

**Uso:**
- Analizza questi report per identificare pattern di successo
- Usa come few-shot examples nei prompt
- Crea template basati su struttura vincente

## Miglioramento Prompt - Workflow

### Step 1: Raccolta Feedback (Settimanale)

Dopo 7 giorni di uso quotidiano:

```sql
-- Analisi feedback ultima settimana
SELECT 
    section_name,
    feedback_type,
    COUNT(*) as count
FROM report_feedback
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY section_name, feedback_type
ORDER BY section_name, count DESC;
```

### Step 2: Identificazione Pattern

**Esempio risultato:**
```
Executive Summary | correction  | 12
Executive Summary | addition    |  5
Cybersecurity     | correction  |  8
Geopolitical      | removal     |  3
```

**Analisi:**
- Executive Summary richiede troppe correzioni
- Cybersecurity ha buona struttura ma dettagli mancanti
- Geopolitical include materiale irrilevante

### Step 3: Aggiornamento Prompt

**Prima (generico):**
```python
prompt = """
Generate an intelligence report with:
1. Executive Summary
2. Key Developments
3. Analysis
"""
```

**Dopo (con feedback integration):**
```python
prompt = """
Generate an intelligence report with:

1. Executive Summary (MAXIMUM 2 paragraphs)
   - Focus ONLY on most critical 2-3 developments
   - Include specific numbers, dates, and key entities
   - Cite articles with [Article N] notation
   
2. Key Developments by Category
   CYBERSECURITY:
   - Always include: threat actor, vulnerability CVE, affected systems count
   - Specify impact scope (users affected, data exposed)
   
   GEOPOLITICAL:
   - Only include events with direct intelligence relevance
   - Exclude consumer tech and entertainment news
   - Focus on: military, diplomatic, sanctions, policy changes
   
3. Analysis
   - Connect with historical patterns from RAG context
   - Highlight emerging trends vs one-off events
"""
```

### Step 4: A/B Testing

**Metodo:**
1. Genera report con prompt originale → Salva come "baseline"
2. Genera report con prompt migliorato → Salva come "improved"
3. Umano revisiona entrambi blind
4. Confronta rating e numero correzioni

**Query per confronto:**
```sql
SELECT 
    r.metadata->>'prompt_version' as version,
    AVG(f.rating) as avg_rating,
    COUNT(CASE WHEN f.feedback_type = 'correction' THEN 1 END) as avg_corrections
FROM reports r
LEFT JOIN report_feedback f ON r.id = f.report_id
WHERE r.report_date > NOW() - INTERVAL '14 days'
GROUP BY r.metadata->>'prompt_version';
```

### Step 5: Iterazione Continua

**Weekly improvement cycle:**

```
Lunedì: Analizza feedback settimana precedente
Martedì: Aggiorna prompt con insights
Mercoledì-Domenica: Genera report con nuovo prompt
Lunedì successivo: Confronta metriche (rating, correzioni)
```

## Few-Shot Learning con Report Approvati

### Export Training Examples

Recupera i migliori report approvati come esempi:

```sql
-- Top 5 report con rating perfetto
SELECT 
    r.id,
    r.draft_content,
    r.final_content,
    AVG(f.rating) as rating
FROM reports r
JOIN report_feedback f ON r.id = f.report_id
WHERE r.status = 'approved'
GROUP BY r.id, r.draft_content, r.final_content
HAVING AVG(f.rating) = 5.0
ORDER BY r.report_date DESC
LIMIT 5;
```

### Integrazione nel Prompt

```python
# In report_generator.py

# Carica esempi di alta qualità
few_shot_examples = self.db.get_best_reports(min_rating=4.5, limit=3)

# Aggiungi al prompt
examples_text = "\n\n".join([
    f"EXAMPLE {i+1} (Rating: {ex['avg_rating']:.1f}/5.0):\n{ex['final_content']}"
    for i, ex in enumerate(few_shot_examples)
])

prompt = f"""
{base_instructions}

EXAMPLES OF HIGH-QUALITY REPORTS:
{examples_text}

NOW GENERATE TODAY'S REPORT:
{context}
"""
```

## Metriche di Successo

### KPI da Tracciare

1. **Quality Score Trend**
   - Target: Media rating > 4.0
   - Trend: +0.2 punti ogni 2 settimane

2. **Correction Rate**
   - Target: < 3 correzioni per report
   - Trend: -20% ogni mese

3. **Time to Approval**
   - Target: < 5 minuti review time
   - Trend: Diminuisce con miglior qualità

4. **Approval Rate**
   - Target: 90% report approvati senza major rewrites
   - Trend: Aumenta con prompt improvements

### Dashboard Metriche (Next.js / FastAPI)

L'interfaccia principale per visualizzare l'andamento della qualità dei report è ora integrata nella dashboard Next.js. I dati vengono recuperati dall'endpoint `/api/v1/dashboard/stats`.

```typescript
// Esempio aggregazione fornita dalla FastAPI
export interface QualityStats {
    reports_reviewed: number;
    average_rating: number | null;
    pending_review: number;
}
```

La pipeline FastAPI calcola automaticamente:
- **reports_reviewed**: Conteggio dei report in stato 'reviewed' o 'approved'.
- **average_rating**: Media matematica del campo `rating` della tabella `report_feedback`.
- **pending_review**: Conteggio dei report ancora in 'draft'.

## Advanced: Fine-Tuning LLM (Futuro)

Se raccogli abbastanza feedback (100+ report), puoi:

### 1. Crea Training Dataset

```python
# Export training pairs
training_data = []

for report_id in approved_report_ids:
    report = db.get_report(report_id)
    
    # Input: prompt + context
    input_data = {
        'prompt': report['metadata']['prompt_used'],
        'recent_articles': report['sources']['recent_articles'],
        'rag_context': report['sources']['historical_context']
    }
    
    # Output: final_content (human-approved)
    output_data = report['final_content']
    
    training_data.append({
        'input': input_data,
        'output': output_data
    })

# Save for fine-tuning
with open('training_data.jsonl', 'w') as f:
    for item in training_data:
        f.write(json.dumps(item) + '\n')
```

### 2. Fine-Tune Model

```bash
# Con OpenAI GPT
openai api fine_tunes.create \
  -t training_data.jsonl \
  -m gpt-3.5-turbo \
  --suffix "intelligence-reports"

# Con Google Gemini (quando disponibile)
# gcloud ai custom-jobs create ...
```

### 3. Usa Fine-Tuned Model

```python
# In report_generator.py
generator = ReportGenerator(
    model_name="ft:gpt-3.5-turbo:intelligence-reports-2025"
)
```

**Benefici:**
- Meno prompt engineering necessario
- Output più consistente
- Meno correzioni umane richieste

## Esempi Reali di Miglioramento

### Esempio 1: Executive Summary Prolisso

**Feedback raccolto (Settimana 1):**
- 8/10 report: Executive Summary > 3 paragrafi
- Commenti: "Troppo lungo", "Vai al punto", "TL;DR necessario"

**Azione:**
- Aggiornato prompt: "MAXIMUM 2 paragraphs, focus only on top 2-3 critical developments"
- Aggiunto esempio di summary conciso

**Risultato (Settimana 2):**
- 9/10 report: Executive Summary = 2 paragrafi
- Commenti: "Perfetto", "Conciso e chiaro"
- Rating medio: 3.2 → 4.5

### Esempio 2: Missing Source Citations

**Feedback raccolto:**
- 7/10 report: Mancano citazioni esplicite agli articoli
- Difficile verificare claims

**Azione:**
- Aggiornato prompt: "Always cite sources with [Article N] notation"
- Modificato formato: Include article number next to each fact

**Risultato:**
- 10/10 report: Citazioni presenti
- Rating medio sezione "Sources": 2.8 → 4.2

### Esempio 3: RAG Context Non Utilizzato

**Feedback raccolto:**
- 6/10 report: "Trend Analysis" section generica
- Context storico non collegato a news attuali

**Azione:**
- Aggiornato prompt: "Explicitly connect each current development with historical patterns from RAG context"
- Aggiunto template: "This mirrors/contrasts with [Historical Event from Context]"

**Risultato:**
- "Trend Analysis" migliora da rating 3.1 → 4.4
- Feedback: "Ottimo uso del context storico"

## Tools per Analisi Feedback

### Script 1: Feedback Summary

```python
# scripts/analyze_feedback.py

from src.storage.database import DatabaseManager
import pandas as pd

db = DatabaseManager()

# Get all feedback
query = """
SELECT 
    r.report_date,
    f.section_name,
    f.feedback_type,
    f.rating,
    LENGTH(f.original_text) as original_len,
    LENGTH(f.corrected_text) as corrected_len
FROM report_feedback f
JOIN reports r ON f.report_id = r.id
WHERE f.created_at > NOW() - INTERVAL '30 days'
"""

df = pd.read_sql(query, db.get_connection())

# Summary stats
print("FEEDBACK SUMMARY (Last 30 days)")
print(f"Total feedback entries: {len(df)}")
print(f"\nBy Type:")
print(df['feedback_type'].value_counts())
print(f"\nBy Section:")
print(df['section_name'].value_counts())
print(f"\nAverage Rating: {df['rating'].mean():.2f}/5.0")

# Correction patterns
corrections = df[df['feedback_type'] == 'correction']
print(f"\nCorrections: {len(corrections)}")
print(f"Avg text change: {(corrections['corrected_len'] - corrections['original_len']).mean():.0f} chars")
```

### Script 2: Prompt Optimizer

```python
# scripts/optimize_prompt.py

from collections import Counter
import re

# Analyze all correction comments
comments = db.execute("""
    SELECT comment 
    FROM report_feedback 
    WHERE feedback_type = 'correction' AND comment IS NOT NULL
""")

# Extract keywords from comments
keywords = []
for comment in comments:
    # Common patterns: "omesso X", "mancava Y", "troppo Z"
    if "omesso" in comment.lower():
        keywords.append("missing_details")
    if "troppo" in comment.lower():
        keywords.append("too_verbose")
    if "irrilevante" in comment.lower():
        keywords.append("off_topic")

# Most common issues
issue_counts = Counter(keywords)

print("TOP ISSUES:")
for issue, count in issue_counts.most_common(5):
    print(f"  {issue}: {count} occurrences")
    
# Auto-generate prompt improvements
if issue_counts['missing_details'] > 5:
    print("\nSUGGESTION: Add to prompt: 'Include specific numbers, dates, and named entities'")

if issue_counts['too_verbose'] > 5:
    print("\nSUGGESTION: Add to prompt: 'Be concise. Maximum 2 paragraphs per section.'")
```

## Workflow di Miglioramento Continuo

### Ciclo Mensile

**Week 1: Baseline**
- Genera 7 report con prompt corrente
- Raccogli feedback dettagliato
- Calcola metriche baseline

**Week 2: Analysis**
- Analizza pattern di feedback
- Identifica top 3 issues
- Draft improved prompts

**Week 3: Testing**
- Genera 7 report con nuovo prompt
- Confronta con baseline
- A/B test se possibile

**Week 4: Rollout**
- Se metriche migliorate → usa nuovo prompt
- Documenta changes in prompt history
- Monitora regression

### Prompt History Tracking

Salva ogni versione del prompt:

```python
# In report_generator.py

PROMPT_VERSION = "v2.1.0"  # Semantic versioning

# When generating report
report['metadata']['prompt_version'] = PROMPT_VERSION
report['metadata']['prompt_hash'] = hashlib.sha256(prompt.encode()).hexdigest()[:8]
```

Query per confrontare versioni:

```sql
SELECT 
    metadata->>'prompt_version' as version,
    COUNT(*) as reports,
    AVG((SELECT AVG(rating) FROM report_feedback WHERE report_id = r.id)) as avg_rating
FROM reports r
GROUP BY metadata->>'prompt_version'
ORDER BY version DESC;
```

## Best Practices

### Per Revisori

1. **Sii consistente** - Applica stessi standard a tutti i report
2. **Spiega le modifiche** - Commenti dettagliati aiutano l'analisi
3. **Valuta oggettivamente** - Rating basato su criteri chiari:
   - 5 = Nessuna modifica necessaria
   - 4 = 1-2 correzioni minori
   - 3 = Diverse correzioni ma struttura OK
   - 2 = Molte correzioni, problemi strutturali
   - 1 = Completa riscrittura necessaria

### Per Sviluppatori

1. **Review feedback settimanalmente** - Non lasciare accumulare
2. **Piccoli cambiamenti iterativi** - Non riscrivere prompt completamente
3. **A/B test sempre** - Confronta nuovo vs vecchio prompt
4. **Documenta cambiam enti** - Mantieni changelog dei prompt
5. **Monitora metriche** - Assicurati che miglioramenti siano misurabili

## Automazione Feedback Loop (Fase 6+)

### Auto-Prompt Tuning

```python
# Future: Automated prompt improvement

def auto_improve_prompt(current_prompt: str, feedback_history: List[Dict]) -> str:
    """
    Use LLM to improve prompt based on feedback.
    """
    
    # Analyze feedback patterns
    common_issues = analyze_feedback_patterns(feedback_history)
    
    # Generate improvement suggestions with LLM
    improvement_prompt = f"""
    Current prompt: {current_prompt}
    
    Common issues from human feedback:
    {format_issues(common_issues)}
    
    Suggest 3 specific improvements to the prompt to address these issues.
    """
    
    suggestions = llm.generate(improvement_prompt)
    
    # Human reviews suggestions
    # Apply selected improvements
    return improved_prompt
```

### Continuous Learning Pipeline

```bash
# Cron job settimanale
0 0 * * 1 /path/to/scripts/weekly_feedback_analysis.sh

# weekly_feedback_analysis.sh:
# 1. Export feedback data
# 2. Run analysis scripts
# 3. Generate improvement suggestions
# 4. Email to developer for review
# 5. Track metrics dashboard
```

## ROI del HITL

### Time Investment
- **Setup iniziale**: 2 ore (implementazione dashboard)
- **Review giornaliera**: 5-10 minuti per report
- **Analisi feedback**: 1 ora/settimana
- **Prompt improvements**: 2 ore/mese

**Total**: ~10 ore/mese

### Benefits
- **Qualità report**: +40% (rating 3.0 → 4.2)
- **Time to approval**: -50% (10min → 5min)
- **Usable without editing**: 30% → 70%
- **Confidence in automation**: 📈

### Break-Even

Dopo ~2 mesi:
- Prompt sufficientemente ottimizzato
- 80%+ report usabili as-is
- Review time < 3 minuti
- ROI positivo: tempo risparmiato > tempo investito

## License

MIT License
