# Production Architecture

> This document describes the full production deployment design for the AML Investigation Agent. The POC builds and runs locally — this document describes what a real bank deployment requires. Not all of this is built in the POC.

---

## Architecture Overview

```
AML Rules Engine (e.g. Actimize / Mantas)
        │
        ▼
Azure Event Hubs                    ← Alert events (async, queue-driven)
        │
        ▼
AKS Worker Pool                     ← LangGraph ainvoke workers
(autoscaled on queue depth)         ← Managed identity — no stored secrets
        │
   ┌────┴────────────────┐
   │                     │
   ▼                     ▼
Azure OpenAI          PostgreSQL + pgvector
(Private Endpoint)    (Case store + append-only audit log)
GPT-4o                Row-level security
Canada Central VNet
   │
   ▼
Refinitiv World-Check / Dow Jones   ← Live sanctions API
        │
        ▼
React UI (Azure AD SSO)             ← Human review interface
        │
        ▼
LangSmith (PII scrubbed)            ← Observability
        │
        ▼
Grafana / Power BI                  ← OSFI E-23 reporting dashboard
```

---

## Component Breakdown

### Alert Ingestion — Azure Event Hubs

**POC:** Direct function call via CLI
**Production:** Azure Event Hubs consumer group

- Each AML monitoring engine (Actimize, Mantas, internal rules) publishes alert events to a dedicated Event Hubs namespace
- Worker pool subscribes to the consumer group and processes alerts concurrently
- Dead-letter queue captures alerts that fail after all retries — surfaced in the Grafana dashboard

```python
# infra/event_hubs_consumer.py (Week 7 pattern)
async def consume_alerts(consumer_client: EventHubConsumerClient):
    async with consumer_client:
        async for event in consumer_client.receive():
            alert = json.loads(event.body_as_str())
            asyncio.create_task(process_alert(alert))
```

---

### Agent Execution — AKS Worker Pool

**POC:** Synchronous, single instance, `graph.invoke()`
**Production:** Async `graph.ainvoke()`, AKS Deployment scaled on Event Hubs queue depth

```yaml
# infra/k8s/agent-deployment.yaml (pattern)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aml-agent-worker
spec:
  replicas: 3                    # Base replica count
  template:
    spec:
      containers:
      - name: agent
        image: aml-agent:latest
        env:
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: aml-secrets
              key: azure-openai-key
```

**KEDA ScaledObject** scales workers based on Event Hubs queue depth — automatically provisions more workers during peak alert volume.

---

### LLM — Azure OpenAI (Private Endpoint)

**POC:** Public OpenAI API, API key in `.env`
**Production:** Azure OpenAI within bank VNet

