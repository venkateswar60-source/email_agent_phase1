"""
app/db.py
=========
PURPOSE:
    All database logic in one place.
    Every read and write to SQLite goes through this file.
    No other file touches the DB directly.

WHY SQLITE:
    - Zero setup. No server. Just a file.
    - Built into Python (no pip install needed).
    - Perfect for local development and single-server deploys.
    - In Phase 3, you can swap this file for PostgreSQL
      without touching any other file.

PHASE 1 TABLES:
    1. emails        → every processed email + what the agent decided
    2. sender_memory → agent's long-term memory about each sender
    3. hitl_queue    → low-confidence emails waiting for human review

CONCEPT — Single Responsibility:
    This file does ONE thing: talk to the database.
    It does not classify, it does not send emails, it does not log.
    Just read and write.
"""

import sqlite3
import logging
from config import settings

logger = logging.getLogger(__name__)


# ── Connection ────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """
    Open the SQLite database file and return a connection object.

    settings.DB_PATH → data/emails.db (defined in config/settings.py)
    DB_PATH.parent.mkdir → creates the /data folder if it doesn't exist yet
    conn.row_factory = sqlite3.Row → rows returned as dict-like objects
                                     so you can do row["sender"] instead of row[0]
    """
    settings.DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Table creation ────────────────────────────────────────────────

def init_db() -> None:
    """
    Create all tables at startup if they don't exist yet.

    IF NOT EXISTS → safe to call every time the app starts.
    Nothing happens if tables are already there.

    Called once in main.py before the polling loop begins.
    """
    conn = get_connection()
    try:
        # Table 1: Core email record
        # Every email the agent processes gets one row here.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emails (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id   TEXT UNIQUE,     -- RFC email ID, used as dedup key
                sender       TEXT,
                subject      TEXT,
                body         TEXT,
                category     TEXT,            -- Important/Spam/Support/Sales/Action
                confidence   REAL,            -- 0.0 to 1.0 from the LLM
                action_taken TEXT,            -- what the agent actually did
                reasoning    TEXT,            -- WHY the agent made this decision
                model_used   TEXT,            -- which Claude model was called
                processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 2: Sender memory (agent's long-term memory)
        # CONCEPT: Instead of treating every email in isolation,
        # the agent builds a profile of each sender over time.
        # "sales@vendor.com has sent 8 sales emails — trust_score = 0.1"
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sender_memory (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sender          TEXT UNIQUE,        -- one row per email address
                email_count     INTEGER DEFAULT 1,  -- total emails from this sender
                spam_count      INTEGER DEFAULT 0,
                support_count   INTEGER DEFAULT 0,
                sales_count     INTEGER DEFAULT 0,
                important_count INTEGER DEFAULT 0,
                action_count    INTEGER DEFAULT 0,
                trust_score     REAL DEFAULT 0.5,   -- 0.0 = untrusted, 1.0 = trusted
                last_seen       DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Table 3: Human-in-the-Loop queue
        # CONCEPT: When the agent is not confident enough to act,
        # it puts the email here for a human to review instead.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS hitl_queue (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id     TEXT,
                sender         TEXT,
                subject        TEXT,
                agent_category TEXT,        -- what the agent guessed
                confidence     REAL,        -- why it was flagged (too low)
                status         TEXT DEFAULT 'pending',  -- pending or reviewed
                created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        logger.info("Database ready → %s", settings.DB_PATH)

    except Exception as e:
        logger.error("DB init failed: %s", e)
        raise   # re-raise so main.py knows startup failed
    finally:
        conn.close()   # always close the connection


# ── Email helpers ─────────────────────────────────────────────────

def email_already_processed(message_id: str) -> bool:
    """
    Check if we have already processed this email.

    WHY: IMAP's UNSEEN flag can reset if the connection drops.
         We use our own DB as the dedup source of truth.
         message_id is the RFC standard unique ID for every email.

    Returns True  → skip this email, we already handled it
    Returns False → new email, process it
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM emails WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def save_email(
    message_id: str, sender: str, subject: str, body: str,
    category: str, confidence: float, action_taken: str,
    reasoning: str, model_used: str
) -> None:
    """
    Insert one processed email into the database.

    INSERT OR IGNORE → if message_id already exists (race condition),
                       silently skip. Never duplicate.
    """
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO emails
                (message_id, sender, subject, body, category,
                 confidence, action_taken, reasoning, model_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (message_id, sender, subject, body, category,
              confidence, action_taken, reasoning, model_used))
        conn.commit()
        logger.debug("Saved: %s → category=%s conf=%.2f", message_id[:30], category, confidence)
    except Exception as e:
        logger.error("save_email failed: %s", e)
    finally:
        conn.close()


# ── Sender memory helpers ─────────────────────────────────────────

def get_sender_memory(sender: str) -> dict | None:
    """
    Retrieve everything we know about this sender.
    Returns None if this is the first email from them.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM sender_memory WHERE sender = ?", (sender,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_sender_memory(sender: str, category: str) -> None:
    """
    Update or create the sender's memory profile after processing their email.

    UPSERT LOGIC:
        First email from sender → INSERT a new row
        Repeat sender          → UPDATE their counts

    trust_score is recalculated each time based on their history:
        Mostly spam (>50%)      → 0.1  (low trust)
        Mostly important/action → 0.9  (high trust)
        Mixed                   → 0.5  (neutral)
    """
    # Map category name to the column we need to increment
    col_map = {
        "Spam":      "spam_count",
        "Support":   "support_count",
        "Sales":     "sales_count",
        "Important": "important_count",
        "Action":    "action_count",
    }
    count_col = col_map.get(category, "important_count")

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id, email_count, spam_count, important_count, action_count "
            "FROM sender_memory WHERE sender = ?",
            (sender,)
        ).fetchone()

        if existing:
            # Sender seen before — increment their counters
            conn.execute(f"""
                UPDATE sender_memory
                SET
                    email_count     = email_count + 1,
                    {count_col}     = {count_col} + 1,
                    last_seen       = CURRENT_TIMESTAMP,
                    trust_score     = CASE
                        WHEN (spam_count + 1.0) / (email_count + 1) > 0.5  THEN 0.1
                        WHEN (important_count + action_count + 1.0) / (email_count + 1) > 0.5 THEN 0.9
                        ELSE 0.5
                    END
                WHERE sender = ?
            """, (sender,))
        else:
            # First email from this sender — create their profile
            conn.execute(f"""
                INSERT INTO sender_memory (sender, {count_col})
                VALUES (?, 1)
            """, (sender,))

        conn.commit()
        logger.debug("Memory updated: sender=%s category=%s", sender[:30], category)

    except Exception as e:
        logger.error("update_sender_memory failed for %s: %s", sender, e)
    finally:
        conn.close()


# ── HITL helpers ──────────────────────────────────────────────────

def add_to_hitl_queue(
    message_id: str, sender: str, subject: str,
    agent_category: str, confidence: float
) -> None:
    """
    Add an email to the human review queue.
    Called when agent confidence is below CONFIDENCE_THRESHOLD.
    """
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO hitl_queue
                (message_id, sender, subject, agent_category, confidence)
            VALUES (?, ?, ?, ?, ?)
        """, (message_id, sender, subject, agent_category, confidence))
        conn.commit()
    except Exception as e:
        logger.error("add_to_hitl_queue failed: %s", e)
    finally:
        conn.close()


def get_pending_hitl() -> list[dict]:
    """Return all emails still waiting for human review."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM hitl_queue WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
