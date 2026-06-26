"""
route.py — match stories to a query (the "sorting layers" logic), tested here so
the website can mirror it.

The idea behind Phase 6 routing:
  - Every subpage/thread has a search query (built once by make_query.py).
  - To fill a layer, we run that query against the stories we already have.
  - We do NOT save matches as rows. Matching happens live, every time, so it's
    always fresh and costs nothing (no AI, just a database search).

How matching works, in order:
  1. Postgres full-text search against the stories.fts column, ranked by
     ts_rank — i.e. how well each story matches THIS query. (Done via the
     search_stories() function added in migration 0003.) This per-query rank is
     what makes a story rank high under a tight thread and low under a broad
     page — the "nested decay" the project wants.
  2. A backup: a plain ILIKE keyword match on title + summary, to catch stories
     full-text search might miss. These are appended after the ranked matches.

The website does the very same thing live with the publishable key; this file is
the readable, testable reference (and handy from the command line):

    python3 ingest/route.py "Oakland city council election"
    python3 ingest/route.py "Oakland city council" --keywords oakland,council
"""

import sys

from config import get
from supabase import create_client


def _client():
    """Connect with the PUBLIC (publishable) key — routing only reads stories."""
    supabase_url = get("SUPABASE_URL")
    public_key = get("SUPABASE_PUBLISHABLE_KEY")
    if not supabase_url or not public_key:
        raise Exception("SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY are not set in .env")
    return create_client(supabase_url, public_key)


def _normalize(query) -> dict:
    """Accept either a plain string or a {'tsquery', 'keywords'} dict."""
    if isinstance(query, dict):
        tsquery = str(query.get("tsquery") or "").strip()
        keywords = query.get("keywords") or []
    else:
        tsquery = str(query or "").strip()
        keywords = []
    keywords = [str(k).strip() for k in keywords if str(k).strip()]
    return {"tsquery": tsquery, "keywords": keywords}


def match_level(query, supabase=None) -> list:
    """Return story ids matching `query`, ranked by relevance to THAT query.

    `query` can be a string (the websearch query) or a dict with optional
    'keywords'. Full-text matches (ts_rank order) come first; keyword-only
    backup matches are appended after. Duplicates are removed, keeping the
    higher-ranked position.
    """
    q = _normalize(query)
    if not q["tsquery"] and not q["keywords"]:
        return []

    supabase = supabase or _client()
    ordered_ids = []
    seen = set()

    # --- 1. Ranked full-text search via the search_stories() function ---
    if q["tsquery"]:
        try:
            rows = supabase.rpc("search_stories", {"q": q["tsquery"]}).execute().data or []
            for row in rows:
                sid = row.get("id")
                if sid and sid not in seen:
                    seen.add(sid)
                    ordered_ids.append(sid)
        except Exception as error:
            print(f"  (full-text search failed — did you run migration 0003? {error})")

    # --- 2. Backup: ILIKE keyword match on title + summary ---
    if q["keywords"]:
        # Build one OR filter, e.g. title.ilike.*union*,summary.ilike.*union*,...
        clauses = []
        for kw in q["keywords"]:
            safe = kw.replace(",", " ").replace("*", " ")
            clauses.append(f"title.ilike.*{safe}*")
            clauses.append(f"summary.ilike.*{safe}*")
        try:
            rows = (
                supabase.table("stories")
                .select("id")
                .or_(",".join(clauses))
                .gt("relevance_score", 0)
                .limit(100)
                .execute()
                .data
            ) or []
            for row in rows:
                sid = row.get("id")
                if sid and sid not in seen:
                    seen.add(sid)
                    ordered_ids.append(sid)
        except Exception as error:
            print(f"  (keyword backup match failed: {error})")

    return ordered_ids


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python3 ingest/route.py "<query>" [--keywords a,b,c]')
        sys.exit(0)

    query_str = sys.argv[1]
    keywords = []
    if "--keywords" in sys.argv:
        idx = sys.argv.index("--keywords")
        if idx + 1 < len(sys.argv):
            keywords = [k.strip() for k in sys.argv[idx + 1].split(",") if k.strip()]

    ids = match_level({"tsquery": query_str, "keywords": keywords})
    print(f'\n{len(ids)} matching stories for: "{query_str}"')
    for n, sid in enumerate(ids, 1):
        print(f"  {n:>3}. {sid}")
    print()
