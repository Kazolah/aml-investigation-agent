# OSFI E-23 Alignment

> **Disclaimer:** This document provides a design-level mapping of the AML Investigation Agent to OSFI Guideline E-23 principles. Formal compliance validation requires review by a qualified model risk officer at a federally regulated financial institution.

Canada's **OSFI Guideline E-23 — Model Risk Management** (effective May 2027) requires federally regulated financial institutions to establish a model risk management framework that addresses the full lifecycle of AI/ML models — from development through deployment, monitoring, and decommissioning.

This document maps each component of the AML Investigation Agent to the relevant E-23 principles.

---

## E-23 Principle Mapping

### 1. Model Lifecycle Governance
> *E-23 requires documented governance of model development, validation, deployment, and retirement.*

**How this project addresses it:**

The `audit_log` node writes a complete record of every state transition to a persistent `audit_log` table at every terminal node. The record includes:
- The full `AMLState` at the time of the terminal decision (serialised JSON)
- Every node that executed, with timestamps and summaries
- The routing decision and the inputs that drove it
- The officer identity and action if human review occurred

This produces an immutable, append-only record of every model decision the system made — traceable from raw alert to final SAR filing or clearing decision.

**Relevant files:**
- [`agent/nodes/audit_log.py`](../agent/nodes/audit_log.py) — writes to `audit_log` table
- [`agent/state.py`](../agent/state.py) — `audit_trail` field uses `operator.add` reducer (append-only)

---

### 2. Human Oversight for High-Risk Decisions
> *E-23 requires human review and approval for consequential model decisions, particularly in high-risk contexts.*

**How this project addresses it:**

The graph is compiled with `interrupt_before=["node_human_review"]`. This means:
- The agent **cannot file a SAR without a human compliance officer taking an explicit action**
- The graph pauses indefinitely at the interrupt point — it does not time out
- The officer has three choices: approve, override (with mandatory note), or send back for re-investigation
- The officer's identity, action, and any override note are logged in the audit trail

Medium and high suspicion scores always route to human review. Low scores with no watchlist hits auto-clear, but the clearing decision is still logged.

**Relevant files:**
- [`agent/nodes/human_review.py`](../agent/nodes/human_review.py) — the interrupt node
- [`agent/graph.py`](../agent/graph.py) — `interrupt_before=["node_human_review"]`
- [`ui/streamlit_app.py`](../ui/streamlit_app.py) — the officer review interface

---

### 3. Model Performance Monitoring
> *E-23 requires ongoing monitoring of model outputs, including tracking of metrics that indicate model drift or degradation.*

**How this project addresses it:**

LangSmith traces every node execution when `LANGCHAIN_TRACING_V2=true` is configured. The raw trace data includes:
- Latency per node
- Token usage per LLM call
- LLM inputs and outputs (full prompt + response)
- Routing decisions and the state that drove them

From this trace data, the following operational metrics can be derived as custom metrics:

| Metric | Computation | Signal |
|---|---|---|
| **Human override rate** | `overrides / total_human_reviews` | Rising rate → model drift or prompt degradation |
| **Suspicion score distribution** | Histogram of `low / medium / high` over time | Shift toward all-high → miscalibrated prompt |
| **Auto-clear rate** | `auto_clears / total_alerts` | Sustained low rate → too many false positives reaching officers |
| **Watchlist retry rate** | `retried / total_watchlist_calls` | Rising rate → external service degradation |
| **LLM parse error rate** | `parse_errors / total_llm_calls` | Rising rate → model output format drift |

> **Note:** These metrics are not built into LangSmith's dashboard. They must be computed using LangSmith's feedback and metadata APIs, or by querying the `audit_log` table directly.

**Relevant files:**
- [`.env.example`](../.env.example) — `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`

---

### 4. Explainability
> *E-23 requires that model decisions be explainable and that the factors driving a decision be documented.*

**How this project addresses it:**

The LLM reasoning node does not produce a black-box score. It returns a structured JSON object with:
- `suspicion_score` — the assessed risk level
- `red_flags` — a list of specific, named observations from the case data
- `sar_narrative` — a human-readable narrative in FINTRAC STR format explaining the suspicious activity
- `reasoning` — a brief explanation of the scoring decision
- `recommendation` — the action recommended

Every field is stored in the `audit_trail` and included in the SAR draft (Parts D and E). An officer reviewing the case sees exactly what the model saw and why it reached its conclusion.

**Relevant files:**
- [`agent/nodes/llm_reasoning.py`](../agent/nodes/llm_reasoning.py) — structured output parsing
- [`agent/prompts/reasoning_prompt.py`](../agent/prompts/reasoning_prompt.py) — prompt that produces the structured output
- [`agent/nodes/sar_generator.py`](../agent/nodes/sar_generator.py) — Part D (narrative) and Part E (red flags)

---

### 5. Consistent Application
> *E-23 requires that models be applied consistently across similar cases, without arbitrary variation.*

**How this project addresses it:**

The same prompt template is applied to every alert, every time. There is no analyst variability in the reasoning step — the model receives the same structured inputs (transaction history, CDD profile, watchlist result, rule fired) and is asked to evaluate them using the same criteria.

The prompt explicitly defines:
- The scoring criteria for `low / medium / high`
- The recommendation logic (`auto_clear / monitor / file_sar`)
- The format of the SAR narrative

**Relevant files:**
- [`agent/prompts/reasoning_prompt.py`](../agent/prompts/reasoning_prompt.py) — the canonical prompt
- [`agent/nodes/llm_reasoning.py`](../agent/nodes/llm_reasoning.py) — `temperature=0` for deterministic output

---

## What This Project Does Not Cover

| E-23 Area | Status | Notes |
|---|---|---|
| Model validation (independent review) | Not implemented | Requires a separate validation team to review model outputs against ground truth |
| Model inventory | Not implemented | Production would require registration in the institution's model inventory system |
| Vendor risk management | Not implemented | Azure OpenAI as a third-party model provider requires vendor due diligence under E-23 |
| PII handling in traces | Not implemented | LangSmith traces contain customer PII in the POC. Production requires PII scrubbing before traces are sent |
| Data governance | Partially | Mock data only. Production requires formal data lineage and access controls |

---

*This document should be reviewed alongside `docs/production_architecture.md` for the full production deployment design.*
