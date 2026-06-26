-- ===== shared pool: authenticated users can READ; backend (secret key) writes =====
alter table stories enable row level security;
create policy "auth read stories" on stories for select to authenticated using (true);
alter table sources enable row level security;
create policy "auth read sources" on sources for select to authenticated using (true);

-- ===== per-user tables: owner-only, auto-stamp user_id on insert =====
-- pages
alter table pages enable row level security;
alter table pages alter column user_id set default auth.uid();
create policy "own pages" on pages for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
-- subpages
alter table subpages enable row level security;
alter table subpages alter column user_id set default auth.uid();
create policy "own subpages" on subpages for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
-- threads
alter table threads enable row level security;
alter table threads alter column user_id set default auth.uid();
create policy "own threads" on threads for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
-- notes
alter table notes enable row level security;
alter table notes alter column user_id set default auth.uid();
create policy "own notes" on notes for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
-- saved
alter table saved enable row level security;
alter table saved alter column user_id set default auth.uid();
create policy "own saved" on saved for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ===== story_threads join: ownership flows through the thread =====
alter table story_threads enable row level security;
create policy "own story_threads" on story_threads for all
  using (exists (select 1 from threads t where t.id = story_threads.thread_id and t.user_id = auth.uid()))
  with check (exists (select 1 from threads t where t.id = story_threads.thread_id and t.user_id = auth.uid()));

-- ===== per-user preferences/theme =====
create table if not exists preferences (
  user_id uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  theme text default 'soft-default',
  dark_mode boolean default false,
  font text default 'jakarta',
  density text default 'comfortable',
  updated_at timestamptz default now()
);
alter table preferences enable row level security;
create policy "own prefs" on preferences for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
