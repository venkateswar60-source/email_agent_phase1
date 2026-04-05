# Phase 1 User Manual — Agentic Email Classifier

---

## What Phase 1 teaches you

By the end of Phase 1, you can explain these 4 concepts
with working code behind them — not just theory:

| Concept | What it means | Where it lives |
|---------|--------------|----------------|
| **Tool Use** | LLM decides the action, not hardcoded if/else | `app/core/tools.py` |
| **Agent Memory** | Agent learns sender patterns over time | `app/core/memory.py` |
| **ReAct Loop** | Think → Act → Observe reasoning pattern | `app/core/agent.py` |
| **Human-in-the-Loop** | Low confidence → human reviews, not agent | `app/core/hitl.py` |

---

## File Map — What Each File Does

```
email_agent_phase1/
│
├── main.py                    ← Entry point. Runs the polling loop.
│                                Calls every other module in the right order.
│
├── requirements.txt           ← Only 3 packages needed for Phase 1.
│
├── .env.example               ← Copy to .env. Fill in your credentials.
│
├── config/
│   └── settings.py            ← All config in one place.
│                                Every other file imports from here.
│                                Change .env → settings updates automatically.
│
├── app/
│   │
│   ├── db.py                  ← Database layer. Only file that touches SQLite.
│   │                            3 tables: emails, sender_memory, hitl_queue.
│   │
│   ├── ingestion/
│   │   └── fetcher.py         ← Reads emails from Gmail via IMAP.
│   │                            Also has get_dummy_emails() for testing.
│   │
│   └── core/                  ← The agent brain.
│       │
│       ├── tools.py           ← Defines 5 tools the LLM can call.
│       │                        TOOL_SCHEMAS → what LLM reads
│       │                        Executor functions → what Python runs
│       │
│       ├── memory.py          ← Two types of memory:
│       │                        Long-term: sender profiles in SQLite
│       │                        Short-term: SessionMemory (one per email)
│       │
│       ├── hitl.py            ← Confidence gate.
│       │                        Above threshold → agent acts.
│       │                        Below threshold → human reviews.
│       │
│       └── agent.py           ← ReAct loop. The main brain.
│                                Calls memory → LLM → HITL → tools → memory
│
└── tests/
    └── test_phase1.py         ← ~30 tests. Run without API key or Gmail.
```

---

## Setup (15 minutes)

### Step 1 — Create a dummy Gmail account

