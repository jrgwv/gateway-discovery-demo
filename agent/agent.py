"""Strands agent with per-request tool discovery through AgentCore Gateway.

Per question:
  1. tools/list            -> what this caller is allowed to see (policy-filtered by Gateway)
  2. semantic search       -> what's relevant to this question
  3. intersect             -> relevant AND visible; only these schemas reach the model
  4. run the agent         -> every call is still evaluated by Cedar at the Gateway

Step 3 is defense in depth: the agent never depends on search results being
policy-filtered, and a tool can't reach the model unless the Gateway listed it.

  python agent/agent.py --persona clinical_ops "Any recruiting phase 3 trials for semaglutide in obesity?"
  python agent/agent.py --persona clinical_ops "What are the top serious adverse events for semaglutide?"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))
from gateway_client import SEARCH_TOOL, access_token, state  # noqa: E402

MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
MAX_TOOLS = int(os.environ.get("MAX_TOOLS", "4"))

SYSTEM_PROMPT = """You are a life sciences research assistant working through governed enterprise tools.
Use only the tools you have been given. If you have no tool that can answer the question,
say plainly that you don't have access to the data needed, and name the kind of data that
would be required. Never guess at counts, trial IDs or label text. If a tool call is denied
by policy, report that the request was not permitted rather than retrying with other arguments.
Cite the source each tool reports."""


def all_visible_tools(client: MCPClient) -> list:
    tools, token = [], None
    while True:
        page = client.list_tools_sync(pagination_token=token)
        tools.extend(page)
        token = page.pagination_token
        if not token:
            return tools


def search(client: MCPClient, question: str) -> list[str]:
    result = client.call_tool_sync(str(uuid.uuid4()), SEARCH_TOOL, {"query": question})
    if result.get("status") == "error" or result.get("isError"):
        print(f"[discovery] search failed: {result.get('content')}", file=sys.stderr)
        return []
    payload = result.get("structuredContent")
    if payload is None:
        for item in result.get("content", []):
            try:
                payload = json.loads(item.get("text", ""))
                break
            except json.JSONDecodeError:
                continue
    if isinstance(payload, dict):
        payload = payload.get("tools", payload.get("results", []))
    return [t["name"] for t in payload or [] if isinstance(t, dict) and "name" in t]


def run(persona: str, question: str) -> None:
    client = MCPClient(url=state()["gatewayUrl"], headers={"Authorization": f"Bearer {access_token(persona)}"})
    with client:
        visible = {t.tool_name: t for t in all_visible_tools(client) if t.tool_name != SEARCH_TOOL}
        relevant = search(client, question)
        selected = [visible[name] for name in relevant if name in visible][:MAX_TOOLS]
        hidden = [name for name in relevant if name not in visible]

        print(f"[discovery] persona={persona} visible={len(visible)} search={relevant}", file=sys.stderr)
        if hidden:
            print(f"[discovery] dropped (search returned, tools/list did not): {hidden}", file=sys.stderr)
        print(f"[discovery] tools sent to model: {[t.tool_name for t in selected]}", file=sys.stderr)

        agent = Agent(
            model=BedrockModel(model_id=MODEL_ID, region_name=state()["region"]),
            tools=selected,
            system_prompt=SYSTEM_PROMPT,
        )
        agent(question)
        print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", required=True, choices=["clinical_ops", "safety_analyst", "safety_lead"])
    parser.add_argument("question")
    args = parser.parse_args()
    run(args.persona, args.question)


if __name__ == "__main__":
    main()
