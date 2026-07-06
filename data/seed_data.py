"""
data/seed_data.py — Generates mock alert scenarios and populates SQLite.

Run this once before using the agent:
    python data/seed_data.py

What it creates:
    data/mock_transactions.db  — SQLite database with:
        - transactions table   — 90-day transaction history for 5 accounts
        - cdd_profiles table   — KYC/CDD profiles for 5 accounts
        - alerts table         — 5 alert scenarios, one per AML rule type
        - audit_log table      — empty, written to by the agent at runtime

It also writes tests/fixtures/ALT-00{1..5}.json for use in unit tests.

Alert scenarios:
    ALT-001  STRUCTURING            ACC-001  Multiple txns just below $10K CAD
    ALT-002  VELOCITY_ANOMALY       ACC-002  10x 90-day average in one day
    ALT-003  HIGH_RISK_JURISDICTION ACC-003  Wire to FATF grey-listed country (MM)
    ALT-004  PEP_COUNTERPARTY       ACC-004  Counterparty matches PEP list
    ALT-005  ADVERSE_MEDIA          ACC-005  Counterparty in adverse media
"""

import json
import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "mock_transactions.db"
FIXTURES_DIR = BASE_DIR.parent / "tests" / "fixtures"

random.seed(42)  # Reproducible data


# ── Helpers ───────────────────────────────────────────────────────────────────

def rand_amount(low: float, high: float) -> float:
    return round(random.uniform(low, high), 2)


def date_n_days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now().isoformat()


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    txn_id          TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    date            TEXT NOT NULL,
    amount          REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'CAD',
    direction       TEXT NOT NULL CHECK(direction IN ('debit','credit')),
    counterparty    TEXT,
    channel         TEXT,
    description     TEXT
);

