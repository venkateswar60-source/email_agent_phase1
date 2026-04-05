"""
app/core/memory.py
==================
PURPOSE:
    Give the agent memory so it learns from past emails.
    Two types of memory — short-term and long-term.

CONCEPT — Why Memory Matters:

    WITHOUT memory (basic classifier):
        Email 1 from spam@bad.com → classify → "Spam"
        Email 2 from spam@bad.com → classify → "Spam" (starts from zero again)
        Email 9 from spam@bad.com → classify → "Spam" (still no learning)

        Every email is a cold start. No intelligence accumulates.

    WITH memory (agentic):
        Email 1 from spam@bad.com → classify → "Spam" → record in DB
        Email 2 from spam@bad.com → "I've seen this sender before, they send spam"
        Email 9 from spam@bad.com → trust_score=0.1, 8 spam emails → skip LLM entirely

        The agent builds intuition. It gets smarter with each email.

TWO TYPES OF MEMORY:

    LONG-TERM (persisted in SQLite via db.py):
        Lives across restarts. Built up over days/weeks.
        Stored per sender: email_count, spam_count, trust_score, etc.
        "I know this sender. Here's their history."

    SHORT-TERM (in-memory Python object, one per email):
        Lives only during processing ONE email.
        Like a notepad — you write thoughts, actions, observations.
        When the email is done, the notepad is thrown away.
        The important conclusion goes into long-term memory (DB).
        This is the ReAct reasoning trace.
"""

import logging
from app.db import get_sender_memory, update_sender_memory

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# LONG-TERM MEMORY
# ══════════════════════════════════════════════════════════════════

def get_sender_context(sender: str) -> dict:
    """
    Look up the agent's long-term memory for this sender.

    Returns a context dict that gets injected into the LLM prompt.
    The LLM reads this BEFORE deciding what to do with the email.

    CONCEPT — Perception step:
        The agent observes its environment (past experience with this sender)
        before deciding what action to take. This is why it's smarter than
        a basic classifier — it has context, not just the current email.

    If first time seeing this sender → returns neutral "unknown" context.
    """
    memory = get_sender_memory(sender)

    if memory is None:
        # First email from this sender — no prior context
        return {
            "known_sender":    False,
            "email_count":     0,
            "dominant_type":   "unknown",
            "trust_score":     0.5,          # neutral — neither trusted nor untrusted
            "history_summary": "First email from this sender. No prior history.",
        }

    # Find which category this sender sends most often
    counts = {
        "Spam":      memory["spam_count"],
        "Sales":     memory["sales_count"],
        "Support":   memory["support_count"],
        "Important": memory["important_count"],
        "Action":    memory["action_count"],
    }
    dominant_type = max(counts, key=counts.get)
    total = memory["email_count"]

    return {
        "known_sender":    True,
        "email_count":     total,
        "dominant_type":   dominant_type,
        "trust_score":     memory["trust_score"],
        "history_summary": (
            f"Seen {total} email(s) from this sender. "
            f"Mostly {dominant_type} ({counts[dominant_type]}/{total}). "
            f"Trust score: {memory['trust_score']:.2f} "
            f"(0=untrusted, 1=trusted)."
        ),
    }


def record_decision(sender: str, category: str) -> None:
    """
    After processing an email, update the sender's long-term memory.

    CONCEPT — Learning step:
        The agent doesn't just classify and forget.
        After every email it updates its knowledge about the sender.
        Over time: trust_score drifts up for good senders, down for spammers.

    Called at the END of process_email() in agent.py.
    """
    try:
        update_sender_memory(sender, category)
        logger.debug("Memory updated: sender=%s category=%s", sender[:30], category)
    except Exception as e:
        logger.error("Failed to record decision in memory: %s", e)


# ══════════════════════════════════════════════════════════════════
# SHORT-TERM MEMORY (Session / Working Memory)
# ══════════════════════════════════════════════════════════════════

class SessionMemory:
    """
    Working memory for ONE email processing session.

    CONCEPT — ReAct Reasoning Trace:
        Every thought, action, and observation the agent makes
        during one email gets recorded here.

        THINK:   "Sender has trust_score=0.1, 8 previous spam emails"
        ACT:     "Calling ignore_spam tool"
        OBSERVE: "Tool returned: spam_ignored"

        At the end, get_trace() returns the full chain as a string.
        That string is stored in the DB so you can replay exactly
        what the agent was thinking for any email.

    LIFECYCLE:
        Created → at start of process_email()
        Used    → throughout the ReAct loop
        Dumped  → get_trace() called at end, then object discarded
    """

    def __init__(self):
        self.thoughts:     list[str] = []   # THINK steps
        self.actions:      list[str] = []   # ACT steps
        self.observations: list[str] = []   # OBSERVE steps
        self.step:         int = 0          # current step counter

    def think(self, thought: str) -> None:
        """Record an agent reasoning step (THINK in ReAct)."""
        self.step += 1
        entry = f"[{self.step}] THINK: {thought}"
        self.thoughts.append(entry)
        logger.debug(entry)

    def act(self, action: str) -> None:
        """Record a tool call decision (ACT in ReAct)."""
        entry = f"[{self.step}] ACT: {action}"
        self.actions.append(entry)
        logger.debug(entry)

    def observe(self, observation: str) -> None:
        """Record the result of a tool call (OBSERVE in ReAct)."""
        entry = f"[{self.step}] OBSERVE: {observation}"
        self.observations.append(entry)
        logger.debug(entry)

    def get_trace(self) -> str:
        """
        Combine all steps into a single string for DB storage.
        Stored in emails.reasoning column.
        Lets you replay the agent's full thought process later.
        """
        all_steps = self.thoughts + self.actions + self.observations
        # Sort by step number so they appear in order
        all_steps.sort(key=lambda s: int(s.split("]")[0].replace("[", "")))
        return " | ".join(all_steps)

    def get_last_thought(self) -> str:
        """Return the most recent reasoning step (for DB reasoning column)."""
        return self.thoughts[-1] if self.thoughts else "No reasoning recorded"
