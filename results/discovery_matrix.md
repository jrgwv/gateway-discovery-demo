# Discovery matrix

Run 2026-10-11 02:00 UTC, policy mode **ENFORCE**.

## tools/list by persona

| Persona | Visible tools |
|---|---|
| clinical_ops | get_drug_label, search_trials |
| safety_analyst | get_drug_label, list_safety_signals, log_safety_signal, search_adverse_events, search_recalls, search_trials |
| safety_lead | get_drug_label, list_safety_signals, log_safety_signal, search_adverse_events, search_recalls, search_trials |

## Semantic search vs. tools/list

| Persona | Query | Search returned | Hidden by tools/list but returned by search |
|---|---|---|---|
| clinical_ops | how many serious adverse event reports are there for semaglutide | search_adverse_events, list_safety_signals, log_safety_signal, get_drug_label, search_recalls, search_trials | search_adverse_events, list_safety_signals, log_safety_signal, search_recalls |
| clinical_ops | find recruiting phase 3 trials for type 2 diabetes | search_trials, search_recalls, search_adverse_events, get_drug_label, log_safety_signal, list_safety_signals | search_recalls, search_adverse_events, log_safety_signal, list_safety_signals |
| clinical_ops | has metformin been recalled | search_recalls, search_adverse_events, get_drug_label, log_safety_signal, list_safety_signals, search_trials | search_recalls, search_adverse_events, log_safety_signal, list_safety_signals |
| clinical_ops | record a new safety signal for review | log_safety_signal, list_safety_signals, search_adverse_events, search_recalls, get_drug_label, search_trials | log_safety_signal, list_safety_signals, search_adverse_events, search_recalls |
| safety_analyst | how many serious adverse event reports are there for semaglutide | search_adverse_events, list_safety_signals, log_safety_signal, get_drug_label, search_recalls, search_trials | none |
| safety_analyst | find recruiting phase 3 trials for type 2 diabetes | search_trials, search_recalls, search_adverse_events, get_drug_label, log_safety_signal, list_safety_signals | none |
| safety_analyst | has metformin been recalled | search_recalls, search_adverse_events, get_drug_label, log_safety_signal, list_safety_signals, search_trials | none |
| safety_analyst | record a new safety signal for review | log_safety_signal, list_safety_signals, search_adverse_events, search_recalls, get_drug_label, search_trials | none |
| safety_lead | how many serious adverse event reports are there for semaglutide | search_adverse_events, list_safety_signals, log_safety_signal, get_drug_label, search_recalls, search_trials | none |
| safety_lead | find recruiting phase 3 trials for type 2 diabetes | search_trials, search_recalls, search_adverse_events, get_drug_label, log_safety_signal, list_safety_signals | none |
| safety_lead | has metformin been recalled | search_recalls, search_adverse_events, get_drug_label, log_safety_signal, list_safety_signals, search_trials | none |
| safety_lead | record a new safety signal for review | log_safety_signal, list_safety_signals, search_adverse_events, search_recalls, get_drug_label, search_trials | none |

## Call-time decisions

| Persona | Tool | Expected (ENFORCE) | Got |
|---|---|---|---|
| clinical_ops | search_adverse_events () | DENY: not in clinical_ops scope | DENY ({"code": -32002, "message": "Tool Execution Denied: Tool call not allowed due to policy en) |
| clinical_ops | search_trials () | ALLOW | ALLOW |
| safety_analyst | log_safety_signal (critical) | DENY: listed, but forbid on severity=critical | DENY ({"code": -32002, "message": "Tool Execution Denied: Tool call not allowed due to policy en) |
| safety_analyst | log_safety_signal (low) | ALLOW | ALLOW |
| safety_lead | log_safety_signal (critical) | ALLOW | ALLOW |

**Verdict:** Search surfaced 16 tool(s) that tools/list hid. Intersect search results with tools/list in the agent.
