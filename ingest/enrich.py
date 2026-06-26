"""
enrich.py — turn the raw firehose of saved stories into sorted, classified ones.

What this script does, in plain English:
  1. Connects to Supabase and pulls stories that haven't been processed yet
     (so we never pay to process the same story twice). It handles at most 300
     per run to keep costs predictable.
  2. Runs a FREE keyword pre-filter first. Stories that don't match any of our
     beats are marked as noise (relevance 0) and are NOT sent to the AI.
  3. Sends the remaining "candidate" stories to the Anthropic AI in small
     batches to classify them (relevance, topics, political lean, reliability,
     a short neutral summary, and a couple of factual bullet points).
  4. Writes the results back to each story and prints a clear cost report so you
     can confirm we're staying well under $5/month.

How to run it (from inside the DESK project folder):

    python3 ingest/enrich.py

IMPORTANT: run the database migration first! Open
`supabase/migrations/0002_enrich.sql` in the Supabase SQL Editor and click Run
(see SETUP.md, step 6). Otherwise this script can't save its results.

Note: this WRITES to the database, so it uses the SECRET Supabase key (not the
public/publishable one). It also uses your Anthropic key to do the AI step.
"""

import json

# Our own helper that reads keys from .env (or the real environment on GitHub Actions).
from config import get

# The Supabase library lets Python read from and write to your database.
from supabase import create_client

# The Anthropic library lets Python talk to the Claude AI models.
from anthropic import Anthropic


# ---------------------------------------------------------------------------
# Settings you might tweak
# ---------------------------------------------------------------------------

# How many unprocessed stories to handle in a single run (caps cost per run).
MAX_PER_RUN = 300

# How many stories to send to the AI in one request (one call, many stories).
CHUNK_SIZE = 12

# Which Claude model to use. If this exact name is ever rejected, look up the
# current Haiku model name at https://docs.claude.com and paste it here.
MODEL = "claude-haiku-4-5-20251001"

# Haiku pricing, in dollars per ONE MILLION tokens (used only for the cost report).
PRICE_PER_M_INPUT = 1.0    # $1 per million input tokens
PRICE_PER_M_OUTPUT = 5.0   # $5 per million output tokens


# ---------------------------------------------------------------------------
# BEAT KEYWORDS  (the free pre-filter — edit these freely)
# ---------------------------------------------------------------------------
# If a story's title or excerpt contains NONE of these words (any beat), it's
# treated as noise and never sent to the AI. Add or remove terms to widen or
# narrow what counts as "worth classifying". Everything here is matched in a
# case-insensitive way, so don't worry about capitalization.
BEAT_KEYWORDS = {
    "ca":       ["california", "legislature", "assembly", "newsom", "sacramento", "ballot"],
    "labor":    ["union", "strike", "workers", "organizing", "teamsters", "seiu", "uaw", "nlrb", "upte", "labor"],
    "national": ["congress", "senate", "supreme court", "biden", "election", "midterm"],
    "local":    ["bay area", "oakland", "berkeley", "san francisco", "east bay"],
    "intl":     ["sahel", "european union", "china", "taiwan", "coup", "trade union"],
}

# Flatten all the keyword lists into one set for a quick "does it match anything?" check.
ALL_KEYWORDS = [term for terms in BEAT_KEYWORDS.values() for term in terms]


# The exact instructions we give the AI. Written once here so it's easy to read.
SYSTEM_PROMPT = (
    "You classify news items for a personal political reader focused on California "
    "legislature & labor, national politics & labor, the SF Bay Area, and international "
    "affairs. Return ONLY a JSON array, one object per item, no prose. For each item:\n"
    "  relevance_score: 0-10, how relevant to those beats (0 = off-topic noise).\n"
    "  topics: array of matching beats from [ca, labor, national, local, intl].\n"
    "  lean: one of left|center|right|unrated. Use a non-unrated value ONLY if the "
    "outlet's political lean is well established; otherwise 'unrated'.\n"
    "  is_academic: true ONLY for academic/research/journal sources, else false.\n"
    "  reliability: 1 (established newsroom), 2 (smaller or partisan but real outlet), "
    "3 (unknown/obscure). When unsure, use 3.\n"
    "  summary: a neutral 1-2 sentence summary ONLY IF a substantive excerpt is "
    "provided. If only a headline is given (no excerpt), return null. NEVER invent or "
    "infer content you were not given. Do not summarize a headline.\n"
    "  context_bullets: up to 2 short factual notes, drawn ONLY from the provided text. "
    "If no excerpt, return an empty array. Never add outside facts.\n"
    "Do not fetch anything. Work only from the text given."
)


