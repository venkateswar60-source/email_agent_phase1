"""
config/settings.py
==================
PURPOSE:
    Single source of truth for ALL configuration values.
    Every file in this project imports from here.
    Nothing is hardcoded anywhere else.

WHY THIS MATTERS:
    Bad:  os.getenv("ANTHROPIC_API_KEY") scattered across 10 files
          → change one thing, hunt through 10 files
    Good: import from config.settings
          → change one thing, everything updates

CONCEPT — Fail Fast:
    validate() runs at startup and crashes immediately if .env is missing keys.
    Better to crash with a clear message than to fail silently mid-processing.
"""

import os
import pathlib
from dotenv import load_dotenv

# Load .env FIRST — every os.getenv() call below reads from it
load_dotenv()

# ── Email credentials ─────────────────────────────────────────────
EMAIL_ADDRESS  = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
IMAP_SERVER    = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT      = int(os.getenv("IMAP_PORT", 993))
SMTP_SERVER    = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", 465))

# ── LLM ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Cheapest Claude model — perfect for classification tasks
# Phase 2 will add a second model for complex emails
MODEL = "claude-haiku-4-5-20251001"

# ── Behaviour ─────────────────────────────────────────────────────
POLL_INTERVAL        = int(os.getenv("POLL_INTERVAL", 300))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", 0.70))

# ── Paths ─────────────────────────────────────────────────────────
ROOT_DIR = pathlib.Path(__file__).parent.parent   # project root folder
DB_PATH  = ROOT_DIR / "data" / "emails.db"
LOG_PATH = ROOT_DIR / "logs" / "app.log"

# ── Valid categories ──────────────────────────────────────────────
# One definition, used by tools.py, agent.py, and tests
VALID_CATEGORIES = {"Important", "Spam", "Support", "Sales", "Action"}


def validate() -> list[str]:
    """
    Check that all required .env values are present.
    Returns a list of error strings. Empty list = all good.

    Called once at startup in main.py before anything runs.
    """
    errors = []
    if not EMAIL_ADDRESS:
        errors.append("EMAIL_ADDRESS is not set in .env")
    if not EMAIL_PASSWORD:
        errors.append("EMAIL_PASSWORD is not set in .env")
    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY is not set in .env")
    return errors
