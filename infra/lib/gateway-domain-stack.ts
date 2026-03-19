import * as cdk from "aws-cdk-lib";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as route53Targets from "aws-cdk-lib/aws-route53-targets";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";

/**
 * Props for the GatewayDomainStack.
 */
export interface GatewayDomainStackProps extends cdk.StackProps {
  /**
   * The Gateway endpoint domain from GatewayMcpStack.
   * Format: {gateway-id}.gateway.bedrock-agentcore.{region}.amazonaws.com
   *
   * Deploy GatewayMcpStack first, get this value from its output,
   * then pass it here. This avoids cross-region CDK reference complexity.
   */
  readonly gatewayEndpoint: string;
  /** Custom domain name for the MCP endpoint (S-3) */
  readonly domainName: string;
  /** Route 53 hosted zone ID for the domain (S-3) */
  readonly hostedZoneId: string;
  /** Route 53 zone name (S-3) */
  readonly zoneName: string;
}

/**
 * CDK Stack for CloudFront custom domain infrastructure in us-east-1.
 *
 * Architecture: MCP Client → Route 53 → CloudFront (WAF) → AgentCore Gateway
 *
 * Deployed in us-east-1 because CloudFront, ACM, and WAF (CLOUDFRONT scope)
 * all require us-east-1. The CloudFront origin points to the Gateway in eu-west-1.
 */
export class GatewayDomainStack extends cdk.Stack {
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: GatewayDomainStackProps) {
    super(scope, id, props);

    const domainName = props.domainName;
    const hostedZoneId = props.hostedZoneId;

    // Tag all resources for cost tracking
    cdk.Tags.of(this).add("project", "knowledge-pack");

    // --- Route 53 Hosted Zone (lookup) ---
    const hostedZone = route53.HostedZone.fromHostedZoneAttributes(
      this,
      "HostedZone",
      {
        hostedZoneId,
        zoneName: props.zoneName,
      }
    );

    // --- ACM Certificate ---
    // Must be in us-east-1 for CloudFront. This stack deploys in us-east-1.
    const certificate = new acm.Certificate(this, "Certificate", {
      domainName,
      validation: acm.CertificateValidation.fromDns(hostedZone),
    });

    // --- WAF WebACL ---
    // IP-based rate limiting: 100 requests per 5 minutes per IP.
    // CLOUDFRONT scope (us-east-1) to attach to CloudFront distribution.
    // Defense-in-depth — complements Gateway's built-in rate controls.
    const webAcl = new wafv2.CfnWebACL(this, "WebAcl", {
      defaultAction: { allow: {} },
      scope: "CLOUDFRONT",
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: "DocsKbGatewayWaf",
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          name: "RateLimitPerIP",
          priority: 1,
          action: { block: {} },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: "DocsKbGatewayRateLimit",
            sampledRequestsEnabled: true,
          },
          statement: {
            rateBasedStatement: {
              limit: 100,
              evaluationWindowSec: 300,
              aggregateKeyType: "IP",
            },
          },
        },
      ],
    });

    // --- CloudFront Access Logs Bucket ---
    const accessLogsBucket = new s3.Bucket(this, "AccessLogsBucket", {
      objectOwnership: s3.ObjectOwnership.OBJECT_WRITER,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      enforceSSL: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // --- CloudFront Distribution ---
    // Reverse proxy to AgentCore Gateway in eu-west-1.
    // Gateway handles outbound auth to Runtime internally — no signing needed.
    this.distribution = new cloudfront.Distribution(this, "Distribution", {
      domainNames: [domainName],
      certificate,
      webAclId: webAcl.attrArn,
      logBucket: accessLogsBucket,
      logFilePrefix: "cloudfront/",
      defaultBehavior: {
        origin: new origins.HttpOrigin(props.gatewayEndpoint, {
          protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
          // Gateway may take time to process MCP tool calls
          readTimeout: cdk.Duration.seconds(60),
          // M11: Custom origin header — verified by the MCP server to ensure
          // requests came through CloudFront, not directly to the Gateway URL.
          // This is defense-in-depth; the value is not a secret (it's in the
          // CFN template), but it raises the bar vs casual direct access.
          customHeaders: {
            "X-Origin-Verify": "knowledge-pack-cloudfront",
          },
        }),
        // Caching MUST be disabled for dynamic MCP interactions
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        // Forward most headers but NOT Host — Gateway needs its own hostname
        // to identify the gateway. ALL_VIEWER forwards the CloudFront domain as Host,
        // which causes "Invalid GatewayId". Use ALL_VIEWER_EXCEPT_HOST_HEADER instead.
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        // POST is required for MCP Streamable HTTP
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        viewerProtocolPolicy:
          cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      // Minimal price class — no need for global edge locations
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
    });

    // --- Route 53 A Record (alias) ---
    // Maps the custom domain to the CloudFront distribution.
    new route53.ARecord(this, "AliasRecord", {
      zone: hostedZone,
      recordName: "knowledge-packs",
      target: route53.RecordTarget.fromAlias(
        new route53Targets.CloudFrontTarget(this.distribution)
      ),
    });

    // --- Stack Outputs ---
    new cdk.CfnOutput(this, "McpEndpointUrl", {
      value: `https://${domainName}/mcp`,
      description: "The MCP endpoint URL for IDE configuration",
    });

    new cdk.CfnOutput(this, "CloudFrontDistributionId", {
      value: this.distribution.distributionId,
      description: "CloudFront distribution ID (for cache invalidation)",
    });

    new cdk.CfnOutput(this, "CloudFrontDomainName", {
      value: this.distribution.distributionDomainName,
      description: "CloudFront distribution domain name (d*.cloudfront.net)",
    });

    // --- cdk-nag suppressions ---
    NagSuppressions.addResourceSuppressions(this.distribution, [
      { id: "AwsSolutions-CFR1", reason: "Public demo MCP endpoint — geo restrictions not required" },
    ]);
    NagSuppressions.addResourceSuppressions(accessLogsBucket, [
      { id: "AwsSolutions-S1", reason: "This IS the access logs bucket — no recursive logging needed" },
    ]);
  }
}