CREATE TABLE IF NOT EXISTS cdd_profiles (
    account_id          TEXT PRIMARY KEY,
    full_name           TEXT NOT NULL,
    date_of_birth       TEXT,
    nationality         TEXT,
    country_of_residence TEXT,
    risk_rating         TEXT NOT NULL CHECK(risk_rating IN ('LOW','MEDIUM','HIGH')),
    onboarding_date     TEXT,
    occupation          TEXT,
    is_pep              INTEGER NOT NULL DEFAULT 0,
    last_review_date    TEXT,
    fatf_listed_country INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id        TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL,
    rule_fired      TEXT NOT NULL,
    amount          REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'CAD',
    counterparty    TEXT,
    counterparty_account TEXT,
    timestamp       TEXT NOT NULL,
    alert_metadata  TEXT  -- JSON blob
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id        TEXT NOT NULL,
    thread_id       TEXT NOT NULL,
    node_name       TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    decision        TEXT,
    officer_id      TEXT,
    summary         TEXT,
    full_state      TEXT  -- JSON blob of complete AMLState at terminal node
);
"""


# ── CDD Profiles ──────────────────────────────────────────────────────────────

CDD_PROFILES = [
    {
        "account_id": "ACC-001",
        "full_name": "James Whitfield",
        "date_of_birth": "1978-04-12",
        "nationality": "CA",
        "country_of_residence": "CA",
        "risk_rating": "LOW",
        "onboarding_date": "2018-06-01",
        "occupation": "Retail Business Owner",
        "is_pep": 0,
        "last_review_date": "2023-06-01",
        "fatf_listed_country": 0,
    },
    {
        "account_id": "ACC-002",
        "full_name": "Sarah Kowalski",
        "date_of_birth": "1985-11-03",
        "nationality": "CA",
        "country_of_residence": "CA",
        "risk_rating": "MEDIUM",
        "onboarding_date": "2020-02-14",
        "occupation": "Import/Export Consultant",
        "is_pep": 0,
        "last_review_date": "2023-08-15",
        "fatf_listed_country": 0,
    },
    {
        "account_id": "ACC-003",
        "full_name": "Aung Kyaw Zin",
        "date_of_birth": "1972-07-28",
        "nationality": "MM",
        "country_of_residence": "CA",
        "risk_rating": "HIGH",
        "onboarding_date": "2019-09-10",
        "occupation": "International Trade Broker",
        "is_pep": 0,
        "last_review_date": "2022-09-10",
        "fatf_listed_country": 1,  # Myanmar (MM) — FATF grey list
    },
    {
        "account_id": "ACC-004",
        "full_name": "Natasha Volkov",
        "date_of_birth": "1980-02-17",
        "nationality": "UA",
        "country_of_residence": "CA",
        "risk_rating": "HIGH",
        "onboarding_date": "2021-03-22",
        "occupation": "Business Consultant",
        "is_pep": 0,
        "last_review_date": "2023-03-22",
        "fatf_listed_country": 0,
    },
    {
        "account_id": "ACC-005",
        "full_name": "Marcus Tran",
        "date_of_birth": "1990-09-05",
        "nationality": "CA",
        "country_of_residence": "CA",
        "risk_rating": "LOW",
        "onboarding_date": "2022-11-30",
        "occupation": "Freelance Photographer",
        "is_pep": 0,
        "last_review_date": "2023-11-30",
        "fatf_listed_country": 0,
    },
]


# ── Transaction generators ────────────────────────────────────────────────────

def gen_structuring_txns(account_id: str) -> list[dict]:
    """ALT-001: Multiple transactions just below $10,000 CAD in 24 hours."""
    txns = []
    # Normal baseline: small daily transactions over 87 days
    for i in range(3, 90):
        txns.append({
            "txn_id": f"TXN-{account_id}-B{i:03d}",
            "account_id": account_id,
            "date": date_n_days_ago(i),
            "amount": rand_amount(200, 1500),
            "currency": "CAD",
            "direction": "credit",
            "counterparty": "Various Retail Customers",
            "channel": "POS",
            "description": "Retail sales receipt",
        })
    # Alert trigger: 4 cash deposits just below $10K on day 0 and day 1
    for j, (day, suffix) in enumerate([(0, "A"), (0, "B"), (0, "C"), (1, "D")]):
        txns.append({
            "txn_id": f"TXN-{account_id}-STR{suffix}",
            "account_id": account_id,
            "date": date_n_days_ago(day),
            "amount": rand_amount(9200, 9850),
            "currency": "CAD",
            "direction": "credit",
            "counterparty": "Cash Deposit",
            "channel": "BRANCH",
            "description": "Cash deposit",
        })
    return txns


def gen_velocity_txns(account_id: str) -> list[dict]:
    """ALT-002: Account transacts 10x its 90-day average in one day."""
    txns = []
    # Normal baseline: ~$500/day
    for i in range(1, 90):
        txns.append({
            "txn_id": f"TXN-{account_id}-B{i:03d}",
            "account_id": account_id,
            "date": date_n_days_ago(i),
            "amount": rand_amount(300, 700),
            "currency": "CAD",
            "direction": "debit",
            "counterparty": "Various Suppliers",
            "channel": "EFT",
            "description": "Supplier payment",
        })
    # Alert trigger: large single-day volume (~$52,000 across 4 transactions)
    for k in range(4):
        txns.append({
            "txn_id": f"TXN-{account_id}-VEL{k:02d}",
            "account_id": account_id,
            "date": date_n_days_ago(0),
            "amount": rand_amount(12000, 14000),
            "currency": "CAD",
            "direction": "debit",
            "counterparty": "Overseas Supplier Co",
            "channel": "WIRE",
            "description": "International wire transfer",
        })
    return txns


def gen_high_risk_jurisdiction_txns(account_id: str) -> list[dict]:
    """ALT-003: Wire transfer to FATF grey-listed country (Myanmar - MM)."""
    txns = []
    # Normal baseline
    for i in range(2, 90):
        txns.append({
            "txn_id": f"TXN-{account_id}-B{i:03d}",
            "account_id": account_id,
            "date": date_n_days_ago(i),
            "amount": rand_amount(1000, 6000),
            "currency": "CAD",
            "direction": "debit",
            "counterparty": "Global Trade Corp",
            "channel": "WIRE",
            "description": "Trade payment",
        })
    # Alert trigger: large wire to MM-domiciled entity
    txns.append({
        "txn_id": f"TXN-{account_id}-HRJ001",
        "account_id": account_id,
        "date": date_n_days_ago(1),
        "amount": 48500.00,
        "currency": "CAD",
        "direction": "debit",
        "counterparty": "Global Trade Corp",
        "channel": "SWIFT",
        "description": "SWIFT wire — beneficiary bank: Ayeyarwady Bank, Yangon, MM",
    })
    txns.append({
        "txn_id": f"TXN-{account_id}-HRJ002",
        "account_id": account_id,
        "date": date_n_days_ago(0),
        "amount": 51200.00,
        "currency": "CAD",
        "direction": "debit",
        "counterparty": "Global Trade Corp",
        "channel": "SWIFT",
        "description": "SWIFT wire — beneficiary bank: Ayeyarwady Bank, Yangon, MM",
    })
    return txns


def gen_pep_txns(account_id: str) -> list[dict]:
    """ALT-004: Counterparty matches a PEP list entry (Viktor Marchenko)."""
    txns = []
    for i in range(2, 90):
        txns.append({
            "txn_id": f"TXN-{account_id}-B{i:03d}",
            "account_id": account_id,
            "date": date_n_days_ago(i),
            "amount": rand_amount(500, 3000),
            "currency": "CAD",
            "direction": "credit",
            "counterparty": "Various Clients",
            "channel": "EFT",
            "description": "Consulting fee received",
        })
    # Alert trigger: large credit from PEP-linked entity
    txns.append({
        "txn_id": f"TXN-{account_id}-PEP001",
        "account_id": account_id,
        "date": date_n_days_ago(1),
        "amount": 35000.00,
        "currency": "CAD",
        "direction": "credit",
        "counterparty": "Viktor Marchenko",
        "channel": "WIRE",
        "description": "International wire received — sender: Viktor Marchenko",
    })
    txns.append({
        "txn_id": f"TXN-{account_id}-PEP002",
        "account_id": account_id,
        "date": date_n_days_ago(0),
        "amount": 42000.00,
        "currency": "CAD",
        "direction": "credit",
        "counterparty": "Viktor Marchenko",
        "channel": "WIRE",
        "description": "International wire received — sender: Viktor Marchenko",
    })
    return txns


def gen_adverse_media_txns(account_id: str) -> list[dict]:
    """ALT-005: Counterparty name in adverse media (FastCash Express Ltd)."""
    txns = []
    for i in range(2, 90):
        txns.append({
            "txn_id": f"TXN-{account_id}-B{i:03d}",
            "account_id": account_id,
            "date": date_n_days_ago(i),
            "amount": rand_amount(100, 800),
            "currency": "CAD",
            "direction": "debit",
            "counterparty": "Various",
            "channel": "INTERAC",
            "description": "Interac e-transfer",
        })
    # Alert trigger: transfer to adverse media entity
    txns.append({
        "txn_id": f"TXN-{account_id}-AM001",
        "account_id": account_id,
        "date": date_n_days_ago(0),
        "amount": 4800.00,
        "currency": "CAD",
        "direction": "debit",
        "counterparty": "FastCash Express Ltd",
        "channel": "EFT",
        "description": "EFT payment — FastCash Express Ltd",
    })
    return txns


# ── Alerts ────────────────────────────────────────────────────────────────────

ALERTS = [
    {
        "alert_id": "ALT-001",
        "account_id": "ACC-001",
        "rule_fired": "STRUCTURING",
        "amount": 9650.00,
        "currency": "CAD",
        "counterparty": "Cash Deposit",
        "counterparty_account": None,
        "timestamp": date_n_days_ago(0) + "T09:14:22",
        "alert_metadata": json.dumps({
            "rule_description": "Multiple cash deposits below $10,000 CAD reporting threshold within 24 hours",
            "transaction_count": 4,
            "total_structured_amount": 38420.00,
            "threshold": 10000.00,
        }),
    },
    {
        "alert_id": "ALT-002",
        "account_id": "ACC-002",
        "rule_fired": "VELOCITY_ANOMALY",
        "amount": 52400.00,
        "currency": "CAD",
        "counterparty": "Overseas Supplier Co",
        "counterparty_account": "IBAN-DE89370400440532013000",
        "timestamp": date_n_days_ago(0) + "T11:32:07",
        "alert_metadata": json.dumps({
            "rule_description": "Account daily volume exceeds 10x 90-day average",
            "current_day_volume": 52400.00,
            "avg_daily_volume_90d": 487.50,
            "velocity_ratio": 10.75,
        }),
    },
    {
        "alert_id": "ALT-003",
        "account_id": "ACC-003",
        "rule_fired": "HIGH_RISK_JURISDICTION",
        "amount": 51200.00,
        "currency": "CAD",
        "counterparty": "Global Trade Corp",
        "counterparty_account": "SWIFT-AYABANK-MM-00192",
        "timestamp": date_n_days_ago(0) + "T14:05:55",
        "alert_metadata": json.dumps({
            "rule_description": "SWIFT wire to FATF grey-listed jurisdiction",
            "destination_country": "MM",
            "destination_country_name": "Myanmar",
            "fatf_list": "grey",
            "beneficiary_bank": "Ayeyarwady Bank",
            "beneficiary_city": "Yangon",
        }),
    },
    {
        "alert_id": "ALT-004",
        "account_id": "ACC-004",
        "rule_fired": "PEP_COUNTERPARTY",
        "amount": 42000.00,
        "currency": "CAD",
        "counterparty": "Viktor Marchenko",
        "counterparty_account": "IBAN-UA903052992990004149123456789",
        "timestamp": date_n_days_ago(0) + "T16:48:11",
        "alert_metadata": json.dumps({
            "rule_description": "Incoming wire from politically exposed person",
            "pep_name": "Viktor Marchenko",
            "pep_position": "Former Deputy Minister of Finance",
            "pep_country": "UA",
            "match_confidence": 0.97,
        }),
    },
    {
        "alert_id": "ALT-005",
        "account_id": "ACC-005",
        "rule_fired": "ADVERSE_MEDIA",
        "amount": 4800.00,
        "currency": "CAD",
        "counterparty": "FastCash Express Ltd",
        "counterparty_account": None,
        "timestamp": date_n_days_ago(0) + "T08:22:33",
        "alert_metadata": json.dumps({
            "rule_description": "Counterparty linked to adverse media — potential informal value transfer",
            "media_headline": "FastCash Express Ltd named in RCMP investigation into informal value transfer network",
            "media_source": "CBC News",
            "media_date": "2023-08-22",
        }),
    },
]


# ── Main seeding function ─────────────────────────────────────────────────────

def seed_database(db_path: Path = DB_PATH) -> None:
    print(f"Seeding database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    # CDD profiles
    conn.executemany(
        """INSERT OR REPLACE INTO cdd_profiles
           (account_id, full_name, date_of_birth, nationality,
            country_of_residence, risk_rating, onboarding_date,
            occupation, is_pep, last_review_date, fatf_listed_country)
           VALUES (:account_id, :full_name, :date_of_birth, :nationality,
                   :country_of_residence, :risk_rating, :onboarding_date,
                   :occupation, :is_pep, :last_review_date, :fatf_listed_country)""",
        CDD_PROFILES,
    )
    print(f"  ✓ {len(CDD_PROFILES)} CDD profiles inserted")

    # Transactions — one generator per alert scenario
    generators = {
        "ACC-001": gen_structuring_txns,
        "ACC-002": gen_velocity_txns,
        "ACC-003": gen_high_risk_jurisdiction_txns,
        "ACC-004": gen_pep_txns,
        "ACC-005": gen_adverse_media_txns,
    }
    total_txns = 0
    for account_id, gen_fn in generators.items():
        txns = gen_fn(account_id)
        conn.executemany(
            """INSERT OR REPLACE INTO transactions
               (txn_id, account_id, date, amount, currency,
                direction, counterparty, channel, description)
               VALUES (:txn_id, :account_id, :date, :amount, :currency,
                       :direction, :counterparty, :channel, :description)""",
            txns,
        )
        total_txns += len(txns)
        print(f"  ✓ {len(txns)} transactions inserted for {account_id}")

    # Alerts
    conn.executemany(
        """INSERT OR REPLACE INTO alerts
           (alert_id, account_id, rule_fired, amount, currency,
            counterparty, counterparty_account, timestamp, alert_metadata)
           VALUES (:alert_id, :account_id, :rule_fired, :amount, :currency,
                   :counterparty, :counterparty_account, :timestamp, :alert_metadata)""",
        ALERTS,
    )
    print(f"  ✓ {len(ALERTS)} alerts inserted")

    conn.commit()
    conn.close()
    print(f"\n  Database ready: {db_path}")
    print(f"  Total rows: {len(CDD_PROFILES)} profiles, {total_txns} transactions, {len(ALERTS)} alerts\n")


def seed_fixtures(fixtures_dir: Path = FIXTURES_DIR) -> None:
    """Write one alert JSON fixture per rule type for use in unit tests."""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    for alert in ALERTS:
        fixture_path = fixtures_dir / f"{alert['alert_id']}.json"
        with open(fixture_path, "w") as f:
            json.dump(alert, f, indent=2)
        print(f"  ✓ Fixture written: {fixture_path.name}")


def load_alert(alert_id: str, db_path: Path = DB_PATH) -> dict:
    """Load a single alert by ID from the database. Used by run_agent.py."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"Alert '{alert_id}' not found. Run seed_data.py first.")
    alert = dict(row)
    if alert.get("alert_metadata"):
        alert["alert_metadata"] = json.loads(alert["alert_metadata"])
    return alert


if __name__ == "__main__":
    os.makedirs(BASE_DIR, exist_ok=True)
    seed_database()
    print("Seeding fixtures...")
    seed_fixtures()
    print("Done. Run: python run_agent.py --list-alerts\n")
