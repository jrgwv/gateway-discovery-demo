"""Create the AgentCore Gateway, its targets, the policy engine and Cedar policies.

Order matters, and this script is the clearest place to show it:

  1. Policy engine          (gateway needs its ARN at creation)
  2. Gateway                (semantic search can ONLY be enabled here; policy in LOG_ONLY)
  3. Targets                (Lambda + inline tool schemas)
  4. Cedar policies         (validated against the schema generated from the targets)
  5. Switch to ENFORCE      (after you've watched LOG_ONLY decisions)
  6. Demo users             (one Cognito user per persona)

Usage:
  cd infra && cdk deploy --outputs-file ../cdk-outputs.json && cd ..
  python scripts/setup_gateway.py            # steps 1-4 and 6, leaves policy in LOG_ONLY
  python scripts/setup_gateway.py --enforce  # step 5
"""

from __future__ import annotations

import argparse
import json
import secrets
import string
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_FILE = ROOT / "cdk-outputs.json"
STATE_FILE = ROOT / ".demo-state.json"      # gitignored
USERS_FILE = ROOT / ".demo-users.json"      # gitignored: contains demo passwords
STACK = "GatewayDiscoveryDemo"
GATEWAY_NAME = "discovery-demo-gateway"
ENGINE_NAME = "discovery_demo_engine"
PERSONAS = ("clinical_ops", "safety_analyst", "safety_lead")
TARGETS = {  # target name -> (CDK output holding the Lambda ARN, schema file)
    "openfda": ("OpenFdaFnArn", "openfda.json"),
    "ctgov": ("ClinicalTrialsFnArn", "ctgov.json"),
    "pv": ("SignalLogFnArn", "pv.json"),
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def wait_for(describe, ok: set[str], label: str, timeout: int = 300) -> dict:
    """Poll a Get* call until its status is in `ok`; fail loudly on *FAILED / *UNSUCCESSFUL."""
    deadline = time.monotonic() + timeout
    while True:
        resp = describe()
        status = resp.get("status", "")
        if status in ok:
            print(f"  {label}: {status}")
            return resp
        if "FAILED" in status or "UNSUCCESSFUL" in status:
            sys.exit(f"  {label}: {status} {resp.get('statusReasons') or resp.get('statusReason') or ''}")
        if time.monotonic() > deadline:
            sys.exit(f"  {label}: timed out in status {status}")
        time.sleep(5)


def gateway_config(outputs: dict) -> dict:
    """Arguments shared by CreateGateway and UpdateGateway (UpdateGateway needs them all again)."""
    return {
        "name": GATEWAY_NAME,
        "roleArn": outputs["GatewayRoleArn"],
        "protocolType": "MCP",
        "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": {
            "customJWTAuthorizer": {
                "discoveryUrl": outputs["DiscoveryUrl"],
                "allowedClients": [outputs["AppClientId"]],
            }
        },
        "protocolConfiguration": {"mcp": {"searchType": "SEMANTIC"}},
        "exceptionLevel": "DEBUG",  # readable errors while you build; remove for production
    }


def create_engine(ctl, state: dict) -> None:
    if state.get("policyEngineId"):
        return
    print("1. Creating policy engine")
    resp = ctl.create_policy_engine(name=ENGINE_NAME, description="Persona-scoped tool access for the discovery demo")
    state.update(policyEngineId=resp["policyEngineId"], policyEngineArn=resp["policyEngineArn"])
    save_state(state)
    wait_for(lambda: ctl.get_policy_engine(policyEngineId=state["policyEngineId"]), {"ACTIVE", "READY"}, "policy engine")


def create_gateway(ctl, state: dict, outputs: dict) -> None:
    if state.get("gatewayId"):
        return
    print("2. Creating gateway (semantic search on, policy in LOG_ONLY)")
    resp = ctl.create_gateway(
        **gateway_config(outputs),
        description="Strands dynamic tool discovery with least-privilege enterprise tools",
        policyEngineConfiguration={"arn": state["policyEngineArn"], "mode": "LOG_ONLY"},
    )
    state.update(gatewayId=resp["gatewayId"], gatewayArn=resp["gatewayArn"], gatewayUrl=resp["gatewayUrl"])
    save_state(state)
    wait_for(lambda: ctl.get_gateway(gatewayIdentifier=state["gatewayId"]), {"READY"}, "gateway")


def create_targets(ctl, state: dict, outputs: dict) -> None:
    print("3. Creating targets")
    state.setdefault("targets", {})
    for name, (output_key, schema_file) in TARGETS.items():
        if name in state["targets"]:
            continue
        tools = json.loads((ROOT / "tools" / "schemas" / schema_file).read_text())
        resp = ctl.create_gateway_target(
            gatewayIdentifier=state["gatewayId"],
            name=name,
            description=f"{name} tools",
            targetConfiguration={
                "mcp": {"lambda": {"lambdaArn": outputs[output_key], "toolSchema": {"inlinePayload": tools}}}
            },
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        state["targets"][name] = resp["targetId"]
        save_state(state)
        wait_for(
            lambda tid=resp["targetId"]: ctl.get_gateway_target(gatewayIdentifier=state["gatewayId"], targetId=tid),
            {"READY"}, f"target {name}",
        )


def create_policies(ctl, state: dict) -> None:
    print("4. Creating Cedar policies")
    state.setdefault("policies", {})
    for path in sorted((ROOT / "policies").glob("*.cedar")):
        name = path.stem
        if name in state["policies"]:
            continue
        statement = path.read_text().replace("{{GATEWAY_ARN}}", state["gatewayArn"])
        resp = ctl.create_policy(
            policyEngineId=state["policyEngineId"],
            name=name,
            description=f"Discovery demo: {name}",
            validationMode="FAIL_ON_ANY_FINDINGS",
            definition={"cedar": {"statement": statement}},
        )
        state["policies"][name] = resp["policyId"]
        save_state(state)
        wait_for(
            lambda pid=resp["policyId"]: ctl.get_policy(policyEngineId=state["policyEngineId"], policyId=pid),
            {"ACTIVE", "READY"}, f"policy {name}",
        )


def set_mode(ctl, state: dict, outputs: dict, mode: str) -> None:
    print(f"5. Switching policy engine association to {mode}")
    ctl.update_gateway(
        gatewayIdentifier=state["gatewayId"],
        **gateway_config(outputs),
        policyEngineConfiguration={"arn": state["policyEngineArn"], "mode": mode},
    )
    wait_for(lambda: ctl.get_gateway(gatewayIdentifier=state["gatewayId"]), {"READY"}, "gateway")
    state["policyMode"] = mode
    save_state(state)


def create_users(cognito, outputs: dict) -> None:
    users = load_json(USERS_FILE)
    alphabet = string.ascii_letters + string.digits
    for persona in PERSONAS:
        username = f"demo-{persona.replace('_', '-')}"
        if username in users:
            continue
        password = "".join(secrets.choice(alphabet) for _ in range(20)) + "!a1A"
        pool = outputs["UserPoolId"]
        cognito.admin_create_user(UserPoolId=pool, Username=username, MessageAction="SUPPRESS")
        cognito.admin_set_user_password(UserPoolId=pool, Username=username, Password=password, Permanent=True)
        cognito.admin_add_user_to_group(UserPoolId=pool, Username=username, GroupName=persona)
        users[username] = {"persona": persona, "password": password}
        USERS_FILE.write_text(json.dumps(users, indent=2))
        print(f"  user {username} -> {persona}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enforce", action="store_true", help="switch the policy engine to ENFORCE")
    parser.add_argument("--log-only", action="store_true", help="switch the policy engine back to LOG_ONLY")
    args = parser.parse_args()

    outputs = load_json(OUTPUTS_FILE).get(STACK)
    if not outputs:
        sys.exit("Run `cdk deploy --outputs-file ../cdk-outputs.json` in infra/ first.")
    region = outputs["DiscoveryUrl"].split(".")[1]
    ctl = boto3.client("bedrock-agentcore-control", region_name=region)
    state = load_json(STATE_FILE) | {"region": region, "appClientId": outputs["AppClientId"]}

    if args.enforce or args.log_only:
        set_mode(ctl, state, outputs, "ENFORCE" if args.enforce else "LOG_ONLY")
        return

    create_engine(ctl, state)
    create_gateway(ctl, state, outputs)
    create_targets(ctl, state, outputs)
    create_policies(ctl, state)
    print("6. Creating demo users")
    create_users(boto3.client("cognito-idp", region_name=region), outputs)
    state.setdefault("policyMode", "LOG_ONLY")
    save_state(state)
    print(f"\nGateway URL: {state['gatewayUrl']}")
    print("Policy is in LOG_ONLY. Run the discovery matrix, check the logs, then: python scripts/setup_gateway.py --enforce")


if __name__ == "__main__":
    main()
