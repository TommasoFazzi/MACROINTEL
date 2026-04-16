## Context

`macro_indicators` stores one row per `(date, indicator_key)`. Each row has `value` but no record of the prior day's value. The Strategic Intelligence Layer Phase 3 screening function `_get_macro_indicators_for_screening()` issues `SELECT indicator_key, value, previous_value, category FROM macro_indicators WHERE date = %s` — a query that has been failing with `column "previous_value" does not exist` on every pipeline run since the v2 code merged to main.

Without `previous_value`, `indicators_delta` is always empty → `phase3_data.get('indicators_delta')` is falsy → `macro_v2_result = None` → `use_strategic_v2 = False` → v1 fallback runs. The v2 path has never executed in production.

## Goals / Non-Goals

**Goals:**

- Add `previous_value` column to `macro_indicators` with backfill of existing rows
- Populate `previous_value` at insert time in `_save_macro_indicator()` with zero extra round-trips
- Unblock Phase 3 indicator delta calculation so `use_strategic_v2 = True` on the next pipeline run

**Non-Goals:**

- No changes to Phase 3 screening or convergence matching logic
- No changes to the v2/v1 branching conditions
- No changes to the reporting prompt or output format

## Decisions

### 1. Store `previous_value` on the row (denormalized) vs. compute it at read time

**Chosen: denormalize onto the row.**

Alternatives considered:
- *CTE/subquery at read time* — migration 009 already has this pattern (the `previous_values` CTE in the get-macro-context function). It adds a self-join on a table that grows daily. At read time during report generation this is acceptable, but `_get_macro_indicators_for_screening()` runs up to 4 times per pipeline call with no caching. Denormalizing avoids repeated self-joins.
- *Separate `macro_indicator_deltas` table* — over-engineering for a single numeric field. Extra table, extra join, more migration surface.

The denormalized column is consistent with how `market_tool.py` already queries `mi.previous_value` — that code was written expecting this column to exist.

### 2. Populate `previous_value` in `_save_macro_indicator()` via inline subquery

**Chosen: single INSERT with a scalar subquery for `previous_value`.**

```sql
INSERT INTO macro_indicators (date, indicator_key, value, unit, category, previous_value)
VALUES (%s, %s, %s, %s, %s,
    (SELECT value FROM macro_indicators
     WHERE indicator_key = %s AND date < %s
     ORDER BY date DESC LIMIT 1))
ON CONFLICT (date, indicator_key)
DO UPDATE SET
    value = EXCLUDED.value,
    previous_value = EXCLUDED.previous_value,
    updated_at = NOW()
```

Alternatives considered:
- *Fetch previous value in Python before INSERT* — two round-trips per indicator (36 indicators = 72 queries). Rejected for performance.
- *Trigger on INSERT* — triggers are invisible to developers reading Python; hard to debug. Rejected for maintainability.

### 3. Backfill strategy

One-shot correlated UPDATE in the migration:

```sql
UPDATE macro_indicators mi
SET previous_value = (
    SELECT value FROM macro_indicators prev
    WHERE prev.indicator_key = mi.indicator_key
      AND prev.date < mi.date
    ORDER BY prev.date DESC
    LIMIT 1
);
```

Safe on the existing dataset (historical rows, no concurrent writes during migration). Rows with no prior history remain `NULL` — the screening code already handles `None` gracefully (`if prev_val is None: continue`).

## Risks / Trade-offs

- **[Risk] Backfill scan on large table** → Mitigation: `macro_indicators` is indexed on `(indicator_key, date DESC)` (migration 009), so the correlated subquery uses an index scan per indicator key. Estimated rows: ~36 indicators × ~180 days ≈ 6500 rows. No performance concern.
- **[Risk] NULL `previous_value` for first row of each indicator** → Acceptable. The screening code skips indicators with no prior value (`if prev_val is None: continue`). Delta cannot be computed without two data points — this is correct behaviour.
- **[Risk] ON CONFLICT update overwrites `previous_value`** → Intentional. If a row is re-fetched on the same day (e.g. pipeline re-run), `previous_value` is recalculated from the DB state at that moment, which is the correct prior value.

## Migration Plan

1. Apply `036_add_previous_value_macro_indicators.sql` on production:
   ```bash
   docker compose -p app exec -T postgres psql \
     -U intelligence_user -d intelligence_ita \
     < migrations/036_add_previous_value_macro_indicators.sql
   ```
2. Deploy updated `openbb_service.py` (new INSERT statement)
3. Verify next pipeline run logs show `[v2] regime=` instead of v1 fallback

**Rollback**: `ALTER TABLE macro_indicators DROP COLUMN IF EXISTS previous_value;` — safe, no downstream code breaks (column going missing triggers the same warning+fallback already in place, reverting to v1).
