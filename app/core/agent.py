"""
app/core/agent.py
=================
PURPOSE:
    The agent brain. Runs the full ReAct loop for one email.
    Coordinates memory, LLM call, tool execution, and HITL gate.

CONCEPT — ReAct Pattern (Reason + Act):

    This is the most important pattern in agentic AI.
    Used in production systems at Google, Anthropic, OpenAI.

    BASIC LLM (not agentic):
        input → LLM → output
        One shot. No reasoning. No memory. No tools.

    ReAct (what this file implements):
        THINK:   Read the email. Check memory. What do I know?
        ACT:     Call the right tool with the right arguments.
        OBSERVE: What happened? Did the tool succeed?
        (repeat if needed — for Phase 1 we do one cycle)

    FULL FLOW FOR ONE EMAIL:
        1. Load sender memory  (THINK: what do I know about this sender?)
        2. Build LLM prompt    (THINK: give the LLM all context it needs)
        3. Call Claude API     (ACT:   LLM reads email + picks a tool)
        4. Parse response      (OBSERVE: which tool? what args? confidence?)
        5. HITL gate           (THINK: am I confident enough to act?)
        6. Execute tool        (ACT:   run the action)
        7. Update memory       (OBSERVE: record what happened)
        8. Return result dict  (caller saves to DB)

    INTERVIEW ANSWER:
        "Our agent uses the ReAct pattern. It reasons about each email
         using sender memory as context, calls the appropriate tool
         chosen by the LLM, observes the result, and records its
         full reasoning trace. Low-confidence decisions are routed
         to a human review queue instead of acting automatically."
"""

import logging
import time
import anthropic

from config import settings
from app.core.memory import SessionMemory, get_sender_context, record_decision
from app.core.tools import TOOL_SCHEMAS, TOOL_TO_CATEGORY, execute_tool
from app.core.hitl import should_act_automatically, flag_for_human_review

logger = logging.getLogger(__name__)

# One shared client — reused across all emails (more efficient than creating per-email)
_client = None

def _get_client() -> anthropic.Anthropic:
    """Lazy-initialize the Anthropic client. Reads API key from settings."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


# ── Prompt builders ───────────────────────────────────────────────

def _build_system_prompt(sender_context: dict) -> str:
    """
    Build the system prompt with sender memory injected.

    CONCEPT — Context Injection:
        We give the LLM everything it needs to reason well BEFORE it sees the email.
        Sender history = long-term memory surfaced into the prompt.
        This is what makes the agent smarter than a stateless classifier.

    The LLM reads this first, then reads the email, then picks a tool.
    """
    return f"""You are an intelligent email classification agent.

Your job:
1. Read the email carefully
2. Consider the sender history provided below
3. Choose exactly ONE tool to call
4. Include your confidence score (0.0 to 1.0) as an argument

SENDER HISTORY (your memory about this sender):
{sender_context['history_summary']}
Trust Score: {sender_context['trust_score']} (0.0 = untrusted, 1.0 = trusted)

CONFIDENCE GUIDE:
- 0.90 to 1.00 → Very clear from content. You are certain.
- 0.70 to 0.89 → Fairly confident. Minor ambiguity.
- 0.50 to 0.69 → Uncertain. Consider sender history heavily.
- Below 0.50   → Very unsure. When in doubt, lean toward Important.

RULES:
- Call EXACTLY ONE tool
- Always include confidence as a float argument in your tool call
- If sender history strongly matches a category, weight it heavily
- For ambiguous emails, prefer Important over ignoring"""


def _build_user_message(email: dict) -> str:
    """The email the LLM classifies. Capped at 800 chars to control token cost."""
    return f"""EMAIL TO CLASSIFY:

From: {email['sender']}
Subject: {email['subject']}
Body:
{email['body'][:800]}