1. Go to [gmail.com](https://gmail.com) and create a new account
   (e.g., `myagent.test2024@gmail.com`)
2. **Enable 2-Step Verification:**
   Google Account → Security → 2-Step Verification → Turn On
3. **Create an App Password:**
   Google Account → Security → 2-Step Verification → App Passwords
   → Select "Mail" → Generate → Copy the 16-character password
4. **Enable IMAP:**
   Gmail → Settings (gear icon) → See all settings
   → Forwarding and POP/IMAP → Enable IMAP → Save Changes

### Step 2 — Install

```bash
# Open Git Bash and navigate to the unzipped folder
cd email_agent_phase1

# Create virtual environment
python -m venv venv

# Activate it (Git Bash on Windows)
source venv/Scripts/activate

# Install the 3 packages
pip install -r requirements.txt
```

### Step 3 — Configure .env

```bash
cp .env.example .env
```

Open `.env` and fill in:
```
EMAIL_ADDRESS=myagent.test2024@gmail.com
EMAIL_PASSWORD=abcd efgh ijkl mnop   # your 16-char App Password
ANTHROPIC_API_KEY=sk-ant-...         # from console.anthropic.com
POLL_INTERVAL=60                     # 60 seconds for testing
CONFIDENCE_THRESHOLD=0.70            # 70% confidence required to act
```

### Step 4 — Get your Anthropic API key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an account (free tier works)
3. API Keys → Create Key → Copy it to .env

---

## Running Phase 1

### Day 1 — No Gmail needed

```bash
python main.py --dummy --once
```

This processes 5 hardcoded test emails and exits.
You'll see the full agent in action immediately.

**Expected output:**
```
🤖 AGENTIC EMAIL CLASSIFIER — Phase 1
Mode   : DUMMY EMAILS

📨 New email | From: boss@mycompany.com | Subject: URGENT: Please sign the contract
[1] THINK: Sender context: First email from this sender...
[1] ACT: Executing notify_action_required(...)
[1] OBSERVE: Tool result: notified | action=Sign the contract...
✅ DONE | Category=Action | Conf=92% | Action=notified... | 1243ms

📨 New email | From: sales@softwarevendor.com | Subject: Partnership opportunity
...
✅ DONE | Category=Sales | Conf=88% | Action=sales_reply_sent... | 876ms
```

### Day 2 — With real Gmail

Send these emails TO your dummy Gmail account from any account:

| Subject | Body | What it should classify as |
|---------|------|---------------------------|
| "URGENT: Please approve the invoice" | "Finance needs your sign-off by EOD" | Action |
| "We'd love to show you our product" | "Can we schedule a quick demo?" | Sales |
| "I can't access my dashboard" | "Getting a 404 error since yesterday" | Support |
| "You won a free iPhone!!!" | "Click here to claim your prize" | Spam |
| "Lunch tomorrow?" | "Are you free for lunch?" | Important |

Then run:
```bash
python main.py --once
```

---

## Understanding the Output

### Log levels and what they mean

```
[INFO]    Normal processing. Email received, classified, saved.
[WARNING] Two things trigger WARNING:
            ⭐ IMPORTANT email logged
            🚨 ACTION REQUIRED notification
            🔍 HITL — email flagged for human review
[DEBUG]   Verbose details. Not shown by default.
          (change logging.INFO to logging.DEBUG in main.py to see)
[ERROR]   Something failed. Check the message for details.
```

### What the ✅ DONE line tells you

```
✅ DONE | Subject='URGENT: Please sign...' | Category=Action | Conf=92% | Action=notified... | 1243ms
                    ↑                           ↑                  ↑            ↑                  ↑
              Email subject             LLM decision          How sure    What happened       How long
              (first 40 chars)          (one of 5)           the LLM was  (tool result)       it took
```

---

## Inspect Results in the Database

After running, look at what the agent saved:

```bash
# All processed emails
sqlite3 data/emails.db \
  "SELECT sender, subject, category, confidence, action_taken FROM emails;"

# Full reasoning trace (see the agent's thoughts)
sqlite3 data/emails.db \
  "SELECT subject, reasoning FROM emails;"

# Sender memory (what the agent learned)
sqlite3 data/emails.db \
  "SELECT sender, email_count, trust_score, spam_count, sales_count FROM sender_memory;"

# HITL queue (emails awaiting human review)
sqlite3 data/emails.db \
  "SELECT sender, subject, agent_category, confidence FROM hitl_queue;"
```

---

## Run the Tests

```bash
pytest tests/test_phase1.py -v
```

You should see ~30 tests pass. No API key needed — tests don't call the LLM.

Tests cover:
- Settings validation
- Dummy email structure
- Database operations (save, dedup, retrieve)
- Sender memory (unknown sender, repeat sender, trust score)
- Session memory (ReAct trace recording)
- HITL gate (confidence threshold logic)
- Tool definitions and executors
- Error handling

---

## The 4 Concepts — Deep Explanation

### Concept 1: Tool Use

**Before (basic, not agentic):**
```python
category = call_llm_for_category(email)  # returns "Sales"
if category == "Sales":
    send_reply(email)                     # Python hardcodes the decision
```
The LLM only classifies. Python makes all decisions.

**After (agentic — what Phase 1 does):**
```python
# LLM reads TOOL_SCHEMAS in tools.py
# LLM decides: "I'll call send_sales_reply with reply_tone=polite_decline"
# Python just executes what the LLM decided
response = claude.messages.create(tools=TOOL_SCHEMAS, ...)
tool_name = response.content[0].name         # "send_sales_reply"
tool_args = response.content[0].input        # {"reply_tone": "polite_decline", "confidence": 0.88}
execute_tool(tool_name, email, tool_args)
```

The LLM now has autonomy over:
- WHICH tool to call
- WHAT arguments to pass
- HOW confident it is

**Interview answer:**
> "Instead of hardcoding if/else logic in Python, we give the LLM
> a menu of tools it can call. The LLM decides which tool to use
> and provides the arguments based on its reasoning about the email.
> This is called Function Calling or Tool Use."

---

### Concept 2: Agent Memory

**Short-term memory (SessionMemory in memory.py):**
```
THINK:   "First email from this sender. Trust=0.5."
ACT:     "Calling create_support_ticket with priority=high"
OBSERVE: "ticket_created | id=TKT-20240115123045"
```
Lives only during one email's processing. Stored as the reasoning trace in DB.

**Long-term memory (sender_memory table in DB):**
```
After 10 emails from spam@bad.com:
  email_count  = 10
  spam_count   = 10
  trust_score  = 0.1  ← automatically calculated
  last_seen    = 2024-01-15 12:30:45
```
Persists across restarts. The agent gets smarter over time.

**How memory improves decisions:**
The sender's history is injected into the LLM prompt before every decision:
```
SENDER HISTORY:
Seen 10 email(s) from this sender.
Mostly Spam (10/10). Trust score: 0.10.
```
Now the LLM reasons: "I know this sender. They always send spam."

**Interview answer:**
> "Our agent has two types of memory. Short-term session memory
> tracks reasoning within one email using the ReAct pattern.
> Long-term memory persists sender profiles in SQLite across sessions.
> Before classifying any email, the agent loads the sender's history
> and injects it into the LLM prompt as context."

---

### Concept 3: ReAct Loop

ReAct = **Re**ason + **Act**

```
THINK:   "Email from boss@company.com. Subject: URGENT sign contract.
          First time seeing this sender. Confidence should be moderate."

ACT:     "Call notify_action_required with action_needed='Sign the contract'
          deadline='today EOD' confidence=0.91"

OBSERVE: "Tool returned: notified | action=Sign the contract | deadline=today EOD"
```

In Phase 1, we do one cycle (Think → Act → Observe).
In Phase 2+, the agent can loop: if tool fails → Think again → Try different approach.

Every step is recorded in SessionMemory and stored in the DB as the reasoning column.

**Interview answer:**
> "We use the ReAct pattern — Reason and Act. Before taking any action,
> the agent reasons about the email using its memory context. It then
> calls a tool and observes the result. The full reasoning trace is
> stored in the database, making every decision fully auditable."

---

### Concept 4: Human-in-the-Loop (HITL)

```python
if confidence >= CONFIDENCE_THRESHOLD:   # default 0.70
    execute_tool(...)                     # agent acts automatically
else:
    flag_for_human_review(...)           # goes to hitl_queue table
```

**What triggers HITL:**
- Email is ambiguous (could be Support or Important)
- Sender is new, no history to draw from
- Email content contradicts sender history

**What happens after HITL:**
- Email sits in hitl_queue with status='pending'
- Logged at WARNING level so it's visible
- A human (or dashboard) can review and decide
- In production: triggers a Slack alert

**Tuning the threshold:**
```
0.90 → Very conservative. Agent flags 30%+ of emails.
       Good for high-stakes systems (medical, legal, finance).

0.70 → Balanced. Agent flags ~10% of emails (default).
       Good for most business email systems.

0.50 → Aggressive. Agent acts on almost everything.
       Only for low-risk, high-volume scenarios.
```

**Interview answer:**
> "We use confidence thresholding for Human-in-the-Loop control.
> The LLM returns a 0–1 confidence score with every decision.
> Below 70%, the email goes to a human review queue instead of
> being acted on automatically. The HITL rate is a key operational
> metric — if it goes above 20%, it signals our model needs improvement."

---

## Interview Cheat Sheet — Phase 1

After running Phase 1, you can answer these with real examples:

**"What makes your system 'agentic'?"**
> "Three things: Tool Use (LLM decides its own actions), Memory (it learns
> from every email it processes), and a ReAct loop (it reasons step by step
> rather than doing one-shot classification)."