def matches_a_keyword(story: dict) -> bool:
    """Return True if the story's title or excerpt contains any beat keyword."""
    # Combine title + excerpt into one lowercase blob of text to search through.
    haystack = ((story.get("title") or "") + " " + (story.get("excerpt") or "")).lower()
    return any(term in haystack for term in ALL_KEYWORDS)


def now_iso(supabase) -> str:
    """Ask the database for the current time as a clean timestamp string.

    We let the database supply 'now' so the timestamp matches the database's clock.
    """
    # Supabase/PostgREST doesn't expose now() directly, so we just use Python's
    # idea of UTC now — close enough for an "enriched_at" marker.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def build_item_text(story: dict) -> dict:
    """Shrink a story down to just the fields the AI needs to see."""
    return {
        "title": story.get("title") or "",
        "source_name": story.get("source_name") or "",
        "excerpt": story.get("excerpt") or "",
    }


def classify_chunk(client: Anthropic, stories: list):
    """Send up to CHUNK_SIZE stories to the AI and return (results, usage).

    Returns (list_of_result_dicts, usage_dict). On any problem it returns
    (None, usage_dict) so the caller can skip this chunk without crashing.
    """
    # Build the user message: a numbered JSON list of items for the AI to classify.
    items = [build_item_text(s) for s in stories]
    user_message = (
        "Classify these news items. Return a JSON array with exactly "
        f"{len(items)} objects, in the same order:\n\n"
        + json.dumps(items, ensure_ascii=False, indent=2)
    )

    usage = {"input": 0, "output": 0}

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as error:
        # Network problem, bad key, rate limit, etc. — report briefly and skip.
        print(f"    (skipping a batch — the AI request didn't go through: {error})")
        return None, usage

    # Record how many tokens this call used (for the cost report).
    usage["input"] = getattr(response.usage, "input_tokens", 0) or 0
    usage["output"] = getattr(response.usage, "output_tokens", 0) or 0

    # The reply text is in the first content block.
    reply_text = ""
    if response.content:
        reply_text = getattr(response.content[0], "text", "") or ""

    # The model is told to return ONLY a JSON array, but just in case there's
    # stray text around it, snip out the part between the first [ and last ].
    start = reply_text.find("[")
    end = reply_text.rfind("]")
    if start == -1 or end == -1 or end < start:
        print("    (skipping a batch — the AI's answer wasn't valid JSON.)")
        return None, usage

    try:
        results = json.loads(reply_text[start:end + 1])
    except json.JSONDecodeError:
        print("    (skipping a batch — the AI's answer wasn't valid JSON.)")
        return None, usage

    # Make sure we got a list with one result per story we sent.
    if not isinstance(results, list) or len(results) != len(stories):
        print("    (skipping a batch — the AI returned the wrong number of items.)")
        return None, usage

    return results, usage


def save_result(supabase, story_id: str, result: dict, when: str):
    """Write one AI result back to its story row, plus the enriched_at marker."""
    update = {
        "relevance_score": result.get("relevance_score"),
        "topics": result.get("topics"),
        "lean": result.get("lean"),
        "is_academic": result.get("is_academic"),
        # The AI calls this "reliability"; our column is "reliability_score".
        "reliability_score": result.get("reliability"),
        "summary": result.get("summary"),
        "context_bullets": result.get("context_bullets"),
        "enriched_at": when,
    }
    supabase.table("stories").update(update).eq("id", story_id).execute()


