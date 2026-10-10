// Only safety personas can write to the signal log.
permit (
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"pv___log_safety_signal",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("persona") &&
  (principal.getTag("persona") == "safety_analyst" ||
   principal.getTag("persona") == "safety_lead")
};
