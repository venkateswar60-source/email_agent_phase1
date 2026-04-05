"""
main.py
=======
PURPOSE:
    Entry point. Wires all modules together into one pipeline.
    Runs the polling loop that checks the inbox on a schedule.

FLOW (every POLL_INTERVAL seconds):
    1. Fetch unseen emails (real Gmail or dummy data)
    2. For each email:
        a. Check DB — already processed? Skip.
        b. Run agent ReAct loop → get result
        c. Save result to DB
    3. Log HITL queue summary
    4. Sleep → repeat

HOW TO RUN:
    python main.py               ← real Gmail, continuous polling
    python main.py --dummy       ← 5 dummy emails, continuous polling
    python main.py --dummy --once ← 5 dummy emails, process once, exit (best for testing)
    python main.py --once        ← real Gmail, process once, exit

ARGUMENTS:
    --dummy   use hardcoded test emails instead of Gmail
    --once    process one pass then exit (no infinite loop)
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
import logging
import time
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Load .env BEFORE anything else reads environment variables
load_dotenv()

# ── Logging setup ─────────────────────────────────────────────────
# Must happen BEFORE importing our own modules (they log at import time)
Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),               # → your terminal
        logging.FileHandler("logs/app.log", mode="a"),   # → logs/app.log (append)
    ],
)
logger = logging.getLogger(__name__)

# ── Import our modules AFTER logging is configured ────────────────
from config import settings
from app.db import init_db, email_already_processed, save_email
from app.ingestion.fetcher import fetch_unseen_emails, get_dummy_emails
from app.core.agent import process_email
from app.core.hitl import log_hitl_summary


# ── CLI arguments ─────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Phase 1 — Agentic Email Classifier")
    parser.add_argument(
        "--dummy", action="store_true",
        help="Use 5 hardcoded dummy emails instead of real Gmail"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Process one inbox pass then exit (no loop)"
    )
    return parser.parse_args()


# ── Core pipeline ─────────────────────────────────────────────────

def process_inbox(use_dummy: bool = False) -> int:
    """
    One full pass through the inbox.

    Fetches emails → dedup check → agent loop → save to DB.
    Returns number of emails actually processed (not skipped).

    Called by the polling loop in main().
    """
    # Step 1: Get emails (real or dummy)
    emails = get_dummy_emails() if use_dummy else fetch_unseen_emails()

    if not emails:
        logger.info("No new emails.")
        return 0

    processed = 0

    for email in emails:
        msg_id = email["message_id"]

        # Step 2: Dedup — skip if already in DB
        if email_already_processed(msg_id):
            logger.debug("Already processed — skipping: %s", msg_id[:30])
            continue

        logger.info("─" * 50)
        logger.info(
            "📨 New email | From: %s | Subject: %s",
            email["sender"][:35], email["subject"][:40]
        )

        # Step 3: Run the agent ReAct loop
        try:
            result = process_email(email)
        except Exception as e:
            # Catch-all: agent crashed on this email
            # We still save it so we have a record — use safe defaults
            logger.error("Agent crashed on %s: %s", msg_id[:30], e, exc_info=True)
            result = {
                "category":     "Important",   # never silently lose an email
                "confidence":   0.0,
                "action_taken": f"agent_error | {e}",
                "reasoning":    f"Agent crashed: {e}",
                "model_used":   "none",
                "latency_ms":   0,
            }

        # Step 4: Save everything to DB
        save_email(
            message_id=msg_id,
            sender=email["sender"],
            subject=email["subject"],
            body=email["body"],
            category=result["category"],
            confidence=result["confidence"],
            action_taken=result["action_taken"],
            reasoning=result["reasoning"],
            model_used=result["model_used"],
        )

        processed += 1

    return processed


# ── Main entry point ──────────────────────────────────────────────

def main():
    args = parse_args()

    logger.info("=" * 55)
    logger.info("🤖 AGENTIC EMAIL CLASSIFIER — Phase 1")
    logger.info("Mode   : %s", "DUMMY EMAILS" if args.dummy else "GMAIL")
    logger.info("Loop   : %s", "ONE PASS" if args.once else "CONTINUOUS")
    logger.info("Model  : %s", settings.MODEL)
    logger.info("HITL   : confidence threshold = %.0f%%", settings.CONFIDENCE_THRESHOLD * 100)
    logger.info("=" * 55)

    # Validate .env (only needed for real Gmail mode)
    if not args.dummy:
        errors = settings.validate()
        if errors:
            for err in errors:
                logger.error("CONFIG ERROR: %s", err)
            logger.error("Fix your .env file and restart.")
            sys.exit(1)

    # Create DB tables (safe to run every startup)
    init_db()

    if args.once:
        # Single pass — good for testing
        count = process_inbox(use_dummy=args.dummy)
        logger.info("=" * 55)
        logger.info("Pass complete — %d email(s) processed", count)
        log_hitl_summary()
        logger.info("Done. Exiting.")
        return

    # Continuous polling loop
    logger.info("Polling every %d seconds. Press Ctrl+C to stop.", settings.POLL_INTERVAL)

    while True:
        try:
            logger.info("─── Checking inbox ─────────────────────────────")
            count = process_inbox(use_dummy=args.dummy)
            logger.info("Pass complete — %d email(s) processed", count)
            log_hitl_summary()

        except KeyboardInterrupt:
            logger.info("Stopped by user (Ctrl+C).")
            break

        except Exception as e:
            # Never let the polling loop die from one bad pass
            logger.error("Polling loop error: %s", e, exc_info=True)

        logger.info("Sleeping %d seconds...", settings.POLL_INTERVAL)
        time.sleep(settings.POLL_INTERVAL)


if __name__ == "__main__":
    main()
