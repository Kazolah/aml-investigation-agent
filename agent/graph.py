"""
agent/graph.py — LangGraph graph definition and compilation.

This is the complete graph with all 9 nodes wired:
    1. alert_ingestion
    2. enrichment
    3. cdd_lookup
    4. watchlist_screening  (with inline retry)
    5. llm_reasoning
    6. decision_router_node (sets decision field, routes conditionally)
    7. human_review         (interrupt_before — agent pauses here)
    8. sar_generation
    9. audit_log

    Plus: auto_clear (terminal, skips human review for low-risk alerts)

Conditional edges:
    After watchlist_screening → llm_reasoning OR decision_router_node (if failed)
    After decision_router_node → auto_clear OR human_review
    After human_review → sar_generation OR enrichment (send_back)
    After auto_clear → audit_log
    After sar_generation → audit_log

Checkpointing:
    SqliteSaver persists state at every node boundary. The graph is compiled
    with interrupt_before=["human_review"] so execution pauses before the
    human review node and waits for officer input via the Streamlit UI.
"""

import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from agent.state import AMLState
from agent.nodes.alert_ingestion import alert_ingestion
from agent.nodes.enrichment import enrichment
from agent.nodes.cdd_lookup import cdd_lookup
from agent.nodes.watchlist import watchlist_screening, _TransientServiceError
from agent.nodes.llm_reasoning import llm_reasoning
from agent.nodes.decision_router import decision_router, route_after_watchlist, route_after_human_review
from agent.nodes.auto_clear import auto_clear
from agent.nodes.human_review import human_review
from agent.nodes.sar_generator import sar_generation
from agent.nodes.audit_log import audit_log

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/mock_transactions.db")
MAX_WATCHLIST_RETRIES = 2


def _watchlist_with_retry(state: AMLState) -> dict:
    """
    Wraps watchlist_screening with inline retry logic.

    Retries up to MAX_WATCHLIST_RETRIES times on transient failures.
    After max retries, sets watchlist_failed=True and routes to human review.
    """
    retry_count = state.get("watchlist_result", {}).get("retry_count", 0)

    try:
        return watchlist_screening(state)
    except _TransientServiceError as e:
        retry_count = e.retry_count
        if retry_count >= MAX_WATCHLIST_RETRIES:
            entry = {
                "node": "watchlist_screening",
                "timestamp": datetime.now().isoformat(),
                "summary": (
                    f"Watchlist service failed after {MAX_WATCHLIST_RETRIES} retries. "
                    f"Routing to human review with watchlist_failed=True."
                ),
            }
            return {
                "watchlist_result": {"retry_count": retry_count},
                "watchlist_failed": True,
                "audit_trail": [entry],
            }
        updated_state = dict(state)
        updated_state["watchlist_result"] = {"retry_count": retry_count}
        return _watchlist_with_retry(updated_state)


def _decision_router_node(state: AMLState) -> dict:
    """
    Thin wrapper that calls decision_router() and writes the result to state.
    The actual routing is done via the conditional edge function.
    """
    decision = decision_router(state)
    entry = {
        "node": "decision_router",
        "timestamp": datetime.now().isoformat(),
        "summary": (
            f"Routing decision: {decision}. "
            f"Score={state.get('llm_reasoning', {}).get('suspicion_score', 'N/A')}, "
            f"watchlist_match={state.get('watchlist_result', {}).get('match_found', False)}, "
            f"watchlist_failed={state.get('watchlist_failed', False)}, "
            f"llm_parse_error={state.get('llm_parse_error', False)}"
        ),
    }
    return {"decision": decision, "audit_trail": [entry]}


def build_graph(db_path: str | None = None):
    """
    Build and compile the complete AML investigation graph.

    Args:
        db_path: Path to the SQLite database for checkpointing.

    Returns:
        Compiled LangGraph graph with SQLite checkpointing and
        interrupt_before=["human_review"].
    """
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    builder = StateGraph(AMLState)

    # ── Nodes ──────────────────────────────────────────────────────────────────
    # Note: node names cannot collide with AMLState field names.
    # Nodes that would collide are prefixed with "node_".
    builder.add_node("node_alert_ingestion", alert_ingestion)
    builder.add_node("node_enrichment", enrichment)
    builder.add_node("node_cdd_lookup", cdd_lookup)
    builder.add_node("node_watchlist_screening", _watchlist_with_retry)
    builder.add_node("node_llm_reasoning", llm_reasoning)
    builder.add_node("node_decision_router", _decision_router_node)
    builder.add_node("node_auto_clear", auto_clear)
    builder.add_node("node_human_review", human_review)
    builder.add_node("node_sar_generation", sar_generation)
    builder.add_node("node_audit_log", audit_log)

    # ── Entry point ────────────────────────────────────────────────────────────
    builder.set_entry_point("node_alert_ingestion")

    # ── Linear edges (no branching) ────────────────────────────────────────────
    builder.add_edge("node_alert_ingestion", "node_enrichment")
    builder.add_edge("node_enrichment", "node_cdd_lookup")
    builder.add_edge("node_cdd_lookup", "node_watchlist_screening")

    # ── Conditional: after watchlist → LLM or router (if watchlist failed) ─────
    def _route_after_watchlist(state):
        if state.get("watchlist_failed", False):
            return "node_decision_router"
        return "node_llm_reasoning"

    builder.add_conditional_edges(
        "node_watchlist_screening",
        _route_after_watchlist,
        {
            "node_llm_reasoning": "node_llm_reasoning",
            "node_decision_router": "node_decision_router",
        },
    )

    # ── LLM → router ──────────────────────────────────────────────────────────
    builder.add_edge("node_llm_reasoning", "node_decision_router")

    # ── Conditional: router → auto_clear or human_review ──────────────────────
    def _route_after_decision(state):
        decision = decision_router(state)
        return f"node_{decision}"

    builder.add_conditional_edges(
        "node_decision_router",
        _route_after_decision,
        {
            "node_auto_clear": "node_auto_clear",
            "node_human_review": "node_human_review",
        },
    )

    # ── auto_clear → audit_log → END ──────────────────────────────────────────
    builder.add_edge("node_auto_clear", "node_audit_log")
    builder.add_edge("node_audit_log", END)

    # ── Conditional: human_review → sar_generation or back to enrichment ──────
    def _route_after_human_review(state):
        action = state.get("human_override", {}).get("action", "approve")
        if action == "send_back":
            return "node_enrichment"
        return "node_sar_generation"

    builder.add_conditional_edges(
        "node_human_review",
        _route_after_human_review,
        {
            "node_sar_generation": "node_sar_generation",
            "node_enrichment": "node_enrichment",
        },
    )

    # ── sar_generation → audit_log → END ──────────────────────────────────────
    builder.add_edge("node_sar_generation", "node_audit_log")

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["node_human_review"],
    )
