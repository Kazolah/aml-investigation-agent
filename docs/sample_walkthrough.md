# Sample Walkthrough — Alert to SAR

This document traces a complete investigation of **ALT-003 (HIGH_RISK_JURISDICTION)** from alert ingestion through to SAR generation and audit log.

---

## Setup

```bash
# Seed the database
python data/seed_data.py

# Process the alert
python run_agent.py --alert ALT-003
```

---

## Step 1 — Alert Ingestion

**Input alert (from `data/alerts` table):**

```json
{
  "alert_id": "ALT-003",
  "account_id": "ACC-003",
  "rule_fired": "HIGH_RISK_JURISDICTION",
  "amount": 51200.00,
  "currency": "CAD",
  "counterparty": "Global Trade Corp",
  "counterparty_account": "SWIFT-AYABANK-MM-00192",
  "alert_metadata": {
    "rule_description": "SWIFT wire to FATF grey-listed jurisdiction",
    "destination_country": "MM",
    "destination_country_name": "Myanmar",
    "fatf_list": "grey",
    "beneficiary_bank": "Ayeyarwady Bank",
    "beneficiary_city": "Yangon"
  }
}
```

**Audit trail entry:**
```
alert_ingestion · Alert ALT-003 received — rule=HIGH_RISK_JURISDICTION, account=ACC-003, amount=51200.0 CAD
```

---

## Step 2 — Transaction Enrichment

90 transactions retrieved for ACC-003. Key velocity metrics:

| Metric | Value |
|---|---|
| Total transactions (90d) | 90 |
| Total volume (90d) | CAD 420,733.75 |
| Avg daily volume | CAD 4,152.06 |
| Today's volume | CAD 51,200.00 |
| **Velocity ratio** | **12.33x** |

**Audit trail entry:**
```
enrichment · Retrieved 90 transactions for ACC-003 over past 90 days.
             Velocity ratio: 12.33x (today $51,200.00 vs avg $4,152.06/day)
```

---

## Step 3 — CDD Lookup

Customer profile for ACC-003:

| Field | Value |
|---|---|
| Name | Aung Kyaw Zin |
| Nationality | MM (Myanmar) |
| Country of Residence | CA (Canada) |
| Risk Rating | **HIGH** |
| Occupation | International Trade Broker |
| FATF-listed country | **Yes** |
| EDD Required | **Yes** |

**Audit trail entry:**
```
cdd_lookup · CDD profile loaded for ACC-003 (Aung Kyaw Zin).
             Risk rating: HIGH. EDD required: True — reasons: risk_rating=HIGH, country=CA (FATF grey list)
```

---

## Step 4 — Watchlist Screening (with retry)

**Attempt 1:** Simulated transient failure — service unavailable.

**Attempt 2:** Success.

| Field | Value |
|---|---|
| Screened name | Global Trade Corp |
| Match found | **Yes** |
| List | **OFAC_SDN** |
| Matched entity | Global Trade Corp |
| Program | SDGT (Sanctions evasion — Iran) |
| Confidence | 1.00 |
| Retry count | 1 |

**Audit trail entry:**
```
watchlist_screening · Screened 'Global Trade Corp' against watchlist (retry_count=1).
                      Match found: Global Trade Corp (OFAC_SDN, confidence=1.0)
```

---

## Step 5 — LLM Reasoning

GPT-4o receives the structured prompt with all enriched data and returns:

