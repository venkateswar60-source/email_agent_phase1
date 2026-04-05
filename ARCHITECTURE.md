# Architecture Design Document
## Agentic Email Classifier — All Phases

**Author:** Goda Venkateswara Rao
**Version:** 1.0
**Status:** Phase 1 complete, Phase 2–3 design finalised

---

## 1. Problem Statement

Enterprise email systems process thousands of unstructured emails daily. Rule-based RPA systems fail at this because:

- They classify but don't reason — no understanding of intent
- They have no memory — every email is a cold start
- They act blindly — no confidence awareness, no human escalation path
- They are brittle — one new email pattern breaks the rules

This system solves all four problems using an agentic architecture.

---

## 2. Design Goals

| Goal | Requirement |
|---|---|
| Accuracy | >90% classification confidence on clear emails |
| Safety | Never act automatically on uncertain decisions |
| Cost | <$0.001 per email at production volume |
| Security | No customer data leaves the organisation's network (Phase 3) |
| Auditability | Every decision traceable — input, reasoning, output, confidence |
| Extensibility | Add new email categories without touching agent logic |
| Portability | Swap LLM provider without changing agent code |

---

## 3. Core Patterns

### 3.1 ReAct Pattern (Reason + Act + Observe)

The fundamental loop that makes this agentic rather than just a classifier.

```
THINK:   Read email + sender memory → what do I know?
ACT:     Call LLM with tool menu → which tool should I use?
OBSERVE: Parse response → what did the LLM decide?
THINK:   Is confidence sufficient to act automatically?
ACT:     Execute chosen tool OR flag for human review
OBSERVE: Record outcome → update sender memory
```

**Why this matters vs a basic classifier:**
A basic classifier returns a label. A ReAct agent returns a label, a confidence score, a reason, an action taken, and a full reasoning trace — all stored and auditable.

### 3.2 Tool Calling Pattern

The LLM does not return a text label. It returns a structured tool call:

```json
{
  "type": "tool_use",
  "name": "create_support_ticket",
  "input": {
    "issue_summary": "User locked out, password reset not working",
    "priority": "high",
    "confidence": 0.94
  }
}
```

This gives the LLM autonomy over both the classification AND the action parameters. The Python executor runs whatever the LLM decided.

### 3.3 Adapter Pattern (Phase 2+)

`agent.py` never calls a provider directly. It calls `ModelRouter.complete()`.

```
agent.py
    │
    ▼
ModelRouter.complete(prompt, tools)
    │
    ├── AnthropicAdapter  → client.messages.create(...)
    ├── BedrockAdapter    → bedrock.invoke_model(...)
    └── OpenAIAdapter     → openai.chat.completions.create(...)
```

Swapping from Anthropic to Bedrock = change one line in `config.yaml`. Zero changes to `agent.py`.

### 3.4 Memory Pattern

Two memory types serve different purposes:

**Short-term (SessionMemory):**
- Lives for the duration of one email processing cycle
- Stores the THINK/ACT/OBSERVE trace as it happens
- Discarded after the email is saved to DB
- The trace is stored in `emails.reasoning` column for audit

**Long-term (sender_memory table):**
- Persists across restarts
- One row per sender email address
- Tracks: email count, category counts, trust score
- Injected into the LLM prompt as context for every future email from that sender

### 3.5 HITL Gate Pattern

```
confidence >= CONFIDENCE_THRESHOLD (0.70)
    → execute tool automatically

confidence < CONFIDENCE_THRESHOLD
    → add to hitl_queue table
    → log WARNING (visible to human operator)
    → do NOT execute any action
```

HITL rate is a key operational metric:
- Rate > 30% → model needs improvement or threshold too strict
- Rate < 2% → threshold may be too lenient
- Healthy range: 5–15%

---

## 4. Phase 1 Architecture

### Components

```
┌─────────────────────────────────────────────────────────┐
│                     main.py                             │
│  Polling loop → process_inbox() → per-email pipeline   │
└───────────────────────────┬─────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   fetcher.py          agent.py           db.py
   Gmail IMAP          ReAct loop         SQLite
   OR dummy            ├── memory.py      3 tables
   emails              ├── tools.py
                       └── hitl.py
                            │
                            ▼
                    Anthropic API
                    (claude-haiku)
```

### Database Schema

