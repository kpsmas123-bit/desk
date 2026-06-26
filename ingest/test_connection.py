"""
test_connection.py — a quick check that your keys work and the database is reachable.

Run this AFTER you have:
  1. Filled in your .env file (see SETUP.md, step 2), and
  2. Created the tables in Supabase (see SETUP.md, step 5).

How to run it:
  - Open the Terminal app.
  - Go into this project folder, then run:

        python ingest/test_connection.py

  If everything is set up correctly, you'll see a friendly "Connected" message.
  If something is wrong, you'll see a plain-English explanation of what to fix.
"""

# We reuse the existing key-loader in config.py so all the .env logic lives in one place.
from config import get

# The Supabase library lets Python talk to your database.
from supabase import create_client


def main():
    # 1. Read the two values we need out of the .env file.
    url = get("SUPABASE_URL")
    publishable_key = get("SUPABASE_PUBLISHABLE_KEY")

    # 2. Make sure they aren't blank. If they are, the .env file isn't filled in.
    if not url or not publishable_key:
        print(
            "\n  I couldn't find your Supabase keys.\n"
            "  Please open your .env file and make sure these two lines have values\n"
            "  after the '=' sign:\n"
            "      SUPABASE_URL=...\n"
            "      SUPABASE_PUBLISHABLE_KEY=...\n"
            "  (See SETUP.md, step 2, if you're not sure how.)\n"
        )
        return

    # 3. Connect to Supabase using the PUBLISHABLE (public) key.
    try:
        supabase = create_client(url, publishable_key)
    except Exception as error:
        print(
            "\n  I couldn't connect to Supabase.\n"
            "  Double-check that SUPABASE_URL in your .env file is correct\n"
            "  (it should look like https://something.supabase.co).\n"
            f"\n  Technical detail: {error}\n"
        )
        return

    # 4. Try to read the 'pages' table.
    try:
        result = supabase.table("pages").select("*").execute()
    except Exception as error:
        print(
            "\n  I connected, but couldn't read the 'pages' table.\n"
            "  The most likely reason is that the tables haven't been created yet.\n"
            "  Please follow SETUP.md, step 5, to run the database schema, then try again.\n"
            f"\n  Technical detail: {error}\n"
        )
        return

    # 5. Success — tell the user how many pages we found.
    pages = result.data or []
    print(f"\n  Connected — found {len(pages)} pages.\n")


if __name__ == "__main__":
    main()
