"""The hook number: input tokens for the full tool catalog vs. search-scoped tools.

Measures with real Bedrock Converse calls (maxTokens=1) so the counts are the
model's own tokenizer, not an estimate. With this repo's six tools the absolute
saving is small; the per-tool cost is what scales, so the script also prints a
clearly labelled projection for larger catalogs.

  MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0 python demo/token_compare.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import boto3
from discovery_matrix import QUERIES
from gateway_client import SEARCH_TOOL, list_tools, search_tools, state

MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
PERSONA = "safety_lead"  # sees every tool, so "full catalog" is the whole gateway
RESULTS = Path(__file__).resolve().parent.parent / "results"


def to_tool_spec(tool: dict) -> dict:
    return {"toolSpec": {"name": tool["name"], "description": tool.get("description", ""),
                         "inputSchema": {"json": tool["inputSchema"]}}}


def input_tokens(bedrock, prompt: str, tools: list[dict]) -> int:
    kwargs = {"toolConfig": {"tools": [to_tool_spec(t) for t in tools]}} if tools else {}
    resp = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 1},
        **kwargs,
    )
    return resp["usage"]["inputTokens"]


def main() -> None:
    bedrock = boto3.client("bedrock-runtime", region_name=state()["region"])
    catalog = [t for t in list_tools(PERSONA) if t["name"] != SEARCH_TOOL]
    by_name = {t["name"]: t for t in catalog}

    lines = [f"# Token comparison ({MODEL_ID})", "",
             "| Query | Tools sent | Input tokens | vs. full catalog | Search latency |", "|---|---|---|---|---|"]
    per_tool = None
    for query in QUERIES:
        baseline = input_tokens(bedrock, query, [])
        full = input_tokens(bedrock, query, catalog)
        per_tool = (full - baseline) / len(catalog)

        started = time.perf_counter()
        found = search_tools(PERSONA, query)
        latency_ms = (time.perf_counter() - started) * 1000
        scoped_tools = [by_name[n] for n in found["names"] if n in by_name]
        scoped = input_tokens(bedrock, query, scoped_tools)

        saving = 100 * (full - scoped) / full
        lines.append(f"| {query} | {len(scoped_tools)} of {len(catalog)} | {scoped} (full: {full}) "
                     f"| -{saving:.0f}% | {latency_ms:.0f} ms |")
        print(f"{query}\n  full={full} scoped={scoped} ({len(scoped_tools)} tools) search={latency_ms:.0f}ms")

    avg_scoped = 3  # typical top-k you'd pass after search
    lines += ["", f"Measured average schema cost: **{per_tool:.0f} tokens per tool**.", "",
              "Projection (not measured): tool-schema tokens per request at that average.", "",
              "| Catalog size | All tools in context | Search-scoped (~3 tools) |", "|---|---|---|"]
    for size in (50, 200, 1000):
        lines.append(f"| {size} | {size * per_tool:,.0f} | {avg_scoped * per_tool:,.0f} |")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "token_compare.md").write_text("\n".join(lines) + "\n")
    print(f"\n~{per_tool:.0f} tokens per tool schema. Wrote results/token_compare.md")


if __name__ == "__main__":
    main()
