"""
tests/test_phase1.py
====================
PURPOSE:
    Test every Phase 1 component independently.
    You should be able to run these WITHOUT an API key or Gmail.

RUN:
    pytest tests/test_phase1.py -v

CONCEPT — Unit Testing:
    Each test tests ONE function in isolation.
    If a test fails, you know exactly which function broke.
    Tests run without needing real Gmail or a real Anthropic API key.
"""

import os
import sys
import pytest

# Make sure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Set test environment variables BEFORE importing settings
os.environ.setdefault("EMAIL_ADDRESS",    "test@test.com")
os.environ.setdefault("EMAIL_PASSWORD",   "testpassword")
os.environ.setdefault("ANTHROPIC_API_KEY","test-key-not-real")

from config import settings

# Use a separate test DB — never touch real data during tests
settings.DB_PATH = settings.ROOT_DIR / "data" / "test_phase1.db"

from app.db import init_db, get_connection
from app.core.memory import SessionMemory, get_sender_context, record_decision
from app.core.hitl import should_act_automatically, flag_for_human_review
from app.ingestion.fetcher import get_dummy_emails, _decode_str, _get_body


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_db():
    """
    Create a fresh DB before each test, delete after.
    'autouse=True' means every test gets this automatically.
    """
    if settings.DB_PATH.exists():
        settings.DB_PATH.unlink()
    init_db()
    yield
    if settings.DB_PATH.exists():
        settings.DB_PATH.unlink()


def dummy_email(
    subject="Test Subject",
    body="Test body content",
    sender="user@example.com",
    message_id="<test-001@example.com>"
) -> dict:
    """Helper: build a minimal email dict for testing."""
    return {
        "message_id": message_id,
        "sender": sender,
        "subject": subject,
        "body": body,
        "raw_uid": "1",
    }


# ── Settings tests ────────────────────────────────────────────────

class TestSettings:

    def test_valid_categories_defined(self):
        assert "Important" in settings.VALID_CATEGORIES
        assert "Spam"      in settings.VALID_CATEGORIES
        assert "Support"   in settings.VALID_CATEGORIES
        assert "Sales"     in settings.VALID_CATEGORIES
        assert "Action"    in settings.VALID_CATEGORIES

    def test_confidence_threshold_is_float(self):
        assert isinstance(settings.CONFIDENCE_THRESHOLD, float)
        assert 0.0 < settings.CONFIDENCE_THRESHOLD < 1.0

    def test_validate_catches_missing_key(self):
        # Temporarily remove API key
        original = settings.ANTHROPIC_API_KEY
        settings.ANTHROPIC_API_KEY = ""
        errors = settings.validate()
        assert any("ANTHROPIC_API_KEY" in e for e in errors)
        settings.ANTHROPIC_API_KEY = original   # restore


# ── Fetcher tests ─────────────────────────────────────────────────

class TestFetcher:

    def test_dummy_emails_returns_5(self):
        emails = get_dummy_emails()
        assert len(emails) == 5

    def test_dummy_emails_have_required_keys(self):
        emails = get_dummy_emails()
        required = {"message_id", "sender", "subject", "body", "raw_uid"}
        for email in emails:
            assert required.issubset(email.keys()), f"Missing keys in: {email}"

    def test_dummy_emails_cover_all_categories(self):
        """
        Verify each dummy email maps to a distinct use case.
        Not testing LLM classification — just that subjects are different.
        """
        emails = get_dummy_emails()
        subjects = [e["subject"] for e in emails]
        # All subjects should be unique (different test cases)
        assert len(set(subjects)) == 5

    def test_dummy_email_bodies_not_empty(self):
        for email in get_dummy_emails():
            assert len(email["body"]) > 0, f"Empty body in: {email['subject']}"

    def test_decode_str_handles_none(self):
        assert _decode_str(None) == ""

    def test_decode_str_handles_plain_string(self):
        result = _decode_str("Hello World")
        assert result == "Hello World"


# ── Database tests ────────────────────────────────────────────────

class TestDatabase:

    def test_tables_created_on_init(self):
        conn = get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        conn.close()
        assert "emails"        in table_names
        assert "sender_memory" in table_names
        assert "hitl_queue"    in table_names

    def test_email_not_processed_initially(self):
        from app.db import email_already_processed
        assert email_already_processed("<new@test.com>") is False

    def test_email_marked_processed_after_save(self):
        from app.db import email_already_processed, save_email
        save_email(
            message_id="<test-save@test.com>",
            sender="a@b.com", subject="Test", body="Body",
            category="Spam", confidence=0.9,
            action_taken="ignored", reasoning="test", model_used="haiku"
        )
        assert email_already_processed("<test-save@test.com>") is True

    def test_duplicate_email_not_saved_twice(self):
        from app.db import save_email
        msg_id = "<dup@test.com>"
        for _ in range(3):
            save_email(
                message_id=msg_id, sender="a@b.com",
                subject="Test", body="Body", category="Spam",
                confidence=0.9, action_taken="ignored",
                reasoning="test", model_used="haiku"
            )
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) as c FROM emails WHERE message_id = ?", (msg_id,)
        ).fetchone()["c"]
        conn.close()
        assert count == 1   # only one row despite 3 saves


# ── Memory tests ──────────────────────────────────────────────────

