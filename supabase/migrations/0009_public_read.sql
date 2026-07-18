-- 0009_public_read.sql — open stories + sources for anonymous readers
--
-- The news feed is now public (no login required). Stories and sources
-- already have "auth read" policies from 0004; this adds anon access.

create policy "anon read stories"
  on stories for select
  to anon
  using (true);

create policy "anon read sources"
  on sources for select
  to anon
  using (true);
