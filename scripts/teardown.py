"""Delete what setup_gateway.py created, in reverse order. Then run `cdk destroy` in infra/.

Deletes are asynchronous, so each one is polled until the resource is gone. If
anything can't be deleted, .demo-state.json is kept so you can re-run this.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / ".demo-state.json"
USERS_FILE = ROOT / ".demo-users.json"
OUTPUTS_FILE = ROOT / "cdk-outputs.json"
GONE = {"ResourceNotFoundException", "UserNotFoundException"}


def is_gone(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code") in GONE


def delete(label: str, delete_fn, get_fn=None, timeout: int = 180) -> bool:
    """Delete and wait until a Get* call says the resource no longer exists. Returns True when it's gone."""
    try:
        delete_fn()
    except ClientError as err:
        if not is_gone(err):
            print(f"FAILED {label}: {err}")
            return False
    deadline = time.monotonic() + timeout
    while get_fn is not None:
        try:
            status = get_fn().get("status", "")
        except ClientError as err:
            if is_gone(err):
                break
            print(f"FAILED {label}: {err}")
            return False
        if "FAILED" in status or "UNSUCCESSFUL" in status or time.monotonic() > deadline:
            print(f"FAILED {label}: still present in status {status}")
            return False
        time.sleep(5)
    print(f"deleted {label}")
    return True


def main() -> None:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if not state:
        print("Nothing to tear down.")
        return
    ctl = boto3.client("bedrock-agentcore-control", region_name=state["region"])
    engine, gateway = state.get("policyEngineId"), state.get("gatewayId")

    # Each step only runs once everything that depends on it is gone.
    for name, pid in list(state.get("policies", {}).items()):
        if delete(f"policy {name}",
                  lambda pid=pid: ctl.delete_policy(policyEngineId=engine, policyId=pid),
                  lambda pid=pid: ctl.get_policy(policyEngineId=engine, policyId=pid)):
            del state["policies"][name]
    for name, tid in list(state.get("targets", {}).items()):
        if delete(f"target {name}",
                  lambda tid=tid: ctl.delete_gateway_target(gatewayIdentifier=gateway, targetId=tid),
                  lambda tid=tid: ctl.get_gateway_target(gatewayIdentifier=gateway, targetId=tid)):
            del state["targets"][name]
    if gateway and not state.get("targets"):
        if delete("gateway", lambda: ctl.delete_gateway(gatewayIdentifier=gateway),
                  lambda: ctl.get_gateway(gatewayIdentifier=gateway)):
            for key in ("gatewayId", "gatewayArn", "gatewayUrl"):
                state.pop(key, None)
    if engine and not state.get("policies") and not state.get("gatewayId"):
        if delete("policy engine", lambda: ctl.delete_policy_engine(policyEngineId=engine),
                  lambda: ctl.get_policy_engine(policyEngineId=engine)):
            for key in ("policyEngineId", "policyEngineArn"):
                state.pop(key, None)

    users_left = False
    if USERS_FILE.exists() and OUTPUTS_FILE.exists():
        pool = json.loads(OUTPUTS_FILE.read_text())["GatewayDiscoveryDemo"]["UserPoolId"]
        cognito = boto3.client("cognito-idp", region_name=state["region"])
        users = json.loads(USERS_FILE.read_text())
        for username in list(users):
            if delete(f"user {username}", lambda u=username: cognito.admin_delete_user(UserPoolId=pool, Username=u)):
                del users[username]
        users_left = bool(users)
        if users_left:
            USERS_FILE.write_text(json.dumps(users, indent=2))
        else:
            USERS_FILE.unlink()

    leftovers = [k for k in ("policies", "targets", "gatewayId", "policyEngineId") if state.get(k)]
    if leftovers or users_left:
        STATE_FILE.write_text(json.dumps(state, indent=2))
        sys.exit(f"Not everything was deleted ({', '.join(leftovers + ['users'] * users_left)}). "
                 "State kept; fix the errors above and re-run before `cdk destroy`.")
    STATE_FILE.unlink()
    print("Done. Now run `cdk destroy` in infra/.")


if __name__ == "__main__":
    main()
