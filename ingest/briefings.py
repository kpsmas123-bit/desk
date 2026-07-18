"""
briefings.py — generate deep-topic briefings from Tavily search results.

What this script does, in plain English:
  1. Loads threads with status='tracking' from the threads table.
  2. For each thread, runs 6 base Tavily "advanced" searches across different
     source layers (official records, press releases, news, analysis, and a
     hint-based or calendar search).
  3. Loads the previous briefing's gaps and runs up to 3 targeted gap-fill
     searches to address what was missed last time.
  4. Sends the deduplicated results to Haiku, which synthesizes a structured
     briefing JSON (status line, what changed, next decision point, sources, etc.).
  5. Self-grades the briefing against a rubric (primary-source ratio, staleness,
     fabrication, decision point, gaps).
  6. If any grade fails, appends a learning hint to the thread so the next run
     adjusts its search strategy.
  7. Writes the finished briefing (with grade) to the briefings table.

How to run it (from inside the DESK project folder):

    python3 ingest/briefings.py
"""

import json
import re
from datetime import datetime, date, timedelta

# Our own helper that reads keys from .env (or the real environment on GitHub Actions).
from config import get

# The Supabase library lets Python read from and write to your database.
from supabase import create_client

# The Anthropic library lets Python talk to the Claude AI models.
from anthropic import Anthropic

# The Tavily library lets Python run web searches.
from tavily import TavilyClient


# ---------------------------------------------------------------------------
# Settings you might tweak
# ---------------------------------------------------------------------------

# How many topics to process per run (caps cost per run).
MAX_TOPICS_PER_RUN = 15

# How many Tavily searches to run per topic (base searches).
SEARCHES_PER_TOPIC = 6

# Up to 3 extra searches targeting gaps from the previous briefing.
GAP_SEARCHES_PER_TOPIC = 3

# Which Claude model to use for synthesis.
MODEL = "claude-haiku-4-5-20251001"

# Haiku pricing, in dollars per ONE MILLION tokens (used only for the cost report).
PRICE_PER_M_INPUT = 1.0    # $1 per million input tokens
PRICE_PER_M_OUTPUT = 5.0   # $5 per million output tokens

# Each Tavily "advanced" search costs 2 credits.
TAVILY_CREDITS_PER_ADVANCED = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text):
    """Convert text to a URL-safe slug."""
    return re.sub(r'[^a-z0-9]+', '-', text.lower().strip()).strip('-') or 'item'


def search_topic(tavily, topic, description, archetype, hints):
    """Run 6 base Tavily searches across source layers. Returns deduplicated results."""
    queries = [
        # 1. Official records from .gov sites
        {
            "query": f'"{topic}" official record',
            "include_domains": [
                "congress.gov", "whitehouse.gov", "ca.gov", "senate.gov",
                "house.gov", "supremecourt.gov", "govinfo.gov",
            ],
        },
        # 2. Second .gov pass with different phrasing
        {
            "query": f'"{topic}" site:.gov',
        },
        # 3. Press releases and official statements
        {
            "query": f'"{topic}" press release statement',
        },
        # 4. News coverage
        {
            "query": f'"{topic}" news coverage',
        },
        # 5. Analysis and reports
        {
            "query": f'"{topic}" analysis report',
        },
    ]

    # 6. Hint-based search or calendar/upcoming-events fallback
    if hints and len(hints) > 0:
        # Use the most recent hint to adjust the query
        latest_hint = hints[-1] if isinstance(hints, list) else str(hints)
        queries.append({"query": f'"{topic}" {latest_hint}'})
    else:
        queries.append({"query": f'"{topic}" upcoming hearing vote meeting date'})

    # Run all searches and collect results
    all_results = []
    for q in queries:
        try:
            kwargs = {
                "query": q["query"],
                "search_depth": "advanced",
                "max_results": 10,
            }
            if "include_domains" in q:
                kwargs["include_domains"] = q["include_domains"]

            response = tavily.search(**kwargs)
            for r in response.get("results", []):
                all_results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "date": r.get("published_date", ""),
                })
        except Exception as error:
            print(f"    (search failed for query: {q['query'][:60]}... — {error})")

    # Deduplicate by URL
    seen_urls = set()
    deduped = []
    for r in all_results:
        if r["url"] and r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            deduped.append(r)

    return deduped


