"""
make_query.py — turn a short topic description into a search query, ONCE.

This is the only place Phase 6 uses the AI, and it runs only when a new subpage
or thread is created — never per story. The AI's whole job here is small: take a
title + one-line description (e.g. "Oakland city council" / "votes and races on
the council") and write a concise search query the database can run for free,
over and over, with no further AI.

What it produces for each item:
  - a Postgres "websearch" query string (saved to the item's query column), and
  - a short list of plain keywords (used as a backup match in route.py).

Two ways to use it:

  1. As a library (one item at a time):
         from make_query import build_query
         result = build_query("Oakland city council", "votes and races")
         # -> {"tsquery": "...", "keywords": ["...", "..."]}

  2. From the command line — build queries for every subpage/thread that is
     still 'pending' (this is what the nightly sync calls):
         python3 ingest/make_query.py

Note: writing the result back to the database uses the SECRET Supabase key, so
the command-line use needs SUPABASE_SECRET_KEY (and ANTHROPIC_API_KEY) in .env.
"""

import json

from config import get
from supabase import create_client
from anthropic import Anthropic


# Same Haiku model the enrichment step uses. If this name is ever rejected,
# look up the current Haiku model at https://docs.claude.com and paste it here.
MODEL = "claude-haiku-4-5-20251001"

# The exact instruction we give the AI. It must return ONLY JSON.
PROMPT = (
    "Turn this topic into a concise search query for a news database. Return ONLY JSON:\n"
    '{"tsquery": "<postgres websearch terms, e.g. \'Oakland city council election\'>",\n'
    ' "keywords": ["...","..."]}. Keep it specific enough to avoid false matches,\n'
    "broad enough to catch coverage. No prose."
)


def build_query(title: str, description: str = "") -> dict:
    """Call Haiku ONCE and return {"tsquery": str, "keywords": [str, ...]}.

    Raises a plain Exception if the AI key is missing or the reply can't be read,
    so the caller can decide whether to skip that one item and try again later.
    """
    anthropic_key = get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        raise Exception("ANTHROPIC_API_KEY is not set in .env")

    # Combine the title and description into the topic the AI should work from.
    topic = (title or "").strip()
    if description and description.strip():
        topic = topic + " — " + description.strip()

    client = Anthropic(api_key=anthropic_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": PROMPT + "\n\nTopic: " + topic}],
    )

    reply_text = ""
    if response.content:
        reply_text = getattr(response.content[0], "text", "") or ""

    # The model is told to return ONLY JSON, but snip from the first { to the
    # last } just in case there's any stray text around it.
    start = reply_text.find("{")
    end = reply_text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise Exception("AI did not return JSON. Got:\n" + reply_text)

    data = json.loads(reply_text[start:end + 1])

    # Tidy the result into a predictable shape.
    tsquery = str(data.get("tsquery") or "").strip()
    keywords = data.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = [str(keywords)]
    keywords = [str(k).strip() for k in keywords if str(k).strip()]

    if not tsquery:
        # Fall back to the title so the item still gets *some* usable query.
        tsquery = (title or "").strip()

    return {"tsquery": tsquery, "keywords": keywords}


def build_pending_queries() -> int:
    """Build queries for every subpage/thread still marked 'pending'.

    Returns how many items were built. Prints "Built query for: <title>" for
    each one, as the phase asks. Safe to run repeatedly — once an item is built
    it's marked 'tracking' and is skipped next time.
    """
    supabase_url = get("SUPABASE_URL")
    secret_key = get("SUPABASE_SECRET_KEY")
    if not supabase_url or not secret_key:
        print("  (skipping query-building — SUPABASE_URL / SUPABASE_SECRET_KEY not set)")
        return 0

    supabase = create_client(supabase_url, secret_key)
    built = 0

    # --- subpages: store the query in the `query` column ---
    try:
        rows = (
            supabase.table("subpages")
            .select("id, title, description, query, status")
            .eq("status", "pending")
            .execute()
            .data
        ) or []
    except Exception as error:
        print(f"  (couldn't read subpages — did you run migration 0003? {error})")
        rows = []

    for sp in rows:
        # Only build if it doesn't already have a query.
        if (sp.get("query") or "").strip():
            continue
        try:
            result = build_query(sp.get("title", ""), sp.get("description", ""))
            supabase.table("subpages").update(
                {"query": result["tsquery"], "status": "tracking"}
            ).eq("id", sp["id"]).execute()
            print(f"Built subpage query for: {sp.get('title', '(untitled subpage)')}")
            built += 1
        except Exception as error:
            print(f"  (skipped subpage '{sp.get('title')}' — will retry next run: {error})")

    # --- threads: store the query in the `derived_query` column ---
    try:
        rows = (
            supabase.table("threads")
            .select("id, title, description, derived_query, status")
            .eq("status", "pending")
            .execute()
            .data
        ) or []
    except Exception as error:
        print(f"  (couldn't read threads: {error})")
        rows = []

    for th in rows:
        if (th.get("derived_query") or "").strip():
            continue
        try:
            result = build_query(th.get("title", ""), th.get("description", ""))
            supabase.table("threads").update(
                {"derived_query": result["tsquery"], "status": "tracking"}
            ).eq("id", th["id"]).execute()
            print(f"Built query for: {th.get('title', '(untitled thread)')}")
            built += 1
        except Exception as error:
            print(f"  (skipped thread '{th.get('title')}' — will retry next run: {error})")

    if built == 0:
        print("No pending subpages or threads — nothing to build. 👍")

    return built


if __name__ == "__main__":
    build_pending_queries()