**emails** — one row per processed email
```sql
id           INTEGER PRIMARY KEY
message_id   TEXT UNIQUE          -- dedup key (RFC email ID)
sender       TEXT
subject      TEXT
body         TEXT
category     TEXT                 -- Important/Support/Sales/Spam/Action
confidence   REAL                 -- 0.0 to 1.0
action_taken TEXT                 -- what the agent did
reasoning    TEXT                 -- full THINK/ACT/OBSERVE trace
model_used   TEXT
processed_at DATETIME
```

**sender_memory** — agent's long-term knowledge per sender
```sql
id              INTEGER PRIMARY KEY
sender          TEXT UNIQUE
email_count     INTEGER
spam_count      INTEGER
support_count   INTEGER
sales_count     INTEGER
important_count INTEGER
action_count    INTEGER
trust_score     REAL              -- 0.0 untrusted, 1.0 trusted
last_seen       DATETIME
```

**hitl_queue** — emails awaiting human review
```sql
id             INTEGER PRIMARY KEY
message_id     TEXT
sender         TEXT
subject        TEXT
agent_category TEXT              -- agent's best guess
confidence     REAL              -- why it was flagged (too low)
status         TEXT              -- pending / reviewed
created_at     DATETIME
```

### Security in Phase 1

| Concern | Mitigation |
|---|---|
| API key exposure | `.env` file, excluded from git via `.gitignore` |
| Email body size | Truncated to 800 chars before LLM call |
| Uncertain actions | HITL gate — 0.70 confidence threshold |
| Duplicate processing | `message_id` UNIQUE constraint in DB |
| Agent crash | Try/except around LLM call — safe fallback to Important |

### Limitations (addressed in later phases)

- Secrets in `.env` — not suitable for production servers
- No PII scrubbing — raw email content sent to external API
- Polling loop — inefficient, misses emails during downtime
- Single LLM provider — no fallback if Anthropic API is down
- No cost tracking — no visibility into token spend
- No visual observability — logs only

---

## 5. Phase 2 Architecture

### New Components

#### 5.1 Model Provider Abstraction

**File:** `app/providers/base.py`
```python
from abc import ABC, abstractmethod

class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        tools: list[dict],
        max_tokens: int = 300
    ) -> ProviderResponse:
        pass
```

**File:** `app/providers/anthropic_provider.py`
```python
class AnthropicProvider(LLMProvider):
    def complete(self, system, user, tools, max_tokens=300):
        response = self.client.messages.create(
            model=self.model,
            system=system,
            tools=tools,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
        )
        return ProviderResponse.from_anthropic(response)
```

**File:** `app/providers/router.py`
```python
class ModelRouter:
    def __init__(self, config: dict):
        self.primary   = self._load(config["provider"], config["model"])
        self.fallback  = self._load(config["fallback_provider"], config["fallback_model"])

    def complete(self, **kwargs) -> ProviderResponse:
        try:
            return self.primary.complete(**kwargs)
        except ProviderError:
            logger.warning("Primary provider failed — using fallback")
            return self.fallback.complete(**kwargs)
```

**File:** `config/model_config.yaml`
```yaml
provider: anthropic
model: claude-haiku-4-5-20251001

fallback_provider: openai
fallback_model: gpt-4o-mini

cost_limits:
  max_tokens_per_email: 1000
  max_tokens_per_run: 50000
  alert_at_usd: 1.00
```

#### 5.2 AWS Parameter Store (replaces .env)

```python
import boto3

class SecretProvider:
    def __init__(self, region: str = "ap-south-1"):
        self.client = boto3.client("ssm", region_name=region)
        self._cache = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            response = self.client.get_parameter(
                Name=f"/email-agent/{name}",
                WithDecryption=True
            )
            self._cache[name] = response["Parameter"]["Value"]
        return self._cache[name]
```

Parameters stored in AWS:
```
/email-agent/ANTHROPIC_API_KEY
/email-agent/OPENAI_API_KEY
/email-agent/GMAIL_PASSWORD
```

Each parameter is encrypted using AWS KMS. IAM policy restricts access to only the Lambda execution role or EC2 instance profile running the agent.

#### 5.3 Semantic Caching

```python
class SemanticCache:
    """
    Before calling the LLM, check if we've classified a
    semantically similar email before.

    Uses cosine similarity on sentence embeddings.
    Threshold: 0.92 similarity = cache hit.

    Saves ~60-80% of LLM calls for repeat email patterns
    (newsletters, automated notifications, vendor pitches).
    """
    def get(self, email_text: str) -> CachedResult | None: ...
    def set(self, email_text: str, result: dict) -> None: ...
```

#### 5.4 Token Budgeting

