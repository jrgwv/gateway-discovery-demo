"""Step one of the post: does discovery respect policy?

For each persona this records:
  * what tools/list returns (documented as policy-filtered)
  * what semantic search returns for the same intents (not documented either way)
  * any tool search surfaced that tools/list hid  <- the "leak" column
  * call-time decisions for three probes, including the listed-but-denied case

Writes results/discovery_matrix.md so the numbers can go straight into the post.

  python demo/discovery_matrix.py              # all probes
  python demo/discovery_matrix.py --no-writes  # skip the probes that write to the signal log
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from gateway_client import SEARCH_TOOL, call_tool, list_tools, search_tools, state

PERSONAS = ("clinical_ops", "safety_analyst", "safety_lead")
QUERIES = (
    "how many serious adverse event reports are there for semaglutide",
    "find recruiting phase 3 trials for type 2 diabetes",
    "has metformin been recalled",
    "record a new safety signal for review",
)
RESULTS = Path(__file__).resolve().parent.parent / "results"


def short(name: str) -> str:
    return name.split("___", 1)[-1]


def verdict_for(mode: str, searches: int, errors: int, leaks: int) -> str:
    """Only claim what the run can support: failed searches and LOG_ONLY runs prove nothing about filtering."""
    if searches and errors == searches:
        return ("Inconclusive: every search call failed, so the search tool itself is blocked or broken for these "
                "personas (see the errors above). Nothing can be said about whether its results are policy-filtered.")
    if leaks:
        text = f"Search surfaced {leaks} tool(s) that tools/list hid. Intersect search results with tools/list in the agent."
    else:
        text = "Search results stayed inside each persona's tools/list."
    if errors:
        text += f" {errors} of {searches} search calls failed and are not covered by this result."
    if mode != "ENFORCE":
        text = f"Inconclusive in {mode}: policy is not being enforced, so this run does not test filtering. " + text
    return text


def probes(write: bool) -> list[tuple[str, str, dict, str]]:
    """(persona, tool, args, what we expect under ENFORCE)."""
    rows = [
        ("clinical_ops", "openfda___search_adverse_events", {"drug_name": "semaglutide", "top_n": 3},
         "DENY: not in clinical_ops scope"),
        ("clinical_ops", "ctgov___search_trials", {"condition": "type 2 diabetes", "page_size": 2},
         "ALLOW"),
    ]
    if write:
        signal = {"drug_name": "demo-drug", "signal_description": "DEMO ONLY: discovery matrix probe",
                  "evidence_refs": ["faers:demo-drug"]}
        rows += [
            ("safety_analyst", "pv___log_safety_signal", {**signal, "severity": "critical"},
             "DENY: listed, but forbid on severity=critical"),
            ("safety_analyst", "pv___log_safety_signal", {**signal, "severity": "low"}, "ALLOW"),
            ("safety_lead", "pv___log_safety_signal", {**signal, "severity": "critical"}, "ALLOW"),
        ]
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-writes", action="store_true")
    args = parser.parse_args()

    mode = state().get("policyMode", "LOG_ONLY")
    lines = [
        "# Discovery matrix",
        "",
        f"Run {datetime.now(UTC):%Y-%m-%d %H:%M} UTC, policy mode **{mode}**.",
        "",
    ]
    if mode != "ENFORCE":
        lines += ["> LOG_ONLY: nothing is enforced, so this run is a smoke test only. Check the policy decision "
                  "logs for what *would* be denied, then re-run under ENFORCE for the real answer.", ""]

    lines += ["## tools/list by persona", "", "| Persona | Visible tools |", "|---|---|"]
    listed: dict[str, set[str]] = {}
    for persona in PERSONAS:
        names = {t["name"] for t in list_tools(persona)}
        listed[persona] = names
        visible = ", ".join(sorted(short(n) for n in names if n != SEARCH_TOOL)) or "(none)"
        lines.append(f"| {persona} | {visible} |")
        print(f"[list] {persona}: {sorted(names)}")

    lines += ["", "## Semantic search vs. tools/list", "",
              "| Persona | Query | Search returned | Hidden by tools/list but returned by search |",
              "|---|---|---|---|"]
    leaks = searches = errors = 0
    for persona in PERSONAS:
        for query in QUERIES:
            result = search_tools(persona, query)
            searches += 1
            if not result["ok"]:
                errors += 1
                lines.append(f"| {persona} | {query} | error: {result['text'][:120]} | n/a |")
                print(f"[search] {persona} | {query} -> ERROR {result['text'][:200]}")
                continue
            leaked = [n for n in result["names"] if n not in listed[persona]]
            leaks += len(leaked)
            lines.append(
                f"| {persona} | {query} | {', '.join(short(n) for n in result['names']) or '(none)'} "
                f"| {', '.join(short(n) for n in leaked) or 'none'} |"
            )
            print(f"[search] {persona} | {query} -> {result['names']} leaked={leaked}")

    lines += ["", "## Call-time decisions", "", "| Persona | Tool | Expected (ENFORCE) | Got |", "|---|---|---|---|"]
    for persona, tool, arguments, expected in probes(write=not args.no_writes):
        response = call_tool(persona, tool, arguments)
        got = "ALLOW" if response["ok"] else f"DENY ({response['text'][:90]})"
        lines.append(f"| {persona} | {short(tool)} ({arguments.get('severity', '')}) | {expected} | {got} |")
        print(f"[call] {persona} {tool} -> {got}")

    verdict = verdict_for(mode, searches, errors, leaks)
    lines += ["", f"**Verdict:** {verdict}", ""]
    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / "discovery_matrix.md"
    out.write_text("\n".join(lines))
    print(f"\n{verdict}\nWrote {out}")


if __name__ == "__main__":
    main()
