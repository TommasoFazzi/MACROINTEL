## ADDED Requirements

### Requirement: macro_indicators stores previous value alongside current value
The `macro_indicators` table SHALL include a nullable `previous_value NUMERIC(20,6)` column
containing the most recent prior value for the same `indicator_key` recorded before the current row's `date`.

SQL schema addition:
```sql
ALTER TABLE macro_indicators
    ADD COLUMN IF NOT EXISTS previous_value NUMERIC(20, 6);
```

#### Scenario: New indicator row populated with previous value
- **WHEN** `_save_macro_indicator(date, key, value, unit, category)` is called for a key that has at least one prior row in `macro_indicators`
- **THEN** the inserted row's `previous_value` equals the `value` from the most recent row for that `indicator_key` where `date < current date`

#### Scenario: First row for an indicator has NULL previous value
- **WHEN** `_save_macro_indicator` is called for an `indicator_key` with no prior rows
- **THEN** `previous_value` is `NULL` and no error is raised

#### Scenario: Re-insert on same date (ON CONFLICT) updates previous_value
- **WHEN** `_save_macro_indicator` is called for a `(date, indicator_key)` that already exists
- **THEN** `previous_value` is recalculated from the current DB state and the row is updated atomically

#### Scenario: Backfill populates existing rows
- **WHEN** migration 036 is applied to a database with existing `macro_indicators` rows
- **THEN** every row whose `indicator_key` has at least one prior row gets `previous_value` set to the value of the closest preceding row; rows with no prior history remain `NULL`
