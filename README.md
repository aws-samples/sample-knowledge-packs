# Knowledge Packs

> **Important:** This is sample code for non-production usage. You should work with your security and legal teams to meet your organizational security, regulatory and compliance requirements before deployment.

Context engineering for your product documentation. Turn any docs repo into an MCP server your developers' AI agents can query — under your brand, on your infrastructure, for under $1/month.

## What Is a Knowledge Pack?

A Knowledge Pack is a curated, queryable documentation bundle exposed via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). You point it at a docs repo, it ingests into an [Amazon Bedrock Knowledge Base](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html) with S3 Vectors, and serves results through a FastMCP server. Any MCP-compatible IDE connects with three lines of JSON.

**For ISVs:** Ship Knowledge Packs alongside your product. Your developers get AI-assisted documentation in their IDE — under your brand, synced to your release cycle.

**For teams:** Build Knowledge Packs from internal docs, runbooks, and architecture guides. Your agents get the right context without hallucinating from training data.

## Try the demo

A live demo serves [Spryker](https://spryker.com) developer documentation (2,179 docs indexed). No credentials, no deployment needed.

**Kiro IDE** — Install the demo Power:
1. Clone this repository
2. In Kiro, open the Powers panel
3. Click "Install from a folder"
4. Select the `dist/spryker-docs/` directory from the cloned repo

Or install from the [Kiro Powers marketplace](https://kiro.dev/powers) once published.

**Any MCP-compatible IDE** — Add to your MCP config:

```json
{
  "mcpServers": {
    "docs-kb": { "url": "https://<your-custom-domain>/mcp" }
  }
}
```

Works in Kiro, Cursor, VS Code, Claude Code — anything that speaks MCP Streamable HTTP.

## Build your own

### Prerequisites

- AWS account with [CDK bootstrapped](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html) in your target region
- Python 3.12+
- Node.js 18+ (for CDK)
- A Git-hosted documentation repository (public or private)
- (Optional) A Route 53 hosted zone for custom domain

### Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description | Example |
|----------|-------------|---------|
| `CUSTOM_DOMAIN` | Your custom domain for the MCP endpoint | `knowledge-packs.example.com` |
| `ZONE_NAME` | Route 53 hosted zone name | `example.com` |
| `HOSTED_ZONE_ID` | Route 53 hosted zone ID | `Z0123456789ABCDEFGHIJ` |
| `MCP_ENDPOINT` | Full MCP endpoint URL | `https://knowledge-packs.example.com/mcp` |

The `.env` file is gitignored. CDK and scripts read these values automatically. If no `.env` is set, CDK context flags (`-c domainName=...`) also work.

### Deploy everything

```bash
# 1. Install dependencies
make install

# 2. Deploy all stacks, sync docs, run tests
make deploy-all REPO_URL=https://github.com/your-org/your-docs.git
```

This runs: lint → unit tests → CDK synth → deploy KB → sync docs → deploy Gateway + Runtime → deploy CloudFront + domain → e2e tests. One command, full pipeline.

After deploy, your MCP endpoint is live. Stack outputs show the URL.

### Deploy step by step

If you prefer more control:

```bash
make install                  # Python + Node dependencies
make cdk-deploy               # Deploy KB infrastructure
make sync REPO_URL=https://github.com/your-org/your-docs.git  # Ingest docs
make deploy-mcp               # Deploy Gateway + Runtime + Cognito
make deploy-domain            # Deploy CloudFront + custom domain
make e2e                      # Verify everything works
```

### Run locally (no remote infra)

For development or if you don't need a public URL:

```bash
make install
make cdk-deploy
make sync REPO_URL=https://github.com/your-org/your-docs.git

# Run the MCP server locally (stdio transport)
KB_ALLOWLIST='{"my-product": "<KB_ID>"}' DEFAULT_KB=my-product python scripts/kb_server.py
```

Add to your IDE's MCP config:

```json
{
  "mcpServers": {
    "my-docs": {
      "command": "python",
      "args": ["scripts/kb_server.py"],
      "env": {
        "KB_ALLOWLIST": "{\"my-product\": \"YOUR_KB_ID\"}",
        "DEFAULT_KB": "my-product"
      }
    }
  }
}
```

The KB ID is in `deployed-resources.json` after deploy, or in the `KnowledgePackStack` stack outputs.

## Customize for your docs

To point this at your own documentation:

1. **Docs source** — Change the `REPO_URL` in `make sync`. Use `--include-prefix` to limit to specific directories.
2. **Domain** — Edit `domainName` and `hostedZoneId` in `infra/lib/gateway-domain-stack.ts`.
3. **Region** — Edit `env.region` in `infra/bin/app.ts`. S3 Vectors and Bedrock must be in the same region.
4. **KB descriptions** — Set `KB_DESCRIPTIONS` env var to help the LLM know when to use your tool (see `kb_server.py` docstring).
5. **Kiro Power** — Edit `.kiro/powers/spryker-docs/POWER.md` with your product name, example queries, and instructions. Run `make build-power` to copy to `dist/`.

## Architecture

```
Docs Repo → sync_docs.py → S3 → Bedrock Knowledge Base (S3 Vectors + Titan Embed v2)
                                          ↑
MCP Clients → CloudFront → AgentCore Gateway → AgentCore Runtime (FastMCP)
```

Three CDK stacks:
- `KnowledgePackStack` — Bedrock KB, S3 data bucket, S3 Vectors (eu-west-1)
- `GatewayMcpStack` — Cognito, AgentCore Runtime + Gateway (eu-west-1)
- `GatewayDomainStack` — CloudFront, ACM, WAF, Route 53 (us-east-1)

## Cost

| Component | Monthly Cost |
|---|---|
| S3 Vectors (15 MB, 6K queries) | < $0.02 |
| Bedrock Titan Embed v2 (queries) | < $0.01 |
| AgentCore Runtime | ~$0.32 |
| WAF (optional) | $6.00 |
| **Total** | **~$0.37 without WAF, ~$6.37 with** |

Compare: OpenSearch Serverless minimum is $350–700/month for the same workload.

## Why Not Third-Party Indexing?

| | Third-party services | Knowledge Packs |
|---|---|---|
| Who controls indexing? | The service provider | You |
| Private docs? | Paid plans only | Your AWS account |
| Sync with releases? | Periodic scraping | You trigger sync |
| Branding? | Their domain | Your domain |
| Cost to ISV | No control | ~$6/month (with WAF), <$1 without |

## Security

See [`threat-model.tc.json`](threat-model.tc.json) for the full threat model in [Threat Composer](https://github.com/awslabs/threat-composer) format — 10 STRIDE-classified threats, 12 mitigations, 7 assumptions with verified AWS documentation references.

Key security controls:
- WAF IP rate limiting (100 req/5 min per IP)
- CloudFront access logging enabled
- S3 bucket: public access blocked, SSL enforced, server access logging enabled
- IAM policies scoped to specific resource ARNs
- [cdk-nag](https://github.com/cdklabs/cdk-nag/) AwsSolutionsChecks enabled on all stacks
- Input validation on query length and result count
- Cognito M2M OAuth between Gateway and Runtime

### Gateway inbound authorization

The AgentCore Gateway uses `authorizerType: NONE` (no inbound authentication). This is the [documented production pattern](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-inbound-auth.html) for public MCP endpoints — AWS explicitly states that No Authorization gateways should only be used for production gateways you intend to make public.

The Runtime behind the Gateway still requires JWT authentication via Cognito OAuth. Unauthenticated callers can reach the Gateway but cannot access the Knowledge Base without valid tokens.

**If you deploy this in an organization with multiple teams**, add an IAM policy or SCP using the `bedrock-agentcore:GatewayAuthorizerType` condition key to prevent accidental creation of no-auth gateways:

```json
{
  "Effect": "Deny",
  "Action": "bedrock-agentcore:CreateGateway",
  "Resource": "*",
  "Condition": {
    "StringEquals": {
      "bedrock-agentcore:GatewayAuthorizerType": "NONE"
    }
  }
}
```

Grant an exception only to roles that intentionally deploy public gateways. This guardrail is not included in the CDK stacks because it's an organizational policy, not a per-project resource.

### Security scanning

Run all security scanners and generate reports for review:

```bash
make security-install   # Install scanning tools (one-time)
make security-scan      # Run all scanners, output to security-reports/
```

Reports are written to `security-reports/` (gitignored). The scan suite covers:

| Scanner | What it checks |
|---------|---------------|
| [Bandit](https://github.com/PyCQA/bandit) | Python SAST — common security issues |
| [pip-audit](https://pypi.org/project/pip-audit/) | Python dependency vulnerabilities |
| [detect-secrets](https://github.com/Yelp/detect-secrets/) | Hardcoded secrets and credentials |
| [pip-licenses](https://pypi.org/project/pip-licenses/) | Open source license compliance |
| [npm audit](https://docs.npmjs.com/cli/v7/commands/npm-audit) | Node.js dependency vulnerabilities |
| [cdk-nag](https://github.com/cdklabs/cdk-nag/) | CDK infrastructure security (AwsSolutionsChecks) |

For container image scanning (requires Docker or [finch](https://github.com/runfinch/finch)):

```bash
make security-scan-container   # Trivy scan of the built container image
```

## Project Structure

```
├── scripts/
│   ├── kb_server.py         # FastMCP server (~120 lines)
│   ├── kb_client.py         # Bedrock KB retrieve API wrapper
│   ├── sync_docs.py         # Clone → preprocess → S3 → ingest
│   ├── validate_kb.py       # Validation queries against live KB
│   └── cost_estimate.py     # Living cost calculator
├── tests/                   # Unit, property-based, and e2e tests
├── infra/                   # CDK stacks (TypeScript)
│   ├── lib/knowledge-pack-stack.ts    # KB + S3 Vectors
│   ├── lib/gateway-mcp-stack.ts       # AgentCore + Cognito
│   └── lib/gateway-domain-stack.ts    # CloudFront + WAF
├── dist/spryker-docs/              # Kiro Power (copy to .kiro/powers/)
├── threat-model.tc.json     # Threat Composer threat model
├── deployed-resources.json  # Current deployed resource IDs
├── Makefile                 # Automation targets
└── Dockerfile               # Container for AgentCore deployment
```

## Make Targets

```bash
make install          # Install Python + CDK dependencies
make check            # Lint + unit tests + CDK synth (pre-deploy gate)
make deploy-all       # Full deployment: check → deploy → sync → test
make destroy-all      # Tear down all stacks (reverse order)
make sync             # Ingest docs into KB (requires REPO_URL)
make e2e              # Run e2e tests against deployed KB
make cost-estimate    # Show cost breakdown
make build-power      # Build dist/spryker-docs/ from .kiro/powers/
make status           # Show all deployed resource status
make logs             # Tail AgentCore Runtime CloudWatch logs
make security-install # Install security scanning tools
make security-scan    # Run all security scanners → security-reports/
```

## Clean up

```bash
make destroy-all
```

Destroys all three stacks in reverse order. S3 buckets auto-delete contents.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
