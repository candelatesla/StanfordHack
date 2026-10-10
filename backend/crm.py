"""SQLite mock CRM."""
import sqlite3

import config
from agent.rules import RULES

DEMO_DEAL = {
    "id": "acme-health",
    "customer": "Acme Health",
    "tier": "Enterprise",
    "list_arr": 150000,
    "discount_pct": 0,
    "term_years": 1,
    "payment_terms_days": 30,
    "liability_cap_multiple": 1,
    "hipaa_baa": 0,
    "eu_residency": 0,
    "stage": "Negotiation",
}
FIELDS = [k for k in DEMO_DEAL if k != "id"]
EDITABLE = {"tier", "discount_pct", "term_years", "payment_terms_days", "liability_cap_multiple",
            "hipaa_baa", "eu_residency", "stage"}


def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init(reset: bool = False):
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS deals (
            id TEXT PRIMARY KEY, customer TEXT, tier TEXT, list_arr REAL, discount_pct REAL,
            term_years INTEGER, payment_terms_days INTEGER, liability_cap_multiple REAL,
            hipaa_baa INTEGER, eu_residency INTEGER, stage TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS deal_log (
            ts DATETIME DEFAULT CURRENT_TIMESTAMP, deal_id TEXT, field TEXT, old TEXT, new TEXT)""")
        if reset:
            c.execute("DELETE FROM deals WHERE id = ?", (DEMO_DEAL["id"],))
            c.execute("DELETE FROM deal_log WHERE deal_id = ?", (DEMO_DEAL["id"],))
        cols = ", ".join(DEMO_DEAL)
        marks = ", ".join("?" for _ in DEMO_DEAL)
        c.execute(f"INSERT OR IGNORE INTO deals ({cols}) VALUES ({marks})", tuple(DEMO_DEAL.values()))


def _with_derived(row: dict) -> dict:
    d = dict(row)
    d["hipaa_baa"] = bool(d["hipaa_baa"])
    d["eu_residency"] = bool(d["eu_residency"])
    d["list_arr"] = RULES["tiers"].get(d["tier"], {}).get("list_arr", d["list_arr"])
    uplift = 1 + RULES["compliance"]["eu_residency"]["uplift_pct"] / 100 if d["eu_residency"] else 1
    d["arr"] = round(d["list_arr"] * uplift * (1 - d["discount_pct"] / 100), 2)
    d["payment_terms"] = f"Net {d['payment_terms_days']}"
    return d


def get_deal(deal_id: str = DEMO_DEAL["id"]) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
    return _with_derived(row)


def update_deal(deal_id: str, changes: dict) -> tuple[dict, dict]:
    old = get_deal(deal_id)
    changes = {k: v for k, v in changes.items() if k in EDITABLE}
    if changes:
        with _conn() as c:
            sets = ", ".join(f"{k} = ?" for k in changes)
            c.execute(f"UPDATE deals SET {sets} WHERE id = ?", (*changes.values(), deal_id))
            c.executemany("INSERT INTO deal_log (deal_id, field, old, new) VALUES (?, ?, ?, ?)",
                          [(deal_id, k, str(old.get(k)), str(v)) for k, v in changes.items()])
    if "tier" in changes:  # keep list price in sync with the tier
        with _conn() as c:
            c.execute("UPDATE deals SET list_arr = ? WHERE id = ?",
                      (RULES["tiers"][changes["tier"]]["list_arr"], deal_id))
    return old, get_deal(deal_id)