class TestMemory:

    def test_unknown_sender_returns_neutral_context(self):
        ctx = get_sender_context("brand.new@unknown.com")
        assert ctx["known_sender"]  is False
        assert ctx["trust_score"]   == 0.5
        assert ctx["email_count"]   == 0
        assert "No prior history"   in ctx["history_summary"]

    def test_sender_remembered_after_decision(self):
        record_decision("known@sender.com", "Sales")
        ctx = get_sender_context("known@sender.com")
        assert ctx["known_sender"] is True
        assert ctx["email_count"]  == 1

    def test_dominant_type_updates_with_multiple_emails(self):
        for _ in range(4):
            record_decision("repeat@spam.com", "Spam")
        record_decision("repeat@spam.com", "Important")

        ctx = get_sender_context("repeat@spam.com")
        assert ctx["dominant_type"] == "Spam"
        assert ctx["email_count"]   == 5

    def test_trust_score_drops_for_repeat_spammer(self):
        for _ in range(6):
            record_decision("spammer@bad.com", "Spam")
        ctx = get_sender_context("spammer@bad.com")
        assert ctx["trust_score"] < 0.5   # should be 0.1


class TestSessionMemory:

    def test_think_records_thought(self):
        mem = SessionMemory()
        mem.think("This looks like spam")
        assert len(mem.thoughts) == 1
        assert "spam" in mem.thoughts[0].lower()

    def test_trace_contains_all_step_types(self):
        mem = SessionMemory()
        mem.think("Analyzing email")
        mem.act("Calling ignore_spam")
        mem.observe("Tool succeeded")
        trace = mem.get_trace()
        assert "THINK"   in trace
        assert "ACT"     in trace
        assert "OBSERVE" in trace

    def test_step_counter_increments(self):
        mem = SessionMemory()
        mem.think("Step 1")
        mem.think("Step 2")
        assert mem.step == 2

    def test_empty_memory_returns_safe_default(self):
        mem = SessionMemory()
        result = mem.get_last_thought()
        assert result == "No reasoning recorded"


# ── HITL tests ────────────────────────────────────────────────────

class TestHITL:

    def test_high_confidence_acts_automatically(self):
        # 0.95 is well above default threshold of 0.70
        assert should_act_automatically(0.95) is True

    def test_low_confidence_flags_for_human(self):
        # 0.40 is below threshold
        assert should_act_automatically(0.40) is False

    def test_exactly_at_threshold_acts(self):
        # At the threshold itself → act (>= not >)
        assert should_act_automatically(settings.CONFIDENCE_THRESHOLD) is True

    def test_just_below_threshold_flags(self):
        below = settings.CONFIDENCE_THRESHOLD - 0.01
        assert should_act_automatically(below) is False

    def test_flag_for_human_adds_to_queue(self):
        from app.db import get_pending_hitl
        email = dummy_email(message_id="<hitl-test@test.com>")
        flag_for_human_review(email, "Support", 0.45)
        queue = get_pending_hitl()
        assert len(queue) == 1
        assert queue[0]["agent_category"] == "Support"
        assert queue[0]["confidence"]     == 0.45

    def test_flag_returns_action_string(self):
        email = dummy_email(message_id="<hitl-ret@test.com>")
        result = flag_for_human_review(email, "Sales", 0.55)
        assert "hitl_flagged" in result
        assert "Sales"        in result


# ── Tools tests ───────────────────────────────────────────────────

class TestTools:

    def test_tool_to_category_covers_all_categories(self):
        from app.core.tools import TOOL_TO_CATEGORY
        categories = set(TOOL_TO_CATEGORY.values())
        assert categories == settings.VALID_CATEGORIES

    def test_executor_map_has_all_tools(self):
        from app.core.tools import EXECUTOR_MAP, TOOL_SCHEMAS
        tool_names = {t["name"] for t in TOOL_SCHEMAS}
        executor_names = set(EXECUTOR_MAP.keys())
        assert tool_names == executor_names

    def test_execute_unknown_tool_returns_error(self):
        from app.core.tools import execute_tool
        result = execute_tool("nonexistent_tool", dummy_email(), {})
        assert "tool_error" in result

    def test_log_important_email_returns_string(self):
        from app.core.tools import log_important_email
        result = log_important_email(
            dummy_email(), reason="Urgent personal message", confidence=0.9
        )
        assert "logged_important" in result

    def test_ignore_spam_returns_string(self):
        from app.core.tools import ignore_spam
        result = ignore_spam(
            dummy_email(), spam_signals="Prize scam keywords", confidence=0.95
        )
        assert "spam_ignored" in result

    def test_notify_action_returns_string(self):
        from app.core.tools import notify_action_required
        result = notify_action_required(
            dummy_email(),
            action_needed="Sign the contract",
            deadline="today 5pm",
            confidence=0.88
        )
        assert "notified" in result

    def test_tool_schemas_have_required_fields(self):
        from app.core.tools import TOOL_SCHEMAS
        for tool in TOOL_SCHEMAS:
            assert "name"         in tool
            assert "description"  in tool
            assert "input_schema" in tool
            # Every tool must require a confidence field
            required = tool["input_schema"].get("required", [])
            assert "confidence" in required, f"Tool {tool['name']} missing confidence"
