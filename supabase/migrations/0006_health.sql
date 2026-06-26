-- ===========================================================================
-- 0006_health.sql — per-thread health signal
--
-- Two columns so a quiet thread is visibly quiet (not silently mistaken for
-- "nothing happened"). Filled by the pipeline's health step after each sync:
--   last_match_at : the published date of the most recent story that matched
--                   this thread's query (null = nothing has ever matched)
--   match_count   : how many stories currently match this thread's query
-- ===========================================================================
alter table threads add column if not exists last_match_at timestamptz;
alter table threads add column if not exists match_count int default 0;
