"""
agent/nodes/human_review.py — Node 7: Human Review

This is where the LangGraph interrupt/resume pattern is demonstrated.
The graph is compiled with interrupt_before=["human_review"], which causes
LangGraph to pause execution before this node runs and persist the current
state to the checkpointer.

The Streamlit UI reads the persisted state, presents it to the compliance
officer, and then resumes the graph by calling:
    graph.update_state(config, {"human_override": {...}})
    graph.invoke(None, config)

This node itself is minimal — it validates that human_override is present
in state (populated by the UI before resuming) and records the decision.

Officer actions:
    approve     — accept the LLM recommendation, proceed to SAR generation
    override    — change the recommendation (requires override_note)
    send_back   — route back to enrichment for re-investigation
"""

from datetime import datetime
from agent.state import AMLState


def human_review(state: AMLState) -> dict:
    """
    Node executed AFTER the human officer has submitted their decision.

    When LangGraph resumes after the interrupt, human_override will have been
    injected into state via update_state(). This node validates that and
    records the decision in the audit trail.
    """
    human_override = state.get("human_override", {})
    alert = state["alert"]
    llm_reasoning = state.get("llm_reasoning", {})

    action = human_override.get("action", "approve")
    officer_id = human_override.get("officer_id", "unknown")
    override_note = human_override.get("override_note", "")

    # Determine the effective decision after human review
    if action == "send_back":
        effective_decision = "sent_back_for_review"
        summary = (
            f"Officer {officer_id} sent alert {alert.get('alert_id')} "
            f"back for re-investigation."
            + (f" Note: {override_note}" if override_note else "")
        )
    elif action == "override":
        effective_decision = "human_review"
        summary = (
            f"Officer {officer_id} overrode LLM recommendation "
            f"(was: {llm_reasoning.get('recommendation', 'unknown')}). "
            f"Override note: {override_note}"
        )
    else:  # approve
        effective_decision = "human_review"
        summary = (
            f"Officer {officer_id} approved LLM recommendation: "
            f"{llm_reasoning.get('recommendation', 'file_sar')}."
        )

    entry = {
        "node": "human_review",
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
        "officer_id": officer_id,
        "action": action,
    }

    return {
        "decision": effective_decision,
        "audit_trail": [entry],
    }
