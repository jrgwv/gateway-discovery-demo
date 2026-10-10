"""Cognito pre-token-generation trigger (V2) that adds a single `persona` claim.

Cedar policies in AgentCore read JWT claims as string tags on the principal
(principal.getTag("persona")). Cognito's `cognito:groups` claim is an array, so
we flatten group membership into one predictable string claim instead of
depending on how array claims are mapped.
"""

PRECEDENCE = ("safety_lead", "safety_analyst", "clinical_ops")


def lambda_handler(event, _context):
    groups = set(event["request"].get("groupConfiguration", {}).get("groupsToOverride", []) or [])
    persona = next((p for p in PRECEDENCE if p in groups), "none")
    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "accessTokenGeneration": {"claimsToAddOrOverride": {"persona": persona}},
            "idTokenGeneration": {"claimsToAddOrOverride": {"persona": persona}},
        }
    }
    return event
