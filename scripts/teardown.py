"""Delete what setup_gateway.py created, in reverse order. Then run `cdk destroy` in infra/."""

from __future__ import annotations

import json
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / ".demo-state.json"
USERS_FILE = ROOT / ".demo-users.json"
OUTPUTS_FILE = ROOT / "cdk-outputs.json"


def quietly(label: str, fn, **kwargs) -> None:
    try:
        fn(**kwargs)
        print(f"deleted {label}")
    except Exception as err:  # noqa: BLE001 - best-effort cleanup
        print(f"skip {label}: {err}")


def main() -> None:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if not state:
        print("Nothing to tear down.")
        return
    ctl = boto3.client("bedrock-agentcore-control", region_name=state["region"])

    for name, pid in state.get("policies", {}).items():
        quietly(f"policy {name}", ctl.delete_policy, policyEngineId=state["policyEngineId"], policyId=pid)
    for name, tid in state.get("targets", {}).items():
        quietly(f"target {name}", ctl.delete_gateway_target, gatewayIdentifier=state["gatewayId"], targetId=tid)
    if state.get("gatewayId"):
        time.sleep(10)  # targets must finish deleting first
        quietly("gateway", ctl.delete_gateway, gatewayIdentifier=state["gatewayId"])
    if state.get("policyEngineId"):
        time.sleep(10)
        quietly("policy engine", ctl.delete_policy_engine, policyEngineId=state["policyEngineId"])

    if USERS_FILE.exists() and OUTPUTS_FILE.exists():
        pool = json.loads(OUTPUTS_FILE.read_text())["GatewayDiscoveryDemo"]["UserPoolId"]
        cognito = boto3.client("cognito-idp", region_name=state["region"])
        for username in json.loads(USERS_FILE.read_text()):
            quietly(f"user {username}", cognito.admin_delete_user, UserPoolId=pool, Username=username)
        USERS_FILE.unlink()

    STATE_FILE.unlink()
    print("Done. Now run `cdk destroy` in infra/.")


if __name__ == "__main__":
    main()
