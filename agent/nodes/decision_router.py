"""
agent/nodes/decision_router.py — Node 6: Decision Router

A pure routing function — no I/O, no LLM call.
Reads suspicion score, watchlist result, and error flags from state,
and returns the name of the next node.

All five routing paths:
    1. score=low  + no watchlist hit + no errors → auto_clear
    2. score=high + watchlist hit                → human_review
    3. score=medium (any watchlist result)        → human_review
    4. watchlist_failed=true                      → human_review
    5. llm_parse_error=true                       → human_review

The router is also used as a LangGraph conditional edge function —
it receives state and returns a string node name.
"""

from datetime import datetime
from agent.state import AMLState


def decision_router(state: AMLState) -> str:
    """
    Evaluate state and return the name of the next node.

    Used as both a standalone node and as a LangGraph conditional edge function.
    When used as a conditional edge, LangGraph calls this with the current state
    and routes to the returned node name.
    """
    watchlist_failed = state.get("watchlist_failed", False)
    llm_parse_error = state.get("llm_parse_error", False)
    watchlist_result = state.get("watchlist_result", {})
    llm_reasoning = state.get("llm_reasoning", {})

    match_found = watchlist_result.get("match_found", False)
    score = llm_reasoning.get("suspicion_score", "medium")

    # Path 4 — watchlist service failed, cannot screen
    if watchlist_failed:
        return "human_review"

    # Path 5 — LLM output unparseable
    if llm_parse_error:
        return "human_review"

    # Path 1 — clean case: low score, no watchlist match
    if score == "low" and not match_found:
        return "auto_clear"

    # Path 2 — high risk + watchlist hit
    if score == "high" and match_found:
        return "human_review"

    # Path 3 — medium score or any other combination
    return "human_review"


def route_after_watchlist(state: AMLState) -> str:
    """
    Conditional edge: after watchlist screening, route to LLM or skip.

    If watchlist_failed is True, bypass LLM and go directly to
    decision_router_node so the case is flagged for human review.
    """
    if state.get("watchlist_failed", False):
        return "decision_router_node"
    return "llm_reasoning"


def route_after_human_review(state: AMLState) -> str:
    """
    Conditional edge: after human review, route based on officer action.

    - approve or override → sar_generation
    - send_back           → enrichment (re-run from Node 2)
    """
    human_override = state.get("human_override", {})
    action = human_override.get("action", "approve")

    if action == "send_back":
        return "enrichment"
    return "sar_generation"
