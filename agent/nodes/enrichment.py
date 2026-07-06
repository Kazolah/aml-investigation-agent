"""
agent/nodes/enrichment.py — Node 2: Transaction Enrichment

Queries SQLite for the past 90 days of transaction history for the flagged
account. Computes velocity metrics that tell the LLM whether today's activity
is unusual for this specific customer or just large in absolute terms.

Velocity metrics computed:
    txn_count_90d       — total number of transactions in the window
    total_volume_90d    — sum of all transaction amounts
    avg_daily_volume    — mean daily volume (excluding today)
    peak_day_volume     — highest single-day volume in the window
    current_day_volume  — total volume for today (the alert day)
    velocity_ratio      — current_day_volume / avg_daily_volume
"""

import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

from agent.state import AMLState

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/mock_transactions.db")


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def enrichment(state: AMLState) -> dict:
    alert = state["alert"]
    account_id = alert["account_id"]
    today = datetime.now().date()
    cutoff = (today - timedelta(days=90)).isoformat()

    conn = _get_connection()
    rows = conn.execute(
        """SELECT txn_id, date, amount, currency, direction,
                  counterparty, channel, description
           FROM transactions
           WHERE account_id = ? AND date >= ?
           ORDER BY date DESC""",
        (account_id, cutoff),
    ).fetchall()
    conn.close()

    transaction_history = [dict(r) for r in rows]

    # ── Velocity metrics ──────────────────────────────────────────────────────
    daily_volumes: dict[str, float] = defaultdict(float)
    for txn in transaction_history:
        daily_volumes[txn["date"]] += txn["amount"]

    today_str = today.isoformat()
    current_day_volume = daily_volumes.get(today_str, 0.0)

    # Average daily volume — exclude today so we compare against the baseline
    baseline_volumes = [v for d, v in daily_volumes.items() if d != today_str]
    avg_daily_volume = (
        round(sum(baseline_volumes) / len(baseline_volumes), 2)
        if baseline_volumes
        else 0.0
    )
    peak_day_volume = round(max(daily_volumes.values()), 2) if daily_volumes else 0.0
    velocity_ratio = (
        round(current_day_volume / avg_daily_volume, 2)
        if avg_daily_volume > 0
        else 0.0
    )

    velocity_metrics = {
        "txn_count_90d": len(transaction_history),
        "total_volume_90d": round(sum(t["amount"] for t in transaction_history), 2),
        "avg_daily_volume": avg_daily_volume,
        "peak_day_volume": peak_day_volume,
        "current_day_volume": round(current_day_volume, 2),
        "velocity_ratio": velocity_ratio,
    }

    entry = {
        "node": "enrichment",
        "timestamp": datetime.now().isoformat(),
        "summary": (
            f"Retrieved {len(transaction_history)} transactions for {account_id} "
            f"over past 90 days. "
            f"Velocity ratio: {velocity_ratio}x "
            f"(today ${current_day_volume:,.2f} vs avg ${avg_daily_volume:,.2f}/day)"
        ),
    }

    return {
        "transaction_history": transaction_history,
        "velocity_metrics": velocity_metrics,
        "audit_trail": [entry],
    }
