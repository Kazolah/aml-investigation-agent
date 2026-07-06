"""
agent/nodes/cdd_lookup.py — Node 3: CDD Lookup

Retrieves the customer's KYC/CDD profile from SQLite and determines whether
Enhanced Due Diligence (EDD) is required.

EDD is triggered if:
    - Customer risk rating is HIGH
    - Customer's country of residence is on the FATF grey list
    - Customer is a Politically Exposed Person (PEP)

The edd_required flag is passed to the LLM reasoning node and influences
the suspicion score.
"""

import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

from agent.state import AMLState

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "data/mock_transactions.db")


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cdd_lookup(state: AMLState) -> dict:
    alert = state["alert"]
    account_id = alert["account_id"]

    conn = _get_connection()
    row = conn.execute(
        "SELECT * FROM cdd_profiles WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    conn.close()

    if row is None:
        raise ValueError(
            f"No CDD profile found for account '{account_id}'. "
            "Run data/seed_data.py to populate the database."
        )

    cdd_profile = dict(row)
    # Convert SQLite integers to Python booleans
    cdd_profile["is_pep"] = bool(cdd_profile["is_pep"])
    cdd_profile["fatf_listed_country"] = bool(cdd_profile["fatf_listed_country"])

    # ── EDD determination ─────────────────────────────────────────────────────
    edd_reasons = []
    if cdd_profile["risk_rating"] == "HIGH":
        edd_reasons.append("risk_rating=HIGH")
    if cdd_profile["fatf_listed_country"]:
        edd_reasons.append(f"country={cdd_profile['country_of_residence']} (FATF grey list)")
    if cdd_profile["is_pep"]:
        edd_reasons.append("customer_is_pep")

    edd_required = len(edd_reasons) > 0

    entry = {
        "node": "cdd_lookup",
        "timestamp": datetime.now().isoformat(),
        "summary": (
            f"CDD profile loaded for {account_id} ({cdd_profile['full_name']}). "
            f"Risk rating: {cdd_profile['risk_rating']}. "
            f"EDD required: {edd_required}"
            + (f" — reasons: {', '.join(edd_reasons)}" if edd_reasons else "")
        ),
    }

    return {
        "cdd_profile": cdd_profile,
        "edd_required": edd_required,
        "audit_trail": [entry],
    }
