"""
config.py — loads your secret keys from the .env file.

Other scripts in this project import this file to read a key by name, like:

    from config import get

    api_key = get("OPENSTATES_API_KEY")

This keeps all the secret-loading logic in one place.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Figure out where this project lives, then point to the .env file
# that should sit next to this folder (one level up from /ingest).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Load whatever values are in .env into the program's environment.
# If there is no .env file, load_dotenv simply does nothing — and that's fine,
# because get() below will then read the real environment variables instead.
# (This is what makes the same scripts work later inside GitHub Actions, where
# there is no .env file and the keys are provided as environment variables.)
load_dotenv(ENV_FILE)

# Only show the "no .env" reminder when we're clearly running on someone's own
# computer with no keys set up yet. If the keys are already in the environment
# (for example, on GitHub Actions), we stay quiet so we don't clutter the logs.
if not ENV_FILE.exists() and not os.getenv("SUPABASE_URL"):
    print(
        "\n  Heads up: I couldn't find a .env file.\n"
        "  Please copy .env.example to a new file called .env and fill in your keys.\n"
        f"  Expected location: {ENV_FILE}\n"
    )


def get(name: str) -> str:
    """Return the value of one key from .env by its name (e.g. "ANTHROPIC_API_KEY")."""
    return os.getenv(name, "")
