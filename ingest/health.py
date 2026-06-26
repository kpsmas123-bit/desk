"""
health.py — write a per-thread health signal after each sync.

Plain English: for every thread that is live ('tracking'), run its saved query
against the stories we already have and record two things on the thread row:

  - match_count   : how many stories match its query right now
  - last_match_at : the publish date of the most recent matching story

This is what lets the website show "No matches yet — may need broader sources"
for a quiet thread, instead of a quiet thread looking the same as a broken one.

It reuses the SAME matching the website uses for threads: the search_stories()
SQL function (migration 0003), so the count here equals what the user sees.

Run order: this runs LAST in the sync (after enrich + after make_query has built
any brand-new queries), so a thread created this cycle still gets a health count
from the stories already in the shared pool.

Two ways to use it:

    python3 ingest/health.py                 # health-check every tracking thread

    from health import update_thread_health
    update_thread_health()

Writing to the threads table is per-user (RLS), so this uses the SECRET Supabase
key — which bypasses RLS by design and so can health-check every user's threads.
"""

from config import get
from supabase import create_client


def _client():
    """Connect with the SECRET key (we both read across all users and write)."""
    supabase_url = get("SUPABASE_URL")
    secret_key = get("SUPABASE_SECRET_KEY")
    if not supabase_url or not secret_key:
        raise Exception("SUPABASE_URL / SUPABASE_SECRET_KEY are not set")
    return create_client(supabase_url, secret_key)


def _latest_published(stories: list):
    """Return the most recent non-null published_at among matched stories, or None.

    published_at comes back as an ISO 8601 string (e.g. '2026-06-25T12:00:00+00:00').
    For stories from the same source format, lexicographic max == chronological max,
    which is plenty precise for a health signal.
    """
    latest = None
    for story in stories:
        published = story.get("published_at")
        if published and (latest is None or published > latest):
            latest = published
    return latest


def update_thread_health(supabase=None) -> int:
    """Recompute match_count + last_match_at for every 'tracking' thread.

    Returns how many threads were updated. Prints one line per thread.
    """
    supabase = supabase or _client()

    try:
        threads = (
            supabase.table("threads")
            .select("id, title, derived_query, status")
            .eq("status", "tracking")
            .execute()
            .data
        ) or []
    except Exception as error:
        print(f"  (couldn't read threads for health check: {error})")
        return 0

    updated = 0
    for thread in threads:
        query = (thread.get("derived_query") or "").strip()
        title = thread.get("title") or "(untitled thread)"
        if not query:
            # 'tracking' but no query — nothing to measure; leave it alone.
            continue

        try:
            stories = supabase.rpc("search_stories", {"q": query}).execute().data or []
        except Exception as error:
            print(f"  (search failed for '{title}' — skipping: {error})")
            continue

        match_count = len(stories)
        last_match_at = _latest_published(stories)

        try:
            (
                supabase.table("threads")
                .update({"match_count": match_count, "last_match_at": last_match_at})
                .eq("id", thread["id"])
                .execute()
            )
        except Exception as error:
            print(f"  (couldn't save health for '{title}': {error})")
            continue

        updated += 1
        when = last_match_at or "never"
        print(f"  health: {title} — {match_count} match(es), latest {when}")

    if updated == 0:
        print("  (no tracking threads with queries to health-check yet)")

    return updated


if __name__ == "__main__":
    update_thread_health()
