import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";

/**
 * CDK Stack for Knowledge Pack infrastructure.
 *
 * Provisions all AWS resources for the Bedrock KB:
 * - S3 bucket for documentation data source
 * - S3 Vectors (VectorBucket + Index) for embedding storage
 * - Bedrock Knowledge Base with Titan Embed Text v2
 * - S3 data source connector for documentation files
 */
export class KnowledgePackStack extends cdk.Stack {
  /** S3 bucket storing documentation markdown files */
  public readonly dataBucket: s3.Bucket;
  /** S3 Vectors Index CfnResource (use getAtt('IndexArn') for the ARN) */
  public readonly vectorIndex: cdk.CfnResource;
  /** IAM role for Bedrock with s3vectors and s3 permissions */
  public readonly bedrockRole: iam.Role;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Tag all resources for cost tracking (Requirement 1.4)
    cdk.Tags.of(this).add("project", "knowledge-pack");

    // --- S3 access logging bucket ---
    const accessLogsBucket = new s3.Bucket(this, "AccessLogsBucket", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: [{ expiration: cdk.Duration.days(90) }],
    });
    NagSuppressions.addResourceSuppressions(accessLogsBucket, [
      { id: "AwsSolutions-S1", reason: "This IS the access logs bucket — no recursive logging needed." },
    ]);

    // --- S3 data bucket (Requirement 1.5, 2.1) ---
    this.dataBucket = new s3.Bucket(this, "DataBucket", {
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      // Block all public access — only IAM principals with explicit permissions
      // can read/write. Defense-in-depth for data integrity (M8).
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      serverAccessLogsBucket: accessLogsBucket,
      serverAccessLogsPrefix: "data-bucket/",
    });

    // --- S3 Vectors VectorBucket (Requirement 1.3) ---
    const vectorBucket = new cdk.CfnResource(this, "VectorBucket", {
      type: "AWS::S3Vectors::VectorBucket",
      properties: {
        VectorBucketName: "knowledge-pack-vectors",
      },
    });

    // --- S3 Vectors Index (Requirement 1.3) ---
    // VectorBucket Ref returns the ARN, so we use VectorBucketArn (not VectorBucketName)
    // to reference the parent bucket. VectorBucketName has a 63-char limit that the ARN exceeds.
    this.vectorIndex = new cdk.CfnResource(this, "VectorIndex", {
      type: "AWS::S3Vectors::Index",
      properties: {
        VectorBucketArn: vectorBucket.ref,
        IndexName: "example-docs-index",
        Dimension: 1024,
        DistanceMetric: "cosine",
        DataType: "float32",
        // Bedrock stores chunk text as AMAZON_BEDROCK_TEXT metadata on each vector.
        // By default all metadata keys are filterable, which has a 2 KB limit per vector.
        // Chunks larger than ~2 KB fail with "Filterable metadata must have at most 2048 bytes".
        // Marking these as non-filterable moves them under the 40 KB total metadata limit instead.
        MetadataConfiguration: {
          NonFilterableMetadataKeys: [
            "AMAZON_BEDROCK_TEXT",
            "AMAZON_BEDROCK_METADATA",
          ],
        },
      },
    });
    this.vectorIndex.addDependency(vectorBucket);

    // --- IAM role for Bedrock (Requirement 1.2) ---
    this.bedrockRole = new iam.Role(this, "BedrockKbRole", {
      assumedBy: new iam.ServicePrincipal("bedrock.amazonaws.com"),
    });

    // S3 Vectors permissions on the Index ARN
    this.bedrockRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "s3vectors:PutVectors",
          "s3vectors:GetVectors",
          "s3vectors:DeleteVectors",
          "s3vectors:QueryVectors",
          "s3vectors:GetIndex",
        ],
        resources: [this.vectorIndex.getAtt("IndexArn").toString()],
      })
    );

    // Bedrock InvokeModel permission for the embedding model (needed during ingestion)
    this.bedrockRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:InvokeModel"],
        resources: [
          "arn:aws:bedrock:eu-west-1::foundation-model/amazon.titan-embed-text-v2:0",
        ],
      })
    );

    // S3 permissions on the data source bucket
    this.bedrockRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [this.dataBucket.bucketArn],
      })
    );
    this.bedrockRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: [this.dataBucket.arnForObjects("*")],
      })
    );

    // --- Bedrock Knowledge Base (Requirement 1.2, 2.2) ---
    // Bedrock validates the role's s3vectors permissions at KB creation time.
    // We must ensure the IAM policy is fully created before the KB resource.
    // CDK creates inline policies as a child "DefaultPolicy" construct on the role.
    const roleDefaultPolicy = this.bedrockRole.node.findChild("DefaultPolicy") as iam.Policy;

    const knowledgeBase = new cdk.CfnResource(this, "KnowledgeBase", {
      type: "AWS::Bedrock::KnowledgeBase",
      properties: {
        Name: "knowledge-pack",
        RoleArn: this.bedrockRole.roleArn,
        KnowledgeBaseConfiguration: {
          Type: "VECTOR",
          VectorKnowledgeBaseConfiguration: {
            EmbeddingModelArn:
              "arn:aws:bedrock:eu-west-1::foundation-model/amazon.titan-embed-text-v2:0",
          },
        },
        StorageConfiguration: {
          Type: "S3_VECTORS",
          S3VectorsConfiguration: {
            IndexArn: this.vectorIndex.getAtt("IndexArn").toString(),
          },
        },
      },
    });
    // Explicit dependency: KB must wait for the IAM policy to be created
    knowledgeBase.node.addDependency(roleDefaultPolicy);

    // Suppress cdk-nag wildcard finding: s3:GetObject on bucket/* is the minimum
    // scope for Bedrock to read all documentation objects during ingestion.
    // The action is already scoped to GetObject only (read-only).
    NagSuppressions.addResourceSuppressions(
      roleDefaultPolicy,
      [
        {
          id: "AwsSolutions-IAM5",
          reason:
            "Bedrock KB requires s3:GetObject on all objects in the data bucket for ingestion. " +
            "Action is scoped to GetObject only (read-only) on a single purpose-built bucket.",
          appliesTo: [`Resource::<DataBucketE3889A50.Arn>/*`],
        },
      ],
      true
    );

    // --- S3 Data Source (Requirement 2.2, 2.3) ---
    const dataSource = new cdk.CfnResource(this, "DataSource", {
      type: "AWS::Bedrock::DataSource",
      properties: {
        Name: "example-docs-s3",
        KnowledgeBaseId: knowledgeBase
          .getAtt("KnowledgeBaseId")
          .toString(),
        DataSourceConfiguration: {
          Type: "S3",
          S3Configuration: {
            BucketArn: this.dataBucket.bucketArn,
            // S3 data source allows max 1 inclusion prefix.
            // Sync script already filters to only upload docs/dg/dev/ and docs/ca/dev/,
            // so using "docs/" as the single prefix is safe.
            InclusionPrefixes: ["docs/"],
          },
        },
        // Default chunking (~300 tokens) is fine — the sync script preprocesses
        // markdown to strip frontmatter and Jekyll tags before upload.
      },
    });

    // --- CloudWatch Logging for KB ingestion debugging ---
    // Uses vended log delivery (DeliverySource → DeliveryDestination → Delivery)
    // to capture per-document ingestion status and failure reasons.
    const kbLogGroup = new logs.LogGroup(this, "KbLogGroup", {
      logGroupName: "/aws/bedrock/knowledge-base/knowledge-pack",
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const deliverySource = new cdk.CfnResource(this, "KbDeliverySource", {
      type: "AWS::Logs::DeliverySource",
      properties: {
        Name: "knowledge-pack-source",
        LogType: "APPLICATION_LOGS",
        ResourceArn: knowledgeBase.getAtt("KnowledgeBaseArn").toString(),
      },
    });

    const deliveryDestination = new cdk.CfnResource(
      this,
      "KbDeliveryDestination",
      {
        type: "AWS::Logs::DeliveryDestination",
        properties: {
          Name: "knowledge-pack-dest",
          OutputFormat: "json",
          DestinationResourceArn: kbLogGroup.logGroupArn,
        },
      }
    );

    const delivery = new cdk.CfnResource(this, "KbDelivery", {
      type: "AWS::Logs::Delivery",
      properties: {
        DeliverySourceName: "knowledge-pack-source",
        DeliveryDestinationArn: deliveryDestination.getAtt("Arn").toString(),
      },
    });
    delivery.addDependency(deliverySource);
    delivery.addDependency(deliveryDestination);

    // --- Stack Outputs ---
    new cdk.CfnOutput(this, "KnowledgeBaseId", {
      value: knowledgeBase.getAtt("KnowledgeBaseId").toString(),
    });
    new cdk.CfnOutput(this, "DataSourceId", {
      value: dataSource.getAtt("DataSourceId").toString(),
    });
    new cdk.CfnOutput(this, "DataBucketName", {
      value: this.dataBucket.bucketName,
    });
  }
}
