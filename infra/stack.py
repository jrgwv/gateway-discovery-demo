"""Everything except the AgentCore Gateway and Policy resources.

The Gateway, its targets and the Cedar policies are created by
scripts/setup_gateway.py, because policy creation has to wait for the
Gateway's tool schema to exist, and semantic search can only be chosen when
the Gateway is created. Keeping that ordering in one readable script is
clearer for a how-to than a chain of custom resources.
"""

from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

ROOT = Path(__file__).resolve().parent.parent
PERSONAS = ("clinical_ops", "safety_analyst", "safety_lead")


class DiscoveryDemoStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- The one real system of record: a safety signal log -----------------
        signal_table = dynamodb.Table(
            self, "SignalTable",
            partition_key=dynamodb.Attribute(name="drug", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.DESTROY,  # demo only; RETAIN for anything real
        )

        # --- Tool Lambdas (Gateway targets) ------------------------------------
        tool_code = lambda_.Code.from_asset(str(ROOT / "tools"), exclude=["schemas", "__pycache__", "*.pyc"])

        def tool_fn(logical_id: str, handler: str, env: dict | None = None) -> lambda_.Function:
            return lambda_.Function(
                self, logical_id,
                runtime=lambda_.Runtime.PYTHON_3_14,
                architecture=lambda_.Architecture.ARM_64,
                handler=handler,
                code=tool_code,
                timeout=Duration.seconds(20),
                memory_size=256,
                environment={"LOG_LEVEL": "INFO", **(env or {})},
                log_group=logs.LogGroup(
                    self, f"{logical_id}Logs",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
            )

        openfda_fn = tool_fn("OpenFdaTools", "openfda_handler.lambda_handler")
        ctgov_fn = tool_fn("ClinicalTrialsTools", "ctgov_handler.lambda_handler")
        pv_fn = tool_fn("SignalLogTools", "pv_handler.lambda_handler", {"SIGNAL_TABLE": signal_table.table_name})

        # Least privilege at the data layer too: put and query only, no scan/delete/update.
        pv_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:PutItem", "dynamodb:Query"],
            resources=[signal_table.table_arn],
        ))

        # --- Identity: Cognito users in persona groups ---------------------------
        pretoken_fn = lambda_.Function(
            self, "PersonaClaimFn",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(str(ROOT / "cognito_pretoken")),
            timeout=Duration.seconds(5),
        )
        user_pool = cognito.UserPool(
            self, "Users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(username=True),
            # Access-token customization (V2 trigger) needs the Essentials plan or higher.
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            removal_policy=RemovalPolicy.DESTROY,
        )
        user_pool.add_trigger(
            cognito.UserPoolOperation.PRE_TOKEN_GENERATION_CONFIG, pretoken_fn, cognito.LambdaVersion.V2_0
        )
        for persona in PERSONAS:
            cognito.CfnUserPoolGroup(self, f"Group-{persona}", user_pool_id=user_pool.user_pool_id, group_name=persona)

        app_client = user_pool.add_client(
            "AgentClient",
            generate_secret=False,
            # USER_PASSWORD_AUTH keeps the demo scriptable; use SRP or hosted UI for real users.
            auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
            access_token_validity=Duration.minutes(60),
        )

        # --- Gateway execution role ----------------------------------------------
        account, region = self.account, self.region
        gateway_role = iam.Role(
            self, "GatewayExecutionRole",
            role_name=f"BedrockAgentCoreGatewayDemo-{region}",  # matches the *BedrockAgentCore* PassRole pattern in the docs
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"},
                },
            ),
        )
        for fn in (openfda_fn, ctgov_fn, pv_fn):
            fn.grant_invoke(gateway_role)

        # Required for Policy in AgentCore. IDs aren't known until setup_gateway.py runs;
        # tighten these ARNs to the specific gateway and policy engine afterwards.
        gateway_role.add_to_policy(iam.PolicyStatement(
            sid="PolicyEngineConfiguration",
            actions=["bedrock-agentcore:GetPolicyEngine"],
            resources=[f"arn:aws:bedrock-agentcore:{region}:{account}:policy-engine/*"],
        ))
        gateway_role.add_to_policy(iam.PolicyStatement(
            sid="PolicyEngineAuthorization",
            actions=["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions"],
            resources=[
                f"arn:aws:bedrock-agentcore:{region}:{account}:policy-engine/*",
                f"arn:aws:bedrock-agentcore:{region}:{account}:gateway/*",
            ],
        ))

        # --- Outputs consumed by scripts/setup_gateway.py ----------------------------
        outputs = {
            "UserPoolId": user_pool.user_pool_id,
            "AppClientId": app_client.user_pool_client_id,
            "DiscoveryUrl": f"https://cognito-idp.{region}.amazonaws.com/{user_pool.user_pool_id}/.well-known/openid-configuration",
            "GatewayRoleArn": gateway_role.role_arn,
            "OpenFdaFnArn": openfda_fn.function_arn,
            "ClinicalTrialsFnArn": ctgov_fn.function_arn,
            "SignalLogFnArn": pv_fn.function_arn,
            "SignalTableName": signal_table.table_name,
        }
        for key, value in outputs.items():
            CfnOutput(self, key, value=value)
