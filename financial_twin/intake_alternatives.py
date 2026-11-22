import json
import re

from financial_twin import create_root
from financial_twin.branching import (PROPOSAL_SCHEMA, SAMPLES, current_flows, ensure_education_proposal,
                                      ensure_work_proposal, normalize_proposal, proposal_prompt)


ALTERNATIVES_SCHEMA = {
    "type": "object",
    "properties": {"alternatives": {"type": "array", "items": {
        "type": "object", "properties": {"description": {"type": "string"}, "proposal": PROPOSAL_SCHEMA},
        "required": ["description", "proposal"]}}},
    "required": ["alternatives"],
}


def extract_alternatives(statement, scenario, api_key, ask, cache_get, cache_put):
    return extract_alternatives_for_parent(statement, create_root(scenario), api_key, ask, cache_get, cache_put)


def extract_alternatives_for_parent(statement, parent, api_key, ask, cache_get, cache_put):
    if not re.search(r"\b(?:or|versus|vs\.?|either)\b", statement, re.I):
        return []
    prompt = (
        "Identify distinct alternative future plans the user wants to compare. Return an empty array if the "
        "statement does not compare alternative plans. Create one proposal per alternative, never combine "
        "mutually exclusive options. Each description must describe just that option. "
        "For a sabbatical, pause earnings for the specified duration and resume them afterward. "
        "The current situation is the baseline. " + proposal_prompt(statement, parent) +
        " The user's complete statement is " + json.dumps(statement)
    )
    raw = cache_get("intake_alternatives", prompt, ALTERNATIVES_SCHEMA)
    if raw is None:
        raw = ask(prompt, ALTERNATIVES_SCHEMA, api_key)
        alternatives = _normalize(raw, parent, statement)
        stored = cache_put("intake_alternatives", prompt, ALTERNATIVES_SCHEMA, raw)
        if stored != raw:
            alternatives = _normalize(stored, parent, statement)
        return alternatives
    return _normalize(raw, parent, statement)


def _normalize(raw, parent, statement):
    options = raw.get("alternatives") if isinstance(raw, dict) else None
    if not isinstance(options, list) or len(options) > 5:
        raise ValueError("The AI returned invalid alternative plans. Please rephrase and try again.")
    plans = []
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get("description"), str):
            raise ValueError("The AI returned invalid alternative plans. Please rephrase and try again.")
        description = option["description"].strip()
        if not description or len(description) > 4000:
            raise ValueError("The AI returned invalid alternative plans. Please rephrase and try again.")
        proposal = ensure_education_proposal(option.get("proposal"), description, parent["date"])
        proposal = ensure_work_proposal(proposal, description, parent["date"], current_flows(parent)[0])
        plans.append(normalize_proposal(proposal, parent))
    if re.search(r"\bsabbatical\b", statement, re.I) and re.search(r"\bpart[ -]?time\b", statement, re.I):
        titles = " ".join(plan["title"] for plan in plans)
        income, expenses = current_flows(parent)
        clauses = re.split(r"\b(?:or|versus|vs\.?)\b", statement, flags=re.I)
        sabbatical_clause = next((clause for clause in clauses if re.search(r"\bsabbatical\b", clause, re.I)), "")
        part_time_clause = next((clause for clause in clauses if re.search(r"\bpart[ -]?time\b", clause, re.I)), "")
        if not re.search(r"\bsabbatical\b|\b(?:break|leave)\b", titles, re.I):
            proposal = {"title": "Sabbatical", "summary": "Take a sabbatical and then return to work.",
                        "assumptions": "", "changes": [], "one_offs": [], "assets": [], "investment_multiplier": 1}
            proposal = ensure_work_proposal(proposal, sabbatical_clause, parent["date"], income)
            if proposal["changes"]:
                plan = normalize_proposal(proposal, parent)
                plan["income_basis_chf"] = income
                plans.append(plan)
        if not re.search(r"\bpart[ -]?time\b", titles, re.I):
            percentage = re.search(r"\bpart[ -]?time\b[^\d]{0,20}(\d{1,3})\s*(?:%|percent)|"
                                   r"\b(\d{1,3})\s*(?:%|percent)\s*(?:work(?:ing)?\s*)?part[ -]?time\b",
                                   part_time_clause, re.I)
            fraction = next((group for group in percentage.groups() if group), None) if percentage else None
            if fraction and int(fraction) <= 100:
                proposal = {"title": "Go part-time", "summary": "Reduce working time.", "assumptions": "",
                            "changes": [], "one_offs": [], "assets": [], "investment_multiplier": 1}
                proposal = ensure_work_proposal(proposal, "part-time " + fraction + "%", parent["date"], income)
            else:
                proposal = SAMPLES["part_time"](income, expenses)
            plan = normalize_proposal(proposal, parent)
            plan["income_basis_chf"] = income
            plans.append(plan)
    return plans
