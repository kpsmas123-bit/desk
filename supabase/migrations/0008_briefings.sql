-- ==========================================================================
-- 0008_briefings.sql — thread-driven briefings
--
-- Threads ARE the briefing topics. When a user creates a thread (from a
-- story or from the library), the daily pipeline picks it up once it
-- reaches 'tracking' status, runs Tavily search + Haiku synthesis, and
-- writes a row to the briefings table. Each refresh tries to fill the
-- previous run's declared gaps.
--
-- Changes to existing tables:
--   threads  — add archetype, search_hints columns
--            — add public read policy (library is open)
--
-- New table:
--   briefings — one row per thread per generation (history preserved)
-- ==========================================================================

-- ---- extend threads for briefing support ----

alter table threads add column if not exists archetype text;
alter table threads add column if not exists search_hints jsonb not null default '[]'::jsonb;

-- public read: anyone can browse the thread library (titles, descriptions).
-- writing still requires auth (the existing "own threads" policy handles that).
create policy "public read threads"
  on threads for select
  to anon, authenticated
  using (true);

-- ---- briefings: generated output (history preserved) ----

create table if not exists briefings (
  id                    uuid primary key default gen_random_uuid(),
  thread_id             uuid references threads(id) on delete set null,
  slug                  text not null,
  topic                 text not null,
  archetype             text,
  status_line           text,
  as_of                 date,
  background            text,
  what_changed          jsonb,
  next_decision_point   jsonb,
  official_record       jsonb,
  coverage              jsonb,
  analysis              jsonb,
  gaps                  jsonb,
  sources_total         int not null default 0,
  primary_source_count  int not null default 0,
  generated_at          timestamptz not null default now(),
  grade                 jsonb
);

alter table briefings enable row level security;

create policy "public read briefings"
  on briefings for select
  to anon, authenticated
  using (true);

-- fast lookup: latest briefing per thread
create index if not exists briefings_slug_gen
  on briefings (slug, generated_at desc);

-- prevent duplicate generations in the same run
create unique index if not exists briefings_slug_gen_unique
  on briefings (slug, generated_at);
