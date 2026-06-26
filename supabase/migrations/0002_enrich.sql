alter table stories add column if not exists relevance_score int;
alter table stories add column if not exists topics jsonb;
alter table stories add column if not exists enriched_at timestamptz;
create index if not exists stories_relevance_idx on stories(relevance_score);