**"How does your agent handle uncertainty?"**
> "The LLM returns a confidence score with every decision. If confidence
> falls below our threshold (configurable in .env), the email goes to
> a human review queue. The agent knows its own limits."

**"Walk me through what happens when your agent processes an email."**
> "First it loads long-term memory about the sender from SQLite.
> That context gets injected into the LLM prompt. The LLM reads the email
> and picks one of 5 tools to call, with a confidence score.
> If confidence is above threshold, the tool executes immediately.
> If not, the email goes to the HITL queue.
> Either way, the full reasoning trace is saved to the database."

**"What database does it use and why?"**
> "SQLite for Phase 1. Zero setup, file-based, built into Python.
> It's perfect for local development and single-server deployments.
> The database layer is isolated in app/db.py — swapping to PostgreSQL
> in production only requires changing that one file."

**"How would you scale this?"**
> "Phase 2 adds cost optimization: semantic caching, tiered model routing,
> and token budgeting. Phase 3 adds 4-layer guardrails, A/B testing,
> and production metrics. The folder structure already has placeholders
> for all of that — each phase extends without rewriting Phase 1."

---

## What's Next — Phase 2 Preview

When you're comfortable with Phase 1, Phase 2 adds:

```
app/cost/
├── token_counter.py   ← measure every LLM call in USD
├── cache.py           ← skip the LLM for similar emails (save 20%+ cost)
├── router.py          ← send simple emails to cheap Haiku, complex to Sonnet
└── batch.py           ← strip email noise before LLM sees it (37% token reduction)
```

The key insight of Phase 2:
> "You can't optimize what you don't measure.
>  Track cost per email. Then cut it."
