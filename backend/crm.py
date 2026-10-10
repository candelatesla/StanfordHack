"""SQLite mock CRM: accounts, their deals (past and open), call notes and a change log."""
import json
import re
import sqlite3
import time

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
    "implementation_fee_waived": 0,
    "renewal_cap_pct": 7,
    "pilot_days": 0,
    "start_delay_days": 0,
    "extra_creator_seats": 0,
    "stage": "Negotiation",
}
TERMS = {k: v for k, v in DEMO_DEAL.items() if k not in ("id", "customer", "tier", "list_arr", "stage")}
EDITABLE = {"tier", "discount_pct", "term_years", "payment_terms_days", "liability_cap_multiple",
            "hipaa_baa", "eu_residency", "stage", "implementation_fee_waived", "renewal_cap_pct", "pilot_days",
            "start_delay_days", "extra_creator_seats"}
BOOLS = ("hipaa_baa", "eu_residency", "implementation_fee_waived")
CLOSED = ("Closed Won", "Closed Lost")
INDUSTRIES = ["Healthcare", "Retail", "Financial services", "Logistics", "Education", "Manufacturing", "SaaS / Tech",
              "Media", "Public sector", "Other"]

ACCOUNTS = [
    dict(id="acme-health", name="Acme Health", industry="Healthcare", region="US", employees=2400,
         contact_name="Dana Whitfield", contact_title="VP Data & Analytics", customer_since="2024-03-12",
         notes="Analytics team on Growth since 2024. Expanding to the clinical org needs HIPAA BAA, so Enterprise."),
    dict(id="brightline-retail", name="Brightline Retail", industry="Retail", region="US", employees=5100,
         contact_name="Marcus Lee", contact_title="Head of BI", customer_since="2023-01-20",
         notes="Started on Starter, upgraded to Growth. Renewal due; procurement pushes for a multi-year price lock."),
    dict(id="northwind-logistics", name="Northwind Logistics", industry="Logistics", region="US", employees=1300,
         contact_name="Priya Raman", contact_title="Director of Operations", customer_since="2022-06-01",
         notes="Happy Growth customer. Enterprise upsell lost on price in 2025; revisit when fleet analytics expands."),
    dict(id="helios-bank", name="Helios Bank", industry="Financial services", region="EU (Germany)", employees=8700,
         contact_name="Jonas Weber", contact_title="Head of Data Platform", customer_since=None,
         notes="Prospect. Hard requirements: EU data residency, SSO, security review before pilot."),
    dict(id="cedar-learning", name="Cedar Learning", industry="Education", region="US", employees=420,
         contact_name="Alicia Gomez", contact_title="COO", customer_since=None,
         notes="Small prospect, budget-sensitive. Interested in a pilot first."),
]

# (account, deal id, name, tier, stage, created, closed, term overrides)
DEALS = [
    ("acme-health", "acme-growth-2024", "Growth: analytics team", "Growth", "Closed Won", "2024-02-01", "2024-03-12",
     dict(discount_pct=10, payment_terms_days=30)),
    ("acme-health", "acme-health", "Enterprise upgrade", "Enterprise", "Negotiation", "2026-08-18", None, {}),
    ("brightline-retail", "brightline-2023", "Starter: first team", "Starter", "Closed Won", "2022-12-01",
     "2023-01-20", {}),
    ("brightline-retail", "brightline-2024", "Upgrade to Growth", "Growth", "Closed Won", "2024-04-02", "2024-05-15",
     dict(discount_pct=10, term_years=2)),
    ("brightline-retail", "brightline-renewal-2026", "Growth renewal 2026", "Growth", "Negotiation", "2026-09-01",
     None, dict(term_years=2)),
    ("northwind-logistics", "northwind-2022", "Growth: operations", "Growth", "Closed Won", "2022-05-02",
     "2022-06-01", dict(term_years=3, discount_pct=15)),
    ("northwind-logistics", "northwind-ent-2025", "Enterprise upsell", "Enterprise", "Closed Lost", "2025-02-10",
     "2025-04-30", {}),
    ("helios-bank", "helios-2026", "Enterprise: data platform", "Enterprise", "Discovery", "2026-09-20", None,
     dict(eu_residency=1)),
    ("cedar-learning", "cedar-2026", "Starter pilot", "Starter", "Discovery", "2026-10-01", None, {}),
]

