"""ClinicalTrials.gov tool: search registered studies (public API v2)."""

from __future__ import annotations

from common import ToolError, clamp_int, dispatch, http_get_json, require_str

BASE = "https://clinicaltrials.gov/api/v2/studies"
STATUSES = {
    "RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING",
    "COMPLETED", "TERMINATED", "SUSPENDED", "WITHDRAWN", "ENROLLING_BY_INVITATION",
}
PHASES = {"EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4"}
FIELDS = "|".join([
    "NCTId", "BriefTitle", "OverallStatus", "Phase", "LeadSponsorName",
    "EnrollmentCount", "StartDate", "PrimaryCompletionDate", "Condition", "InterventionName",
])


def search_trials(event: dict, _ctx: dict) -> dict:
    condition = require_str(event, "condition", max_len=120)
    params = {
        "query.cond": condition,
        "query.intr": (event.get("intervention") or "").strip()[:120],
        "pageSize": clamp_int(event.get("page_size"), 10, 1, 25),
        "fields": FIELDS,
        "countTotal": "true",
        "format": "json",
    }
    status = event.get("status")
    if status:
        if status not in STATUSES:
            raise ToolError(f"'status' must be one of {sorted(STATUSES)}.")
        params["filter.overallStatus"] = status
    phase = event.get("phase")
    if phase:
        if phase not in PHASES:
            raise ToolError(f"'phase' must be one of {sorted(PHASES)}.")
        params["filter.advanced"] = f"AREA[Phase]{phase}"

    body = http_get_json(BASE, params) or {}
    studies = []
    for study in body.get("studies", []):
        proto = study.get("protocolSection", {})
        ident = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        design = proto.get("designModule", {})
        sponsor = proto.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
        arms = proto.get("armsInterventionsModule", {})
        studies.append({
            "nct_id": ident.get("nctId"),
            "title": ident.get("briefTitle"),
            "status": status_mod.get("overallStatus"),
            "phases": design.get("phases", []),
            "enrollment": design.get("enrollmentInfo", {}).get("count"),
            "lead_sponsor": sponsor.get("name"),
            "start_date": status_mod.get("startDateStruct", {}).get("date"),
            "primary_completion": status_mod.get("primaryCompletionDateStruct", {}).get("date"),
            "interventions": [i.get("name") for i in arms.get("interventions", [])][:5],
            "url": f"https://clinicaltrials.gov/study/{ident.get('nctId')}" if ident.get("nctId") else None,
        })
    return {
        "condition": condition,
        "total_matches": body.get("totalCount", len(studies)),
        "studies": studies,
        "source": "ClinicalTrials.gov API v2",
    }


HANDLERS = {"search_trials": search_trials}


def lambda_handler(event, context):
    return dispatch(HANDLERS, event, context)
