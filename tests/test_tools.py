"""Offline tests for the tool Lambdas: Gateway context handling, input validation and response shaping.

Network calls are stubbed, so these run anywhere: pytest -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "cognito_pretoken"))

import common  # noqa: E402
import ctgov_handler  # noqa: E402
import openfda_handler  # noqa: E402
import pv_handler  # noqa: E402


def gw_context(tool: str) -> SimpleNamespace:
    return SimpleNamespace(client_context=SimpleNamespace(custom={
        "bedrockAgentCoreToolName": tool,
        "bedrockAgentCoreAwsRequestId": "req-123",
        "bedrockAgentCoreMcpMessageId": "msg-456",
        "bedrockAgentCoreGatewayId": "gw-789",
    }))


def test_tool_prefix_is_stripped():
    assert common.resolve_tool_name(gw_context("openfda___get_drug_label")) == "get_drug_label"


def test_unknown_tool_is_a_readable_error():
    assert "Unknown tool" in openfda_handler.lambda_handler({}, gw_context("openfda___nope"))["error"]


def test_adverse_events_shapes_response_and_builds_query():
    calls = []

    def fake_get(_base, _params, raw_query=None):
        calls.append(raw_query)
        if "count=" in raw_query:
            return {"results": [{"term": "NAUSEA", "count": 900}, {"term": "VOMITING", "count": 400}]}
        if "generic_name" in raw_query:
            return None  # no generic match -> should fall back to brand name
        return {"meta": {"results": {"total": 4321}}, "results": [{}]}

    with mock.patch.object(openfda_handler, "http_get_json", side_effect=fake_get):
        out = openfda_handler.lambda_handler(
            {"drug_name": "Ozempic", "serious_only": True, "top_n": 2},
            gw_context("openfda___search_adverse_events"),
        )
    assert out["total_reports"] == 4321
    assert out["top_reactions"][0] == {"reaction": "NAUSEA", "reports": 900}
    assert 'generic_name:"Ozempic"+AND+serious:1' in calls[0]
    assert 'brand_name:"Ozempic"+AND+serious:1' in calls[1]
    assert "brand_name" in calls[2] and "count=" in calls[2]


def test_adverse_events_no_match_returns_zero():
    with mock.patch.object(openfda_handler, "http_get_json", return_value=None):
        out = openfda_handler.lambda_handler({"drug_name": "notadrug"}, gw_context("openfda___search_adverse_events"))
    assert out["total_reports"] == 0


@pytest.mark.parametrize("bad", ['x" OR 1', "a", "drug;DROP"])
def test_search_injection_is_rejected(bad):
    out = openfda_handler.lambda_handler({"drug_name": bad}, gw_context("openfda___get_drug_label"))
    assert "error" in out


def test_label_prefers_single_ingredient_then_newest():
    def label(substances, effective, brand):
        return {"openfda": {"substance_name": substances, "brand_name": [brand]}, "effective_time": effective,
                "boxed_warning": [brand]}
    body = {"results": [
        label(["SITAGLIPTIN", "METFORMIN HYDROCHLORIDE"], "20260812", "ZITUVIMET"),
        label(["METFORMIN HYDROCHLORIDE"], "20240821", "old"),
        label(["METFORMIN HYDROCHLORIDE"], "20260101", "new"),
    ]}
    with mock.patch.object(openfda_handler, "http_get_json", return_value=body):
        out = openfda_handler.lambda_handler({"drug_name": "metformin"}, gw_context("openfda___get_drug_label"))
    assert out["brand_names"] == ["new"]
    assert out["combination_product"] is False
    assert out["active_ingredients"] == ["METFORMIN HYDROCHLORIDE"]


def test_label_flags_combination_when_nothing_else_matches():
    body = {"results": [{"openfda": {"substance_name": ["A", "B"]}, "effective_time": "20260101"}]}
    with mock.patch.object(openfda_handler, "http_get_json", return_value=body):
        out = openfda_handler.lambda_handler({"drug_name": "combo"}, gw_context("openfda___get_drug_label"))
    assert out["combination_product"] is True


def test_api_key_is_not_logged(caplog):
    caplog.set_level("INFO")
    with mock.patch.object(common.urllib.request, "urlopen", side_effect=TimeoutError("x")):
        with pytest.raises(common.ToolError):
            common.http_get_json("https://example.test/x", {}, raw_query="search=a&api_key=SECRET123&limit=1")
    assert "SECRET123" not in caplog.text and "api_key=***&limit=1" in caplog.text


def test_label_rejects_unknown_section():
    out = openfda_handler.lambda_handler(
        {"drug_name": "metformin", "sections": ["secret_section"]}, gw_context("openfda___get_drug_label")
    )
    assert "Unsupported sections" in out["error"]


def test_recall_classification_validated():
    out = openfda_handler.lambda_handler(
        {"product_query": "metformin", "classification": "Class IV"}, gw_context("openfda___search_recalls")
    )
    assert "classification" in out["error"]


def test_trials_parses_v2_shape():
    body = {"totalCount": 1, "studies": [{"protocolSection": {
        "identificationModule": {"nctId": "NCT01234567", "briefTitle": "A study"},
        "statusModule": {"overallStatus": "RECRUITING", "startDateStruct": {"date": "2025-01"}},
        "designModule": {"phases": ["PHASE3"], "enrollmentInfo": {"count": 500}},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Sponsor"}},
        "armsInterventionsModule": {"interventions": [{"name": "Drug X"}]},
    }}]}
    with mock.patch.object(ctgov_handler, "http_get_json", return_value=body) as get:
        out = ctgov_handler.lambda_handler(
            {"condition": "obesity", "status": "RECRUITING", "phase": "PHASE3"}, gw_context("ctgov___search_trials")
        )
    params = get.call_args[0][1]
    assert params["filter.advanced"] == "AREA[Phase]PHASE3"
    assert out["studies"][0]["nct_id"] == "NCT01234567"
    assert out["studies"][0]["url"].endswith("NCT01234567")


def test_trials_rejects_bad_phase():
    out = ctgov_handler.lambda_handler({"condition": "x y", "phase": "PHASE9"}, gw_context("ctgov___search_trials"))
    assert "phase" in out["error"]


def test_signal_write_records_correlation_ids():
    table = mock.MagicMock()
    with mock.patch.object(pv_handler, "TABLE", table):
        out = pv_handler.lambda_handler(
            {"drug_name": "Drug X", "signal_description": "cluster of events", "severity": "serious",
             "evidence_refs": ["NCT01234567", "faers:drug x"]},
            gw_context("pv___log_safety_signal"),
        )
    item = table.put_item.call_args.kwargs["Item"]
    assert out["status"] == "OPEN"
    assert item["drug"] == "DRUG X"
    assert item["agentcore_request_id"] == "req-123"
    assert item["mcp_message_id"] == "msg-456"


def test_signal_write_validates_severity_and_refs():
    with mock.patch.object(pv_handler, "TABLE", mock.MagicMock()):
        bad_sev = pv_handler.lambda_handler(
            {"drug_name": "X", "signal_description": "d", "severity": "urgent"}, gw_context("pv___log_safety_signal"))
        bad_ref = pv_handler.lambda_handler(
            {"drug_name": "X", "signal_description": "d", "severity": "low", "evidence_refs": ["javascript:x"]},
            gw_context("pv___log_safety_signal"))
    assert "severity" in bad_sev["error"]
    assert "evidence" in bad_ref["error"]


def test_schemas_match_handlers():
    for schema_file, module in (("openfda.json", openfda_handler), ("ctgov.json", ctgov_handler), ("pv.json", pv_handler)):
        names = {t["name"] for t in json.loads((ROOT / "tools" / "schemas" / schema_file).read_text())}
        assert names == set(module.HANDLERS), schema_file


def test_policies_reference_real_tools():
    actions = {
        f"{target}___{t['name']}"
        for target, f in (("openfda", "openfda.json"), ("ctgov", "ctgov.json"), ("pv", "pv.json"))
        for t in json.loads((ROOT / "tools" / "schemas" / f).read_text())
    }
    import re
    for path in (ROOT / "policies").glob("*.cedar"):
        text = path.read_text()
        assert "{{GATEWAY_ARN}}" in text, path.name
        for action in re.findall(r'AgentCore::Action::"([^"]+)"', text):
            assert action in actions, f"{path.name}: {action}"


@pytest.mark.parametrize("groups,expected", [
    (["clinical_ops"], "clinical_ops"),
    (["clinical_ops", "safety_lead"], "safety_lead"),
    ([], "none"),
])
def test_pretoken_persona_claim(groups, expected):
    import handler as pretoken
    event = {"request": {"groupConfiguration": {"groupsToOverride": groups}}, "response": {}}
    out = pretoken.lambda_handler(event, None)
    claims = out["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"]["claimsToAddOrOverride"]
    assert claims == {"persona": expected}
