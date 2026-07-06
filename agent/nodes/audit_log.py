"""
agent/nodes/audit_log.py — Node 9: Audit Log

Terminal node for all paths. Writes the complete audit_trail from state
to the audit_log table in SQLite.

This is the tamper-evident record required by OSFI E-23. In production,
this would be a PostgreSQL append-only table with row-level security.

Written at every terminal node:
    - auto_clear path: alert cleared, no SAR
    - file_sar path: SAR generated and approved by human officer

The full AMLState is serialised as JSON and stored alongside the summary
for complete traceability.
"""

import json
import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

from agent.state import AMLState

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/mock_transactions.db")


def audit_log(state: AMLState) -> dict:
    alert = state.get("alert", {})
    alert_id = alert.get("alert_id", "UNKNOWN")
    human_override = state.get("human_override", {})
    decision = state.get("decision", "unknown")
    sar_draft = state.get("sar_draft", {})

    now = datetime.now().isoformat()

    # Serialise full state for the audit record
    try:
        full_state_json = json.dumps(dict(state), default=str)
    except Exception:
        full_state_json = "{}"

    # Build summary
    if decision == "auto_clear":
        summary = (
            f"Alert {alert_id} cleared automatically. "
            f"Suspicion score: {state.get('llm_reasoning', {}).get('suspicion_score', 'low')}. "
            f"No SAR filed."
        )
    elif decision == "file_sar":
        sar_id = sar_draft.get("sar_id", "N/A")
        officer_id = human_override.get("officer_id", "N/A")
        summary = (
            f"SAR {sar_id} filed for alert {alert_id}. "
            f"Approved by officer {officer_id}. "
            f"PDF: {sar_draft.get('pdf_path', 'N/A')}"
        )
    else:
        summary = f"Alert {alert_id} — terminal state: {decision}"

    # Determine thread_id from alert_id (same convention as run_agent.py)
    thread_id = alert_id

    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO audit_log
           (alert_id, thread_id, node_name, timestamp, decision,
            officer_id, summary, full_state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            alert_id,
            thread_id,
            "audit_log",
            now,
            decision,
            human_override.get("officer_id"),
            summary,
            full_state_json,
        ),
    )
    conn.commit()
    conn.close()

    entry = {
        "node": "audit_log",
        "timestamp": now,
        "summary": f"Audit record written for alert {alert_id}. Decision: {decision}.",
    }

    return {"audit_trail": [entry]}
