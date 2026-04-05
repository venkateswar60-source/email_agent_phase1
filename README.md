# Agentic Email Classifier

A production-grade, multi-phase agentic AI system that classifies emails using the **ReAct pattern** (Reason + Act + Observe). Built with security-first design, cost optimisation, and enterprise deployment in mind.

> **Status:** Phase 1 complete — Phase 2 in development

---

## What This Project Demonstrates

This is not a basic LLM wrapper. It is a full agentic pipeline that shows:

- **ReAct pattern** implemented from scratch — no LangChain abstraction hiding the mechanics
- **Tool calling** — the LLM decides which action to take, Python executes it
- **Long-term memory** — the agent builds a sender profile over time and uses it in future classifications
- **Human-in-the-Loop (HITL)** — low-confidence decisions are gated for human review, not acted on blindly
- **Security-layered architecture** — secrets management, PII handling, and guardrails designed per phase
- **Cost-aware design** — model tiering, token budgeting, and caching built in from Phase 2

---

## Architecture Overview

```
email_agent/
├── Phase 1/                     ← Foundation (current)
│   └── email_agent_phase1/
│       ├── main.py              ← Entry point, polling loop
│       ├── config/
│       │   └── settings.py      ← Config + .env loading
│       ├── app/
│       │   ├── core/
│       │   │   ├── agent.py     ← ReAct loop brain
│       │   │   ├── tools.py     ← 5 tool definitions + executors
│       │   │   ├── memory.py    ← Short-term + long-term memory
│       │   │   └── hitl.py      ← Human-in-the-Loop gate
│       │   ├── ingestion/
│       │   │   └── fetcher.py   ← Gmail IMAP + dummy email source
│       │   └── db.py            ← All SQLite operations
│       ├── data/
│       │   └── emails.db        ← SQLite database (auto-created)
│       ├── logs/
│       │   └── app.log          ← Structured application logs
│       └── tests/
│           └── test_phase1.py   ← Unit tests
│
├── Phase 2/                     ← Cost optimisation + model flexibility (planned)
└── Phase 3/                     ← Production / AWS Bedrock (planned)
```

---

## How the ReAct Loop Works

Every email goes through a full Reason → Act → Observe cycle:

```
Email arrives
     │
     ▼
[THINK] Load sender memory from DB
     │    "I've seen 8 emails from this sender — mostly spam, trust_score=0.1"
     ▼
[THINK] Build LLM prompt with sender context + email content
     │
     ▼
[ACT]   Call Claude API with 5 tool schemas
     │    LLM reads email + decides: "I'll call ignore_spam(confidence=0.92)"
     ▼
[OBSERVE] Parse tool_use block from response
     │    Extract: tool_name, tool_args, confidence score
     ▼
[THINK] Is confidence >= threshold (0.70)?
     │    Yes → act automatically
     │    No  → flag for human review (HITL queue)
     ▼
[ACT]   Execute the tool (log, create ticket, send reply, ignore, notify)
     ▼
[OBSERVE] Record result
     │
     ▼
Update sender long-term memory in DB
Save full reasoning trace to emails table
```

---

## The 5 Tools

The LLM chooses one tool per email. Tools are the agent's actions — not hardcoded if/else logic.

| Tool | Category | When used |
|---|---|---|
| `log_important_email` | Important | Personal emails, urgent messages from known contacts |
| `create_support_ticket` | Support | User needs help, bug reports, account issues |
| `send_sales_reply` | Sales | Vendor pitches, partnership requests, cold outreach |
| `ignore_spam` | Spam | Obvious spam, scams, phishing attempts |
| `notify_action_required` | Action | Deadlines, approvals, things requiring a response |

---

## Security Design

### Phase 1 (current)
| Layer | Implementation |
|---|---|
| Secrets | `.env` file — local development only |
| Input | Email body truncated to 800 chars before LLM call |
| Output | Confidence threshold gates automated actions |
| Audit | Full reasoning trace stored per email in SQLite |
| Access | SQLite — single user, local only |

### Phase 2 (planned)
| Layer | Implementation |
|---|---|
| Secrets | AWS Parameter Store — encrypted, IAM-scoped, audit trail |
| Cost | Token budgeting, semantic caching, model tiering |
| Observability | LangSmith tracing — every LLM call traced |

### Phase 3 (planned)
| Layer | Implementation |
|---|---|
| LLM provider | AWS Bedrock — private VPC endpoint, no public internet |
| PII | Scrubbing layer before any LLM call (AWS Comprehend / Presidio) |
| Guardrails | Bedrock Guardrails — input + output filtering |
| Queue | AWS SQS — event-driven, replaces polling loop |
| Monitoring | CloudWatch dashboards — latency, cost, HITL rate |
| Deployment | Docker + AWS Lambda — containerised, auto-scaling |
| Audit | Immutable append-only log — 7-year retention for compliance |

---

## Phase Roadmap

### Phase 1 — Foundation
- [x] Gmail IMAP ingestion
- [x] ReAct agent loop (custom, no LangChain)
- [x] 5 tool definitions with Pydantic-style schemas
- [x] Long-term sender memory (SQLite)
- [x] HITL confidence gate
- [x] Deduplication (process each email exactly once)
- [x] Structured logging to file + terminal
- [x] Dummy email mode for testing without Gmail

