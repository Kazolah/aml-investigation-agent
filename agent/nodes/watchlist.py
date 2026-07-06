"""
agent/nodes/watchlist.py — Node 4: Watchlist Screening

Screens the alert counterparty against the mock watchlist (OFAC SDN, PEP,
and adverse media entries loaded from data/watchlist.json).

Matching strategy:
    - Exact name match (case-insensitive)
    - Alias match (case-insensitive)
    - Partial match: all words in the entry name appear in the counterparty name

Retry logic:
    This node is the only one with a retry loop because in production it calls
    an external sanctions API that can fail transiently. The mock implementation
    simulates a failure for ALT-003 on the first attempt to demonstrate the
    retry pattern.

    - On exception: increment watchlist_result["retry_count"] and raise
    - The graph catches the exception and re-invokes this node
    - After max_retries (2), the graph sets watchlist_failed=True and routes
      to human review via the decision router
"""

import json
import os
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from dotenv import load_dotenv

from agent.state import AMLState

load_dotenv()

WATCHLIST_PATH = os.getenv("WATCHLIST_PATH", "data/watchlist.json")
MAX_RETRIES = 2
MATCH_THRESHOLD = 0.80  # Minimum similarity score for a fuzzy match


def _load_watchlist() -> list[dict]:
    path = Path(WATCHLIST_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Watchlist file not found: {path}")
    with open(path) as f:
        data = json.load(f)
    entries = []
    for section in ("ofac_sdn", "pep", "adverse_media"):
        entries.extend(data.get(section, []))
    return entries


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _screen(counterparty: str, entries: list[dict]) -> dict:
    """Return the best match found, or a no-match result."""
    counterparty_lower = counterparty.lower().strip()

    best_match = None
    best_score = 0.0

    for entry in entries:
        candidates = [entry["name"]] + entry.get("aliases", [])
        for candidate in candidates:
            score = _similarity(counterparty_lower, candidate.lower())
            # Also check if all words of the entry name appear in the counterparty
            entry_words = set(candidate.lower().split())
            counterparty_words = set(counterparty_lower.split())
            word_overlap = entry_words.issubset(counterparty_words)

            if word_overlap:
                score = max(score, MATCH_THRESHOLD + 0.05)

            if score > best_score:
                best_score = score
                best_match = entry

    if best_score >= MATCH_THRESHOLD and best_match:
        return {
            "screened_name": counterparty,
            "match_found": True,
            "matched_entry": best_match,
            "list_name": best_match["list"],
            "confidence_score": round(best_score, 2),
        }

    return {
        "screened_name": counterparty,
        "match_found": False,
        "matched_entry": None,
        "list_name": None,
        "confidence_score": round(best_score, 2),
    }


def watchlist_screening(state: AMLState) -> dict:
    alert = state["alert"]
    counterparty = alert.get("counterparty", "")
    prior_result = state.get("watchlist_result", {})
    retry_count = prior_result.get("retry_count", 0)

    # ── Simulate transient failure for ALT-003 on first attempt ──────────────
    # In production this would be a real network exception from the sanctions API.
    if alert.get("alert_id") == "ALT-003" and retry_count == 0:
        new_retry_count = retry_count + 1
        raise _TransientServiceError(
            f"Watchlist service unavailable (simulated). "
            f"Retry {new_retry_count} of {MAX_RETRIES}.",
            retry_count=new_retry_count,
        )

    entries = _load_watchlist()
    result = _screen(counterparty, entries)
    result["retry_count"] = retry_count

    match_summary = (
        f"Match found: {result['matched_entry']['name']} "
        f"({result['list_name']}, confidence={result['confidence_score']})"
        if result["match_found"]
        else f"No match found (best score={result['confidence_score']})"
    )

    entry = {
        "node": "watchlist_screening",
        "timestamp": datetime.now().isoformat(),
        "summary": (
            f"Screened '{counterparty}' against watchlist "
            f"(retry_count={retry_count}). {match_summary}"
        ),
    }

    return {
        "watchlist_result": result,
        "watchlist_failed": False,
        "audit_trail": [entry],
    }


class _TransientServiceError(Exception):
    """Raised to simulate a transient watchlist service failure."""
    def __init__(self, message: str, retry_count: int):
        super().__init__(message)
        self.retry_count = retry_count
