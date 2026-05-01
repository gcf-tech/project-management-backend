-- Fase 1, Tarea 4 — Weekly tracker performance indexes (MySQL).
--
-- Run on production MySQL. SQLAlchemy `Base.metadata.create_all()` already
-- creates these indexes for the test SQLite DB and any fresh schema, but the
-- production DB pre-dates this change.
--
-- This project does not yet use Alembic, so the migration is delivered as a
-- raw SQL file. Apply manually:
--   mysql -u <user> -p <database> < migrations/sql/2026-04-30_weekly_perf_indexes.sql
--
-- Verify with:
--   SHOW INDEX FROM weekly_blocks;
--   EXPLAIN SELECT * FROM weekly_blocks
--     WHERE user_id = 1 AND week_start = '2026-04-27' AND rrule_string IS NULL;
--   -- Expect: type=ref, key=idx_weekly_blocks_user_week.

-- ── (user_id, series_id) — speeds up get_virtual_projections,
--    delete_materializations_* and get_series_origin lookups.
ALTER TABLE weekly_blocks
    ADD INDEX idx_weekly_blocks_user_series (user_id, series_id);

-- The (user_id, week_start) index is already present on the production schema
-- as `idx_weekly_blocks_user_week` (declared in app/db/models.py since the
-- weekly tracker MVP). No-op here; retained as a comment for reviewers.
-- ALTER TABLE weekly_blocks ADD INDEX idx_weekly_blocks_user_week (user_id, week_start);
