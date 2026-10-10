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
    return _no_rule()


def _no_rule():
    return {"verdict": "info", "rule_id": None, "allowed": {}, "approval_needed": None,
            "reason": "No rule in Company Brain covers this; flag for follow-up."}


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
    for key in FEATURE_LABELS:
        if key in feature or feature in key and feature:
            tiers = [t for t, v in rules["tiers"].items() if key in v["features"]]
            return _tier_gate(key, deal, tiers, "product_features")
    return _no_rule()