- Deployed in Azure Canada Central region
- Private endpoint — traffic never traverses public internet within the bank's Azure tenant
- Managed identity replaces API keys — no stored secrets
- Data residency: all inference stays within the Canada Central regional boundary
- Model: GPT-4o (or equivalent — evaluated per bank's model risk policy)

> **Important:** Azure OpenAI still operates within Microsoft's managed cloud infrastructure. Institutions with strict on-premises data requirements should evaluate self-hosted open-weight models (e.g. Llama 3) deployed on Azure Arc or AKS.

```python
# Production LLM client swap (langchain_openai supports Azure natively)
from langchain_openai import AzureChatOpenAI

llm = AzureChatOpenAI(
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version=os.getenv("OPENAI_API_VERSION"),
    # No API key — uses managed identity via DefaultAzureCredential
)
```

---

### Case Store — PostgreSQL + pgvector

**POC:** SQLite (transactions, CDD profiles, audit log, checkpointer)
**Production:** Azure Database for PostgreSQL — Flexible Server

**Schema additions:**

```sql
-- Append-only audit log — no UPDATE or DELETE permissions on this table
CREATE TABLE audit_log (
    id          BIGSERIAL PRIMARY KEY,
    alert_id    TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    node_name   TEXT NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decision    TEXT,
    officer_id  TEXT,
    summary     TEXT,
    full_state  JSONB         -- complete AMLState at terminal node
);

-- Row-level security — officers can SELECT, nobody can UPDATE/DELETE
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_log_select ON audit_log FOR SELECT USING (true);
-- No INSERT policy needed — service account writes via application role

-- SAR narrative embeddings for similarity search
CREATE TABLE sar_embeddings (
    id          BIGSERIAL PRIMARY KEY,
    alert_id    TEXT NOT NULL,
    sar_id      TEXT NOT NULL,
    narrative   TEXT,
    embedding   vector(1536),  -- text-embedding-3-small dimension
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON sar_embeddings USING ivfflat (embedding vector_cosine_ops);
```

**pgvector — similar case retrieval:**

At LLM reasoning time, retrieve the 3 most similar historical SAR narratives as context:

```python
similar_cases = db.execute("""
    SELECT sar_id, narrative, 1 - (embedding <=> %s) AS similarity
    FROM sar_embeddings
    ORDER BY embedding <=> %s
    LIMIT 3
""", [query_embedding, query_embedding]).fetchall()
```

---

### Human Review UI — React + Azure AD SSO

**POC:** Streamlit, no authentication
**Production:** React SPA, Azure AD SSO, officer identity in every audit record

- Officers log in via Azure AD — identity is JWT-verified on every API call
- Officer ID is written to `audit_log.officer_id` on every human review action
- Override notes are mandatory and stored in the audit record
- The UI is a React SPA served from Azure Static Web Apps, backed by a FastAPI service on AKS

---

### Observability — LangSmith + Grafana

**POC:** LangSmith with full PII visible
**Production:** LangSmith with PII scrubbed, feeding Grafana

**PII scrubbing before traces are sent:**

```python
from langsmith import Client
from langsmith.run_helpers import traceable

def scrub_pii(state: dict) -> dict:
    """Remove customer PII from state before sending to LangSmith."""
    scrubbed = dict(state)
    if "cdd_profile" in scrubbed:
        scrubbed["cdd_profile"] = {
            "account_id": scrubbed["cdd_profile"].get("account_id"),
            "risk_rating": scrubbed["cdd_profile"].get("risk_rating"),
            # full_name, date_of_birth, nationality — redacted
        }
    return scrubbed
```

**Grafana dashboard panels (OSFI E-23 reporting):**

| Panel | Query | E-23 Principle |
|---|---|---|
| Alert volume (hourly) | `COUNT(*) FROM audit_log GROUP BY hour` | Model usage |
| Auto-clear rate | `auto_clears / total` | Performance |
| Human override rate | `overrides / human_reviews` | Drift detection |
| LLM parse error rate | `parse_errors / llm_calls` | Model reliability |
| Watchlist retry rate | `retried / total_watchlist_calls` | External dependency health |
| Latency p95 (LLM node) | LangSmith trace metadata | Performance SLA |

---

### Secrets Management — Azure Key Vault

**POC:** `.env` file
**Production:** Azure Key Vault + managed identity

```python
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

credential = DefaultAzureCredential()
client = SecretClient(vault_url=os.getenv("AZURE_KEY_VAULT_URL"), credential=credential)

openai_key = client.get_secret("azure-openai-key").value
```

---

## Production Gap Summary

| Concern | POC | Production |
|---|---|---|
| Alert ingestion | CLI invoke | Azure Event Hubs |
| Concurrency | Synchronous | Async ainvoke, AKS autoscaled |
| LLM | Public OpenAI API | Azure OpenAI private endpoint |
| Authentication | None | Azure AD SSO |
| Secrets | `.env` | Azure Key Vault + managed identity |
| Case store | SQLite | PostgreSQL + pgvector |
| Audit log | SQLite INSERT | PostgreSQL append-only, RLS |
| Watchlist | Mock JSON | Refinitiv World-Check / Dow Jones API |
| Retry scope | Watchlist only | All external API calls |
| PII in traces | Exposed | Scrubbed before LangSmith |
| Data residency | Local | Azure Canada Central |
| Analytics | LangSmith only | Grafana + Power BI |
| Similar case retrieval | Not implemented | pgvector similarity search |

---

*See `docs/osfi_e23_alignment.md` for the regulatory compliance mapping.*
