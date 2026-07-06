"""
tests/test_nodes.py — Unit tests for individual graph nodes.

Each test exercises a node in isolation using fixture alert data.
No graph infrastructure required — nodes are plain functions.

Run:
    pytest tests/test_nodes.py -v
"""

import json
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(alert_id: str) -> dict:
    with open(FIXTURES / f"{alert_id}.json") as f:
        data = json.load(f)
    if isinstance(data.get("alert_metadata"), str):
        data["alert_metadata"] = json.loads(data["alert_metadata"])
    return data


# ── Node 1: Alert Ingestion ────────────────────────────────────────────────────

class TestAlertIngestion:
    def test_valid_alert_passes(self):
        from agent.nodes.alert_ingestion import alert_ingestion
        alert = load_fixture("ALT-001")
        result = alert_ingestion({"alert": alert})
        assert "audit_trail" in result
        assert result["audit_trail"][0]["node"] == "alert_ingestion"
        assert "ALT-001" in result["audit_trail"][0]["summary"]

    def test_missing_fields_raises(self):
        from agent.nodes.alert_ingestion import alert_ingestion
        with pytest.raises(ValueError, match="missing required fields"):
            alert_ingestion({"alert": {"alert_id": "X"}})

    def test_all_five_alerts_ingest(self):
        from agent.nodes.alert_ingestion import alert_ingestion
        for aid in ["ALT-001", "ALT-002", "ALT-003", "ALT-004", "ALT-005"]:
            alert = load_fixture(aid)
            result = alert_ingestion({"alert": alert})
            assert result["audit_trail"][0]["node"] == "alert_ingestion"


# ── Node 2: Transaction Enrichment ────────────────────────────────────────────

class TestEnrichment:
    def test_returns_transaction_history(self):
        from agent.nodes.enrichment import enrichment
        alert = load_fixture("ALT-001")
        result = enrichment({"alert": alert})
        assert "transaction_history" in result
        assert len(result["transaction_history"]) > 0

    def test_velocity_metrics_present(self):
        from agent.nodes.enrichment import enrichment
        alert = load_fixture("ALT-001")
        result = enrichment({"alert": alert})
        metrics = result["velocity_metrics"]
        for key in ["txn_count_90d", "total_volume_90d", "avg_daily_volume",
                    "peak_day_volume", "current_day_volume", "velocity_ratio"]:
            assert key in metrics, f"Missing metric: {key}"

    def test_velocity_anomaly_has_high_ratio(self):
        from agent.nodes.enrichment import enrichment
        alert = load_fixture("ALT-002")
        result = enrichment({"alert": alert})
        ratio = result["velocity_metrics"]["velocity_ratio"]
        assert ratio > 5, f"Expected velocity_ratio > 5 for VELOCITY_ANOMALY, got {ratio}"

    def test_audit_trail_appended(self):
        from agent.nodes.enrichment import enrichment
        alert = load_fixture("ALT-001")
        result = enrichment({"alert": alert})
        assert result["audit_trail"][0]["node"] == "enrichment"


# ── Node 3: CDD Lookup ────────────────────────────────────────────────────────

class TestCDDLookup:
    def test_returns_cdd_profile(self):
        from agent.nodes.cdd_lookup import cdd_lookup
        alert = load_fixture("ALT-001")
        result = cdd_lookup({"alert": alert})
        profile = result["cdd_profile"]
        assert profile["account_id"] == "ACC-001"
        assert "risk_rating" in profile
        assert "full_name" in profile

    def test_edd_not_required_for_low_risk(self):
        from agent.nodes.cdd_lookup import cdd_lookup
        alert = load_fixture("ALT-001")  # ACC-001 is LOW risk
        result = cdd_lookup({"alert": alert})
        assert result["edd_required"] is False

    def test_edd_required_for_high_risk(self):
        from agent.nodes.cdd_lookup import cdd_lookup
        alert = load_fixture("ALT-003")  # ACC-003 is HIGH risk + FATF country
        result = cdd_lookup({"alert": alert})
        assert result["edd_required"] is True

    def test_fatf_country_triggers_edd(self):
        from agent.nodes.cdd_lookup import cdd_lookup
        alert = load_fixture("ALT-003")  # Myanmar (MM) — FATF grey list
        result = cdd_lookup({"alert": alert})
        assert result["cdd_profile"]["fatf_listed_country"] is True
        assert result["edd_required"] is True

    def test_audit_trail_appended(self):
        from agent.nodes.cdd_lookup import cdd_lookup
        alert = load_fixture("ALT-001")
        result = cdd_lookup({"alert": alert})
        assert result["audit_trail"][0]["node"] == "cdd_lookup"


# ── Node 4: Watchlist Screening ───────────────────────────────────────────────

class TestWatchlistScreening:
    def test_pep_match_found(self):
        from agent.nodes.watchlist import watchlist_screening
        # ALT-004: counterparty is Viktor Marchenko (PEP list)
        alert = load_fixture("ALT-004")
        state = {
            "alert": alert,
            "watchlist_result": {"retry_count": 0},
        }
        result = watchlist_screening(state)
        assert result["watchlist_result"]["match_found"] is True
        assert result["watchlist_result"]["list_name"] == "PEP"

    def test_ofac_match_found(self):
        from agent.nodes.watchlist import watchlist_screening
        # ALT-003: counterparty is Global Trade Corp (OFAC SDN) — but first call raises
        # Use retry_count=1 to skip the simulated failure
        alert = load_fixture("ALT-003")
        state = {
            "alert": alert,
            "watchlist_result": {"retry_count": 1},
        }
        result = watchlist_screening(state)
        assert result["watchlist_result"]["match_found"] is True
        assert result["watchlist_result"]["list_name"] == "OFAC_SDN"

    def test_no_match_for_clean_counterparty(self):
        from agent.nodes.watchlist import watchlist_screening
        alert = load_fixture("ALT-001")  # ACC-001 counterparty: "Cash Deposit"
        state = {"alert": alert, "watchlist_result": {}}
        result = watchlist_screening(state)
        assert result["watchlist_result"]["match_found"] is False
        assert result["watchlist_failed"] is False

    def test_adverse_media_match(self):
        from agent.nodes.watchlist import watchlist_screening
        alert = load_fixture("ALT-005")  # FastCash Express Ltd
        state = {"alert": alert, "watchlist_result": {}}
        result = watchlist_screening(state)
        assert result["watchlist_result"]["match_found"] is True
        assert result["watchlist_result"]["list_name"] == "ADVERSE_MEDIA"

    def test_simulated_failure_raises_on_first_attempt(self):
        from agent.nodes.watchlist import watchlist_screening, _TransientServiceError
        alert = load_fixture("ALT-003")
        state = {"alert": alert, "watchlist_result": {"retry_count": 0}}
        with pytest.raises(_TransientServiceError):
            watchlist_screening(state)

    def test_audit_trail_appended(self):
        from agent.nodes.watchlist import watchlist_screening
        alert = load_fixture("ALT-001")
        state = {"alert": alert, "watchlist_result": {}}
        result = watchlist_screening(state)
        assert result["audit_trail"][0]["node"] == "watchlist_screening"
