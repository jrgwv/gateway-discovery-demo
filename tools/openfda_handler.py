"""openFDA tools: adverse event signal counts, drug recalls, and drug label sections.

Real public data from https://open.fda.gov. No key is required for light use; set
OPENFDA_API_KEY to raise the daily limit.
"""

from __future__ import annotations

import os
import re
import urllib.parse

from common import ToolError, clamp_int, dispatch, http_get_json, require_str, truncate

BASE = "https://api.fda.gov/drug"
API_KEY = os.environ.get("OPENFDA_API_KEY", "")
SEARCH_SAFE_CHARS = '+:"()[]'
SAFE_TERM = re.compile(r"^[A-Za-z0-9 \-/.,']{2,80}$")
LABEL_CANDIDATES = 10
LABEL_SECTIONS = {
    "boxed_warning",
    "indications_and_usage",
    "contraindications",
    "warnings_and_cautions",
    "warnings",
    "adverse_reactions",
    "drug_interactions",
}


def _term(event: dict, key: str) -> str:
    value = require_str(event, key, max_len=80)
    if not SAFE_TERM.match(value):
        raise ToolError(f"'{key}' contains unsupported characters.")
    return value.replace('"', "")


def _query(search: str, **params) -> str:
    """openFDA wants literal '+AND+' and quoted phrases, so encode by hand."""
    parts = ["search=" + urllib.parse.quote(search, safe=SEARCH_SAFE_CHARS)]
    for key, value in params.items():
        if value not in (None, ""):
            parts.append(f"{key}={urllib.parse.quote(str(value), safe='.')}")
    if API_KEY:
        parts.append(f"api_key={API_KEY}")
    return "&".join(parts)


def _first_match(
    endpoint: str, fields: tuple[str, ...], drug: str, suffix: str = "", limit: int = 1
) -> tuple[str, dict] | None:
    """Try generic name, then brand name, and report which search matched."""
    for field in fields:
        search = f'{field}:"{drug}"{suffix}'
        body = http_get_json(f"{BASE}/{endpoint}", {}, raw_query=_query(search, limit=limit))
        if body and body.get("results"):
            return search, body
    return None


def search_adverse_events(event: dict, _ctx: dict) -> dict:
    drug = _term(event, "drug_name")
    top_n = clamp_int(event.get("top_n"), 10, 1, 25)
    match = _first_match(
        "event.json",
        ("patient.drug.openfda.generic_name", "patient.drug.openfda.brand_name"),
        drug,
        "+AND+serious:1" if event.get("serious_only") else "",
    )
    if match is None:
        return {"drug_name": drug, "total_reports": 0, "top_reactions": [],
                "note": "No FAERS reports matched this drug name."}
    search, total_body = match
    counts = http_get_json(
        f"{BASE}/event.json", {},
        raw_query=_query(search, count="patient.reaction.reactionmeddrapt.exact", limit=top_n),
    ) or {"results": []}

    return {
        "drug_name": drug,
        "serious_only": bool(event.get("serious_only")),
        "total_reports": total_body.get("meta", {}).get("results", {}).get("total", 0),
        "top_reactions": [{"reaction": r["term"], "reports": r["count"]} for r in counts.get("results", [])],
        "source": "FDA Adverse Event Reporting System (FAERS) via openFDA",
        "caveat": ("Spontaneous reports listing this drug as suspect or concomitant; "
                   "counts do not establish causation or incidence."),
    }


def search_recalls(event: dict, _ctx: dict) -> dict:
    product = _term(event, "product_query")
    limit = clamp_int(event.get("limit"), 5, 1, 20)
    search = f'product_description:"{product}"'
    classification = event.get("classification")
    if classification:
        if classification not in {"Class I", "Class II", "Class III"}:
            raise ToolError("'classification' must be 'Class I', 'Class II' or 'Class III'.")
        search += f'+AND+classification:"{classification}"'

    body = http_get_json(f"{BASE}/enforcement.json", {}, raw_query=_query(search, limit=limit, sort="report_date:desc"))
    if body is None:
        return {"product_query": product, "recalls": [], "note": "No recalls matched."}
    return {
        "product_query": product,
        "total_matches": body.get("meta", {}).get("results", {}).get("total", 0),
        "recalls": [
            {
                "recall_number": r.get("recall_number"),
                "classification": r.get("classification"),
                "status": r.get("status"),
                "report_date": r.get("report_date"),
                "recalling_firm": r.get("recalling_firm"),
                "reason": truncate(r.get("reason_for_recall"), 300),
                "product": truncate(r.get("product_description"), 200),
            }
            for r in body.get("results", [])
        ],
        "source": "FDA Recall Enterprise System via openFDA",
    }


def _pick_label(results: list[dict]) -> dict:
    """A name search also matches combination products (metformin -> sitagliptin/metformin),
    so prefer the label with the fewest active ingredients, then the most recent."""
    def rank(label: dict) -> tuple[int, int]:
        ingredients = len(label.get("openfda", {}).get("substance_name", [])) or 99
        effective = label.get("effective_time") or "0"
        return ingredients, -int(effective) if effective.isdigit() else 0
    return min(results, key=rank)


def get_drug_label(event: dict, _ctx: dict) -> dict:
    drug = _term(event, "drug_name")
    requested = event.get("sections") or ["boxed_warning", "indications_and_usage", "warnings_and_cautions"]
    if not isinstance(requested, list):
        raise ToolError("'sections' must be a list.")
    unknown = set(requested) - LABEL_SECTIONS
    if unknown:
        raise ToolError(f"Unsupported sections: {sorted(unknown)}. Allowed: {sorted(LABEL_SECTIONS)}.")

    match = _first_match("label.json", ("openfda.generic_name", "openfda.brand_name"), drug, limit=LABEL_CANDIDATES)
    if match is None:
        return {"drug_name": drug, "found": False}
    label = _pick_label(match[1]["results"])
    openfda = label.get("openfda", {})
    substances = openfda.get("substance_name", [])
    return {
        "drug_name": drug,
        "found": True,
        "generic_names": openfda.get("generic_name", [])[:3],
        "active_ingredients": substances[:5],
        "combination_product": len(substances) > 1,
        "brand_names": openfda.get("brand_name", [])[:3],
        "manufacturer": (openfda.get("manufacturer_name") or [None])[0],
        "effective_time": label.get("effective_time"),
        "set_id": label.get("set_id"),
        "sections": {s: truncate(label.get(s), 1500) for s in requested if label.get(s)},
        "source": "FDA Structured Product Labeling via openFDA",
    }


HANDLERS = {
    "search_adverse_events": search_adverse_events,
    "search_recalls": search_recalls,
    "get_drug_label": get_drug_label,
}


def lambda_handler(event, context):
    return dispatch(HANDLERS, event, context)
