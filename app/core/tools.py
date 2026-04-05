"""
app/core/tools.py
=================
PURPOSE:
    Define the 5 actions the agent can take as "tools".
    The LLM reads these definitions and DECIDES which one to call.

THE CORE AGENTIC CONCEPT — Tool Use:

    OLD WAY (not agentic):
        category = classify(email)      # LLM returns "Sales"
        if category == "Sales":
            send_reply(email)           # Python hardcodes the action
        elif category == "Support":
            create_ticket(email)

        Problem: LLM only classifies. Python makes the decision.
        The LLM has no autonomy.

    AGENTIC WAY (this file):
        The LLM sees a MENU of tools it can call.
        The LLM decides WHICH tool AND with what arguments.
        Python just executes whatever the LLM decided.

        "I see this is a support email with a login issue.
         I will call create_support_ticket with priority=high
         because the user is completely locked out."

        Now the LLM has autonomy over its actions AND its reasoning.

TWO PARTS IN THIS FILE:
    1. TOOL_SCHEMAS   → JSON descriptions sent to Claude (what it reads)
    2. Executor funcs → Python functions that actually run the action
"""

import logging
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

from config import settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# PART 1: TOOL SCHEMAS
# These are sent to Claude in the API call.
# Claude reads the "description" to decide WHEN to use each tool.
# Claude reads "input_schema" to know WHAT arguments to provide.
# ══════════════════════════════════════════════════════════════════

TOOL_SCHEMAS = [
    {
        "name": "log_important_email",
        "description": (
            "Use this for personal emails, urgent messages from known contacts, "
            "or anything that genuinely requires human attention but no automated response."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why this email is important — one clear sentence."
                },
                "confidence": {
                    "type": "number",
                    "description": "Your confidence score from 0.0 to 1.0"
                }
            },
            "required": ["reason", "confidence"]
        }
    },
    {
        "name": "create_support_ticket",
        "description": (
            "Use this when a user is asking for help, reporting a bug, "
            "can't access their account, or having trouble with a product or service."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_summary": {
                    "type": "string",
                    "description": "One sentence describing the user's problem."
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Ticket priority. Use high if user is fully blocked."
                },
                "confidence": {
                    "type": "number",
                    "description": "Your confidence score from 0.0 to 1.0"
                }
            },
            "required": ["issue_summary", "priority", "confidence"]
        }
    },
    {
        "name": "send_sales_reply",
        "description": (
            "Use this when someone is pitching a product, requesting a partnership, "
            "or doing unsolicited cold outreach trying to sell something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reply_tone": {
                    "type": "string",
                    "enum": ["polite_decline", "interested", "request_more_info"],
                    "description": "How to reply based on whether the pitch seems relevant."
                },
                "confidence": {
                    "type": "number",
                    "description": "Your confidence score from 0.0 to 1.0"
                }
            },
            "required": ["reply_tone", "confidence"]
        }
    },
    {
        "name": "ignore_spam",
        "description": (
            "Use this for obvious spam, scams, unsolicited bulk mail, "
            "fake prize notifications, or phishing attempts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spam_signals": {
                    "type": "string",
                    "description": "What specific signals identified this as spam."
                },
                "confidence": {
                    "type": "number",
                    "description": "Your confidence score from 0.0 to 1.0"
                }
            },
            "required": ["spam_signals", "confidence"]
        }
    },
    {
        "name": "notify_action_required",
        "description": (
            "Use this when the recipient must DO something: approve, sign, "
            "schedule, respond to a deadline, or complete a specific task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_needed": {
                    "type": "string",
                    "description": "Exactly what the recipient needs to do."
                },
                "deadline": {
                    "type": "string",
                    "description": "Deadline if mentioned, otherwise 'not specified'."
                },
                "confidence": {
                    "type": "number",
                    "description": "Your confidence score from 0.0 to 1.0"
                }
            },
            "required": ["action_needed", "deadline", "confidence"]
        }
    },
]


# ── Tool name → category name ─────────────────────────────────────
# Used to derive the category string after the LLM picks a tool.
TOOL_TO_CATEGORY = {
    "log_important_email":    "Important",
    "create_support_ticket":  "Support",
    "send_sales_reply":       "Sales",
    "ignore_spam":            "Spam",
    "notify_action_required": "Action",
}


# ══════════════════════════════════════════════════════════════════
# PART 2: TOOL EXECUTORS
# These are the actual Python functions that run after the LLM
# decides which tool to call.
# Each function receives the email dict and the LLM's arguments.
# Each function returns a short string describing what it did.
# That string gets stored in the DB as action_taken.
# ══════════════════════════════════════════════════════════════════

def log_important_email(email: dict, reason: str, confidence: float) -> str:
    """
    Action: Log at WARNING level so it stands out in the logs.

    In production this would also:
    → Send a Slack notification
    → Send an SMS alert
    → Push to a dashboard

    For Phase 1: a visible WARNING log is enough.
    """
    logger.warning(
        "⭐ IMPORTANT | From: %s | Subject: %s | Reason: %s",
        email["sender"], email["subject"], reason
    )
    return f"logged_important | reason={reason}"