def search_gaps(tavily, topic, gaps):
    """Run up to GAP_SEARCHES_PER_TOPIC targeted searches for previous gaps.

    Returns (results_list, search_count).
    """
    if not gaps:
        return [], 0

    all_results = []
    search_count = 0

    for gap_text in gaps[:GAP_SEARCHES_PER_TOPIC]:
        # Shorten gap text to first 60 chars to keep the query focused
        short_gap = gap_text[:60].strip()
        query = f'"{topic}" {short_gap}'

        try:
            response = tavily.search(
                query=query,
                search_depth="advanced",
                max_results=10,
            )
            search_count += 1
            for r in response.get("results", []):
                all_results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "date": r.get("published_date", ""),
                })
        except Exception as error:
            search_count += 1
            print(f"    (gap search failed: {query[:60]}... — {error})")

    # Deduplicate by URL
    seen_urls = set()
    deduped = []
    for r in all_results:
        if r["url"] and r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            deduped.append(r)

    return deduped, search_count


def synthesize_briefing(client, topic, archetype, results, previous_as_of, previous_gaps):
    """Call Haiku to synthesize search results into briefing JSON. Returns (dict, usage)."""
    archetype_note = f' (archetype: {archetype})' if archetype else ''
    time_boundary = ""
    if previous_as_of:
        time_boundary = (
            f"\n\nThe previous briefing was generated with as_of = \"{previous_as_of}\". "
            "Anything dated AFTER that is a new development and belongs in what_changed. "
            "Anything before that is background context."
        )

    gaps_instruction = ""
    if previous_gaps:
        gaps_list = "\n".join(f"  - {g}" for g in previous_gaps)
        gaps_instruction = (
            f"\n\nThe previous briefing declared these gaps:\n{gaps_list}\n"
            "Address as many as possible using the new search results. "
            "Remove from gaps any that are now covered. Add new gaps you discover."
        )

    system_prompt = (
        "You synthesize web search results into a structured policy/topic briefing. "
        "Return ONLY valid JSON, no prose before or after. The JSON must have these fields:\n"
        "  slug: a URL-safe slug for this topic.\n"
        "  topic: the topic name.\n"
        "  archetype: the topic archetype (or null).\n"
        "  status_line: one sentence summarizing the current status.\n"
        "  as_of: today's date in YYYY-MM-DD format.\n"
        "  background: 2-4 sentences of context for someone new to this topic.\n"
        "  what_changed: array of objects {headline, detail, source_url, date}. "
        "Recent developments only.\n"
        "  next_decision_point: object {description, date, body} or null if topic is "
        "resolved or no upcoming decision can be found.\n"
        "  official_record: array of objects {title, source_url, body, date}. "
        "Government/official sources only.\n"
        "  coverage: array of objects {headline, source_url, outlet, date, angle}. "
        "News coverage.\n"
        "  analysis: array of objects {title, source_url, author, date, thesis}. "
        "Analysis and reports.\n"
        "  gaps: array of strings listing what the search did NOT find.\n"
        "  sources_total: integer — DERIVE by counting entries across what_changed + "
        "official_record + coverage + analysis.\n"
        "  primary_source_count: integer — DERIVE by counting official_record entries + "
        ".gov URLs in other sections.\n"
        "\nCRITICAL RULES:\n"
        "- EVERY entry in what_changed, official_record, coverage, and analysis MUST have "
        "a non-empty source_url/url from the search results.\n"
        "- If next_decision_point can't be found, set to null and list the gap in gaps.\n"
        "- Never fabricate facts not present in the search results.\n"
        "- gaps must always be non-empty — there is always something the search missed."
        + gaps_instruction
    )

    user_message = (
        f"Topic: {topic}{archetype_note}{time_boundary}\n\n"
        f"Search results ({len(results)} items):\n\n"
        + json.dumps(results, ensure_ascii=False, indent=2)
    )

    usage = {"input": 0, "output": 0}

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
    except Exception as error:
        print(f"    (synthesis failed — the AI request didn't go through: {error})")
        return None, usage

    usage["input"] = getattr(response.usage, "input_tokens", 0) or 0
    usage["output"] = getattr(response.usage, "output_tokens", 0) or 0

    reply_text = ""
    if response.content:
        reply_text = getattr(response.content[0], "text", "") or ""

    # Extract JSON from first { to last }
    start = reply_text.find("{")
    end = reply_text.rfind("}")
    if start == -1 or end == -1 or end < start:
        print("    (synthesis failed — the AI's answer wasn't valid JSON.)")
        return None, usage

    try:
        briefing = json.loads(reply_text[start:end + 1])
    except json.JSONDecodeError:
        print("    (synthesis failed — the AI's answer wasn't valid JSON.)")
        return None, usage

    return briefing, usage


