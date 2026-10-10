// Clinical operations: trial landscape and label lookups only.
// No adverse-event mining, no recall data, no access to the signal log.
permit (
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"ctgov___search_trials",
    AgentCore::Action::"openfda___get_drug_label"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("persona") &&
  principal.getTag("persona") == "clinical_ops"
};
