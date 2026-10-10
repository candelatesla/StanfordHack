"""Deterministic guardrails. The LLM never decides what is allowed; this module does."""
import json

import config

RULES = json.loads(config.RULES_PATH.read_text())

FEATURE_LABELS = {
    "hipaa_baa": "HIPAA BAA",
    "eu_residency": "EU data residency",
    "sso": "SSO",
    "api": "API access",
    "dedicated_csm": "dedicated CSM",
    "core_analytics": "core analytics",
    "soc2": "SOC 2 report",
    "gdpr": "GDPR DPA",
    "sla": "SLA",
    "on_premise": "on-premise deployment",
    "termination_for_convenience": "termination for convenience",
    "implementation_fee": "implementation fee waiver",
    "pilot": "pilot",
    "renewal_cap": "renewal price cap",
    "start_date": "delayed start date",
    "seats": "extra creator seats",
}


def check(trig: dict, deal: dict, rules: dict = RULES) -> dict:
    cat = trig.get("category")
    req = trig.get("request") or {}
    if cat == "pricing":
        return check_discount(req, deal, rules)
    if cat == "payment":
        return check_payment(req, deal, rules)
    if cat == "compliance":
        return check_compliance(req, deal, rules)
    if cat == "legal":
        return check_legal(req, deal, rules)
    if cat == "product":
        return check_product(req, deal, rules)
    if cat == "pilot":
        return check_pilot(req, deal, rules)
    if cat == "contract":
        return check_contract(req, deal, rules)
    # security, integration, implementation, support, objection, general, ...: knowledge questions.
    # No decision to make; the answer comes from the retrieved Company Brain sources.
    return _no_rule()


def _no_rule():
    return {"verdict": "info", "rule_id": None, "allowed": {}, "approval_needed": None,
            "reason": "Answer from Company Brain sources"}


def check_pilot(req, deal, rules):
    p = rules["pilot"]
    days = req.get("pilot_days")
    free = (req.get("feature") or "") in ("free_pilot", "free_trial")
    if free:
        return {"verdict": "counter", "rule_id": "pilot", "allowed": {"pilot_days": min(days or 30, p["max_days_without_approval"])},
                "approval_needed": p["approver"],
                "reason": f"Free pilots need {p['approver']}; offer a paid {p['max_days_without_approval']}-day pilot credited toward the contract"}
    if days is None or float(days) <= p["max_days_without_approval"]:
        d = int(days or p["max_days_without_approval"])
        return {"verdict": "approved", "rule_id": "pilot", "allowed": {"pilot_days": d}, "approval_needed": None,
                "reason": f"Paid {d}-day pilot is pre-approved; the fee is credited toward the contract"}
    return {"verdict": "counter", "rule_id": "pilot", "allowed": {"pilot_days": p["max_days_without_approval"]},
            "approval_needed": p["approver"],
            "reason": f"{float(days):g}-day pilot needs {p['approver']}; {p['max_days_without_approval']} days is pre-approved"}


