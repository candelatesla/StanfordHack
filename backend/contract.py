"""Templated order form + clause-level diff."""


def clauses(deal: dict) -> dict[str, str]:
    d = deal
    return {
        "Subscription Tier": f"Nimbus Analytics {d['tier']}",
        "Annual Fee": f"${d['arr']:,.0f} per year" + (f" (list ${d['list_arr']:,.0f})" if d["arr"] != d["list_arr"] else ""),
        "Discount": f"{d['discount_pct']:g}% off list" if d["discount_pct"] else "None",
        "Term": f"{d['term_years']} year{'s' if d['term_years'] != 1 else ''}",
        "Payment Terms": f"Net {d['payment_terms_days']}, billed annually in advance",
        "Limitation of Liability": f"{d['liability_cap_multiple']:g}x fees paid in prior 12 months",
        "HIPAA BAA": "Nimbus standard BAA attached as Exhibit C" if d["hipaa_baa"] else "Not included",
        "EU Data Residency": "Frankfurt region, +10% uplift" if d["eu_residency"] else "Not included",
        "Implementation Fee": "Waived" if d["implementation_fee_waived"] else "$15,000 one-time",
        "Renewal Price Cap": f"Increases capped at {d['renewal_cap_pct']:g}% per renewal",
        "Start Date": f"{d['start_delay_days']} days after signature" if d["start_delay_days"] else "On signature",
        "Pilot": f"Paid {d['pilot_days']}-day pilot, fee credited to contract" if d["pilot_days"] else "None",
        "Additional Creator Seats": (f"{d['extra_creator_seats']} at $540/seat/yr" if d["extra_creator_seats"] else "None"),
    }


def diff(old: dict, new: dict) -> list[dict]:
    a, b = clauses(old), clauses(new)
    return [{"clause": k, "old": a[k], "new": b[k]} for k in b if a.get(k) != b[k]]
