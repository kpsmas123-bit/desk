-- ===== page / subpage tree (user-editable navigation) =====
create table pages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  title text not null,
  slug text unique not null,
  position int default 0
);
create table subpages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  page_id uuid references pages(id) on delete cascade,
  title text not null,
  slug text not null,
  query text,
  position int default 0
);
-- ===== sources (the feeds; filled in a later phase) =====
create table sources (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  name text not null,
  feed_url text,
  type text not null,            -- 'rss' | 'openstates' | 'gdelt'
  page_slug text,
  active boolean default true
);
-- ===== stories (ingested news items) =====
create table stories (
  id uuid primary key default gen_random_uuid(),
  url text unique not null,
  title text not null,
  source_name text,
  published_at timestamptz,
  fetched_at timestamptz default now(),
  excerpt text,
  summary text,
  context_bullets jsonb,
  media_type text,               -- 'article' | 'podcast' | 'video' | 'pdf'
  alt_links jsonb,
  reliability_score int,
  region text,                   -- 'ca' | 'local' | 'national' | 'intl'
  is_academic boolean default false,
  lean text
);
-- full-text search index for the search bar
alter table stories add column fts tsvector
  generated always as (to_tsvector('english',
    coalesce(title,'') || ' ' || coalesce(summary,'') || ' ' || coalesce(excerpt,''))) stored;
create index stories_fts_idx on stories using gin(fts);
-- ===== threads + tagging (a story can belong to many threads) =====
create table threads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  subpage_id uuid references subpages(id) on delete set null,
  title text not null,
  description text,
  derived_query text,
  status text default 'pending', -- 'pending' | 'tracking'
  position int default 0,
  created_at timestamptz default now()
);
create table story_threads (
  story_id uuid references stories(id) on delete cascade,
  thread_id uuid references threads(id) on delete cascade,
  added_at timestamptz default now(),
  primary key (story_id, thread_id)
);
-- ===== notes + saved =====
create table notes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  thread_id uuid references threads(id) on delete cascade,
  story_id uuid references stories(id) on delete set null,
  body text,
  updated_at timestamptz default now()
);
create table saved (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  story_id uuid references stories(id) on delete cascade,
  saved_at timestamptz default now()
);
-- ===== seed the starting navigation =====
insert into pages (title, slug, position) values
  ('CA Leg + Labor','ca-leg-labor',0),
  ('Local Bay','local-bay',1),
  ('National','national',2),
  ('International','international',3);
insert into subpages (page_id, title, slug, position)
  select id,'State legislature & bills','state-legislature-bills',0 from pages where slug='ca-leg-labor'
  union all select id,'Labor campaigns','labor-campaigns',1 from pages where slug='ca-leg-labor'
  union all select id,'State prop campaigns','state-prop-camp',2 from pages where slug='ca-leg-labor'
  union all select id,'Midterm / Congress','midterm-congress',3 from pages where slug='ca-leg-labor';
