"""The hook number: input tokens for the full tool catalog vs. search-scoped tools.

Measures with real Bedrock Converse calls (maxTokens=1) so the counts are the
model's own tokenizer, not an estimate. With this repo's six tools the absolute
saving is small; the per-tool cost is what scales, so the script also prints a
clearly labelled projection for larger catalogs.

Sending any tools at all adds a fixed overhead on top of the schemas themselves.
The script separates the two, so the projection doesn't multiply that one-off
cost by the catalog size.

  MODEL_ID=us.amazon.nova-pro-v1:0 python demo/token_compare.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import boto3
from discovery_matrix import QUERIES
from gateway_client import MAX_TOOLS, SEARCH_TOOL, list_tools, search_tools, state

MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-pro-v1:0")
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


def schema_costs(baseline: int, singles: list[int], full: int) -> tuple[float, float]:
    """Split tool tokens into (fixed overhead for sending any tools, average tokens per tool schema).

    With n tools: single_i = baseline + fixed + schema_i and full = baseline + fixed + sum(schema_i),
    so sum(singles) - full = (n - 1) * (baseline + fixed).
    """
    n = len(singles)
    if n < 2:
        raise ValueError("Need at least two tools to separate fixed overhead from per-tool cost.")
    fixed = (sum(singles) - full) / (n - 1) - baseline
    return fixed, (full - baseline - fixed) / n


def main() -> None:
    bedrock = boto3.client("bedrock-runtime", region_name=state()["region"])
    catalog = [t for t in list_tools(PERSONA) if t["name"] != SEARCH_TOOL]
    by_name = {t["name"]: t for t in catalog}

    lines = [f"# Token comparison ({MODEL_ID})", "",
             "| Query | Tools sent | Input tokens | vs. full catalog | Search latency |", "|---|---|---|---|---|"]
    sent_counts = []
    for query in QUERIES:
        full = input_tokens(bedrock, query, catalog)

        started = time.perf_counter()
        found = search_tools(PERSONA, query)
        latency_ms = (time.perf_counter() - started) * 1000
        if not found["ok"]:
            # Don't report "no tools" as a saving: the agent falls back to the full list here.
            lines.append(f"| {query} | search failed: {found['text'][:80]} | n/a (full: {full}) | n/a | {latency_ms:.0f} ms |")
            print(f"{query}\n  search FAILED: {found['text'][:200]}")
            continue
        scoped_tools = [by_name[n] for n in found["names"] if n in by_name][:MAX_TOOLS]
        scoped = input_tokens(bedrock, query, scoped_tools)
        sent_counts.append(len(scoped_tools))

        saving = 100 * (full - scoped) / full
        lines.append(f"| {query} | {len(scoped_tools)} of {len(catalog)} | {scoped} (full: {full}) "
                     f"| {-saving:+.0f}% | {latency_ms:.0f} ms |")
        print(f"{query}\n  full={full} scoped={scoped} ({len(scoped_tools)} tools) search={latency_ms:.0f}ms")

    probe = QUERIES[0]
    fixed, per_tool = schema_costs(
        input_tokens(bedrock, probe, []),
        [input_tokens(bedrock, probe, [tool]) for tool in catalog],
        input_tokens(bedrock, probe, catalog),
    )
    top_k = sum(sent_counts) / len(sent_counts) if sent_counts else MAX_TOOLS
    lines += ["", f"Measured: **{per_tool:.0f} tokens per tool schema** on average, plus a fixed "
                  f"**{fixed:.0f} tokens** whenever any tools are sent.", "",
              "Projection (not measured): tool tokens per request, assuming schemas the size of this repo's "
              f"and {top_k:.1f} tools after search (the average sent above; capped at MAX_TOOLS={MAX_TOOLS}). "
              "The search call itself adds one round trip per question.", "",
              "| Catalog size | All tools in context | Search-scoped |", "|---|---|---|"]
    for size in (50, 200, 1000):
        lines.append(f"| {size} | {fixed + size * per_tool:,.0f} | {fixed + top_k * per_tool:,.0f} |")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "token_compare.md").write_text("\n".join(lines) + "\n")
    print(f"\n~{per_tool:.0f} tokens per tool schema (+{fixed:.0f} fixed). Wrote results/token_compare.md")


if __name__ == "__main__":
    main()
