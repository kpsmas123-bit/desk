"""
run_ingest.py — run the whole sync in the right order, with one command.

    python3 ingest/run_ingest.py

It runs each step and keeps going even if one step has a hiccup, so a single
failing feed never blocks the rest. The steps, in order:

  1. openstates  — pull California legislative items
  2. rss         — pull the RSS feeds
  3. gdelt       — pull GDELT headlines
  4. enrich      — classify the new stories with the AI (relevance, summary, ...)
  5. build queries — for any subpage/thread you created in the website that is
                     still 'pending', write its search query once (the only AI
                     step in routing) and mark it 'tracking'.

Step 5 is what turns a brand-new thread from "pending" into a live, self-filling
layer. You'll see a line like "Built query for: <title>" for each one.
"""

import make_query


def _run(label, func):
    """Run one step, announce it, and never let it crash the whole sync."""
    print("\n" + "=" * 60)
    print(f"  STEP: {label}")
    print("=" * 60)
    try:
        func()
    except Exception as error:
        print(f"  ({label} didn't finish cleanly — moving on: {error})")


def main():
    # Import lazily so a missing optional dependency in one fetcher doesn't stop
    # the others from loading.
    import openstates
    import rss
    import gdelt
    import enrich

    _run("openstates (CA legislature)", openstates.main)
    _run("rss feeds", rss.main)
    _run("gdelt headlines", gdelt.main)
    _run("enrich (AI classify)", enrich.main)
    _run("build queries for pending subpages/threads", make_query.build_pending_queries)

    print("\n✅ Sync complete.\n")


if __name__ == "__main__":
    main()
