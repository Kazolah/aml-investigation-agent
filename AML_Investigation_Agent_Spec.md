# AML Suspicious Activity Investigation Agent

## Executive Summary

Financial institutions process millions of transactions daily. A small percentage trip AML (Anti-Money Laundering) monitoring rules — but at bank scale, that still means thousands of alerts every day. Today, a compliance analyst manually investigates each one: pulling transaction history, checking KYC profiles, screening watchlists, writing a case narrative, and deciding whether to file a Suspicious Activity Report (SAR). Each investigation takes 2–4 hours.

The problem is not the volume — it is the false positive rate. Industry research from BCG and Celent shows that **85–99% of AML alerts are false positives**. Analysts spend most of their time clearing noise, not catching real financial crime. Fatigue leads to inconsistent decisions. Genuine suspicious activity gets missed.

The regulatory stakes are real. In 2023, FINTRAC fined TD Bank $9.2M CAD for AML program deficiencies. In 2024, TD's US AML failures resulted in a **$3B USD penalty** — the largest in US banking history. Canada's OSFI is tightening its own model risk management requirements under **Guideline E-23**, effective May 2027, which mandates documented, auditable, human-overseen AI model decisions.

**This project builds an agentic AI investigation system that automates the 2–4 hour manual investigation down to under 5 minutes.** The agent enriches the alert, screens watchlists, reasons over the evidence using an LLM, and produces a structured SAR draft — then pauses for a human compliance officer to make the final filing decision. Every step is logged for regulatory audit.

It is not a replacement for existing tools like NICE Actimize or Oracle Mantas. It is an intelligence layer that sits on top of them — turning alert volume into a manageable, auditable, consistent workflow.

---

## Problem Description

### The Current Workflow

When a transaction trips an AML rule, a human analyst manually:

1. Pulls 90 days of transaction history from core banking systems
2. Looks up the customer's KYC/CDD profile and risk rating
3. Screens the counterparty against OFAC SDN lists, PEP databases, and adverse media
4. Reasons over all the evidence and writes a case narrative
5. Decides whether to clear the alert, put it under monitoring, or file a SAR with FINTRAC

**Time per alert: 2–4 hours.**

### Why This Is Broken

| Problem | Impact |
|---|---|
| 85–99% of alerts are false positives | Analysts waste the majority of their time on noise |
| Manual process is inconsistent | Decision quality varies by analyst experience and fatigue |
| No structured audit trail | Difficult to demonstrate regulatory compliance |
| Alert volume is growing | Real-time payments (RTR in Canada) will increase transaction velocity significantly |
| OSFI E-23 takes effect May 2027 | Banks need documented, auditable AI model governance now |

### The Business Case

- **Analyst productivity:** Reducing investigation time from 2–4 hours to under 5 minutes frees analysts to focus on genuine high-risk cases
- **Consistency:** LLM-driven reasoning applies the same criteria to every alert, every time
- **Regulatory readiness:** Built-in audit trail satisfies OSFI E-23 model lifecycle requirements out of the box
- **Cost:** Estimated $50–100M in analyst time is spent annually on false positives at Big 6 bank scale

---

## High-Level Architecture

### Agent Graph

The system is built as a **stateful directed graph** using LangGraph. Each node in the graph is a step in the investigation. The graph supports conditional routing, retry loops, and a hard pause for human review.

```
[Alert Ingestion]
      ↓
[Transaction Enrichment]     ← 90-day history, velocity patterns
      ↓
[CDD Lookup]                 ← KYC profile, risk rating, EDD flag
      ↓
[Watchlist Screening]        ← OFAC SDN, PEP lists, adverse media
      ↓  ↑ (retry loop, max 2x if service fails)
[LLM Reasoning]              ← GPT-4o: suspicion score + red flags + SAR narrative
      ↓
[Decision Router]            ← conditional edges based on score + watchlist
   ↙           ↓          ↘
Auto-Clear   Human       Auto-Flag
             Review
               ↓
    [SAR Draft Generation]
               ↓
          [Audit Log]
```

