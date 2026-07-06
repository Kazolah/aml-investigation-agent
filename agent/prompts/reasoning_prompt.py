"""
agent/prompts/reasoning_prompt.py — Structured prompt template for LLM reasoning.

The prompt instructs GPT-4o to reason over the enriched AML case and return
a strictly structured JSON object. No preamble, no markdown — just JSON.

The output schema is enforced by the LLM reasoning node, which validates and
parses the response. If parsing fails, the raw output is preserved in state
and the case is routed to human review.
"""

SYSTEM_PROMPT = """You are a senior AML (Anti-Money Laundering) compliance analyst at a Canadian bank. 
You are reviewing flagged transaction alerts and must provide a structured assessment.

Your analysis must be objective, evidence-based, and follow FINTRAC (Financial Transactions and 
Reports Analysis Centre of Canada) guidelines for Suspicious Transaction Reports.

You must return ONLY a valid JSON object — no preamble, no markdown, no explanation outside the JSON.

The JSON must contain exactly these fields:
{
  "suspicion_score": "low" | "medium" | "high",
  "red_flags": ["list", "of", "specific", "red", "flags", "observed"],
  "sar_narrative": "Draft narrative in FINTRAC STR format describing the suspicious activity",
  "recommendation": "auto_clear" | "monitor" | "file_sar",
  "reasoning": "Brief explanation of your scoring decision"
}

Scoring guidance:
- "low": Normal business activity, alert likely a false positive, no concerning patterns
- "medium": Some unusual patterns warrant monitoring but insufficient evidence for SAR
- "high": Clear indicators of suspicious activity, SAR filing recommended

Recommendation guidance:
- "auto_clear": Score is low, no watchlist hits, no concerning patterns
- "monitor": Score is medium, place account under enhanced monitoring
- "file_sar": Score is high OR watchlist hit present, file SAR with FINTRAC

The SAR narrative should follow FINTRAC's Suspicious Transaction Report structure:
describe who, what, when, where, how, and why the activity is suspicious."""


def build_reasoning_prompt(
    alert: dict,
    transaction_history: list,
    velocity_metrics: dict,
    cdd_profile: dict,
    edd_required: bool,
    watchlist_result: dict,
    watchlist_failed: bool,
) -> str:
    """
    Build the user-turn prompt from enriched state.

    Keeps the transaction history concise — sends a summary + the 10 most
    recent transactions rather than the full 90-day list to stay within
    context limits while preserving the most relevant recent activity.
    """
    # Summarise transaction history — most recent 10 + velocity stats
    recent_txns = transaction_history[:10]
    txn_summary = "\n".join(
        f"  {t['date']} | {t['direction'].upper():6} | "
        f"{t['currency']} {t['amount']:>10,.2f} | "
        f"{t['channel']:8} | {t['counterparty']} | {t['description']}"
        for t in recent_txns
    )

    watchlist_section = ""
    if watchlist_failed:
        watchlist_section = "WATCHLIST SCREENING: FAILED (service unavailable after retries)"
    elif watchlist_result.get("match_found"):
        entry = watchlist_result.get("matched_entry", {})
        watchlist_section = (
            f"WATCHLIST SCREENING: MATCH FOUND\n"
            f"  List: {watchlist_result.get('list_name')}\n"
            f"  Matched entity: {entry.get('name')}\n"
            f"  Program/Reason: {entry.get('program', entry.get('risk_level', 'N/A'))}\n"
            f"  Added to list: {entry.get('added_date', 'N/A')}\n"
            f"  Confidence: {watchlist_result.get('confidence_score')}"
        )
    else:
        watchlist_section = (
            f"WATCHLIST SCREENING: NO MATCH "
            f"(best score={watchlist_result.get('confidence_score', 0.0)})"
        )

    alert_meta = alert.get("alert_metadata", {})
    if isinstance(alert_meta, str):
        import json
        alert_meta = json.loads(alert_meta)

    return f"""
ALERT DETAILS
=============
Alert ID:     {alert.get('alert_id')}
Rule Fired:   {alert.get('rule_fired')}
Description:  {alert_meta.get('rule_description', 'N/A')}
Amount:       {alert.get('currency')} {alert.get('amount'):,.2f}
Counterparty: {alert.get('counterparty')}
Counterparty Account: {alert.get('counterparty_account', 'N/A')}
Timestamp:    {alert.get('timestamp')}

CUSTOMER PROFILE (KYC/CDD)
===========================
Account:          {cdd_profile.get('account_id')}
Name:             {cdd_profile.get('full_name')}
Nationality:      {cdd_profile.get('nationality')}
Country of Res.:  {cdd_profile.get('country_of_residence')}
Risk Rating:      {cdd_profile.get('risk_rating')}
Occupation:       {cdd_profile.get('occupation')}
Is PEP:           {cdd_profile.get('is_pep')}
FATF Country:     {cdd_profile.get('fatf_listed_country')}
EDD Required:     {edd_required}
Last Review:      {cdd_profile.get('last_review_date')}
Onboarding:       {cdd_profile.get('onboarding_date')}

TRANSACTION VELOCITY (90-DAY WINDOW)
=====================================
Total transactions:  {velocity_metrics.get('txn_count_90d')}
Total volume:        {alert.get('currency')} {velocity_metrics.get('total_volume_90d'):,.2f}
Average daily vol.:  {alert.get('currency')} {velocity_metrics.get('avg_daily_volume'):,.2f}
Peak daily vol.:     {alert.get('currency')} {velocity_metrics.get('peak_day_volume'):,.2f}
Today's volume:      {alert.get('currency')} {velocity_metrics.get('current_day_volume'):,.2f}
Velocity ratio:      {velocity_metrics.get('velocity_ratio')}x baseline

RECENT TRANSACTIONS (10 most recent)
=====================================
  DATE       | DIR    |     AMOUNT      | CHANNEL  | COUNTERPARTY | DESCRIPTION
{txn_summary}

{watchlist_section}

Based on the above evidence, provide your AML assessment as a JSON object.
"""
