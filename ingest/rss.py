"""
rss.py — pull recent stories from news RSS feeds into the Supabase "stories" table.

What this script does, in plain English:
  1. Reads a handful of news RSS feeds (the same kind of feed a podcast app or
     news reader uses).
  2. Turns each entry into a row that fits our "stories" table.
  3. Saves them into Supabase. If a story is already saved, it's skipped (no
     duplicates).
  4. If a feed is down or broken, it's skipped (the script never crashes) and
     reported at the end.
  5. Prints a friendly one-line summary of how many were new vs. already there.

How to run it (from inside the DESK project folder):

    python ingest/rss.py

Note: this WRITES to the database, so it uses the SECRET Supabase key (not the
public/publishable one).
"""

# feedparser knows how to read RSS/Atom feeds and hand us back clean Python data.
import feedparser

# Python's built-in tools for cleaning up text and handling dates.
from html.parser import HTMLParser
from email.utils import parsedate_to_datetime

# Our own helper that reads keys from .env (or the real environment on GitHub Actions).
from config import get

# The Supabase library lets Python read from and write to your database.
from supabase import create_client


# ---------------------------------------------------------------------------
# EDIT THESE LATER
# ---------------------------------------------------------------------------
# Each line is a (name, feed address, region label) trio. The name is what we
# save as the source; the feed address is the RSS link; the region label is a
# short tag ("ca" for California, "national" for nationwide). Add, remove, or
# change these freely — just keep the same three-part shape and the quotes.
FEEDS = [
    # --- National (clean feeds that still work) ---
    ("NPR Top",        "https://feeds.npr.org/1001/rss.xml",            "national"),
    ("NPR Politics",   "https://feeds.npr.org/1014/rss.xml",            "national"),
    ("Politico",       "https://rss.politico.com/politics-news.xml",    "national"),
    ("The Hill",       "https://thehill.com/news/feed/",                "national"),
    ("The Intercept",  "https://theintercept.com/feed/?lang=en",        "national"),
    ("The Conversation US","https://theconversation.com/us/articles.atom","national"),

    # --- International ---
    ("Guardian World", "https://www.theguardian.com/world/rss",         "intl"),
    ("NPR World",      "https://feeds.npr.org/1004/rss.xml",            "intl"),
    ("Al Jazeera",     "https://www.aljazeera.com/xml/rss/all.xml",     "intl"),
    ("Le Monde EN",    "https://www.lemonde.fr/en/international/rss_full.xml","intl"),

    # --- California ---
    ("CalMatters",     "https://calmatters.org/feed/",                  "ca"),
    ("KQED News",      "https://www.kqed.org/news/feed",                "ca"),   # corrected URL
    ("LA Times CA",    "https://www.latimes.com/california/rss2.0.xml", "ca"),

    # --- Local Bay ---
    ("Berkeleyside",   "https://www.berkeleyside.org/feed",             "local"),
    ("Mission Local",  "https://missionlocal.org/feed/",                "local"),
    ("SF Standard",    "https://sfstandard.com/feed/",                  "local"),

    # --- Labor (national + CA) ---
    ("Labor Notes",    "https://labornotes.org/feed",                   "national"),
    ("In These Times", "https://inthesetimes.com/rss",                  "national"),
    ("AFL-CIO Blog",   "https://aflcio.org/feeds/blog",                 "national"),
]
# ---------------------------------------------------------------------------


class _TagStripper(HTMLParser):
    """A tiny helper that throws away HTML tags and keeps only the plain text."""

    def __init__(self):
        super().__init__()
        self.text_parts = []

    def handle_data(self, data):
        self.text_parts.append(data)

    def get_text(self):
        return "".join(self.text_parts)


def strip_html(raw: str):
    """Turn a chunk of HTML (like '<p>Hello <b>world</b></p>') into plain text.

    Returns None if there's nothing to clean, so the database gets a null.
    """
    if not raw:
        return None
    stripper = _TagStripper()
    stripper.feed(raw)
    cleaned = stripper.get_text().strip()
    return cleaned or None


