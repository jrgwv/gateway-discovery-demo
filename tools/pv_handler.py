"""Pharmacovigilance signal log: the one tool in this demo that writes.

Backed by a real DynamoDB table. Each record stores the AgentCore request and
MCP message IDs so it can be joined to the Gateway's policy decision logs,
which is where the caller's identity and the allow/deny decision live.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.conditions import Key
from common import ToolError, clamp_int, dispatch, require_str

TABLE = boto3.resource("dynamodb").Table(os.environ["SIGNAL_TABLE"]) if os.environ.get("SIGNAL_TABLE") else None
SEVERITIES = ("low", "moderate", "serious", "critical")
EVIDENCE_REF = re.compile(r"^(NCT\d{8}|[A-Z]-\d{4}-\d{4}|faers:[A-Za-z0-9 \-]{2,80}|https://\S{5,300})$")


def _table():
    if TABLE is None:
        raise ToolError("SIGNAL_TABLE is not configured.")
    return TABLE


def _drug_key(name: str) -> str:
    return " ".join(name.upper().split())


def log_safety_signal(event: dict, ctx: dict) -> dict:
    drug = require_str(event, "drug_name", max_len=80)
    description = require_str(event, "signal_description", max_len=2000)
    severity = event.get("severity")
    if severity not in SEVERITIES:
        raise ToolError(f"'severity' must be one of {list(SEVERITIES)}.")
    refs = event.get("evidence_refs") or []
    if not isinstance(refs, list) or len(refs) > 10:
        raise ToolError("'evidence_refs' must be a list of at most 10 references.")
    bad = [r for r in refs if not isinstance(r, str) or not EVIDENCE_REF.match(r)]
    if bad:
        raise ToolError(f"Unrecognized evidence references: {bad[:3]}. Use NCT IDs, recall numbers, faers:<drug> or https URLs.")

    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    signal_id = str(uuid.uuid4())
    item = {
        "drug": _drug_key(drug),
        "created": f"{now}#{signal_id}",
        "signal_id": signal_id,
        "drug_name": drug,
        "signal_description": description,
        "severity": severity,
        "evidence_refs": refs,
        "status": "OPEN",
        # Correlation IDs for joining to Gateway / Policy decision logs.
        "agentcore_request_id": ctx.get("bedrockAgentCoreAwsRequestId", "local"),
        "mcp_message_id": ctx.get("bedrockAgentCoreMcpMessageId", "local"),
        "gateway_id": ctx.get("bedrockAgentCoreGatewayId", "local"),
    }
    _table().put_item(Item=item, ConditionExpression="attribute_not_exists(created)")
    return {"signal_id": signal_id, "status": "OPEN", "severity": severity, "recorded_at": now}


def list_safety_signals(event: dict, _ctx: dict) -> dict:
    drug = require_str(event, "drug_name", max_len=80)
    limit = clamp_int(event.get("limit"), 10, 1, 50)
    response = _table().query(
        KeyConditionExpression=Key("drug").eq(_drug_key(drug)),
        ScanIndexForward=False,
        Limit=limit,
    )
    fields = ("signal_id", "severity", "status", "signal_description", "evidence_refs")
    return {
        "drug_name": drug,
        "signals": [
            {**{k: item.get(k) for k in fields}, "recorded_at": item["created"].split("#")[0]}
            for item in response.get("Items", [])
        ],
    }


HANDLERS = {
    "log_safety_signal": log_safety_signal,
    "list_safety_signals": list_safety_signals,
}


def lambda_handler(event, context):
    return dispatch(HANDLERS, event, context)
