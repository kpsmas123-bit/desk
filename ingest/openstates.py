"""
openstates.py — pull recent California bills into the Supabase "stories" table.

What this script does, in plain English:
  1. Asks the OpenStates website for the 20 most-recently-active California bills.
  2. Turns each bill into a row that fits our "stories" table.
  3. Saves them into Supabase. If a bill is already saved, it's skipped (no duplicates).
  4. Prints a friendly summary of how many were new vs. already there.

How to run it (from inside the DESK project folder):

    python ingest/openstates.py

Note: this WRITES to the database, so it uses the SECRET Supabase key (not the
public/publishable one).
"""

# requests lets us talk to the OpenStates website (an HTTP API).
import requests

# Our own helper that reads keys from .env (or the real environment on GitHub Actions).
from config import get

# The Supabase library lets Python read from and write to your database.
from supabase import create_client


# The OpenStates "list bills" endpoint (their version 3 API).
OPENSTATES_URL = "https://v3.openstates.org/bills"


def fetch_bills(api_key: str) -> list:
    """Ask OpenStates for recent California bills and return them as a list."""
    params = {
        "jurisdiction": "California",
        "sort": "latest_action_desc",   # newest activity first
        "per_page": 20,                 # keep small — the free tier is rate-limited
        "include": "abstracts",         # also fetch short summaries when available
        "apikey": api_key,
    }

    # timeout=30 means: give up after 30 seconds instead of hanging forever.
    response = requests.get(OPENSTATES_URL, params=params, timeout=30)

    # If OpenStates says "no" (bad key, rate limit, etc.), raise so we can explain it.
    response.raise_for_status()

    # The bills are in the "results" part of the answer.
    return response.json().get("results", [])


def build_row(bill: dict):
    """Turn one OpenStates bill into a row for our "stories" table.

    Returns None if the bill has no openstates_url, since that column is required
    and must be unique. Any other missing field becomes null instead of crashing.
    """
    # .get(...) safely returns None if the field is missing.
    url = bill.get("openstates_url")
    if not url:
        # No unique link to anchor this story on — skip it.
        return None

    # Build a readable title like "AB 1234 — An act relating to…".
    identifier = bill.get("identifier") or ""
    raw_title = bill.get("title") or ""
    title = f"{identifier} — {raw_title}".strip(" —") or identifier or raw_title

    # The most recent action gives us a date and a short description.
    latest_action_date = bill.get("latest_action_date")
    latest_action_description = bill.get("latest_action_description")

    # Prefer the latest action text for the excerpt; otherwise use the first abstract.
    excerpt = latest_action_description
    if not excerpt:
        abstracts = bill.get("abstracts") or []
        if abstracts:
            excerpt = abstracts[0].get("abstract")

    return {
        "url": url,
        "title": title,
        "source_name": "OpenStates",
        "published_at": latest_action_date,   # may be None — that's allowed
        "excerpt": excerpt,                   # may be None — that's allowed
        "region": "ca",
        "media_type": "article",
        # summary, context_bullets, reliability_score, is_academic, lean, alt_links
        # are intentionally left out — they get filled in a later phase.
    }


def main():
    # ---- 1. Read the keys we need -------------------------------------------------
    api_key = get("OPENSTATES_API_KEY")
    supabase_url = get("SUPABASE_URL")
    secret_key = get("SUPABASE_SECRET_KEY")

    if not api_key:
        print(
            "\n  I couldn't find your OpenStates API key.\n"
            "  Please open your .env file and make sure OPENSTATES_API_KEY has a value.\n"
        )
        return

    if not supabase_url or not secret_key:
        print(
            "\n  I couldn't find your Supabase keys.\n"
            "  Please open your .env file and make sure these two lines have values:\n"
            "      SUPABASE_URL=...\n"
            "      SUPABASE_SECRET_KEY=...\n"
            "  (We use the SECRET key here because we're writing to the database.)\n"
        )
        return

    # ---- 2. Fetch the bills from OpenStates --------------------------------------
    try:
        bills = fetch_bills(api_key)
    except requests.exceptions.Timeout:
        print(
            "\n  OpenStates took too long to answer.\n"
            "  Please check your internet connection and try again in a minute.\n"
        )
        return
    except requests.exceptions.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        if status in (401, 403):
            print(
                "\n  OpenStates rejected the request — your API key looks wrong.\n"
                "  Double-check OPENSTATES_API_KEY in your .env file.\n"
            )
        elif status == 429:
            print(
                "\n  OpenStates says we've made too many requests for now (rate limit).\n"
                "  Please wait a little while and run the script again.\n"
            )
        else:
            print(
                f"\n  OpenStates returned an error (code {status}).\n"
                "  Please try again in a few minutes.\n"
            )
        return
    except requests.exceptions.RequestException:
        print(
            "\n  I couldn't reach OpenStates.\n"
            "  Please check your internet connection and try again.\n"
        )
        return

    # ---- 3. Turn the bills into database rows ------------------------------------
    rows = []
    skipped_no_url = 0
    for bill in bills:
        row = build_row(bill)
        if row is None:
            skipped_no_url += 1
        else:
            rows.append(row)

    fetched_count = len(bills)

    if not rows:
        print(
            f"\n  Fetched {fetched_count} California bills, but none could be saved\n"
            "  (they were missing the required link). Nothing was added.\n"
        )
        return

    # ---- 4. Save to Supabase (skip anything already there) -----------------------
    try:
        supabase = create_client(supabase_url, secret_key)
        # upsert with ignore_duplicates=True means: "insert new rows, and if a row
        # with the same url already exists, just leave it alone" (on conflict do nothing).
        result = (
            supabase.table("stories")
            .upsert(rows, on_conflict="url", ignore_duplicates=True)
            .execute()
        )
    except Exception:
        print(
            "\n  I reached OpenStates fine, but couldn't save to Supabase.\n"
            "  Things to check:\n"
            "    - SUPABASE_URL and SUPABASE_SECRET_KEY are correct in your .env file.\n"
            "    - You've already created the tables (see SETUP.md, step 5).\n"
        )
        return

    # Supabase returns only the rows it actually inserted; the rest were duplicates.
    inserted = len(result.data or [])
    skipped_existing = len(rows) - inserted
    skipped_total = skipped_existing + skipped_no_url

    # ---- 5. Friendly summary ------------------------------------------------------
    print(
        f"\n  Fetched {fetched_count} California bills. "
        f"Inserted {inserted} new, skipped {skipped_total} already in database.\n"
    )


if __name__ == "__main__":
    main()
