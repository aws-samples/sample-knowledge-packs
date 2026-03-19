import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as cr from "aws-cdk-lib/custom-resources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";
import * as path from "path";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";

/**
 * Props for the GatewayMcpStack.
 *
 * Multi-KB: accepts a JSON-encoded allowlist mapping aliases to KB IDs,
 * plus a default alias. Replaces the old single `knowledgeBaseId` prop.
 */
export interface GatewayMcpStackProps extends cdk.StackProps {
  /** JSON-encoded KB allowlist: {"alias": "KB_ID", ...} */
  readonly kbAllowlist: string;
  /** Default KB alias (must be a key in kbAllowlist) */
  readonly defaultKb: string;
  /** Optional JSON-encoded KB descriptions: {"alias": "topics covered"} */
  readonly kbDescriptions?: string;
  /** Cognito domain prefix — must be globally unique (S-8) */
  readonly cognitoDomainPrefix?: string;
}

/**
 * CDK Stack for AgentCore Runtime + Gateway in eu-west-1.
 *
 * Architecture:
 *   MCP Client → Gateway (noAuth inbound) → [OAuth via Cognito] → Runtime (JWT auth) → KB
 *
 * Creates:
 * 1. Cognito User Pool + resource server + M2M app client
 * 2. AgentCore Runtime with JWT auth (Cognito)
 * 3. Gateway with noAuth inbound
 * 4. Gateway MCP Target with OAuth credentials
 */
export class GatewayMcpStack extends cdk.Stack {
  public readonly gatewayEndpoint: string;