### Decision Routing

| Condition | Route |
|---|---|
| Score LOW + no watchlist hits | Auto-clear → audit log → done |
| Score HIGH + watchlist hit | Recommend SAR → human review |
| Score MEDIUM or ambiguous | Human review |
| Watchlist service failed after 2 retries | Human review with failure flag |
| LLM output unparseable | Human review with raw output attached |

### POC vs Production

This project is built in two clearly defined layers:

**POC (what gets built and published):**
Single-instance, synchronous agent. Mock data via SQLite. Streamlit UI for the human review node. Demonstrates all agentic patterns.

**Production design (documented as architecture):**
Async, queue-driven, multi-worker. Azure Event Hubs for alert ingestion. AKS for horizontally scalable agent workers. PostgreSQL for case store and audit log. React UI behind Azure AD SSO.

```
AML Rules Engine (e.g. Actimize)
        ↓
   Azure Event Hubs              ← alert events
        ↓
  Agent Worker Pool              ← LangGraph workers (AKS, autoscaled on queue depth)
        ↓
  Azure OpenAI (private endpoint) ← within bank VNet
        ↓
  PostgreSQL                     ← case store + append-only audit log
        ↓
  Human Review UI                ← React + Azure AD SSO
        ↓
  Analytics Dashboard            ← Grafana / Power BI → OSFI E-23 reporting
```

---

## Tech Stack

### Stack Overview

| Component | Technology | Layer |
|---|---|---|
| Agent orchestration | LangGraph (Python) | POC + Production |
| LLM | Azure OpenAI GPT-4o | POC + Production |
| Case store | SQLite → PostgreSQL | POC → Production |
| Watchlist | Mock JSON → Sanctions API | POC → Production |
| Human review UI | Streamlit → React + Azure AD | POC → Production |
| Observability | LangSmith | POC + Production |
| Message queue | — → Azure Event Hubs | Production |
| Container orchestration | — → Azure Kubernetes Service | Production |
| Analytics | — → Grafana / Power BI | Production |
| Secrets management | `.env` → Azure Key Vault | POC → Production |

---

## Why This Tech Stack

### LangGraph — not plain Python, not CrewAI, not AutoGen

This is the most important justification in the project. **The choice of LangGraph is driven by four hard requirements** that plain Python cannot satisfy cleanly:

**1. Retry cycles**
The watchlist screening node calls an external service that can fail. The agent must retry up to twice, then reroute — without crashing or losing state. In LangGraph this is a cycle in the graph, declared in three lines. In plain Python it requires manual state-tracking retry logic wrapped around every external call.

**2. Human-in-the-loop pause**
A compliance officer must review and approve before any SAR is filed. The agent must pause indefinitely, wait for human input, accept a modified decision, and resume from exactly where it stopped. LangGraph's `interrupt_before` does this natively. Plain Python requires building an async queue, a callback handler, and a state resumption mechanism from scratch.

**3. Crash recovery via checkpointing**
If the agent process crashes between node 4 and node 5 — mid-investigation — it must resume from that exact point without reprocessing the alert. LangGraph's SQLite checkpointer persists state at every node automatically. Plain Python restarts from the beginning.

**4. Automatic audit trail**
OSFI E-23 requires a complete, traceable record of every model decision. LangGraph's state object accumulates data at every node transition. The audit log node simply writes that accumulated state to a ledger — no manual instrumentation at each step.

> **Why not CrewAI or AutoGen?**
> CrewAI is role-based and better suited for parallel agent collaboration. AutoGen is Microsoft-backed and strong for conversational multi-agent workflows. Neither offers LangGraph's fine-grained control over graph state, conditional routing, and the `interrupt_before` human pause pattern. For a regulated workflow where every decision must be explainable and auditable, LangGraph's explicit graph model is the right tool.

### Azure OpenAI GPT-4o — not OpenAI directly

Banks operate within strict data residency and network isolation requirements. Azure OpenAI runs within the bank's Azure tenant on a private endpoint — transaction data never leaves the bank's network. Using OpenAI's public API is not viable in a real financial institution deployment.