def main():
    # ---- 1. Read the keys we need -------------------------------------------------
    supabase_url = get("SUPABASE_URL")
    secret_key = get("SUPABASE_SECRET_KEY")
    anthropic_key = get("ANTHROPIC_API_KEY")

    if not supabase_url or not secret_key:
        print(
            "\n  I couldn't find your Supabase keys.\n"
            "  Please open your .env file and make sure these two lines have values:\n"
            "      SUPABASE_URL=...\n"
            "      SUPABASE_SECRET_KEY=...\n"
            "  (We use the SECRET key here because we're writing to the database.)\n"
        )
        return

    if not anthropic_key:
        print(
            "\n  I couldn't find your Anthropic API key.\n"
            "  Please open your .env file and make sure ANTHROPIC_API_KEY has a value.\n"
            "  (That's the key the AI classification step needs.)\n"
        )
        return

    # ---- 2. Connect and pull unprocessed stories ---------------------------------
    try:
        supabase = create_client(supabase_url, secret_key)
        response = (
            supabase.table("stories")
            .select("id, title, source_name, excerpt")
            .is_("enriched_at", "null")   # only stories we haven't processed yet
            .limit(MAX_PER_RUN)
            .execute()
        )
        stories = response.data or []
    except Exception:
        print(
            "\n  I couldn't read your stories from Supabase.\n"
            "  Things to check:\n"
            "    - SUPABASE_URL and SUPABASE_SECRET_KEY are correct in your .env file.\n"
            "    - You've run BOTH migrations (0001_init.sql AND 0002_enrich.sql) in the\n"
            "      Supabase SQL Editor. The second one adds the columns this script needs.\n"
        )
        return

    if not stories:
        print("\n  Nothing to do — every story has already been processed. 🎉\n")
        return

    when = now_iso(supabase)

    # ---- 3. FREE keyword pre-filter ----------------------------------------------
    candidates = []   # stories worth sending to the AI
    noise_count = 0   # stories that matched nothing

    for story in stories:
        if matches_a_keyword(story):
            candidates.append(story)
        else:
            # No keyword match → mark as noise (relevance 0), don't pay for AI.
            try:
                supabase.table("stories").update(
                    {"relevance_score": 0, "enriched_at": when}
                ).eq("id", story["id"]).execute()
                noise_count += 1
            except Exception:
                print("    (couldn't save one noise story — moving on.)")

    # ---- 4. AI classify the candidates, in small chunks --------------------------
    client = Anthropic(api_key=anthropic_key)

    total_input_tokens = 0
    total_output_tokens = 0
    classified_count = 0
    skipped_chunks = 0

    # Walk through the candidates CHUNK_SIZE at a time.
    for start in range(0, len(candidates), CHUNK_SIZE):
        chunk = candidates[start:start + CHUNK_SIZE]
        results, usage = classify_chunk(client, chunk)

        total_input_tokens += usage["input"]
        total_output_tokens += usage["output"]

        if results is None:
            # The chunk failed (bad JSON, network, etc.) — already logged. Skip it.
            # We deliberately leave these stories unprocessed so a later run retries them.
            skipped_chunks += 1
            continue

        # Save each story's result back to its row.
        for story, result in zip(chunk, results):
            try:
                save_result(supabase, story["id"], result, when)
                classified_count += 1
            except Exception:
                print("    (couldn't save one classified story — moving on.)")

    # ---- 5. COST REPORT ----------------------------------------------------------
    input_cost = (total_input_tokens / 1_000_000) * PRICE_PER_M_INPUT
    output_cost = (total_output_tokens / 1_000_000) * PRICE_PER_M_OUTPUT
    run_cost = input_cost + output_cost

    # If we ran this twice a day, every day, for a 30-day month:
    projected_monthly = run_cost * 2 * 30

    print("\n" + "=" * 56)
    print("  ENRICHMENT RUN COMPLETE")
    print("=" * 56)
    print(f"  Stories looked at this run : {len(stories)}")
    print(f"  Sent to AI (candidates)    : {len(candidates)}")
    print(f"  Skipped as noise (free)    : {noise_count}")
    print(f"  Successfully classified    : {classified_count}")
    if skipped_chunks:
        print(f"  Batches skipped (retry later): {skipped_chunks}")
    print("  " + "-" * 54)
    print(f"  Input tokens               : {total_input_tokens:,}")
    print(f"  Output tokens              : {total_output_tokens:,}")
    print(f"  Estimated cost this run    : ${run_cost:.4f}")
    print(f"  Projected monthly (2x/day) : ${projected_monthly:.4f}")
    print("=" * 56)
    if projected_monthly <= 5:
        print("  ✅ Comfortably under the $5/month target.")
    else:
        print("  ⚠️  Projected over $5/month — consider tightening BEAT_KEYWORDS.")
    print()


if __name__ == "__main__":
    main()