```python
class TokenBudget:
    def __init__(self, max_per_run: int, max_per_email: int):
        self.run_used   = 0
        self.max_run    = max_per_run
        self.max_email  = max_per_email

    def check(self, estimated_tokens: int) -> bool:
        if self.run_used + estimated_tokens > self.max_run:
            logger.warning("Run token budget exhausted — stopping")
            return False
        return True

    def record(self, used: int) -> None:
        self.run_used += used
```

#### 5.5 LangSmith Tracing

Every LLM call wrapped with LangSmith for visual observability:

```python
from langsmith import traceable

@traceable(name="classify_email", tags=["phase2", "haiku"])
def classify_with_tracing(system, user, tools):
    return provider.complete(system=system, user=user, tools=tools)
```

Dashboard shows: latency per call, token cost, tool selection distribution, HITL rate over time.

### Phase 2 Security Additions

| Concern | Phase 1 | Phase 2 |
|---|---|---|
| API keys | `.env` file | AWS Parameter Store (encrypted, IAM-scoped) |
| Key rotation | Manual | Parameter Store versioning |
| Audit trail | None | CloudTrail logs every parameter access |
| Cost control | None | Token budgeting + spend alerts |
| Observability | Log files | LangSmith visual dashboard |

---

## 6. Phase 3 Architecture

### Infrastructure Overview

```
Internet
    │
    ▼
AWS SQS Queue
(email events)
    │
    ▼
AWS Lambda (Docker container)
├── PII Scrubber (Presidio)
├── ModelRouter → BedrockAdapter
│                  └── AWS Bedrock (Claude 3.5)
│                       Private VPC endpoint
│                       No public internet
├── HITL Gate
├── Tool Executor
└── Audit Logger
    │
    ├── RDS PostgreSQL (classifications)
    ├── DynamoDB (audit log — immutable)
    └── CloudWatch (metrics + alerts)
```

### 6.1 AWS Bedrock Adapter

```python
class BedrockProvider(LLMProvider):
    def __init__(self, model_id: str, region: str):
        # Uses IAM role — no API key needed
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            # Traffic stays within VPC via private endpoint
        )
        self.model_id = model_id

    def complete(self, system, user, tools, max_tokens=300):
        response = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "system": system,
                "tools": tools,
                "messages": [{"role": "user", "content": user}],
                "max_tokens": max_tokens,
            })
        )
        return ProviderResponse.from_bedrock(response)
```

**Key difference from Phase 2:**
- No API key — authenticates via IAM role attached to Lambda
- Traffic goes through VPC private endpoint — never touches public internet
- Data processing agreement covered under AWS Enterprise terms
- CloudTrail captures every Bedrock API call automatically

### 6.2 PII Scrubbing Layer

```python
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

class PIIScrubber:
    """
    Runs BEFORE the email body reaches the LLM.
    Strips: names, email addresses, phone numbers,
            account numbers, SSNs, credit card numbers.

    Input:  "John Smith account #4521 needs refund"
    Output: "[PERSON] account [ACCOUNT_NUMBER] needs refund"

    The classification is still accurate.
    The PII never leaves the network.
    """
    def scrub(self, text: str) -> tuple[str, list]:
        results = self.analyzer.analyze(text=text, language="en")
        anonymized = self.anonymizer.anonymize(text=text, analyzer_results=results)
        return anonymized.text, results  # results kept for internal audit only
```

### 6.3 SQS Event Queue (replaces polling)

```
Phase 1/2: while True: fetch_emails(); sleep(300)
Phase 3:   email arrives → SQS message → Lambda triggered immediately
```

Benefits:
- No missed emails during downtime (SQS retains messages)
- Lambda scales horizontally — 100 emails arrive simultaneously → 100 Lambda instances
- Dead letter queue for emails that fail processing 3+ times
- Zero polling cost vs constant running process

### 6.4 Immutable Audit Log

```python
# DynamoDB append-only table
# TTL: never (or 7 years for financial compliance)
{
    "pk": "email#<message_id>",
    "timestamp": "2026-04-01T22:19:18Z",
    "input_hash": "<sha256 of scrubbed email>",
    "model_used": "anthropic.claude-3-5-sonnet-v2",
    "tool_selected": "create_support_ticket",
    "confidence": 0.94,
    "action_taken": "ticket_created | id=TKT-20260401221918",
    "human_reviewed": false,
    "reasoning_trace": "..."
}
```

This table is write-once. No update or delete permissions granted to any role. Satisfies audit requirements for financial services.

### Phase 3 Security — Full Stack