CALLS = [
    ("acme-health", "acme-health", "2026-09-24 15:00", "Discovery: Enterprise upgrade",
     "Dana outlined the clinical rollout and asked whether we sign a HIPAA BAA. We walked through Enterprise "
     "pricing; she will bring procurement to the next call.", ["Send Enterprise proposal", "Share security overview"]),
    ("brightline-retail", "brightline-renewal-2026", "2026-09-30 11:00", "Renewal kickoff",
     "Marcus is happy with adoption but procurement wants a multi-year price lock and net-60 terms.",
     ["Check renewal cap options", "Send renewal quote"]),
    ("helios-bank", "helios-2026", "2026-09-22 09:30", "Intro call",
     "Jonas needs EU residency and SSO, and a security review before any pilot.",
     ["Send DPA and security package", "Book technical deep dive"]),
]


def _conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init(reset: bool = False):
    """Create tables; seed demo data when empty. reset=True drops everything and reseeds (demo data only)."""
    with _conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(deals)")}
        if cols and "account_id" not in cols:  # database from before accounts existed
            reset = True
        if reset:
            for t in ("deals", "deal_log", "accounts", "calls"):
                c.execute(f"DROP TABLE IF EXISTS {t}")
        c.execute("""CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY, name TEXT, industry TEXT, region TEXT, employees INTEGER, contact_name TEXT,
            contact_title TEXT, customer_since TEXT, notes TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS deals (
            id TEXT PRIMARY KEY, account_id TEXT, name TEXT, customer TEXT, tier TEXT, list_arr REAL,
            discount_pct REAL, term_years INTEGER, payment_terms_days INTEGER, liability_cap_multiple REAL,
            hipaa_baa INTEGER, eu_residency INTEGER, implementation_fee_waived INTEGER, renewal_cap_pct REAL,
            pilot_days INTEGER, start_delay_days INTEGER, extra_creator_seats INTEGER, stage TEXT,
            created_on TEXT, closed_on TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS deal_log (
            ts DATETIME DEFAULT CURRENT_TIMESTAMP, deal_id TEXT, field TEXT, old TEXT, new TEXT, call_id TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS calls (
            id TEXT PRIMARY KEY, account_id TEXT, deal_id TEXT, ts TEXT, title TEXT, recap TEXT,
            agreed TEXT, open_items TEXT, next_steps TEXT)""")
        if c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
            return
        for a in ACCOUNTS:
            _insert(c, "accounts", a)
        names = {a["id"]: a["name"] for a in ACCOUNTS}
        for acc, did, name, tier, stage, created, closed, terms in DEALS:
            _insert(c, "deals", {"id": did, "account_id": acc, "name": name, "customer": names[acc], "tier": tier,
                                 "list_arr": RULES["tiers"][tier]["list_arr"], **TERMS, **terms, "stage": stage,
                                 "created_on": created, "closed_on": closed})
        for i, (acc, did, ts, title, recap, steps) in enumerate(CALLS):
            _insert(c, "calls", {"id": f"seed{i}", "account_id": acc, "deal_id": did, "ts": ts, "title": title,
                                 "recap": recap, "agreed": "[]", "open_items": "[]", "next_steps": json.dumps(steps)})


def _insert(c, table: str, row: dict):
    c.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))


def _with_derived(row) -> dict:
    d = dict(row)
    for k in BOOLS:
        d[k] = bool(d[k])
    d["list_arr"] = RULES["tiers"].get(d["tier"], {}).get("list_arr", d["list_arr"])
    uplift = 1 + RULES["compliance"]["eu_residency"]["uplift_pct"] / 100 if d["eu_residency"] else 1
    d["arr"] = round(d["list_arr"] * uplift * (1 - d["discount_pct"] / 100), 2)
    d["payment_terms"] = f"Net {d['payment_terms_days']}"
    d["open"] = d["stage"] not in CLOSED
    return d


def get_deal(deal_id: str = DEMO_DEAL["id"]) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM deals WHERE id = ?", (deal_id,)).fetchone()
    return _with_derived(row)


def update_deal(deal_id: str, changes: dict, call_id: str | None = None) -> tuple[dict, dict]:
    old = get_deal(deal_id)
    changes = {k: v for k, v in changes.items() if k in EDITABLE and v != old.get(k)}
    if changes:
        with _conn() as c:
            sets = ", ".join(f"{k} = ?" for k in changes)
            c.execute(f"UPDATE deals SET {sets} WHERE id = ?", (*changes.values(), deal_id))
            c.executemany("INSERT INTO deal_log (deal_id, field, old, new, call_id) VALUES (?, ?, ?, ?, ?)",
                          [(deal_id, k, str(old.get(k)), str(v), call_id) for k, v in changes.items()])
    if "tier" in changes:  # keep list price in sync with the tier
        with _conn() as c:
            c.execute("UPDATE deals SET list_arr = ? WHERE id = ?",
                      (RULES["tiers"][changes["tier"]]["list_arr"], deal_id))
    return old, get_deal(deal_id)