### PostgreSQL + pgvector — not just a document store

PostgreSQL is the production choice for three reasons: it handles relational case data, it supports append-only audit tables with row-level security (required for OSFI E-23 tamper-evident logs), and pgvector enables similarity search across historical SAR narratives — so the LLM can be grounded with the 3 most similar past cases when reasoning about a new alert.

### LangSmith — observability from day one

LangSmith traces every node execution: latency, token usage, LLM input/output, and retry counts. In the POC this is the primary observability tool. In production, PII must be scrubbed before traces are sent. The key metric LangSmith enables is the **human override rate** — how often compliance officers disagree with the LLM recommendation. A rising override rate is an early signal of model drift.

---

## Detailed Build Plan

### What Gets Built (BOB Scope)

The BOB build covers the full POC layer: working LangGraph agent, all 9 nodes, human review UI, audit log, LangSmith tracing, and deployment. The production architecture is documented but not fully built — that distinction is made explicit in the README.

---

### Week 1 — Foundation: State Object + Mock Data

**Goal:** Working data layer. No agent logic yet.

**Tasks:**
- Define `AMLState` TypedDict — the state object passed between all graph nodes
- Build `seed_data.py` — generates 5 realistic alert scenarios, one per AML rule type
- Set up SQLite with two tables: `transactions` (90-day history) and `cdd_profiles` (KYC data)
- Create `watchlist.json` — mock OFAC SDN and PEP entries
- Validate that seed data looks realistic: proper BIC codes, ISO 4217 currency codes, FATF jurisdiction flags

**AML rule scenarios to seed:**
- STRUCTURING — multiple transactions just below $10K CAD in 24 hours
- VELOCITY_ANOMALY — account transacts 10x its 90-day average in one day
- HIGH_RISK_JURISDICTION — wire to FATF grey-listed country
- PEP_COUNTERPARTY — counterparty matches a PEP list entry
- ADVERSE_MEDIA — counterparty name in negative news mock data

**State object:**
```python
class AMLState(TypedDict):
    alert: dict                  # raw alert input
    transaction_history: list    # 90-day enrichment
    cdd_profile: dict            # KYC/CDD data
    watchlist_result: dict       # screening result + retry count
    llm_reasoning: dict          # score, red_flags, narrative, recommendation
    decision: str                # auto_clear | human_review | file_sar
    human_override: dict         # compliance officer input + identity
    sar_draft: dict              # structured SAR fields
    audit_trail: list            # full transition log
```

**Deliverable:** `seed_data.py` runs, populates SQLite, data is queryable.

---

### Week 2 — Nodes 1–4: Linear Path (No LLM)

**Goal:** Alert flows through enrichment, CDD, and watchlist. No LLM yet.

**Tasks:**
- Build `alert_ingestion.py` — accepts alert JSON, loads into state
- Build `enrichment.py` — queries SQLite for 90-day transaction history, computes velocity metrics
- Build `cdd_lookup.py` — queries SQLite for KYC profile, sets EDD flag if risk rating is HIGH or country is FATF-listed
- Build `watchlist.py` — checks counterparty against `watchlist.json`, returns match result
- Add retry logic to watchlist node: if lookup raises an exception, retry up to 2x before setting `watchlist_failed: true` in state
- Wire all nodes into `graph.py` as a linear chain
- Write `test_nodes.py` — unit test each node with fixture data

**Deliverable:** `python run_agent.py --alert ALT-001` runs the alert through nodes 1–4 and prints enriched state to console.

---

### Week 3 — Nodes 5–6: LLM Reasoning + Conditional Routing

**Goal:** LLM reasons over enriched data. Graph branches based on output.

**Tasks:**
- Set up Azure OpenAI credentials in `.env.example`
- Build `llm_reasoning.py` — constructs prompt from state, calls GPT-4o, parses JSON response
- Build `decision_router.py` — reads `llm_reasoning.suspicion_score` and `watchlist_result`, returns next node name
- Add conditional edges to `graph.py` based on router output
- Handle LLM output failure: if JSON parsing fails, route to human review with raw output in state
- Test all five routing paths with fixture alerts