def check_contract(req, deal, rules):
    feature = req.get("feature") or ""
    if feature == "implementation_fee":
        imp = rules["implementation"]
        term = int(req.get("term_years") or deal.get("term_years") or 1)
        if term >= imp["waive_min_term_years"]:
            return {"verdict": "approved", "rule_id": "implementation_fee", "allowed": {"implementation_fee_waived": True},
                    "approval_needed": None,
                    "reason": f"${imp['fee']:,} implementation fee can be waived on a {term}-year term"}
        return {"verdict": "counter", "rule_id": "implementation_fee",
                "allowed": {"implementation_fee_waived": True, "term_years": imp["waive_min_term_years"]},
                "approval_needed": imp["approver"],
                "reason": f"Waiving the ${imp['fee']:,} fee on a 1-year term needs {imp['approver']}; "
                          f"it's pre-approved with a {imp['waive_min_term_years']}-year term"}
    if req.get("renewal_cap_pct") is not None or feature == "renewal_cap":
        rn = rules["renewal"]
        cap = req.get("renewal_cap_pct")
        if cap is None:
            return {"verdict": "info", "rule_id": "renewal_cap", "allowed": {"renewal_cap_pct": rn["rep_cap_min_pct"]},
                    "approval_needed": None,
                    "reason": f"Renewal increases can be capped at {rn['rep_cap_min_pct']}% without approval (standard up to {rn['standard_uplift_max_pct']}%)"}
        cap = float(cap)
        if cap >= rn["rep_cap_min_pct"]:
            return {"verdict": "approved", "rule_id": "renewal_cap", "allowed": {"renewal_cap_pct": cap}, "approval_needed": None,
                    "reason": f"Capping renewal increases at {cap:g}% is within policy"}
        return {"verdict": "counter", "rule_id": "renewal_cap", "allowed": {"renewal_cap_pct": rn["rep_cap_min_pct"]},
                "approval_needed": rn["approver"],
                "reason": f"A {cap:g}% renewal cap needs {rn['approver']}; {rn['rep_cap_min_pct']}% is pre-approved"}
    if req.get("start_delay_days") is not None or feature == "start_date":
        sd = rules["start_date"]
        days = req.get("start_delay_days")
        if days is None or float(days) <= sd["max_delay_days_without_approval"]:
            d = int(days or sd["max_delay_days_without_approval"])
            return {"verdict": "approved", "rule_id": "start_date", "allowed": {"start_delay_days": d}, "approval_needed": None,
                    "reason": f"A start date up to {sd['max_delay_days_without_approval']} days after signature is pre-approved"}
        return {"verdict": "counter", "rule_id": "start_date",
                "allowed": {"start_delay_days": sd["max_delay_days_without_approval"]}, "approval_needed": sd["approver"],
                "reason": f"A {float(days):g}-day delayed start needs {sd['approver']}; up to {sd['max_delay_days_without_approval']} days is pre-approved"}
    if req.get("seats") is not None or feature == "seats":
        st = rules["seats"]
        included = st["included_creators"].get(deal.get("tier"), 0)
        seats = int(req.get("seats") or 0)
        extra = max(0, seats - included)
        blocks = -(-extra // st["block_size"]) if extra else 0
        price = st["extra_creator_price"] * (1 - st["block_discount_pct"] / 100)
        return {"verdict": "info", "rule_id": "seats", "allowed": {"extra_creator_seats": blocks * st["block_size"]} if blocks else {},
                "approval_needed": None,
                "reason": (f"{deal.get('tier')} includes {included} creators; {seats} needs {extra} more. "
                           f"Blocks of {st['block_size']} at ${price:,.0f}/seat/yr ({st['block_discount_pct']}% off)")
                if seats else f"{deal.get('tier')} includes {included} creator seats; viewers are unlimited"}
    return _no_rule()


def _cap_for_term(term: int, rules: dict) -> int:
    table = {int(k): v for k, v in rules["discounts"]["by_term_years"].items()}
    eligible = [y for y in table if y <= term]
    return table[max(eligible)] if eligible else rules["discounts"]["rep_max_pct"]


def check_discount(req, deal, rules):
    asked = req.get("discount_pct")
    term = int(req.get("term_years") or deal.get("term_years") or 1)
    cap = _cap_for_term(term, rules)
    if asked is None:
        return {"verdict": "info", "rule_id": "discount_multi_year", "allowed": {"discount_pct": cap, "term_years": term},
                "approval_needed": None, "reason": f"Up to {cap}% is pre-approved for a {term}-year term"}
    asked = float(asked)
    if asked <= cap:
        return {"verdict": "approved", "rule_id": "discount_multi_year",
                "allowed": {"discount_pct": asked, "term_years": term}, "approval_needed": None,
                "reason": f"{asked:g}% is within the {cap}% pre-approved cap for a {term}-year term"}
    approver = "VP Sales"
    for tier in sorted(rules["discounts"]["approval"], key=lambda a: a["above_pct"]):
        if asked > tier["above_pct"]:
            approver = tier["approver"]
    return {"verdict": "counter", "rule_id": "discount_multi_year",
            "allowed": {"discount_pct": cap, "term_years": term}, "approval_needed": approver,
            "reason": f"{asked:g}% needs {approver} approval; max {cap}% pre-approved for a {term}-year term"}


def check_payment(req, deal, rules):
    pt = rules["payment_terms"]
    days = req.get("payment_terms_days")
    if days is None:
        return {"verdict": "info", "rule_id": "payment_terms", "allowed": {}, "approval_needed": None,
                "reason": f"Default is Net {pt['default_days']}"}
    days = int(days)
    arr = deal.get("arr", 0)
    if days <= pt["default_days"]:
        return {"verdict": "approved", "rule_id": "payment_terms", "allowed": {"payment_terms_days": days},
                "approval_needed": None, "reason": f"Net {days} is within standard terms"}
    qualifying = [a for a in pt["allowed"] if days <= a["days"] and arr >= a["min_arr"]]
    if qualifying:
        return {"verdict": "approved", "rule_id": "payment_terms", "allowed": {"payment_terms_days": days},
                "approval_needed": None,
                "reason": f"Net {days} pre-approved for deals >= ${qualifying[0]['min_arr']:,} ARR (this deal: ${arr:,.0f})"}
    best = max([a["days"] for a in pt["allowed"] if arr >= a["min_arr"]] + [pt["default_days"]])
    verdict = "escalate" if days > pt["escalate_above_days"] else "counter"
    return {"verdict": verdict, "rule_id": "payment_terms", "allowed": {"payment_terms_days": best},
            "approval_needed": pt["approver"],
            "reason": f"Net {days} not pre-approved for ${arr:,.0f} ARR; Net {best} is the max without {pt['approver']}"}


def _tier_gate(feature, deal, tiers_allowed, rule_id, extra=""):
    if deal.get("tier") in tiers_allowed:
        return {"verdict": "info", "rule_id": rule_id, "allowed": {feature: True}, "approval_needed": None,
                "reason": f"{FEATURE_LABELS.get(feature, feature)} available on {', '.join(tiers_allowed)} tier{extra}"}
    return {"verdict": "counter", "rule_id": rule_id, "allowed": {"tier": tiers_allowed[0], feature: True},
            "approval_needed": None,
            "reason": f"{FEATURE_LABELS.get(feature, feature)} requires {tiers_allowed[0]} tier (deal is {deal.get('tier')}){extra}"}


def check_compliance(req, deal, rules):
    comp = rules["compliance"]
    feature = (req.get("feature") or "").lower()
    if "hipaa" in feature or "baa" in feature:
        return _tier_gate("hipaa_baa", deal, comp["hipaa_baa"]["tiers"], "compliance_hipaa")
    if "residency" in feature or feature.startswith("eu"):
        er = comp["eu_residency"]
        return _tier_gate("eu_residency", deal, er["tiers"], "data_residency", f"; +{er['uplift_pct']}% uplift")
    if "soc" in feature:
        return {"verdict": "info", "rule_id": "compliance_soc2", "allowed": {}, "approval_needed": None,
                "reason": "SOC 2 Type II report available under NDA" if comp["soc2_type2"] else "No SOC 2 report"}
    if "gdpr" in feature or "dpa" in feature:
        return {"verdict": "info", "rule_id": "compliance_gdpr", "allowed": {}, "approval_needed": None,
                "reason": f"{comp['gdpr_dpa'].capitalize()} DPA with SCCs; custom redlines go to Legal"}
    return _no_rule()


def check_legal(req, deal, rules):
    lc = rules["legal"]["liability_cap_multiple"]
    mult = req.get("liability_multiple")
    feature = (req.get("feature") or "").lower()
    if mult is not None:
        mult = float(mult)
        if mult <= lc["max_without_approval"]:
            return {"verdict": "approved", "rule_id": "liability_cap", "allowed": {"liability_cap_multiple": mult},
                    "approval_needed": None, "reason": f"{mult:g}x fees is within the {lc['max_without_approval']}x limit"}
        return {"verdict": "escalate", "rule_id": "liability_cap",
                "allowed": {"liability_cap_multiple": lc["max_without_approval"]}, "approval_needed": lc["approver"],
                "reason": f"Liability cap {mult:g}x exceeds {lc['max_without_approval']}x limit"}
    if "termination" in feature or "convenience" in feature:
        if rules["legal"]["termination_for_convenience"]:
            return {"verdict": "approved", "rule_id": "termination", "allowed": {}, "approval_needed": None,
                    "reason": "Termination for convenience allowed"}
        return {"verdict": "escalate", "rule_id": "termination", "allowed": {}, "approval_needed": "Legal",
                "reason": "Termination for convenience is not offered"}
    return _no_rule()


def check_product(req, deal, rules):
    feature = (req.get("feature") or "").lower().replace(" ", "_")
    tier_features = {f for v in rules["tiers"].values() for f in v["features"]}
    for key in tier_features:
        if feature and (key in feature or feature in key):
            tiers = [t for t, v in rules["tiers"].items() if key in v["features"]]
            return _tier_gate(key, deal, tiers, "product_features")
    return _no_rule()  # other product questions are answered from the docs