def parse_date(raw: str):
    """Turn a feed's published date (a string) into a proper timestamp.

    Returns None if the date is missing or we can't make sense of it, so the
    database gets a null instead of a crash.
    """
    if not raw:
        return None
    try:
        # parsedate_to_datetime understands the usual RSS date format and gives
        # us a datetime; .isoformat() makes a clean text timestamp for Supabase.
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return None


def has_audio(entry) -> bool:
    """Return True if this feed entry attaches an audio file (i.e. it's a podcast)."""
    # Feed entries can list attachments under "enclosures"; we look for an audio one.
    for enclosure in getattr(entry, "enclosures", []) or []:
        enclosure_type = enclosure.get("type", "")
        if enclosure_type.startswith("audio"):
            return True
    return False


def build_row(entry, name: str, region: str):
    """Turn one feed entry into a row for our "stories" table.

    Returns None if the entry has no link, since that column is required and
    must be unique. Any other missing field becomes null instead of crashing.
    """
    # .get(...) safely returns None if the field is missing.
    url = entry.get("link")
    if not url:
        # No unique link to anchor this story on — skip it.
        return None

    return {
        "url": url,
        "title": entry.get("title"),
        "source_name": name,
        "published_at": parse_date(entry.get("published")),
        "excerpt": strip_html(entry.get("summary")),
        "region": region,
        # If the entry has an audio attachment we call it a podcast, else an article.
        "media_type": "podcast" if has_audio(entry) else "article",
        "lean": "unrated",
        # summary, context_bullets, reliability_score, is_academic, alt_links
        # are intentionally left out — they get filled in a later phase.
    }


def main():
    # ---- 1. Read the keys we need -------------------------------------------------
    # (Reading the feeds needs no key, but we still need our Supabase keys to save.)
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

    # ---- 2. Read each feed (skipping any that fail) ------------------------------
    rows = []
    skipped_no_url = 0
    failed_feeds = []

    for name, feed_url, region in FEEDS:
        try:
            parsed = feedparser.parse(feed_url)

            # feedparser doesn't raise on network/parse errors; instead it sets
            # .bozo to 1 and stashes the problem in .bozo_exception. If it also
            # gave us no entries, treat the feed as failed and move on.
            if getattr(parsed, "bozo", 0) and not parsed.entries:
                failed_feeds.append(name)
                continue

            for entry in parsed.entries:
                row = build_row(entry, name, region)
                if row is None:
                    skipped_no_url += 1
                else:
                    rows.append(row)
        except Exception:
            # Belt and suspenders: if anything unexpected goes wrong with one
            # feed, record it as failed and keep going with the others.
            failed_feeds.append(name)
            continue

    fetched_count = len(rows) + skipped_no_url

    if not rows:
        print(
            f"\n  RSS: fetched {fetched_count}, inserted 0 new, "
            f"skipped {skipped_no_url} duplicates, failed feeds: {failed_feeds}\n"
        )
        return

    # ---- 3. Save to Supabase (skip anything already there) -----------------------
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
            "\n  I read the feeds fine, but couldn't save to Supabase.\n"
            "  Things to check:\n"
            "    - SUPABASE_URL and SUPABASE_SECRET_KEY are correct in your .env file.\n"
            "    - You've already created the tables (see SETUP.md, step 5).\n"
        )
        return

    # Supabase returns only the rows it actually inserted; the rest were duplicates.
    inserted = len(result.data or [])
    skipped_duplicates = (len(rows) - inserted) + skipped_no_url

    # ---- 4. Friendly summary ------------------------------------------------------
    print(
        f"\n  RSS: fetched {fetched_count}, inserted {inserted} new, "
        f"skipped {skipped_duplicates} duplicates, failed feeds: {failed_feeds}\n"
    )


if __name__ == "__main__":
    main()
