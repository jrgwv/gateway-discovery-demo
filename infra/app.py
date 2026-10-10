#!/usr/bin/env python3
import aws_cdk as cdk
from stack import DiscoveryDemoStack

app = cdk.App()
DiscoveryDemoStack(
    app,
    "GatewayDiscoveryDemo",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-1",
    ),
)
app.synth()
