"""
gdelt.py — pull recent news articles into the Supabase "stories" table.

What this script does, in plain English:
  1. Asks GDELT (a free, public news database — no API key needed) for recent
     articles matching a few searches we care about.
  2. Turns each article into a row that fits our "stories" table.
  3. Saves them into Supabase. If an article is already saved, it's skipped (no
     duplicates).
  4. Prints a friendly one-line summary of how many were new vs. already there.

How to run it (from inside the DESK project folder):

    python ingest/gdelt.py

Note: this WRITES to the database, so it uses the SECRET Supabase key (not the
public/publishable one).
"""

# requests lets us talk to the GDELT website (an HTTP API).
import requests

# time lets us pause briefly between requests so we don't trip GDELT's rate limit.
import time

# Our own helper that reads keys from .env (or the real environment on GitHub Actions).
from config import get

# The Supabase library lets Python read from and write to your database.
from supabase import create_client


# The GDELT DOC 2.0 "article list" endpoint (free, no API key required).
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# How long to wait (seconds) between GDELT queries. We now run MANY more queries
# than before — the base SEARCHES list PLUS one query for every user's tracking
# thread — so without a pause GDELT starts returning "429 too many requests".
QUERY_DELAY_SECONDS = 8


# ---------------------------------------------------------------------------
# EDIT THESE LATER
# ---------------------------------------------------------------------------
# Each line is a (search phrase, region label) pair. The search phrase is what
# GDELT looks for; the region label is just a short tag we save in the database
# ("ca" for California, "national" for nationwide). Add, remove, or change these
# freely — just keep the same (phrase, label) shape and the surrounding quotes.
SEARCHES = [
    # --- California legislature & policy ---
    ('"California legislature" (bill OR assembly OR senate) sourcelang:english', "ca"),
    ('California governor Newsom policy sourcelang:english', "ca"),
    ('California ballot measure proposition sourcelang:english', "ca"),

    # --- California / national labor ---
    ('California labor union organizing sourcelang:english', "ca"),
    ('(UPTE OR "academic workers" OR UAW) California university sourcelang:english', "ca"),
    ('(strike OR "work stoppage" OR "union election") workers sourcelang:english', "national"),
    ('(Teamsters OR SEIU OR "United Auto Workers") sourcelang:english', "national"),
    ('"National Labor Relations Board" OR NLRB sourcelang:english', "national"),
    ('"Working Families Party" OR "labor coalition" sourcelang:english', "national"),

    # --- National politics ---
    ('Congress legislation vote sourcelang:english', "national"),
    ('"2026 midterms" OR "midterm election" campaign sourcelang:english', "national"),
    ('Supreme Court ruling sourcelang:english', "national"),

    # --- Local Bay ---
    ('("East Bay" OR Oakland OR Berkeley OR "San Francisco") (city council OR housing OR labor) sourcelang:english', "local"),

    # --- International affairs / IR ---
    ('(Sahel OR "West Africa") (coup OR insurgency OR security) sourcelang:english', "intl"),
    ('European Union election government sourcelang:english', "intl"),
    ('(China OR Taiwan OR "South China Sea") policy sourcelang:english', "intl"),
    ('international labor movement "trade union" sourcelang:english', "intl"),
]
# ---------------------------------------------------------------------------


def fetch_tracking_thread_queries(supabase) -> list:
    """Return the search queries from EVERY user's 'tracking' threads.

    This is the shared-pool network effect: any discovery thread that any user
    creates also gets fetched here, and its results land in the shared `stories`
    table for everyone. The backend uses the SECRET key, so it sees all users'
    threads (RLS is bypassed by design for ingest scripts).

    We pair each query with region None — a thread's stories aren't tied to one
    region; they're matched live by full-text search (search_stories), not by
    the page-level region filter, so leaving region blank is correct.
    """
    try:
        rows = (
            supabase.table("threads")
            .select("derived_query")
            .eq("status", "tracking")
            .execute()
            .data
        ) or []
    except Exception as error:
        print(f"  (couldn't read tracking threads — skipping shared-pool queries: {error})")
        return []

    # Dedupe identical queries so two users tracking the same topic don't double
    # the GDELT calls.
    seen = set()
    queries = []
    for row in rows:
        q = (row.get("derived_query") or "").strip()
        if q and q not in seen:
            seen.add(q)
            queries.append((q, None))

    if queries:
        print(f"  (+ {len(queries)} shared query(ies) from users' tracking threads)")
    return queries


def fetch_articles(query: str) -> list:
    """Ask GDELT for recent articles matching one search phrase, return a list."""
    params = {
        "query": query,
        "mode": "ArtList",      # we want a list of articles
        "format": "json",       # give us the answer as JSON (easy for Python to read)
        "maxrecords": 25,       # at most 25 articles per search
        "timespan": "48h",      # only articles from the last 48 hours
        "sort": "DateDesc",     # newest first
    }

    # timeout=30 means: give up after 30 seconds instead of hanging forever.
    response = requests.get(GDELT_URL, params=params, timeout=30)

    # If GDELT says "no" (rate limit, server hiccup, etc.), raise so we can explain it.
    response.raise_for_status()

    # GDELT sometimes returns an empty body or non-JSON when there are no results;
    # guard against that so we don't crash, and just treat it as "no articles".
    try:
        data = response.json()
    except ValueError:
        return []

    # The articles live in the "articles" part of the answer.
    return data.get("articles", [])