```json
{
  "suspicion_score": "high",
  "red_flags": [
    "Transaction to FATF grey-listed jurisdiction",
    "High-risk customer profile",
    "Transaction velocity significantly above baseline",
    "Counterparty on OFAC SDN list"
  ],
  "sar_narrative": "The account holder, Aung Kyaw Zin, an international trade broker
    residing in Canada, conducted a SWIFT wire transfer of CAD 51,200.00 to Global Trade Corp,
    with the beneficiary bank being Ayeyarwady Bank in Yangon, Myanmar. This transaction is
    suspicious due to the destination being a FATF grey-listed jurisdiction and the counterparty
    being on the OFAC SDN list for SDGT. The transaction amount is significantly higher than
    the average daily volume, indicating unusual transaction velocity...",
  "recommendation": "file_sar",
  "reasoning": "The transaction involves a high-risk jurisdiction and a counterparty on the
    OFAC SDN list, combined with a high-risk customer profile and unusual transaction velocity.
    These factors strongly indicate potential money laundering activities."
}
```

**Audit trail entry:**
```
llm_reasoning · LLM reasoning complete. Score: high. Recommendation: file_sar. Red flags: 4 identified.
```

---

## Step 6 — Decision Router

| Input | Value |
|---|---|
| Suspicion score | high |
| Watchlist match | true (OFAC_SDN) |
| Watchlist failed | false |
| LLM parse error | false |
| **→ Route** | **human_review** |

**Audit trail entry:**
```
decision_router · Routing decision: human_review.
                  Score=high, watchlist_match=True, watchlist_failed=False, llm_parse_error=False
```

---

## Step 7 — ⏸ INTERRUPT: Human Review

The graph pauses here. State is persisted to the SQLite checkpointer.

**Streamlit UI displays:**
- Alert summary (amount, rule, counterparty)
- Transaction history table (90-day, velocity 12.33x)
- CDD profile (HIGH risk, FATF nationality, EDD required)
- Watchlist result (OFAC SDN match, confidence 100%)
- LLM reasoning (score=HIGH, 4 red flags, draft narrative)

**Officer action:** Override with note

```
action: override
officer_id: officer-001
override_note: "Confirmed via relationship manager — known trading partner.
                However OFAC SDN match requires mandatory SAR filing regardless."
reviewed_at: 2024-01-15T14:32:07
```

**Audit trail entry:**
```
human_review · Officer officer-001 overrode LLM recommendation.
               Override note: Confirmed via relationship manager...
```

---

## Step 8 — SAR Draft Generation

FINTRAC STR document generated:

```
SAR ID:  SAR-ALT-003-20240115

Part A — Reporting Entity:  Northern Trust Bank of Canada
Part B — Subject:           Aung Kyaw Zin, ACC-003, HIGH risk, FATF country
Part C — Transactions:      CAD 51,200 SWIFT wire + 90-day velocity summary
Part D — Suspicious Activity: [LLM narrative + officer override note]
Part E — Red Flags:         [4 flags from LLM reasoning]
Part F — Action Taken:      file_sar | Approved by officer-001 with override note
```

**Output files:**
- `data/sar_output/SAR-ALT-003.json` — structured JSON
- `data/sar_output/SAR-ALT-003.pdf` — formatted PDF

---

## Step 9 — Audit Log

Final audit record written to `audit_log` table:

| Field | Value |
|---|---|
| alert_id | ALT-003 |
| node_name | audit_log |
| decision | file_sar |
| officer_id | officer-001 |
| full_state | [complete AMLState JSON] |

---

## Complete Audit Trail

```
1. alert_ingestion     · Alert ALT-003 received — rule=HIGH_RISK_JURISDICTION, amount=51200.0 CAD
2. enrichment          · 90 transactions, velocity ratio 12.33x
3. cdd_lookup          · HIGH risk, EDD required, FATF country
4. watchlist_screening · OFAC SDN match, confidence=1.0 (after 1 retry)
5. llm_reasoning       · Score: high, 4 red flags, recommendation: file_sar
6. decision_router     · → human_review
7. human_review        · Officer officer-001 override — note recorded
8. sar_generation      · SAR-ALT-003 generated, PDF written
9. audit_log           · Full record written to audit_log table
```

**Total elapsed time (POC):** ~5 seconds (dominated by LLM call)

**Compared to manual investigation:** 2–4 hours → under 5 minutes
