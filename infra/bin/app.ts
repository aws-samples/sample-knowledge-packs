#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { AwsSolutionsChecks } from "cdk-nag";
import { KnowledgePackStack } from "../lib/knowledge-pack-stack";
import { GatewayMcpStack } from "../lib/gateway-mcp-stack";
import { GatewayDomainStack } from "../lib/gateway-domain-stack";

const app = new cdk.App();
cdk.Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));

new KnowledgePackStack(app, "KnowledgePackStack", {
  env: {
    region: "eu-west-1",
  },
});

// --- Gateway approach: AgentCore Gateway (noAuth inbound) + Cognito OAuth (outbound) ---
// Single deploy: creates Cognito, Runtime (JWT auth), OAuth credential provider, Gateway, and Target
// Multi-KB: allowlist JSON + default alias come from CDK context:
//   cd infra && npx cdk deploy GatewayMcpStack -c kbAllowlist='{"example":"<KB_ID>"}' -c defaultKb=example
// Or read automatically via Makefile target `make deploy-mcp`
const kbAllowlist = app.node.tryGetContext("kbAllowlist");
const defaultKb = app.node.tryGetContext("defaultKb");
const kbDescriptions = app.node.tryGetContext("kbDescriptions");
if (kbAllowlist && defaultKb) {
  new GatewayMcpStack(app, "GatewayMcpStack", {
    env: { region: "eu-west-1" },
    kbAllowlist,
    defaultKb,
    ...(kbDescriptions ? { kbDescriptions } : {}),
  });
}

// Stack 2: CloudFront + ACM + WAF + Route 53 in us-east-1
// Deploy GatewayMcpStack first, get the GatewayEndpoint output, then deploy this stack:
//   cd infra && npx cdk deploy GatewayDomainStack -c gatewayEndpoint=<value-from-stack1-output>
const gatewayEndpoint = app.node.tryGetContext("gatewayEndpoint");
if (gatewayEndpoint) {
  new GatewayDomainStack(app, "GatewayDomainStack", {
    env: { region: "us-east-1" },
    gatewayEndpoint,
    domainName: app.node.tryGetContext("domainName") ?? process.env.CUSTOM_DOMAIN ?? "knowledge-packs.example.com",
    hostedZoneId: app.node.tryGetContext("hostedZoneId") ?? process.env.HOSTED_ZONE_ID ?? "",
    zoneName: app.node.tryGetContext("zoneName") ?? process.env.ZONE_NAME ?? "example.com",
  });
}
