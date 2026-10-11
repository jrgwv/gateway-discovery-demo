"""Minimal client for the demo: Cognito login plus raw MCP JSON-RPC calls to the Gateway.

The discovery matrix uses raw JSON-RPC on purpose so the post can show exactly
what the Gateway returns, without an agent framework in between.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from functools import cache
from pathlib import Path

import boto3
import requests

ROOT = Path(__file__).resolve().parent.parent
SEARCH_TOOL = "x_amz_bedrock_agentcore_search"
MAX_TOOLS = int(os.environ.get("MAX_TOOLS", "4"))  # top-k search results the agent passes to the model
# The Gateway docs send this header on raw calls; set it to a version your gateway supports if calls are rejected.
MCP_PROTOCOL_VERSION = os.environ.get("MCP_PROTOCOL_VERSION", "")
_ids = itertools.count(1)


@cache
def state() -> dict:
    path = ROOT / ".demo-state.json"
    if not path.exists():
        raise SystemExit("Run scripts/setup_gateway.py first.")
    return json.loads(path.read_text())


@cache
def users() -> dict:
    return json.loads((ROOT / ".demo-users.json").read_text())


def username_for(persona: str) -> str:
    return next(u for u, info in users().items() if info["persona"] == persona)


@cache
def access_token(persona: str) -> str:
    username = username_for(persona)
    resp = boto3.client("cognito-idp", region_name=state()["region"]).initiate_auth(
        ClientId=state()["appClientId"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": users()[username]["password"]},
    )
    return resp["AuthenticationResult"]["AccessToken"]


def _parse(response: requests.Response) -> dict:
    """Gateway returns JSON; tolerate an SSE-framed body just in case."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return response.json()


def rpc(persona: str, method: str, params: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token(persona)}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **({"MCP-Protocol-Version": MCP_PROTOCOL_VERSION} if MCP_PROTOCOL_VERSION else {}),
    }
    body = {"jsonrpc": "2.0", "id": next(_ids), "method": method, **({"params": params} if params else {})}
    for attempt in range(6):
        response = requests.post(state()["gatewayUrl"], headers=headers, json=body, timeout=60)
        if response.status_code != 429:
            break
        time.sleep(2 ** attempt)  # the Gateway throttles bursts, semantic search especially
    response.raise_for_status()
    return _parse(response)


def list_tools(persona: str) -> list[dict]:
    tools, cursor = [], None
    while True:
        body = rpc(persona, "tools/list", {"cursor": cursor} if cursor else None)
        if "error" in body:
            raise RuntimeError(body["error"])
        tools += body["result"].get("tools", [])
        cursor = body["result"].get("nextCursor")
        if not cursor:
            return tools


def call_tool(persona: str, name: str, arguments: dict) -> dict:
    """Returns {'ok': bool, 'text': str, 'raw': dict}. Policy denials come back as isError results."""
    body = rpc(persona, "tools/call", {"name": name, "arguments": arguments})
    if "error" in body:
        return {"ok": False, "text": json.dumps(body["error"]), "raw": body}
    result = body.get("result", {})
    text = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    return {"ok": not result.get("isError", False), "text": text, "raw": body}


def tool_names_from_search(result: dict) -> list[str]:
    """Pull tool names out of a search result, whatever shape it arrives in."""
    payload = result.get("structuredContent")
    if payload is None:
        for item in result.get("content", []):
            if item.get("type") == "text":
                try:
                    payload = json.loads(item["text"])
                except json.JSONDecodeError:
                    continue
                break
    if isinstance(payload, dict):
        payload = payload.get("tools", payload.get("results", []))
    return [t["name"] for t in payload or [] if isinstance(t, dict) and "name" in t]


def search_tools(persona: str, query: str) -> dict:
    """Returns {'ok': bool, 'names': [...], 'text': str}."""
    response = call_tool(persona, SEARCH_TOOL, {"query": query})
    if not response["ok"]:
        return {"ok": False, "names": [], "text": response["text"]}
    return {"ok": True, "names": tool_names_from_search(response["raw"]["result"]), "text": response["text"]}
