"""
agent/nodes/auto_clear.py — Auto-Clear terminal node

Terminal node for low-risk alerts that do not require human review.
Records the clearing decision in state and passes to the audit log.

This node is reached when:
    - suspicion_score = "low"
    - no watchlist hit
    - no error flags
"""

from datetime import datetime
from agent.state import AMLState


def auto_clear(state: AMLState) -> dict:
    alert = state["alert"]
    llm_reasoning = state.get("llm_reasoning", {})

    entry = {
        "node": "auto_clear",
        "timestamp": datetime.now().isoformat(),
        "summary": (
            f"Alert {alert.get('alert_id')} auto-cleared. "
            f"Suspicion score: {llm_reasoning.get('suspicion_score', 'low')}. "
            f"No watchlist hits. No human review required."
        ),
    }

    return {
        "decision": "auto_clear",
        "audit_trail": [entry],
    }