def grade_briefing(briefing):
    """Run all rubric checks. Returns grade dict."""
    failure_reasons = []

    # Collect all entries across sections for shared checks
    what_changed = briefing.get("what_changed") or []
    official_record = briefing.get("official_record") or []
    coverage = briefing.get("coverage") or []
    analysis = briefing.get("analysis") or []
    all_entries = what_changed + official_record + coverage + analysis

    # --- primary_ratio: official_record + .gov URLs / total entries ---
    gov_count = len(official_record)
    for entry in what_changed + coverage + analysis:
        url = entry.get("source_url") or entry.get("url") or ""
        if ".gov" in url:
            gov_count += 1

    total_entries = len(all_entries) if all_entries else 1  # avoid division by zero
    primary_ratio = gov_count / total_entries
    primary_ratio_pass = primary_ratio >= 0.30

    if not primary_ratio_pass:
        failure_reasons.append(
            f"primary_ratio {primary_ratio:.0%} < 30%"
        )

    # --- decision_point: non-null with date, or null (topic resolved) ---
    ndp = briefing.get("next_decision_point")
    if ndp is None:
        # null means topic resolved — that's acceptable
        decision_point_pass = True
    elif isinstance(ndp, dict) and ndp.get("date"):
        decision_point_pass = True
    else:
        decision_point_pass = False
        failure_reasons.append("next_decision_point missing date")

    # --- fabrication: every entry must have a non-empty url/source_url ---
    fabrication_pass = True
    for entry in all_entries:
        url = entry.get("source_url") or entry.get("url") or ""
        if not url.strip():
            fabrication_pass = False
            failure_reasons.append("empty URL found in entry")
            break

    # --- staleness: as_of within 60 days of today ---
    staleness_pass = True
    as_of_str = briefing.get("as_of") or ""
    try:
        as_of_date = datetime.strptime(as_of_str, "%Y-%m-%d").date()
        if (date.today() - as_of_date).days > 60:
            staleness_pass = False
            failure_reasons.append(f"as_of {as_of_str} is more than 60 days old")
    except (ValueError, TypeError):
        staleness_pass = False
        failure_reasons.append("as_of date is missing or invalid")

    # --- gaps: must be non-empty ---
    gaps = briefing.get("gaps") or []
    gaps_pass = len(gaps) > 0
    if not gaps_pass:
        failure_reasons.append("gaps array is empty")

    all_pass = all([
        primary_ratio_pass,
        decision_point_pass,
        fabrication_pass,
        staleness_pass,
        gaps_pass,
    ])

    return {
        "all_pass": all_pass,
        "primary_ratio": primary_ratio,
        "primary_ratio_pass": primary_ratio_pass,
        "decision_point_pass": decision_point_pass,
        "fabrication_pass": fabrication_pass,
        "staleness_pass": staleness_pass,
        "gaps_pass": gaps_pass,
        "failure_reasons": failure_reasons,
    }