**LLM prompt:**
```
You are an AML compliance analyst. Review the following case and return a JSON
object with exactly these fields:

  suspicion_score: 'low' | 'medium' | 'high'
  red_flags: [list of specific red flags observed]
  sar_narrative: 'Draft narrative in FINTRAC format'
  recommendation: 'auto_clear' | 'monitor' | 'file_sar'
  reasoning: 'Brief explanation of your decision'

CASE DATA:
Transaction History: {transaction_history}
CDD Profile: {cdd_profile}
Watchlist Result: {watchlist_result}
Rule Fired: {rule_fired}

Return JSON only. No preamble. No markdown.
```

**Deliverable:** Full alert runs through LLM, produces a score, routes to correct branch. All 5 routing paths tested.

---

### Week 4 — Node 7: Human Review (The LangGraph Showcase)

**Goal:** Agent pauses for human input. Officer approves, overrides, or sends back.

**Tasks:**
- Add `interrupt_before=["human_review"]` to graph compilation
- Build `human_review.py` — reads current state, waits for external input to resume
- Build `streamlit_app.py` — the compliance officer UI:
  - Alert summary panel
  - 90-day transaction table
  - CDD profile panel
  - Watchlist result panel
  - LLM reasoning display (score, red flags, draft narrative)
  - Three action buttons: Approve / Override / Send Back
  - Override requires a mandatory text note
- Wire Streamlit to resume the graph via LangGraph's `update_state` + `stream` after human input
- Test all three officer actions: approve, override (with note), send back (loops to enrichment)

**Deliverable:** Running demo — alert pauses at human review, officer acts in UI, graph resumes. This is the primary demo video segment.

---

### Week 5 — Nodes 8–9: SAR Generation + Audit Log

**Goal:** SAR draft produced on approval. Full audit trail written.

**Tasks:**
- Build `sar_generator.py` — maps state fields to FINTRAC STR structure:
  - Part A: Reporting entity (mock bank details)
  - Part B: Subject (account holder from CDD)
  - Part C: Transactions (from enrichment)
  - Part D: Suspicious activity description (from LLM narrative, human-reviewed)
  - Part E: Red flags (from LLM reasoning)
  - Part F: Action taken
- Output SAR as JSON + render to PDF via ReportLab
- Build `audit_log.py` — writes `audit_trail` list from state to SQLite `audit_log` table on every terminal node (auto-clear, file SAR)
- Wire LangSmith: add `LANGCHAIN_TRACING_V2=true` to env, verify traces appear in LangSmith dashboard
- Write `test_graph.py` — end-to-end integration tests for all routing paths

**Deliverable:** End-to-end run produces a SAR PDF and a complete audit log. LangSmith shows full trace.

---

### Week 6 — Hardening + Deployment

**Goal:** Project ready to publish, demo, and deploy.

**Tasks:**
- Swap SQLite checkpointer for the persistent version — verify crash recovery works (kill process mid-graph, restart, confirm it resumes)
- Add LangGraph graph visualisation export — include the `.png` in the repo
- Write `README.md`:
  - What it is and why it matters (pull from executive summary)
  - Architecture diagram
  - How to run locally
  - How to run the demo walkthrough
  - OSFI E-23 alignment notes
  - POC vs production distinction — explicitly documented
- Write `docs/osfi_e23_alignment.md` — map each graph node to the relevant E-23 principle
- Write `docs/production_architecture.md` — the full production design with Event Hubs, AKS, managed identity, analytics
- Containerise with Docker: `Dockerfile` for agent + `Dockerfile` for Streamlit UI
- Deploy to Azure Container Apps
- Record demo walkthrough: alert ingestion → enrichment → LLM reasoning → human review pause → SAR generation → audit log

**Deliverable:** Public GitHub repo. Deployed demo. README is the Medium article draft.

