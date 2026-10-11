"""Strands agent with per-request tool discovery through AgentCore Gateway.

Per question:
  1. tools/list            -> what this caller is allowed to see (policy-filtered by Gateway)
  2. semantic search       -> what's relevant to this question
  3. intersect             -> relevant AND visible; only these schemas reach the model
  4. run the agent         -> every call is still evaluated by Cedar at the Gateway

Step 3 is defense in depth: the agent never depends on search results being
policy-filtered, and a tool can't reach the model unless the Gateway listed it.
If the search call itself fails or is denied, the agent falls back to everything
tools/list returned, which is still policy-filtered.

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
from gateway_client import MAX_TOOLS, SEARCH_TOOL, access_token, state  # noqa: E402

MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")

SYSTEM_PROMPT = """You are a life sciences research assistant working through governed enterprise tools.
You can only see the tools this user is permitted to use, so missing tools mean missing access.

Before calling anything, check whether one of your tools directly provides the kind of data the
question asks for. If none does, do not call a tool. Reply that you don't have access to that
data, name the kind of data that would be needed, and stop.

Never substitute a nearby source for the one requested. Questions about adverse events, side
effect frequency or safety reports need adverse event report data (counts of reported cases);
the warnings in a drug label are not that, so do not answer them from a label. Likewise a trial
listing is not recall data. Do not answer from memory either: never
state counts, trial IDs, recall numbers or label text that did not come from a tool result in
this conversation. If a tool returns nothing or you are unsure, say you don't know.

If a tool call is denied by policy, say the request was not permitted and stop. Do not retry with
different arguments. Cite the source each tool reports."""


# Appended when the tool search ranked first for this question is one the caller can't see.
# It never names the tool: the model learns only that the best match is out of reach.
BEST_MATCH_WITHHELD = """

For this question, the tool that best matches it is not available to this user. Do not approximate
the answer with your other tools. Say you don't have access to the data needed, name the kind of
data, and stop."""


def all_visible_tools(client: MCPClient) -> list:
    tools, token = [], None
    while True:
        page = client.list_tools_sync(pagination_token=token)
        tools.extend(page)
        token = page.pagination_token
        if not token:
            return tools


def search(client: MCPClient, question: str) -> list[str] | None:
    """Tool names relevant to the question, or None if the search tool itself failed or was denied."""
    result = client.call_tool_sync(str(uuid.uuid4()), SEARCH_TOOL, {"query": question})
    if result.get("status") == "error" or result.get("isError"):
        print(f"[discovery] search failed: {result.get('content')}", file=sys.stderr)
        return None
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
        if relevant is None:
            # Everything in `visible` is already policy-filtered, so falling back to it is safe;
            # it only costs the token saving.
            print("[discovery] falling back to every tool from tools/list", file=sys.stderr)
            relevant = list(visible)
            selected = list(visible.values())
        else:
            selected = [visible[name] for name in relevant if name in visible][:MAX_TOOLS]
        hidden = [name for name in relevant if name not in visible]
        best_match_withheld = bool(relevant) and relevant[0] not in visible

        print(f"[discovery] persona={persona} visible={len(visible)} search={relevant}", file=sys.stderr)
        if hidden:
            print(f"[discovery] dropped (search returned, tools/list did not): {hidden}", file=sys.stderr)
        if best_match_withheld:
            print("[discovery] top-ranked tool is not permitted; telling the model to decline", file=sys.stderr)
        print(f"[discovery] tools sent to model: {[t.tool_name for t in selected]}", file=sys.stderr)

        agent = Agent(
            model=BedrockModel(model_id=MODEL_ID, region_name=state()["region"]),
            tools=selected,
            system_prompt=SYSTEM_PROMPT + (BEST_MATCH_WITHHELD if best_match_withheld else ""),
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