def create_support_ticket(
    email: dict, issue_summary: str, priority: str, confidence: float
) -> str:
    """
    Action: Create a support ticket.

    In production this would POST to:
    → Zendesk API
    → Linear API
    → Jira API

    For Phase 1: we generate a ticket ID and log it clearly.
    The ticket_id format gives you a real timestamp-based ID
    you could actually use in a real ticketing system.
    """
    # Generate a unique ticket ID from current timestamp
    ticket_id = f"TKT-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    logger.info(
        "🎫 TICKET CREATED | ID=%s | Priority=%s | Issue: %s | From: %s",
        ticket_id, priority.upper(), issue_summary, email["sender"]
    )
    return f"ticket_created | id={ticket_id} | priority={priority}"


def send_sales_reply(email: dict, reply_tone: str, confidence: float) -> str:
    """
    Action: Send an automated reply via Gmail SMTP.

    Falls back to just logging if SMTP credentials aren't configured.
    This way the agent doesn't crash if you're running in dummy mode.

    reply_tone controls the message content:
        polite_decline    → "Not interested, thanks"
        interested        → "Tell me more"
        request_more_info → "Can you share details?"
    """
    if not settings.EMAIL_ADDRESS or not settings.EMAIL_PASSWORD:
        logger.warning("SMTP not configured — logging sales reply only")
        return f"sales_reply_logged | tone={reply_tone} (smtp not set)"

    # Extract plain email address from "Name <email@domain.com>" format
    raw = email["sender"]
    reply_to = raw.split("<")[-1].rstrip(">") if "<" in raw else raw

    tone_messages = {
        "polite_decline":    (
            "Thank you for reaching out. At this time we're not "
            "exploring new partnerships, but we appreciate your interest."
        ),
        "interested":        (
            "Thank you for reaching out! We're potentially interested. "
            "Could you share more details about your offering?"
        ),
        "request_more_info": (
            "Thank you for your message. Could you provide more details "
            "about your product and pricing before we schedule a call?"
        ),
    }

    body = (
        f"Hi,\n\n"
        f"{tone_messages.get(reply_tone, tone_messages['polite_decline'])}\n\n"
        f"Best regards,\nAutomated Response"
    )

    try:
        msg = MIMEText(body)
        msg["Subject"] = f"Re: {email['subject']}"
        msg["From"]    = settings.EMAIL_ADDRESS
        msg["To"]      = reply_to

        # SMTP_SSL opens an encrypted connection on port 465
        with smtplib.SMTP_SSL(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
            server.login(settings.EMAIL_ADDRESS, settings.EMAIL_PASSWORD)
            server.sendmail(settings.EMAIL_ADDRESS, reply_to, msg.as_string())

        logger.info("📧 SALES REPLY SENT | To: %s | Tone: %s", reply_to, reply_tone)
        return f"sales_reply_sent | to={reply_to} | tone={reply_tone}"

    except Exception as e:
        logger.error("SMTP failed for %s: %s", reply_to, e)
        return f"sales_reply_failed | error={e}"


def ignore_spam(email: dict, spam_signals: str, confidence: float) -> str:
    """
    Action: Do nothing.

    DEBUG level means this won't show in normal logs (INFO level).
    It's intentionally quiet — spam doesn't deserve attention.
    We still record it in the DB so you can audit what was ignored.
    """
    logger.debug(
        "🗑️  SPAM IGNORED | From: %s | Signals: %s",
        email["sender"], spam_signals
    )
    return f"spam_ignored | signals={spam_signals}"


def notify_action_required(
    email: dict, action_needed: str, deadline: str, confidence: float
) -> str:
    """
    Action: Notify loudly that something needs to be done.

    WARNING level so it's highly visible in logs.
    In production: Slack message, SMS, push notification.
    """
    logger.warning(
        "🚨 ACTION REQUIRED | From: %s | Action: %s | Deadline: %s | Subject: %s",
        email["sender"], action_needed, deadline, email["subject"]
    )
    return f"notified | action={action_needed} | deadline={deadline}"


# ── Dispatcher ────────────────────────────────────────────────────
# Maps tool name (string from LLM) → executor function (Python callable)
# agent.py calls execute_tool() — it never calls executor functions directly.

EXECUTOR_MAP = {
    "log_important_email":    log_important_email,
    "create_support_ticket":  create_support_ticket,
    "send_sales_reply":       send_sales_reply,
    "ignore_spam":            ignore_spam,
    "notify_action_required": notify_action_required,
}


def execute_tool(tool_name: str, email: dict, tool_args: dict) -> str:
    """
    Route to the correct executor function based on tool_name.

    CONCEPT — This is the "Act" step in the ReAct loop.
    The LLM decided what to do. This function does it.
    If the tool fails, we catch the error and return a failure string
    so the agent can observe the failure and handle it gracefully.

    Returns: action description string (stored in DB as action_taken)
    """
    executor = EXECUTOR_MAP.get(tool_name)

    if executor is None:
        logger.error("LLM requested unknown tool: '%s'", tool_name)
        return f"tool_error | unknown_tool={tool_name}"

    try:
        return executor(email, **tool_args)
    except TypeError as e:
        # LLM sent wrong argument names — log clearly
        logger.error("Tool '%s' got wrong args %s: %s", tool_name, tool_args, e)
        return f"tool_error | bad_args={e}"
    except Exception as e:
        logger.error("Tool '%s' failed: %s", tool_name, e, exc_info=True)
        return f"tool_error | {e}"