def undo_call(deal_id: str, snapshot: dict, call_id: str):
    """Restart a call: put the deal back as it was when the call started and forget what the call logged."""
    with _conn() as c:
        c.execute(f"UPDATE deals SET {', '.join(f'{k} = ?' for k in EDITABLE)}, list_arr = ? WHERE id = ?",
                  (*(snapshot[k] for k in EDITABLE), snapshot["list_arr"], deal_id))
        c.execute("DELETE FROM deal_log WHERE call_id = ?", (call_id,))
        c.execute("DELETE FROM calls WHERE id = ?", (call_id,))


# --- accounts -------------------------------------------------------------------------------------------------

def list_accounts() -> list[dict]:
    with _conn() as c:
        accounts = [dict(r) for r in c.execute("SELECT * FROM accounts ORDER BY name")]
        deals = [_with_derived(r) for r in c.execute("SELECT * FROM deals ORDER BY created_on DESC")]
    for a in accounts:
        mine = [d for d in deals if d["account_id"] == a["id"]]
        a["status"] = "Customer" if a["customer_since"] else "Prospect"
        a["open_deal"] = next(({k: d[k] for k in ("id", "name", "stage", "tier", "arr")} for d in mine if d["open"]),
                              None)
        a["won_arr"] = sum(d["arr"] for d in mine if d["stage"] == "Closed Won")
    return accounts


def get_account(account_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            return None
        a = dict(row)
        a["deals"] = [_with_derived(r) for r in
                      c.execute("SELECT * FROM deals WHERE account_id = ? ORDER BY created_on DESC", (account_id,))]
        a["calls"] = [{**dict(r), **{k: json.loads(r[k] or "[]") for k in ("agreed", "open_items", "next_steps")}}
                      for r in c.execute("SELECT * FROM calls WHERE account_id = ? ORDER BY ts DESC", (account_id,))]
        a["activity"] = [dict(r) for r in c.execute(
            """SELECT l.ts, l.field, l.old, l.new, d.name AS deal FROM deal_log l JOIN deals d ON d.id = l.deal_id
               WHERE d.account_id = ? ORDER BY l.rowid DESC LIMIT 20""", (account_id,))]
    a["status"] = "Customer" if a["customer_since"] else "Prospect"
    return a


def _slug(c, name: str, table: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "account"
    slug, n = base, 2
    while c.execute(f"SELECT 1 FROM {table} WHERE id = ?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    return slug


def create_account(data: dict) -> dict:
    """New customer from the pre-call screen: a prospect with one open deal at Discovery."""
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("Company name is required")
    tier = data.get("tier") if data.get("tier") in RULES["tiers"] else "Growth"
    with _conn() as c:
        aid = _slug(c, name, "accounts")
        _insert(c, "accounts", {"id": aid, "name": name, "industry": data.get("industry") or "Other",
                                "region": data.get("region") or "", "employees": int(data.get("employees") or 0),
                                "contact_name": data.get("contact_name") or "",
                                "contact_title": data.get("contact_title") or "", "customer_since": None,
                                "notes": data.get("notes") or ""})
    open_deal(aid, tier=tier, name=f"New business: {tier}")
    return get_account(aid)


def open_deal(account_id: str, tier: str | None = None, name: str | None = None) -> dict:
    """The account's open deal; opens one (expansion on the current tier) when there is none."""
    acc = get_account(account_id)
    if acc is None:
        raise KeyError(account_id)
    current = next((d for d in acc["deals"] if d["open"]), None)
    if current:
        return current
    won = next((d for d in acc["deals"] if d["stage"] == "Closed Won"), None)
    tier = tier or (won["tier"] if won else "Growth")
    year = time.strftime("%Y")
    with _conn() as c:
        did = _slug(c, f"{account_id}-{year}", "deals")
        _insert(c, "deals", {"id": did, "account_id": account_id, "customer": acc["name"], "tier": tier,
                             "name": name or (f"{year} expansion" if won else f"New business: {tier}"),
                             "list_arr": RULES["tiers"][tier]["list_arr"], **TERMS, "stage": "Discovery",
                             "created_on": time.strftime("%Y-%m-%d"), "closed_on": None})
    return get_deal(did)


def log_call(call_id: str, deal: dict, summary: dict):
    with _conn() as c:
        c.execute("DELETE FROM calls WHERE id = ?", (call_id,))  # End call twice: keep the latest summary
        _insert(c, "calls", {"id": call_id, "account_id": deal["account_id"], "deal_id": deal["id"],
                             "ts": time.strftime("%Y-%m-%d %H:%M"), "title": f"Call: {deal['name']}",
                             "recap": summary.get("recap") or "", "agreed": json.dumps(summary.get("agreed", [])),
                             "open_items": json.dumps(summary.get("open_items", [])),
                             "next_steps": json.dumps(summary.get("next_steps", []))})
