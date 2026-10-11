// A critical-severity signal triggers expedited reporting, so only a safety lead
// may log one. Forbid wins over permit, so this overrides safety_signal_write.
// Note: the analyst still SEES log_safety_signal in tools/list, because listing
// is a meta action with no input context. This rule is enforced at call time.
forbid (
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"pv___log_safety_signal",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  context.input has severity &&
  context.input.severity == "critical"
}
unless {
  principal.hasTag("persona") &&
  principal.getTag("persona") == "safety_lead"
};
