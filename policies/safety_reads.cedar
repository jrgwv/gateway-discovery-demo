// Safety personas can use every read tool, including the signal log history.
permit (
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"openfda___search_adverse_events",
    AgentCore::Action::"openfda___search_recalls",
    AgentCore::Action::"openfda___get_drug_label",
    AgentCore::Action::"ctgov___search_trials",
    AgentCore::Action::"pv___list_safety_signals"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("persona") &&
  (principal.getTag("persona") == "safety_analyst" ||
   principal.getTag("persona") == "safety_lead")
};