---

### Week 7 (Optional) — Production Patterns

**Goal:** Show what a real deployment looks like, not just document it.

**Tasks:**
- Build `infra/event_hubs_consumer.py` — async worker that reads from Azure Event Hubs and invokes the graph with `ainvoke`
- Replace SQLite with PostgreSQL checkpointer
- Add `pgvector` similarity search: embed past SAR narratives, retrieve top-3 similar cases as LLM context
- Build a simple Grafana dashboard: alert volume, auto-clear rate, override rate, latency p95

**Deliverable:** Production pattern demonstrated in code, not just described in docs.

---

## Repository Structure

```
aml-investigation-agent/
├── README.md                      # executive summary, architecture, quickstart
├── requirements.txt
├── Dockerfile
├── .env.example
│
├── agent/
│   ├── graph.py                   # LangGraph graph definition + compilation
│   ├── state.py                   # AMLState TypedDict
│   ├── nodes/
│   │   ├── alert_ingestion.py
│   │   ├── enrichment.py
│   │   ├── cdd_lookup.py
│   │   ├── watchlist.py
│   │   ├── llm_reasoning.py
│   │   ├── decision_router.py
│   │   ├── human_review.py
│   │   ├── sar_generator.py
│   │   └── audit_log.py
│   └── prompts/
│       └── reasoning_prompt.py
│
├── data/
│   ├── seed_data.py               # generates mock alerts + SQLite DB
│   ├── watchlist.json             # mock OFAC SDN + PEP
│   └── mock_transactions.db       # generated by seed_data.py
│
├── ui/
│   └── streamlit_app.py           # human review interface
│
├── tests/
│   ├── test_nodes.py              # unit tests per node
│   ├── test_graph.py              # end-to-end integration tests
│   └── fixtures/                  # one alert JSON per rule type
│
├── infra/                         # production architecture
│   ├── event_hubs_consumer.py     # async worker (Week 7)
│   └── k8s/                       # AKS manifests (Week 7)
│
└── docs/
    ├── architecture.png           # LangGraph graph visualisation
    ├── osfi_e23_alignment.md      # regulatory framing
    ├── production_architecture.md # full production design
    └── sample_walkthrough.md      # alert → SAR trace with audit log output
```

---

## OSFI E-23 Alignment

Canada's OSFI Guideline E-23 (Model Risk Management, effective May 2027) requires financial institutions to maintain documented, auditable, governed AI model decisions. This project addresses E-23 directly:

| E-23 Requirement | How This Project Addresses It |
|---|---|
| Model lifecycle governance | Audit log captures every state transition from alert to SAR decision |
| Human oversight for high-risk decisions | `interrupt_before` human review node is mandatory for medium/high suspicion scores |
| Model performance monitoring | LangSmith traces suspicion score distribution, override rate, latency |
| Explainability | LLM reasoning node produces structured red flags and narrative — not a black box score |
| Consistent application | Same criteria applied to every alert via prompt — no analyst variability |

---

## Production Considerations

> This section documents what a real bank deployment requires. The POC does not implement all of this — that distinction is intentional and explicit.

| Concern | POC | Production |
|---|---|---|
| Alert ingestion | Direct function call | Azure Event Hubs — queue-driven, async |
| Agent execution | Synchronous, single instance | Async `ainvoke`, AKS worker pool scaled on queue depth |
| LLM access | Public API key | Azure OpenAI private endpoint within bank VNet |
| Authentication | None | Azure AD SSO — officer identity logged in audit trail |
| Secrets | `.env` file | Azure Key Vault |
| Audit log | SQLite | PostgreSQL — append-only, row-level security |
| PII in traces | Exposed | Scrubbed before LangSmith |
| Data residency | Local | Azure Canada region only |
| Analytics | LangSmith only | Grafana dashboard → OSFI E-23 reporting |

---

*Built by Latt — Senior Solution Architect, Cloud & Payments*  
*Published as an open portfolio project. See `docs/production_architecture.md` for enterprise deployment design.*
