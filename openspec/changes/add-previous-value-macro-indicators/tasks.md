## 1. Migration

- [x] 1.1 Create `migrations/036_add_previous_value_macro_indicators.sql` — `ALTER TABLE macro_indicators ADD COLUMN IF NOT EXISTS previous_value NUMERIC(20,6)` plus correlated UPDATE backfill
- [x] 1.2 Update `migrations/context.md` to document migration 036

## 2. Insert Logic

- [x] 2.1 Update `_save_macro_indicator()` in `src/integrations/openbb_service.py` to include `previous_value` in the INSERT via inline scalar subquery (select most recent prior value for same `indicator_key`)
- [x] 2.2 Update `src/integrations/context.md` to reflect the updated `_save_macro_indicator` signature/behaviour

## 3. Production Deploy

- [ ] 3.1 Commit and push to `main`
- [ ] 3.2 Apply migration 036 on production via `docker compose exec -T postgres psql < migrations/036_add_previous_value_macro_indicators.sql`
- [ ] 3.3 Verify next pipeline run logs show `[v2] regime=` and `[STEP 3/4] Generating strategic intelligence report (v2)...`
