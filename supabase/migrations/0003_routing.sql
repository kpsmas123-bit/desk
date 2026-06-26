-- ===========================================================================
-- 0003_routing.sql — Phase 6 routing
--
-- Two small, SAFE changes (only adds things; nothing existing is altered):
--   1. Gives subpages the same two fields threads already have, so a subpage
--      can be created from the UI as "pending" and get a search query later:
--        - description : the one-line note you type when creating it
--        - status      : 'pending' (query not built yet) | 'tracking' (built)
--   2. Adds a tiny search helper function, search_stories(q), that finds
--      stories matching a free-text query and sorts them by how well they
--      match THAT query (Postgres ts_rank). This is what makes the same story
--      rank high under a tight thread and low under a broad page ("decay").
--
-- Run this once in the Supabase SQL Editor, the same way you ran 0001 and 0002.
-- ===========================================================================

-- 1. Bring subpages in line with threads -----------------------------------
alter table subpages add column if not exists description text;
alter table subpages add column if not exists status text default 'pending';

-- 2. The ranked full-text search helper -------------------------------------
-- Given a query string, return matching stories ordered by relevance to that
-- exact query. Used live by the website (via /rest/v1/rpc/search_stories) and
-- mirrored by ingest/route.py for testing.
create or replace function search_stories(q text)
returns setof stories
language sql
stable
as $$
  select s.*
  from stories s
  where coalesce(q, '') <> ''
    and s.fts @@ websearch_to_tsquery('english', q)
    and coalesce(s.relevance_score, 0) > 0          -- hide noise, like the feed does
  order by
    ts_rank(s.fts, websearch_to_tsquery('english', q)) desc,
    s.published_at desc nulls last
  limit 100;
$$;

-- Let the website's public (publishable) key call the function.
grant execute on function search_stories(text) to anon, authenticated;
