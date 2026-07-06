"""
agent/state.py — AMLState TypedDict

The single shared state object passed between every node in the LangGraph graph.
Each node reads from this state and returns a partial update — LangGraph merges
the update into the state before passing it to the next node.

Design principles:
- Every field is Optional at definition time because nodes execute sequentially;
  upstream fields are not populated when a node first runs.
- The audit_trail field uses Annotated[list, operator.add] so that each node
  can append to it without overwriting prior entries — LangGraph will merge
  lists using the provided reducer rather than replacing the field.
- All fields are plain Python types (dict, list, str) for SQLite
  checkpointer serialisation compatibility.
"""

import operator
from typing import Annotated, Optional
from typing_extensions import TypedDict


class AMLState(TypedDict, total=False):
    # ── Node 1: Alert Ingestion ───────────────────────────────────────────────
    # Raw alert payload as received from the AML monitoring engine.
    # Fields: alert_id, rule_fired, amount, currency, account_id,
    #         counterparty_name, counterparty_account, timestamp, alert_metadata
    alert: dict

    # ── Node 2: Transaction Enrichment ───────────────────────────────────────
    # List of transaction records for the past 90 days.
    # Each record: txn_id, date, amount, currency, direction, counterparty,
    #              channel, description
    transaction_history: list

    # Computed velocity metrics from the 90-day window.
    # Fields: txn_count_90d, total_volume_90d, avg_daily_volume,
    #         peak_day_volume, current_day_volume, velocity_ratio
    velocity_metrics: dict

    # ── Node 3: CDD Lookup ────────────────────────────────────────────────────
    # Customer KYC/CDD profile from the core banking system.
    # Fields: account_id, full_name, date_of_birth, nationality,
    #         country_of_residence, risk_rating, onboarding_date,
    #         occupation, is_pep, last_review_date, fatf_listed_country
    cdd_profile: dict

    # True if the customer's risk rating is HIGH or country is FATF grey-listed.
    # Influences LLM reasoning and the routing decision.
    edd_required: bool

    # ── Node 4: Watchlist Screening ───────────────────────────────────────────
    # Result of screening the counterparty against OFAC SDN and PEP lists.
    # Fields: screened_name, match_found, matched_entry (if any),
    #         list_name (OFAC_SDN | PEP | ADVERSE_MEDIA), confidence_score,
    #         retry_count
    watchlist_result: dict

    # Set to True if the watchlist service failed after max retries.
    # Triggers immediate routing to human review.
    watchlist_failed: bool

    # ── Node 5: LLM Reasoning ─────────────────────────────────────────────────
    # Structured output from GPT-4o after reasoning over the enriched case.
    # Fields: suspicion_score (low|medium|high), red_flags (list of strings),
    #         sar_narrative (FINTRAC-format draft), recommendation
    #         (auto_clear|monitor|file_sar), reasoning (brief explanation)
    llm_reasoning: dict

    # Set to True if the LLM response could not be parsed as valid JSON.
    # The raw LLM output is preserved in llm_reasoning["raw_output"].
    llm_parse_error: bool

    # ── Node 6: Decision Router ───────────────────────────────────────────────
    # Final routing decision after evaluating score + watchlist + error flags.
    # Values: "auto_clear" | "human_review" | "file_sar"
    decision: str

    # ── Node 7: Human Review ──────────────────────────────────────────────────
    # Input captured from the compliance officer in the Streamlit UI.
    # Fields: officer_id, officer_name, action (approve|override|send_back),
    #         override_note (required if action=override),
    #         reviewed_at (ISO 8601 timestamp)
    human_override: dict

    # ── Node 8: SAR Draft Generation ─────────────────────────────────────────
    # Structured SAR draft in FINTRAC STR format.
    # Fields: part_a (reporting entity), part_b (subject), part_c (transactions),
    #         part_d (suspicious activity description), part_e (red flags),
    #         part_f (action taken), generated_at, pdf_path
    sar_draft: dict

    # ── Audit Trail (all nodes) ───────────────────────────────────────────────
    # Append-only log of every state transition.
    # Each entry: node_name, timestamp, summary (brief description of what happened)
    # Uses operator.add reducer so each node appends without overwriting.
    audit_trail: Annotated[list, operator.add]