  constructor(scope: Construct, id: string, props: GatewayMcpStackProps) {
    super(scope, id, props);

    cdk.Tags.of(this).add("project", "knowledge-pack");

    const region = this.region;
    const accountId = this.account;

    // Parse the allowlist JSON at synth time to build KB ARN list for IAM policy
    const allowlist: Record<string, string> = JSON.parse(props.kbAllowlist);
    const kbArns = Object.values(allowlist).map(
      (kbId) => `arn:aws:bedrock:${region}:${accountId}:knowledge-base/${kbId}`
    );

    // ================================================================
    // 1. Cognito User Pool for Gateway → Runtime OAuth
    // ================================================================

    const userPool = new cognito.UserPool(this, "McpUserPool", {
      userPoolName: "docs-kb-mcp-pool",
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      selfSignUpEnabled: false,
      passwordPolicy: {
        minLength: 12,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      mfa: cognito.Mfa.OFF, // M2M only — no human users
      advancedSecurityMode: cognito.AdvancedSecurityMode.ENFORCED,
    });

    const cognitoDomainPrefix = props.cognitoDomainPrefix ?? "docs-kb-mcp-gw";

    // Cognito domain — required for OAuth token endpoint
    userPool.addDomain("McpDomain", {
      cognitoDomain: { domainPrefix: cognitoDomainPrefix },
    });

    // Resource server — defines the scope for M2M access
    const resourceServer = userPool.addResourceServer("McpResourceServer", {
      identifier: "mcp-runtime",
      scopes: [
        { scopeName: "invoke", scopeDescription: "Invoke the MCP Runtime" },
      ],
    });

    // M2M app client — client_credentials grant for Gateway → Runtime
    const appClient = userPool.addClient("McpM2MClient", {
      userPoolClientName: "gateway-m2m",
      generateSecret: true,
      oAuth: {
        flows: { clientCredentials: true },
        scopes: [
          cognito.OAuthScope.custom("mcp-runtime/invoke"),
        ],
      },
    });
    // Ensure resource server is created before the client (scope dependency)
    appClient.node.addDependency(resourceServer);

    // Discovery URL for JWT validation
    const discoveryUrl = `https://cognito-idp.${region}.amazonaws.com/${userPool.userPoolId}/.well-known/openid-configuration`;

    // ================================================================
    // 2. AgentCore Runtime with JWT auth
    // ================================================================

    const runtime = new agentcore.Runtime(this, "McpRuntime", {
      runtimeName: "docsKbMcp",
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromAsset("..", {
        exclude: ["infra", ".venv", ".git", ".kiro", "data", "research", "node_modules"],
      }),
      protocolConfiguration: agentcore.ProtocolType.MCP,
      environmentVariables: {
        KB_ALLOWLIST: props.kbAllowlist,
        DEFAULT_KB: props.defaultKb,
        ...(props.kbDescriptions ? { KB_DESCRIPTIONS: props.kbDescriptions } : {}),
        MCP_TRANSPORT: "streamable-http",
      },
      // JWT auth — Runtime validates tokens from Cognito
      authorizerConfiguration: agentcore.RuntimeAuthorizerConfiguration.usingJWT(
        discoveryUrl,
        [appClient.userPoolClientId], // allowedClients
      ),
      description: "Docs KB MCP server (JWT auth via Cognito)",
    });

    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:Retrieve"],
        resources: kbArns,
      })
    );

    // ================================================================
    // 3. Gateway with noAuth inbound
    // ================================================================

    const gatewayRole = new iam.Role(this, "GatewayRole", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
    });

    // Gateway needs InvokeAgentRuntime permission
    runtime.grantInvoke(gatewayRole);

    // Gateway needs OAuth token permissions (S-1: scoped to account)
    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:GetWorkloadAccessToken",
          "bedrock-agentcore:GetResourceOauth2Token",
        ],
        resources: [`arn:aws:bedrock-agentcore:${region}:${accountId}:*`],
      })
    );
    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["secretsmanager:GetSecretValue"],
        resources: [`arn:aws:secretsmanager:${region}:${accountId}:secret:bedrock-agentcore*`],
      })
    );

    const cfnGateway = new cdk.aws_bedrockagentcore.CfnGateway(this, "McpGateway", {
      name: "docs-kb-mcp-gw",
      authorizerType: "NONE",
      protocolType: "MCP",
      roleArn: gatewayRole.roleArn,
      protocolConfiguration: {
        mcp: {
          instructions: "Documentation knowledge base MCP server",
          supportedVersions: ["2025-03-26"],
        },
      },
      description: "Public MCP gateway for docs KB - noAuth inbound",
    });

    // ================================================================
    // 4. OAuth Credential Provider (Custom Resource Lambda)
    // ================================================================
    // AgentCore Identity has no CloudFormation resource for OAuth credential providers.
    // We use a Lambda-backed Custom Resource that calls CreateOauth2CredentialProvider.

    // First, get the Cognito client secret via AwsCustomResource (this one works fine)
    const describeClient = new cr.AwsCustomResource(this, "DescribeCognitoClient", {
      onCreate: {
        service: "CognitoIdentityServiceProvider",
        action: "describeUserPoolClient",
        parameters: {
          UserPoolId: userPool.userPoolId,
          ClientId: appClient.userPoolClientId,
        },
        physicalResourceId: cr.PhysicalResourceId.of("cognito-client-secret"),
      },
      policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
        resources: [userPool.userPoolArn],
      }),
    });
    describeClient.node.addDependency(appClient);

    const clientSecret = describeClient.getResponseField(
      "UserPoolClient.ClientSecret"
    );

    // Lambda for creating the OAuth credential provider
    // Uses CDK Provider framework which handles cfn-response protocol automatically
    const oauthProviderOnEventFn = new lambda.Function(this, "OAuthProviderFn", {
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: "oauth-provider.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "custom-resources")),
      timeout: cdk.Duration.seconds(60),
      description: "Creates AgentCore OAuth credential provider for Gateway",
    });

    oauthProviderOnEventFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:CreateOauth2CredentialProvider",
          "bedrock-agentcore:DeleteOauth2CredentialProvider",
          "bedrock-agentcore:GetOauth2CredentialProvider",
          "bedrock-agentcore:CreateTokenVault",
          "bedrock-agentcore:GetTokenVault",
        ],
        // Scoped to this account's AgentCore resources (M10: least-privilege)
        resources: [`arn:aws:bedrock-agentcore:${region}:${accountId}:*`],
      })
    );
    oauthProviderOnEventFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["secretsmanager:CreateSecret", "secretsmanager:DeleteSecret", "secretsmanager:PutSecretValue"],
        // Scoped to secrets created by AgentCore Identity (M10: least-privilege)
        resources: [`arn:aws:secretsmanager:${region}:${accountId}:secret:bedrock-agentcore*`],
      })
    );

    // Provider wraps the Lambda and handles CloudFormation callback protocol
    // totalTimeout limits how long CloudFormation waits — fail fast, don't hang for 1 hour
    const oauthProviderProvider = new cr.Provider(this, "OAuthProviderCR", {
      onEventHandler: oauthProviderOnEventFn,
    });

    const oauthProvider = new cdk.CustomResource(this, "OAuthProvider", {
      serviceToken: oauthProviderProvider.serviceToken,
      properties: {
        ProviderName: "docs-kb-mcp-cognito",
        ClientId: appClient.userPoolClientId,
        ClientSecret: clientSecret,
        DiscoveryUrl: discoveryUrl,
      },
    });
    oauthProvider.node.addDependency(describeClient);

    const oauthProviderArn = oauthProvider.getAttString("CredentialProviderArn");
    const oauthSecretArn = oauthProvider.getAttString("SecretArn");

    // ================================================================
    // 5. Gateway MCP Target with OAuth
    // ================================================================

    // Build the Runtime invocation URL with URL-encoded ARN
    const arnParts = cdk.Fn.split(":", runtime.agentRuntimeArn);
    const arnPartition = cdk.Fn.select(1, arnParts);
    const arnService = cdk.Fn.select(2, arnParts);
    const arnRegion = cdk.Fn.select(3, arnParts);
    const arnAccount = cdk.Fn.select(4, arnParts);
    const arnResource = cdk.Fn.select(5, arnParts);
    const resourceParts = cdk.Fn.split("/", arnResource);
    const resourceType = cdk.Fn.select(0, resourceParts);
    const resourceName = cdk.Fn.select(1, resourceParts);

    const encodedArn = cdk.Fn.join("", [
      "arn%3A", arnPartition,
      "%3A", arnService,
      "%3A", arnRegion,
      "%3A", arnAccount,
      "%3A", resourceType,
      "%2F", resourceName,
    ]);

    const runtimeInvocationUrl = cdk.Fn.join("", [
      "https://bedrock-agentcore.", region, ".amazonaws.com/runtimes/",
      encodedArn,
      "/invocations",
    ]);

    // Gateway Target creation was previously conditional (skipTarget workaround)
    // but the real issue was FastMCP 3.x breaking change (stateless_http moved to run()).
    // Now that the server code is fixed, Target creation works in CloudFormation.
    const cfnGatewayTarget = new cdk.aws_bedrockagentcore.CfnGatewayTarget(
      this, "McpTarget", {
        gatewayIdentifier: cfnGateway.attrGatewayIdentifier,
        name: "docs-kb-mcp-target",
        targetConfiguration: {
          mcp: {
            mcpServer: {
              endpoint: runtimeInvocationUrl,
            },
          },
        },
        credentialProviderConfigurations: [
          {
            credentialProviderType: "OAUTH",
            credentialProvider: {
              oauthCredentialProvider: {
                providerArn: oauthProviderArn,
                scopes: ["mcp-runtime/invoke"],
                grantType: "CLIENT_CREDENTIALS",
              },
            },
          },
        ],
        description: "Docs KB MCP server target (OAuth via Cognito)",
      }
    );
    cfnGatewayTarget.addDependency(cfnGateway);
    cfnGatewayTarget.node.addDependency(oauthProvider);

    new cdk.CfnOutput(this, "GatewayTargetId", {
      value: cfnGatewayTarget.attrTargetId,
      description: "Gateway Target ID (needed for manual deletion if stack delete fails)",
    });

    // Gateway endpoint
    this.gatewayEndpoint = `${cfnGateway.attrGatewayIdentifier}.gateway.bedrock-agentcore.${region}.amazonaws.com`;

    // ================================================================
    // Stack Outputs
    // ================================================================

    new cdk.CfnOutput(this, "RuntimeArn", {
      value: runtime.agentRuntimeArn,
    });
    new cdk.CfnOutput(this, "GatewayEndpoint", {
      value: this.gatewayEndpoint,
    });
    new cdk.CfnOutput(this, "GatewayId", {
      value: cfnGateway.attrGatewayIdentifier,
    });
    new cdk.CfnOutput(this, "OAuthProviderArn", {
      value: oauthProviderArn,
      description: "OAuth credential provider ARN for Gateway Target creation",
    });
    new cdk.CfnOutput(this, "CognitoUserPoolId", {
      value: userPool.userPoolId,
    });
    new cdk.CfnOutput(this, "CognitoClientId", {
      value: appClient.userPoolClientId,
    });
    new cdk.CfnOutput(this, "CognitoDiscoveryUrl", {
      value: discoveryUrl,
    });
    new cdk.CfnOutput(this, "CognitoTokenUrl", {
      value: `https://${cognitoDomainPrefix}.auth.${region}.amazoncognito.com/oauth2/token`,
    });

    // ================================================================
    // cdk-nag suppressions
    // ================================================================

    // Cognito: M2M only (client_credentials) — no human users, MFA not applicable
    NagSuppressions.addResourceSuppressions(userPool, [
      { id: "AwsSolutions-COG2", reason: "M2M-only user pool (client_credentials grant) — no human users to apply MFA to" },
    ]);

    // CDK custom resource Lambdas — runtime and managed policy are CDK-controlled
    NagSuppressions.addStackSuppressions(this, [
      { id: "AwsSolutions-L1", reason: "CDK custom resource Lambdas use CDK-managed runtime — not user-controllable" },
      { id: "AwsSolutions-IAM4", appliesTo: ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"], reason: "CDK custom resource and Provider Lambdas require AWSLambdaBasicExecutionRole" },
    ]);

    // AgentCore Runtime execution role — wildcards generated by L2 construct for CloudWatch Logs and workload identity
    NagSuppressions.addResourceSuppressions(runtime, [
      { id: "AwsSolutions-IAM5", reason: "AgentCore Runtime L2 construct generates CloudWatch Logs and workload identity wildcards — not user-controllable", appliesTo: [
        `Resource::arn:<AWS::Partition>:logs:${region}:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*`,
        `Resource::arn:<AWS::Partition>:logs:${region}:<AWS::AccountId>:log-group:*`,
        `Resource::arn:<AWS::Partition>:logs:${region}:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*`,
        `Resource::arn:<AWS::Partition>:bedrock-agentcore:${region}:<AWS::AccountId>:workload-identity-directory/default/workload-identity/*`,
        "Resource::*",
      ]},
    ], true);

    // Scoped wildcards — all restricted to this account's AgentCore and Secrets Manager resources
    NagSuppressions.addResourceSuppressions(gatewayRole, [
      { id: "AwsSolutions-IAM5", reason: "Gateway role wildcards scoped to account-level AgentCore resources — required for OAuth token and Runtime invocation", appliesTo: [`Resource::arn:aws:bedrock-agentcore:${region}:<AWS::AccountId>:*`, `Resource::arn:aws:secretsmanager:${region}:<AWS::AccountId>:secret:bedrock-agentcore*`, `Resource::<McpRuntimeC9F015E5.AgentRuntimeArn>/*`] },
    ], true);

    NagSuppressions.addResourceSuppressions(oauthProviderOnEventFn, [
      { id: "AwsSolutions-IAM5", reason: "OAuth provider Lambda wildcards scoped to account-level AgentCore and bedrock-agentcore secrets", appliesTo: [`Resource::arn:aws:bedrock-agentcore:${region}:<AWS::AccountId>:*`, `Resource::arn:aws:secretsmanager:${region}:<AWS::AccountId>:secret:bedrock-agentcore*`] },
    ], true);

    NagSuppressions.addResourceSuppressions(oauthProviderProvider, [
      { id: "AwsSolutions-IAM5", reason: "CDK Provider framework invokes onEvent Lambda — wildcard on Lambda ARN is CDK-generated", appliesTo: [`Resource::<OAuthProviderFnE0F86690.Arn>:*`] },
    ], true);
  }
}
