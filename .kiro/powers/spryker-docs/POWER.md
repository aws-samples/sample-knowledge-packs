---
name: spryker-docs
displayName: "Spryker Docs"
description: "Query Spryker developer documentation including architecture, Glue API, ACP integrations, SCCOS deployment, marketplace, and module development. 2,179 docs indexed via Amazon Bedrock Knowledge Base."
keywords: ["spryker", "oryx", "glue", "glue api", "yves", "zed", "acp", "sccos", "marketplace", "merchant portal", "pbc", "back office", "storefront", "bapi", "data import", "publish and synchronize", "p&s", "module development", "spryker cloud commerce os", "payment service provider", "packaged business capabilities", "frontend", "architecture"]
author: "Your Name"
---

# Knowledge Packs Demo

A demo Knowledge Pack serving [Spryker](https://spryker.com) developer documentation. 2,179 markdown files indexed, queryable from any MCP-compatible IDE.

No setup required — install this Power in Kiro and start querying.

Build your own Knowledge Pack from any docs repo — see the [repository](https://github.com/aws-samples/knowledge-packs) for instructions.

## Tools

- `query_docs` — Search Spryker documentation with a natural language question. Returns relevant chunks with source references and relevance scores.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | yes | Natural language question |
| `kb` | string | no | KB alias (default: `example`) |
| `num_results` | integer | no | Max results to return (default: 5, max: 10) |

## Example Queries

- "What is Spryker's architecture? How do Yves, Zed, and Glue relate?"
- "How do I build a Glue API endpoint?"
- "How does ACP handle payment service provider integrations?"
- "How do I deploy to Spryker Cloud Commerce OS?"

No authentication required. No API keys. Just install and query.

## Using with other MCP clients

If you're not using Kiro Powers, add this to your IDE's MCP config:

```json
{
  "mcpServers": {
    "docs-kb": {
      "url": "https://<your-custom-domain>/mcp"
    }
  }
}
```

Works in Kiro, Cursor, VS Code, Claude Code — anything that speaks MCP Streamable HTTP.

## Instructions

When the user is working on a Spryker project or asks about any of the following topics, use the `query_docs` tool to retrieve relevant documentation before answering:

- Spryker architecture (Yves, Zed, Glue, Client layers)
- Glue API development (Storefront and Backend APIs, REST and BAPI)
- ACP app integrations and payment service providers
- Spryker Cloud Commerce OS (SCCOS) deployment and configuration
- Marketplace features and Merchant Portal
- Packaged Business Capabilities (PBCs)
- Oryx frontend framework
- Back Office customization
- Data import/export and P&S (Publish and Synchronize)
- Module development, upgrades, and feature integration guides

Always query the documentation first. Do not answer Spryker questions from training data alone — the docs contain version-specific details that training data gets wrong.