def main():
    # ---- 1. Check keys -------------------------------------------------------
    supabase_url = get("SUPABASE_URL")
    secret_key = get("SUPABASE_SECRET_KEY")
    anthropic_key = get("ANTHROPIC_API_KEY")
    tavily_key = get("TAVILY_API_KEY")

    missing = []
    if not supabase_url:
        missing.append("SUPABASE_URL")
    if not secret_key:
        missing.append("SUPABASE_SECRET_KEY")
    if not anthropic_key:
        missing.append("ANTHROPIC_API_KEY")
    if not tavily_key:
        missing.append("TAVILY_API_KEY")

    if missing:
        print(
            "\n  Missing keys in your .env file:\n"
            + "".join(f"      {k}\n" for k in missing)
            + "  Please add them before running briefings.\n"
        )
        return

    # ---- 2. Connect ----------------------------------------------------------
    sb = create_client(supabase_url, secret_key)
    anthropic_client = Anthropic(api_key=anthropic_key)
    tavily = TavilyClient(api_key=tavily_key)

    # ---- 3. Load tracking threads --------------------------------------------
    try:
        threads_resp = (
            sb.table("threads")
            .select("*")
            .eq("status", "tracking")
            .order("position")
            .limit(MAX_TOPICS_PER_RUN)
            .execute()
        )
        threads = threads_resp.data or []
    except Exception as error:
        print(f"\n  Couldn't load tracking threads: {error}\n")
        return

    if not threads:
        print("\n  No tracking threads found. Nothing to do.\n")
        return

    n = len(threads)
    print(f"\n  Processing {n} thread(s)...\n")

    # ---- 4. Process each thread ----------------------------------------------
    total_input_tokens = 0
    total_output_tokens = 0
    total_searches = 0
    passed = 0
    failed = 0

    for i, thread in enumerate(threads):
        topic_name = thread.get("title") or "Untitled"
        description = thread.get("description") or ""
        archetype = thread.get("archetype")
        slug = slugify(topic_name)
        hints = thread.get("search_hints") or []

        print(f"  [{i+1}/{n}] {topic_name} ({archetype or 'untyped'})...")

        try:
            # a. Load previous briefing (latest by generated_at)
            previous_as_of = None
            previous_gaps = []
            try:
                prev_resp = (
                    sb.table("briefings")
                    .select("as_of, gaps")
                    .eq("thread_id", thread["id"])
                    .order("generated_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if prev_resp.data:
                    previous_as_of = prev_resp.data[0].get("as_of")
                    previous_gaps = prev_resp.data[0].get("gaps") or []
            except Exception:
                pass  # no previous briefing — that's fine

            # b. Base searches
            results = search_topic(tavily, topic_name, description, archetype, hints)
            total_searches += SEARCHES_PER_TOPIC
            print(f"    {len(results)} unique results from {SEARCHES_PER_TOPIC} base searches")

            # c. Gap-fill searches
            gap_results, gap_search_count = search_gaps(tavily, topic_name, previous_gaps)
            total_searches += gap_search_count
            if gap_search_count > 0:
                # Merge gap results into main results, deduplicating
                seen_urls = {r["url"] for r in results if r.get("url")}
                new_from_gaps = 0
                for r in gap_results:
                    if r["url"] and r["url"] not in seen_urls:
                        seen_urls.add(r["url"])
                        results.append(r)
                        new_from_gaps += 1
                print(f"    {new_from_gaps} new results from {gap_search_count} gap-fill searches")

            if not results:
                print("    (no search results — skipping synthesis)")
                failed += 1
                continue

            # d. Synthesize
            briefing, usage = synthesize_briefing(
                anthropic_client, topic_name, archetype, results,
                previous_as_of, previous_gaps,
            )
            total_input_tokens += usage["input"]
            total_output_tokens += usage["output"]

            if briefing is None:
                failed += 1
                continue

            # e. Self-grade
            grade = grade_briefing(briefing)
            print(
                f"    Grade: {'PASS' if grade['all_pass'] else 'FAIL'} "
                f"(primary {grade['primary_ratio']:.0%})"
            )

            if not grade["all_pass"]:
                for reason in grade["failure_reasons"]:
                    print(f"      - {reason}")

            # f. Learning hints (if any metric failed)
            if not grade["all_pass"]:
                today_str = date.today().isoformat()
                new_hints = list(hints)  # copy current hints

                if not grade["primary_ratio_pass"]:
                    new_hints.append(
                        f"Run {today_str}: primary ratio "
                        f"{grade['primary_ratio']:.0%} < 30%. "
                        f"Add more site:.gov searches."
                    )
                if not grade["fabrication_pass"]:
                    new_hints.append(
                        f"Run {today_str}: empty URL found. "
                        f"Ensure all entries have source_url from Tavily."
                    )
                if not grade["staleness_pass"]:
                    as_of = briefing.get("as_of", "unknown")
                    new_hints.append(
                        f"Run {today_str}: as_of {as_of} is stale. "
                        f"Search for recent developments."
                    )

                try:
                    sb.table("threads").update({
                        "search_hints": new_hints,
                    }).eq("id", thread["id"]).execute()
                except Exception as error:
                    print(f"    (couldn't update search hints: {error})")

            # g. Write to Supabase
            row = {
                "thread_id": thread["id"],
                "slug": slug,
                "topic": thread["title"],
                "archetype": thread.get("archetype"),
                "status_line": briefing.get("status_line"),
                "as_of": briefing.get("as_of"),
                "background": briefing.get("background"),
                "what_changed": briefing.get("what_changed"),
                "next_decision_point": briefing.get("next_decision_point"),
                "official_record": briefing.get("official_record"),
                "coverage": briefing.get("coverage"),
                "analysis": briefing.get("analysis"),
                "gaps": briefing.get("gaps"),
                "sources_total": briefing.get("sources_total"),
                "primary_source_count": briefing.get("primary_source_count"),
                "generated_at": datetime.utcnow().isoformat(),
                "grade": grade,
            }

            try:
                sb.table("briefings").insert(row).execute()
            except Exception as error:
                print(f"    (couldn't save briefing: {error})")
                failed += 1
                continue

            if grade["all_pass"]:
                passed += 1
            else:
                failed += 1

        except Exception as error:
            print(f"    (thread failed — moving on: {error})")
            failed += 1

    # ---- 5. Cost report -------------------------------------------------------
    input_cost = (total_input_tokens / 1_000_000) * PRICE_PER_M_INPUT
    output_cost = (total_output_tokens / 1_000_000) * PRICE_PER_M_OUTPUT
    run_cost = input_cost + output_cost
    tavily_credits = total_searches * TAVILY_CREDITS_PER_ADVANCED

    # If we ran this once a day, every day, for a 30-day month:
    projected_monthly = run_cost * 30

    print("\n" + "=" * 56)
    print("  BRIEFING RUN COMPLETE")
    print("=" * 56)
    print(f"  Topics processed           : {n}")
    print(f"  Passed                     : {passed}")
    print(f"  Failed                     : {failed}")
    print("  " + "-" * 54)
    print(f"  Tavily searches            : {total_searches}")
    print(f"  Tavily credits used        : {tavily_credits}")
    print("  " + "-" * 54)
    print(f"  Haiku input tokens         : {total_input_tokens:,}")
    print(f"  Haiku output tokens        : {total_output_tokens:,}")
    print(f"  Estimated cost this run    : ${run_cost:.4f}")
    print(f"  Projected monthly (1x/day) : ${projected_monthly:.4f}")
    print("=" * 56)
    print()


if __name__ == "__main__":
    main()
