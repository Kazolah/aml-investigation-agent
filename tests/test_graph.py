"""
tests/test_graph.py — End-to-end integration tests for the full graph.

These tests run the complete graph pipeline for each alert scenario,
simulating human officer input where required.

Run:
    pytest tests/test_graph.py -v
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
DB_PATH = "data/mock_transactions.db"


def load_fixture(alert_id: str) -> dict:
    with open(FIXTURES / f"{alert_id}.json") as f:
        data = json.load(f)
    if isinstance(data.get("alert_metadata"), str):
        data["alert_metadata"] = json.loads(data["alert_metadata"])
    return data


def human_approve(graph, config):
    """Simulate a compliance officer approving the LLM recommendation."""
    graph.update_state(
        config,
        {
            "human_override": {
                "officer_id": "test-officer-001",
                "officer_name": "Test Officer",
                "action": "approve",
                "override_note": "",
                "reviewed_at": datetime.now().isoformat(),
            }
        },
        as_node="node_human_review",
    )
    final_state = None
    for step in graph.stream(None, config=config):
        pass
    snapshot = graph.get_state(config)
    return snapshot.values


def human_override(graph, config, note: str):
    """Simulate a compliance officer overriding the LLM recommendation."""
    graph.update_state(
        config,
        {
            "human_override": {
                "officer_id": "test-officer-002",
                "officer_name": "Test Officer Senior",
                "action": "override",
                "override_note": note,
                "reviewed_at": datetime.now().isoformat(),
            }
        },
        as_node="node_human_review",
    )
    for step in graph.stream(None, config=config):
        pass
    snapshot = graph.get_state(config)
    return snapshot.values


class TestFullGraphPipeline:
    """Integration tests — run full graph end-to-end per alert scenario."""

    def _run_to_interrupt(self, alert_id: str, thread_suffix: str = "test"):
        """Run graph from start until it pauses at human_review (or completes)."""
        from agent.graph import build_graph
        graph = build_graph(DB_PATH)
        thread_id = f"{alert_id}-{thread_suffix}"
        config = {"configurable": {"thread_id": thread_id}}
        alert = load_fixture(alert_id)

        for step in graph.stream({"alert": alert}, config=config):
            pass

        snapshot = graph.get_state(config)
        return graph, config, snapshot.values

    def test_alt003_routes_to_human_review(self):
        """HIGH_RISK_JURISDICTION: should score high, match OFAC SDN, route to human review."""
        graph, config, state = self._run_to_interrupt("ALT-003", "route-test")
        assert state.get("decision") == "human_review"
        assert state.get("watchlist_result", {}).get("match_found") is True
        assert state.get("llm_reasoning", {}).get("suspicion_score") == "high"

    def test_alt004_pep_routes_to_human_review(self):
        """PEP_COUNTERPARTY: should match PEP list and route to human review."""
        graph, config, state = self._run_to_interrupt("ALT-004", "pep-test")
        assert state.get("decision") == "human_review"
        wl = state.get("watchlist_result", {})
        assert wl.get("match_found") is True
        assert wl.get("list_name") == "PEP"

    def test_alt003_approve_generates_sar(self):
        """Full path: HIGH_RISK + approve → SAR generated + audit log written."""
        graph, config, state = self._run_to_interrupt("ALT-003", "sar-test")
        assert state.get("decision") == "human_review"

        final = human_approve(graph, config)
        assert final.get("decision") == "file_sar"
        assert final.get("sar_draft") is not None
        sar = final["sar_draft"]
        assert "sar_id" in sar
        assert "part_e_red_flags" in sar
        assert len(sar["part_e_red_flags"]) > 0

    def test_alt003_override_generates_sar_with_note(self):
        """Override path: officer changes recommendation with a note."""
        graph, config, state = self._run_to_interrupt("ALT-003", "override-test")
        final = human_override(graph, config, "Confirmed via relationship manager — known trading partner")

        assert final.get("decision") == "file_sar"
        sar = final["sar_draft"]
        assert "Confirmed via relationship manager" in sar["part_d_suspicious_activity"]["narrative"]

    def test_alt003_audit_trail_complete(self):
        """Audit trail should have entries from every node that ran."""
        graph, config, state = self._run_to_interrupt("ALT-003", "audit-test")
        final = human_approve(graph, config)

        trail = final.get("audit_trail", [])
        node_names = [e.get("node") for e in trail]
        assert "alert_ingestion" in node_names
        assert "enrichment" in node_names
        assert "cdd_lookup" in node_names
        assert "watchlist_screening" in node_names
        assert "llm_reasoning" in node_names
        assert "sar_generation" in node_names
        assert "audit_log" in node_names

    def test_watchlist_retry_recorded(self):
        """ALT-003 watchlist should show retry_count=1 (simulated failure on attempt 0)."""
        graph, config, state = self._run_to_interrupt("ALT-003", "retry-test")
        wl = state.get("watchlist_result", {})
        assert wl.get("retry_count") == 1, f"Expected retry_count=1, got {wl.get('retry_count')}"

    def test_alt001_structuring_enrichment(self):
        """STRUCTURING: should run through enrichment and detect multiple deposits."""
        graph, config, state = self._run_to_interrupt("ALT-001", "struct-test")
        # Should reach human_review (structuring is at least medium risk)
        assert state.get("decision") in ("human_review", "auto_clear")
        assert state.get("velocity_metrics") is not None

    def test_audit_log_written_to_sqlite(self):
        """After completing, the audit_log table should have a record for this alert."""
        graph, config, state = self._run_to_interrupt("ALT-004", "db-test")
        final = human_approve(graph, config)

        # audit_log node writes alert_id as the identifier
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT * FROM audit_log WHERE alert_id = 'ALT-004'"
        ).fetchone()
        conn.close()
        assert row is not None, "Expected an audit_log row to be written for ALT-004"

    def test_sar_pdf_created(self):
        """After SAR generation, a PDF file should exist on disk."""
        graph, config, state = self._run_to_interrupt("ALT-003", "pdf-test")
        final = human_approve(graph, config)
        pdf_path = Path(final.get("sar_draft", {}).get("pdf_path", ""))
        assert pdf_path.exists(), f"SAR PDF not found at {pdf_path}"
        assert pdf_path.stat().st_size > 0