Choose the appropriate tool and include your confidence score."""


# ── Core function ─────────────────────────────────────────────────

def process_email(email: dict) -> dict:
    """
    Run the full ReAct loop for one email.

    INPUT:  email dict with keys: message_id, sender, subject, body
    OUTPUT: result dict with everything needed to save to DB

    This function is called by main.py for every new email.
    It never crashes — all errors are caught and returned as results.
    """
    mem = SessionMemory()    # short-term memory for this email's reasoning
    start = time.time()      # track total processing time

    # ──────────────────────────────────────────────────────────────
    # THINK Step 1: Load long-term memory about this sender
    # ──────────────────────────────────────────────────────────────
    sender_context = get_sender_context(email["sender"])
    mem.think(f"Sender context: {sender_context['history_summary']}")

    # ──────────────────────────────────────────────────────────────
    # ACT Step 1: Call the LLM with tools + sender context
    # ──────────────────────────────────────────────────────────────
    mem.think(
        f"Calling Claude model={settings.MODEL} with "
        f"{len(TOOL_SCHEMAS)} tools available"
    )

    tool_name  = "log_important_email"         # safe default if LLM fails
    tool_args  = {"reason": "LLM error fallback", "confidence": 0.5}
    confidence = 0.5

    try:
        client = _get_client()

        response = client.messages.create(
            model=settings.MODEL,
            max_tokens=300,          # tools only need ~100 tokens back — keep it cheap
            system=_build_system_prompt(sender_context),
            tools=TOOL_SCHEMAS,      # the LLM's tool menu
            messages=[{
                "role": "user",
                "content": _build_user_message(email)
            }],
        )

        # ──────────────────────────────────────────────────────────
        # OBSERVE Step 1: Parse what the LLM decided
        # ──────────────────────────────────────────────────────────
        tool_name, tool_args, confidence = _parse_response(response)
        category = TOOL_TO_CATEGORY.get(tool_name, "Important")

        mem.observe(
            f"LLM chose tool={tool_name} "
            f"confidence={confidence:.2f} "
            f"tokens_in={response.usage.input_tokens} "
            f"tokens_out={response.usage.output_tokens}"
        )

    except anthropic.APIError as e:
        logger.error("Anthropic API error: %s", e)
        mem.observe(f"LLM call FAILED: {e} — using safe default (Important)")
        category = "Important"

    except Exception as e:
        logger.error("Unexpected error calling LLM: %s", e, exc_info=True)
        mem.observe(f"Unexpected error: {e} — using safe default (Important)")
        category = "Important"

    # ──────────────────────────────────────────────────────────────
    # THINK Step 2: Should I act or ask a human?
    # ──────────────────────────────────────────────────────────────
    if should_act_automatically(confidence):
        mem.think(
            f"Confidence {confidence:.0%} ≥ threshold {settings.CONFIDENCE_THRESHOLD:.0%} "
            f"→ acting automatically"
        )

        # ──────────────────────────────────────────────────────────
        # ACT Step 2: Execute the tool the LLM chose
        # ──────────────────────────────────────────────────────────
        mem.act(f"Executing {tool_name}({tool_args})")
        action_taken = execute_tool(tool_name, email, tool_args)

        # ──────────────────────────────────────────────────────────
        # OBSERVE Step 2: What happened?
        # ──────────────────────────────────────────────────────────
        mem.observe(f"Tool result: {action_taken}")

    else:
        # Confidence below threshold → don't act, ask a human
        mem.think(
            f"Confidence {confidence:.0%} < threshold {settings.CONFIDENCE_THRESHOLD:.0%} "
            f"→ flagging for human review"
        )
        action_taken = flag_for_human_review(email, category, confidence)
        mem.observe("Email added to HITL queue — human review required")

    # ──────────────────────────────────────────────────────────────
    # Update long-term memory with this decision
    # ──────────────────────────────────────────────────────────────
    record_decision(email["sender"], category)

    latency_ms = int((time.time() - start) * 1000)

    logger.info(
        "✅ DONE | Subject='%s' | Category=%s | Conf=%.0f%% | "
        "Action=%s | %dms",
        email["subject"][:40],
        category,
        confidence * 100,
        action_taken[:35],
        latency_ms,
    )

    return {
        "category":      category,
        "confidence":    confidence,
        "action_taken":  action_taken,
        "reasoning":     mem.get_trace(),
        "model_used":    settings.MODEL,
        "latency_ms":    latency_ms,
    }


# ── Response parser ───────────────────────────────────────────────

def _parse_response(response) -> tuple[str, dict, float]:
    """
    Extract tool_name, tool_args, and confidence from Claude's API response.

    Claude returns a list of content blocks. We look for a "tool_use" block.
    That block has:
        .name  → the tool Claude chose (e.g. "create_support_ticket")
        .input → the arguments dict Claude populated

    Confidence is embedded in the args because we asked for it in TOOL_SCHEMAS.
    Falls back to safe defaults if parsing fails.
    """
    tool_name  = "log_important_email"
    tool_args  = {"reason": "Could not parse LLM response", "confidence": 0.5}
    confidence = 0.5

    for block in response.content:
        if block.type == "tool_use":
            tool_name = block.name
            tool_args = dict(block.input)

            # Extract confidence from args (we put it there via TOOL_SCHEMAS)
            raw_conf = tool_args.get("confidence", 0.5)
            try:
                confidence = float(raw_conf)
                # Clamp to valid range
                confidence = max(0.0, min(1.0, confidence))
            except (TypeError, ValueError):
                confidence = 0.5

            break   # we only expect one tool_use block per response

    return tool_name, tool_args, confidence
