"""
agent/nodes/alert_ingestion.py — Node 1: Alert Ingestion

Accepts the raw alert payload and loads it into AMLState.
Validates required fields and appends the first audit trail entry.
No data is fetched here — this is purely intake and validation.
"""

from datetime import datetime
from agent.state import AMLState


REQUIRED_FIELDS = {"alert_id", "account_id", "rule_fired", "amount", "currency"}


def alert_ingestion(state: AMLState) -> dict:
    alert = state.get("alert", {})

    missing = REQUIRED_FIELDS - set(alert.keys())
    if missing:
        raise ValueError(f"Alert is missing required fields: {missing}")

    entry = {
        "node": "alert_ingestion",
        "timestamp": datetime.now().isoformat(),
        "summary": (
            f"Alert {alert['alert_id']} received — "
            f"rule={alert['rule_fired']}, "
            f"account={alert['account_id']}, "
            f"amount={alert['amount']} {alert['currency']}"
        ),
    }

    return {"audit_trail": [entry]}
