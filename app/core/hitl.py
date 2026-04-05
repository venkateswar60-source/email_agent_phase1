"""
app/core/hitl.py
================
PURPOSE:
    Decide whether the agent should act automatically
    OR send the email to a human for review.

CONCEPT — Human-in-the-Loop (HITL):

    A pure automation system acts on EVERYTHING.
    An agentic system knows its own limitations.

    REAL WORLD ANALOGY:
        A new employee doesn't delete all emails on their own.
        When unsure: "I'm not confident — let me check with my manager first."

    HOW IT WORKS HERE:
        The LLM returns a confidence score (0.0 to 1.0) with every decision.
        We compare it to CONFIDENCE_THRESHOLD (default: 0.70 from .env).

        confidence ≥ 0.70  →  Agent acts automatically
        confidence < 0.70  →  Email goes to hitl_queue table in DB

    WHY THIS IS IMPORTANT IN INTERVIEWS:
        "Agents should not blindly act on uncertain decisions.
         We use confidence thresholding to gate automated actions.
         This prevents costly mistakes — a misclassified Action email
         that gets ignored could mean a missed contract signing."

    HITL RATE is a key operational metric:
        If HITL rate is too high (>30%) → model needs improvement
        If HITL rate is too low (<2%)   → threshold might be too lenient
        Healthy range: 5–15%
"""

import logging
from app.db import add_to_hitl_queue, get_pending_hitl
from config import settings

logger = logging.getLogger(__name__)


def should_act_automatically(confidence: float) -> bool:
    """
    Single function that makes the HITL gate decision.

    SINGLE RESPONSIBILITY PRINCIPLE:
        Everything that needs to ask "should we act?" calls this one function.
        The threshold lives in config/settings.py — never hardcoded here.
        To change the threshold: update .env, restart. No code change needed.

    Returns:
        True  → confidence is high enough, agent acts
        False → confidence too low, send to human review
    """
    return confidence >= settings.CONFIDENCE_THRESHOLD


def flag_for_human_review(
    email: dict, agent_category: str, confidence: float
) -> str:
    """
    Route a low-confidence email to the human review queue.

    WHAT HAPPENS NEXT:
        The email sits in the hitl_queue table as status='pending'.
        A human (or a future dashboard) reads it, decides the correct category,
        and marks it as reviewed.
        In production: this would trigger a Slack alert to the team.

    Returns: action string for DB storage (shows up in emails.action_taken)
    """
    add_to_hitl_queue(
        message_id=email["message_id"],
        sender=email["sender"],
        subject=email["subject"],
        agent_category=agent_category,
        confidence=confidence,
    )

    logger.warning(
        "🔍 HITL FLAGGED | Subject: '%s' | "
        "Agent guessed: %s | Confidence: %.0f%% (threshold: %.0f%%) "
        "→ Sent to human review queue",
        email["subject"],
        agent_category,
        confidence * 100,
        settings.CONFIDENCE_THRESHOLD * 100,
    )

    return (
        f"hitl_flagged | "
        f"agent_guess={agent_category} | "
        f"confidence={confidence:.2f}"
    )


def log_hitl_summary() -> None:
    """
    Print a summary of emails waiting for human review.
    Called at the end of each polling pass in main.py.

    In production: this data feeds a dashboard or Slack digest.
    """
    pending = get_pending_hitl()

    if not pending:
        logger.info("HITL Queue: empty ✓")
        return

    logger.warning("=" * 55)
    logger.warning("🔍 HITL QUEUE — %d email(s) need human review:", len(pending))
    for item in pending:
        logger.warning(
            "  From: %-30s | Guess: %-10s | Conf: %.0f%%",
            item["sender"][:30],
            item["agent_category"],
            item["confidence"] * 100,
        )
    logger.warning("=" * 55)
