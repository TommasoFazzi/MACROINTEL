# Gemini Model Policy

## Rule

This project uses **two Gemini models** with strictly separated responsibilities. Never swap or cross-assign them.

| Layer | Model | Files |
|-------|-------|-------|
| **NLP layer** — speed-critical, structured tasks | `gemini-2.0-flash` | `src/nlp/narrative_processor.py`, `src/nlp/relevance_filter.py` |
| **LLM/Report layer** — deep reasoning, long context | `gemini-2.5-flash` | `src/llm/report_generator.py`, `src/llm/oracle_orchestrator.py`, `src/llm/oracle_engine.py`, `src/llm/query_router.py` |

## Mandatory Timeout

**Always** specify `request_options={"timeout": N}` on every `generate_content()` call.

```python
# NLP layer
response = model.generate_content(prompt, request_options={"timeout": 30})

# LLM/Report layer
response = model.generate_content(prompt, request_options={"timeout": 60})
```

**Why:** With `transport='rest'`, omitting timeout causes a ~900-second hang on network issues. This has caused full pipeline stalls in production.

## Initialization Pattern

```python
import google.generativeai as genai

# NLP layer
genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-2.0-flash")

# LLM layer
model = genai.GenerativeModel("gemini-2.5-flash")
```

## f-string Prompt Pattern

LLM prompts in `report_generator.py` use Python f-strings. To avoid literal `{variable}` text in the output:

```python
# CORRECT: pre-compute before f-string
narrative_section = build_narrative_section(storylines)
macro_context = get_macro_context_text()
prompt = f"""
{narrative_section}
{macro_context}
"""

# WRONG: double-braces make it literal text
prompt = f"""
{{narrative_section}}  # outputs the literal string "{narrative_section}"
"""
```

## Adding a New Gemini Call

Checklist before merging:
- [ ] Correct model for the layer (2.0-flash for NLP, 2.5-flash for LLM)
- [ ] `request_options={"timeout": N}` specified
- [ ] Variables pre-computed before f-string (if applicable)
- [ ] Structured output uses Pydantic v2 model (not raw JSON parsing)