def build_row(article: dict, region: str):
    """Turn one GDELT article into a row for our "stories" table.

    Returns None if the article has no url, since that column is required and
    must be unique. Any other missing field becomes null instead of crashing.
    """
    # .get(...) safely returns None if the field is missing.
    url = article.get("url")
    if not url:
        # No unique link to anchor this story on — skip it.
        return None

    return {
        "url": url,
        "title": article.get("title"),
        "source_name": article.get("domain"),     # e.g. "latimes.com"
        "published_at": article.get("seendate"),  # may be None — that's allowed
        "excerpt": None,                           # GDELT doesn't give us a snippet
        "region": region,
        "media_type": "article",
        "lean": "unrated",
        # summary, context_bullets, reliability_score, is_academic, alt_links
        # are intentionally left out — they get filled in a later phase.
    }


def main():
    # ---- 1. Read the keys we need -------------------------------------------------
    # (GDELT itself needs no key, but we still need our Supabase keys to save.)
    supabase_url = get("SUPABASE_URL")
    secret_key = get("SUPABASE_SECRET_KEY")

    if not supabase_url or not secret_key:
        print(
            "\n  I couldn't find your Supabase keys.\n"
            "  Please open your .env file and make sure these two lines have values:\n"
            "      SUPABASE_URL=...\n"
            "      SUPABASE_SECRET_KEY=...\n"
            "  (We use the SECRET key here because we're writing to the database.)\n"
        )
        return

    # ---- 2. Connect to Supabase (needed to read tracking threads + to save) -------
    try:
        supabase = create_client(supabase_url, secret_key)
    except Exception:
        print(
            "\n  I couldn't connect to Supabase.\n"
            "  Things to check:\n"
            "    - SUPABASE_URL and SUPABASE_SECRET_KEY are correct in your .env file.\n"
        )
        return

    # Combine the fixed base SEARCHES with one query per user's tracking thread,
    # so every discovery thread enriches the shared pool for everyone.
    searches = list(SEARCHES) + fetch_tracking_thread_queries(supabase)

    # ---- 3. Fetch the articles from GDELT ----------------------------------------
    all_articles = []
    for index, (query, region) in enumerate(searches):
        # Pause between calls (but not before the very first) to stay under
        # GDELT's burst rate limit now that we run many more queries.
        if index > 0:
            time.sleep(QUERY_DELAY_SECONDS)
        try:
            articles = fetch_articles(query)
        except requests.exceptions.Timeout:
            print(
                f"\n  GDELT took too long to answer for the search '{query}'.\n"
                "  Skipping that one and moving on.\n"
            )
            continue
        except requests.exceptions.HTTPError as error:
            status = error.response.status_code if error.response is not None else None
            if status == 429:
                print(
                    f"\n  GDELT says we've made too many requests for now (rate limit),\n"
                    f"  so I'm skipping the search '{query}'. Try again in a little while.\n"
                )
            else:
                print(
                    f"\n  GDELT returned an error (code {status}) for the search '{query}'.\n"
                    "  Skipping that one and moving on.\n"
                )
            continue
        except requests.exceptions.RequestException:
            print(
                f"\n  I couldn't reach GDELT for the search '{query}'.\n"
                "  Please check your internet connection. Skipping that one for now.\n"
            )
            continue

        # Remember which region each article belongs to (so we can tag it later).
        for article in articles:
            all_articles.append((article, region))

    fetched_count = len(all_articles)

    # ---- 4. Turn the articles into database rows ---------------------------------
    rows = []
    skipped_no_url = 0
    for article, region in all_articles:
        row = build_row(article, region)
        if row is None:
            skipped_no_url += 1
        else:
            rows.append(row)

    if not rows:
        print(
            f"\n  GDELT: fetched {fetched_count}, inserted 0 new, "
            f"skipped {skipped_no_url} duplicates.\n"
        )
        return

    # ---- 5. Save to Supabase (skip anything already there) -----------------------
    try:
        # upsert with ignore_duplicates=True means: "insert new rows, and if a row
        # with the same url already exists, just leave it alone" (on conflict do nothing).
        result = (
            supabase.table("stories")
            .upsert(rows, on_conflict="url", ignore_duplicates=True)
            .execute()
        )
    except Exception:
        print(
            "\n  I reached GDELT fine, but couldn't save to Supabase.\n"
            "  Things to check:\n"
            "    - SUPABASE_URL and SUPABASE_SECRET_KEY are correct in your .env file.\n"
            "    - You've already created the tables (see SETUP.md, step 5).\n"
        )
        return

    # Supabase returns only the rows it actually inserted; the rest were duplicates.
    inserted = len(result.data or [])
    skipped_duplicates = (len(rows) - inserted) + skipped_no_url

    # ---- 6. Friendly summary ------------------------------------------------------
    print(
        f"\n  GDELT: fetched {fetched_count}, inserted {inserted} new, "
        f"skipped {skipped_duplicates} duplicates.\n"
    )


if __name__ == "__main__":
    main()