| Layer | Implementation |
|---|---|
| Network | VPC private endpoint — Bedrock traffic never hits public internet |
| Authentication | IAM role per Lambda — no API keys anywhere |
| PII | Presidio scrubber before every LLM call |
| Input guardrails | Bedrock Guardrails — blocks injection attempts, enforces topic policy |
| Output guardrails | Bedrock Guardrails — validates output schema, blocks harmful content |
| Secrets | AWS Secrets Manager — auto-rotation, zero-code access |
| Audit | CloudTrail (infra) + DynamoDB (decisions) + CloudWatch (metrics) |
| Deployment | Docker image scanning, ECR vulnerability checks |

---

## 7. Data Flow Diagram

```
Phase 1 data flow:

Gmail IMAP ──► fetcher.py ──► main.py ──► agent.py
                                              │
                              memory.py ◄─────┤
                              (sender context)│
                                              ▼
                                      LLM API call
                                      (Anthropic)
                                              │
                                      tool_use response
                                              │
                                    ┌─────────┴─────────┐
                                    ▼                   ▼
                               conf >= 0.70         conf < 0.70
                                    │                   │
                               tools.py            hitl.py
                               (execute)           (queue)
                                    │
                                    ▼
                                  db.py
                              (save result)


Phase 3 data flow (same logic, different infrastructure):

SQS ──► Lambda ──► PIIScrubber ──► ModelRouter ──► BedrockAdapter
                                                         │
                                               Bedrock (private VPC)
                                                         │
                                               tool_use response
                                                         │
                                                  HITL Gate
                                                         │
                                              Tool Executor
                                                         │
                                     ┌───────────────────┤
                                     ▼                   ▼
                                 RDS PostgreSQL      DynamoDB
                                 (classifications)   (audit log)
                                                         │
                                                    CloudWatch
                                                   (metrics + alerts)
```

---

## 8. Interview Design Questions

**Q: Why ReAct instead of a simple prompt-and-parse classifier?**

ReAct gives the agent memory, reasoning transparency, and action autonomy. A basic classifier returns "Spam". The ReAct agent returns: category=Spam, confidence=0.94, reason="sender domain matches known phishing pattern, subject uses urgency triggers", action=ignored, latency=843ms. Every decision is explainable and auditable.

**Q: How would you scale this to 100,000 emails/day?**

Replace the polling loop with SQS. Lambda auto-scales horizontally — 100 emails arrive simultaneously, 100 Lambda instances process them in parallel. Add semantic caching to avoid LLM calls for repeat patterns. Use Bedrock batch inference for non-urgent emails processed overnight at 50% cost reduction.

**Q: How do you prevent the agent from acting on wrong classifications?**

Three layers: confidence threshold (0.70 minimum to act), HITL queue for uncertain decisions, and Bedrock Guardrails validating output schema before any action executes. A misclassified email goes to human review, not to an irreversible action.

**Q: How do you handle PII in a regulated environment?**

PII scrubbing runs before the LLM call using Microsoft Presidio. Names, account numbers, SSNs, and contact details are replaced with type tokens ([PERSON], [ACCOUNT_NUMBER]) before any text leaves the network. Classification accuracy is maintained because the model classifies intent, not identity. The scrubbing results are stored internally for audit but never sent externally.

**Q: Why AWS Bedrock over direct Anthropic API for production?**

Data residency compliance. Under RBI, SEBI, and GDPR, financial organisations must know exactly where customer data is processed. Bedrock Claude in ap-south-1 means data stays within AWS India region, covered under the bank's existing AWS enterprise agreement, with CloudTrail audit logs on every API call. Direct Anthropic API cannot provide these guarantees.

---

## 9. Technology Choices — Rationale

| Choice | Alternative | Why this |
|---|---|---|
| Python | Node.js | ML ecosystem, Anthropic SDK, Pandas, Presidio all Python-native |
| SQLite → PostgreSQL | MongoDB | Relational data (emails have clear schema), SQL skills universal in enterprise |
| Anthropic direct SDK | LangChain | Learning phase — understand the primitives before abstracting them |
| LangGraph (Phase 2+) | Custom orchestration | Stateful agent graphs — LangGraph is the right tool at that scale |
| AWS Parameter Store | HashiCorp Vault | AWS-native, zero additional infrastructure, free tier available |
| AWS Bedrock | Azure OpenAI | AWS-first architecture, same Claude model, better India region support |
| Docker + Lambda | EC2 | Serverless — pay per invocation, auto-scale, no idle cost |

---

*Document maintained alongside codebase. Update when architecture decisions change.*