### Phase 2 — Cost Optimisation + Model Flexibility
- [ ] **Model provider abstraction** — adapter pattern (Anthropic / Bedrock / OpenAI)
- [ ] **Model selection via config** — change provider without touching agent code
- [ ] **AWS Parameter Store** — replace `.env` for secrets
- [ ] **Semantic caching** — don't call LLM for near-identical emails
- [ ] **Token budgeting** — enforce per-run and per-email token limits
- [ ] **LangSmith tracing** — visual observability dashboard
- [ ] **Batch processing** — process multiple emails in one API call where possible

### Phase 3 — Production / Publishable
- [ ] **AWS Bedrock** — private VPC endpoint, enterprise-grade data handling
- [ ] **PII scrubbing** — strip sensitive data before any LLM call
- [ ] **Bedrock Guardrails** — input and output content filtering
- [ ] **SQS event queue** — replace polling with event-driven processing
- [ ] **CloudWatch dashboards** — operational metrics and alerting
- [ ] **Docker + Lambda** — containerised deployment, auto-scaling
- [ ] **Immutable audit log** — compliance-grade decision trail
- [ ] **REST API** — expose classification as a service endpoint
- [ ] **Multi-tenant** — support multiple email accounts

---

## Getting Started

### Prerequisites

- Python 3.11+
- Gmail account with IMAP enabled
- Gmail App Password (not your regular password)
- Anthropic API key — get one at `console.anthropic.com`

### Installation

```bash
# Clone the repo
git clone git clone https://github.com/venkateswar60-source/email_agent_phase1.git
cd agentic-email-classifier/Phase\ 1/email_agent_phase1

# Create virtual environment
python -m venv venv

# Activate (Windows Git Bash)
source venv/Scripts/activate

# Activate (Mac/Linux)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

```bash
# Copy the example config
cp .env.example .env
```

Edit `.env`:

```env
# Anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Gmail (only needed for real email mode)
EMAIL_ADDRESS=your@gmail.com
EMAIL_PASSWORD=xxxx-xxxx-xxxx-xxxx   # 16-char App Password

# Gmail App Password: Google Account → Security → 2-Step → App Passwords
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465

# Agent behaviour
POLL_INTERVAL=300          # seconds between inbox checks
CONFIDENCE_THRESHOLD=0.70  # below this → human review queue
```

### Running

```bash
# Test with dummy emails (no Gmail, minimal API cost)
python main.py --dummy --once

# Process real Gmail once and exit
python main.py --once

# Continuous polling (runs until Ctrl+C)
python main.py
```

---

## Cost Guide

All phases are designed for minimal API spend.

| Model | Input cost | Output cost | Use case |
|---|---|---|---|
| Claude Haiku | $0.80 / 1M tokens | $4.00 / 1M tokens | Phase 1 default — fast, cheap |
| Claude Sonnet | $3.00 / 1M tokens | $15.00 / 1M tokens | Complex emails, Phase 2+ |
| GPT-4o mini | $0.15 / 1M tokens | $0.60 / 1M tokens | Fallback option |

**Typical cost per email (Haiku):** ~$0.0005 (half a cent per 1,000 emails)

**Phase 2 caching** reduces repeat classification costs by 60–80%.

---

## Understanding the Output

```
2026-04-01 22:19:18 [INFO]    Calling Claude model=claude-haiku-4-5-20251001
2026-04-01 22:19:19 [INFO]    LLM chose tool=ignore_spam confidence=0.94
2026-04-01 22:19:19 [INFO]    DONE | Subject='YOU WON!!' | Category=Spam | Conf=94% | 843ms
2026-04-01 22:19:19 [WARNING] ACTION REQUIRED | Subject='Sign contract by EOD' | Action=Review and sign contract
2026-04-01 22:19:19 [WARNING] HITL FLAGGED | Subject='Catching up?' | Agent guessed=Important | Confidence=61%
```

- `INFO` — normal processing
- `WARNING` — action required or HITL flagged (needs human attention)
- `ERROR` — something failed (agent continues safely)

---

## Interview Reference

**"Explain your agent architecture"**
> "The agent uses the ReAct pattern — Reason, Act, Observe. For each email it loads sender history from a long-term memory store, builds a context-rich prompt, calls the LLM with a menu of tools it can choose from, parses which tool was selected, then gates the action behind a confidence threshold. Low-confidence decisions go to a human review queue instead of acting automatically."

**"How did you handle security?"**
> "In Phase 1, I focused on output security — the HITL gate ensures the agent never acts on uncertain decisions. In Phase 2, I'm replacing .env with AWS Parameter Store so secrets are fetched at runtime with an IAM-scoped audit trail. Phase 3 moves to AWS Bedrock via a private VPC endpoint so no customer data ever touches the public internet, with PII scrubbing applied before any LLM call."

**"Why not use LangChain?"**
> "I built the ReAct loop from scratch deliberately. LangChain is a valid choice but it hides what's actually happening — the tool call parsing, the confidence extraction, the memory management. Building it manually means I understand every component and can explain every design decision. I'd use LangGraph in Phase 2 for the stateful multi-agent patterns where it genuinely adds value."

---

## Contributing

Contributions welcome. Please read the architecture overview above before opening a PR.

Areas where help is most valuable:
- Phase 2 implementation (model adapter pattern, LangSmith integration)
- Additional tool definitions for new email categories
- Test coverage for edge cases
- Documentation and examples

---

## Author

**Goda Venkateswara Rao**
Senior AI & Automation Architect — Bengaluru, India
12+ years in enterprise automation, agentic AI, and RPA systems

---

## Acknowledgements

Built using the [Anthropic Python SDK](https://github.com/anthropic-sdk/anthropic-sdk-python).
ReAct pattern based on the original [ReAct paper](https://arxiv.org/abs/2210.03629) by Yao et al.
