-- ===========================================================================
-- 0005_slug_peruser.sql — make page slugs unique PER USER, not globally
--
-- Why this is needed (and was not obvious from the multi-user plan):
-- In 0001_init.sql the pages table declared `slug text unique not null`, which
-- is a GLOBAL unique constraint. Once Desk is multi-user, STEP 3 gives every
-- new user the SAME starter slugs ('ca-leg-labor', 'local-bay', 'national',
-- 'international'). With a global unique constraint, the FIRST user to sign up
-- claims those slugs and EVERY later user's starter-set insert fails with a
-- duplicate-key error — they'd land on an empty Desk. That would also make the
-- STEP 6c isolation test fail for the wrong reason.
--
-- Fix: drop the global unique constraint on pages.slug and replace it with a
-- composite unique index on (user_id, slug). Now each user owns their own copy
-- of every slug, the app's REGION[slug] lookup still works (the app only ever
-- sees the current user's pages via RLS), and starter seeding succeeds for all.
--
-- Run this in the Supabase SQL Editor AFTER 0004.
-- ===========================================================================

-- The unique constraint auto-created by `slug text unique` is named
-- "pages_slug_key" by Postgres convention. Drop it if present.
alter table pages drop constraint if exists pages_slug_key;

-- Per-user uniqueness: the same slug may exist once per user, never twice for
-- the same user.
create unique index if not exists pages_user_slug_uidx on pages (user_id, slug);
