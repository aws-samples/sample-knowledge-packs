#!/usr/bin/env python3
"""
Cost Estimate Calculator — Knowledge Pack
=======================================================

A living cost calculation for the entire system. Run this script to get
an up-to-date cost breakdown based on actual deployed resource counts
and current AWS pricing.

Usage:
    python scripts/cost_estimate.py
    python scripts/cost_estimate.py --queries-per-day 500

Pricing sources (February 2026):
    - S3: https://aws.amazon.com/s3/pricing/
    - Bedrock: https://aws.amazon.com/bedrock/pricing/
    - AgentCore: https://aws.amazon.com/bedrock/agentcore/pricing/
    - CloudFront: https://aws.amazon.com/cloudfront/pricing/
    - WAF: https://aws.amazon.com/waf/pricing/
    - Lambda: https://aws.amazon.com/lambda/pricing/
"""

import argparse

# ============================================================
# Section 1: Pricing Constants (update when AWS changes prices)
# ============================================================

# fmt: off

# S3 Standard (eu-west-1, first 50 TB tier)
S3_STORAGE_PER_GB          = 0.023     # $/GB/month
S3_PUT_PER_1K              = 0.005     # $ per 1,000 PUT requests
S3_GET_PER_1K              = 0.0004    # $ per 1,000 GET requests

# S3 Vectors (eu-west-1)
S3V_STORAGE_PER_GB         = 0.06      # $/GB/month
S3V_PUT_PER_GB             = 0.20      # $ per GB uploaded
S3V_QUERY_API_PER_1M       = 2.50      # $ per 1M query API calls
S3V_QUERY_DATA_PER_TB      = 4.00      # $ per TB data processed (first 100K vectors)

# Bedrock — Titan Embed Text v2 (eu-west-1)
TITAN_EMBED_PER_1M_TOKENS  = 0.02      # $ per 1M input tokens

# AgentCore Runtime (consumption-based)
AC_CPU_PER_VCPU_HOUR       = 0.0895    # $ per vCPU-hour (active processing only)
AC_MEM_PER_GB_HOUR         = 0.00945   # $ per GB-hour

# CloudFront (free tier: 10M requests/month, 1 TB transfer)
CF_FREE_TIER_REQUESTS      = 10_000_000
CF_REQUEST_PER_10K         = 0.0100    # $ per 10,000 HTTPS requests (after free tier)

# Lambda@Edge (free tier: 1M requests/month)
LE_FREE_TIER_REQUESTS      = 1_000_000
LE_REQUEST_PER_1M          = 0.60      # $ per 1M requests (after free tier)

# WAF
WAF_WEBACL_PER_MONTH       = 5.00      # $ per WebACL/month
WAF_RULE_PER_MONTH         = 1.00      # $ per rule/month
WAF_REQUEST_PER_1M         = 0.60      # $ per 1M requests

# ECR
ECR_STORAGE_PER_GB         = 0.10      # $/GB/month

# OpenSearch Serverless (comparison only)
OSS_OCU_PER_HOUR           = 0.24      # $ per OCU-hour
OSS_HOURS_PER_MONTH        = 730       # hours in a month

# fmt: on


# ============================================================
# Section 2: Resource Inventory (actual deployed values)
# ============================================================

# S3 data bucket
S3_FILES_UPLOADED = 775
S3_TOTAL_BYTES = 4_600_000  # ~4.4 MB after preprocessing
S3_TOTAL_GB = S3_TOTAL_BYTES / (1024**3)

# S3 Vectors
VECTOR_COUNT = 3000  # estimated chunks from 775 docs (default ~300 token chunking)
VECTOR_DIM = 1024
VECTOR_BYTES = VECTOR_DIM * 4  # float32
METADATA_BYTES = 1200  # ~1.2 KB non-filterable metadata per vector
AVG_VECTOR_TOTAL_BYTES = VECTOR_BYTES + METADATA_BYTES
TOTAL_VECTOR_GB = (VECTOR_COUNT * AVG_VECTOR_TOTAL_BYTES) / (1024**3)

# Titan Embed v2 — tokens for ingestion
# ~4.4 MB text / 4.7 chars per token ≈ 1.16M tokens
INGESTION_TOKENS_M = S3_TOTAL_BYTES / 4.7 / 1_000_000

# AgentCore Runtime — per-query resource usage
AC_ACTIVE_CPU_SEC = 2  # seconds of active CPU per query (I/O wait is free)
AC_VCPU = 1
AC_MEMORY_GB = 0.5
AC_SESSION_SEC = 3  # total session duration including I/O

# Titan Embed v2 — tokens per query
TOKENS_PER_QUERY = 25  # ~20 words ≈ 25 tokens

# ECR container image
ECR_IMAGE_GB = 0.2  # ~200 MB

# WAF rules
WAF_WEBACL_COUNT = 1
WAF_RULE_COUNT = 1


# ============================================================
# Section 3: Cost Calculations
# ============================================================


def calculate_costs(queries_per_day: int = 200) -> None:
    """Calculate and print the full cost breakdown."""

    queries_per_month = queries_per_day * 30

    # --- Header ---
    print("=" * 70)
    print("  KNOWLEDGE PACK — COST ESTIMATE")
    print(f"  Scenario: {queries_per_day} queries/day ({queries_per_month:,}/month)")
    print("=" * 70)

    # --- Section A: One-time ingestion ---
    print("\n📦 ONE-TIME INGESTION COSTS")
    print("-" * 50)

    embed_cost = INGESTION_TOKENS_M * TITAN_EMBED_PER_1M_TOKENS
    s3_put_cost = (S3_FILES_UPLOADED / 1000) * S3_PUT_PER_1K
    s3v_put_cost = TOTAL_VECTOR_GB * S3V_PUT_PER_GB
    ingestion_total = embed_cost + s3_put_cost + s3v_put_cost

    print(f"  Titan Embed v2 ({INGESTION_TOKENS_M:.2f}M tokens)    ${embed_cost:.4f}")
    print(f"  S3 PUT ({S3_FILES_UPLOADED} files)                    ${s3_put_cost:.4f}")
    print(f"  S3 Vectors PUT ({TOTAL_VECTOR_GB:.4f} GB)           ${s3v_put_cost:.4f}")
    print("                                          ─────────")
    print(f"  Total one-time                            ${ingestion_total:.4f}")

    # --- Section B: KB monthly costs ---
    print(f"\n📊 MONTHLY KB COSTS ({queries_per_month:,} queries)")
    print("-" * 50)

    s3_stor = S3_TOTAL_GB * S3_STORAGE_PER_GB
    s3v_stor = TOTAL_VECTOR_GB * S3V_STORAGE_PER_GB
    s3v_api = (queries_per_month / 1_000_000) * S3V_QUERY_API_PER_1M
    data_per_query_gb = (AVG_VECTOR_TOTAL_BYTES * VECTOR_COUNT) / (1024**3)
    data_month_tb = (data_per_query_gb * queries_per_month) / 1024
    s3v_data = data_month_tb * S3V_QUERY_DATA_PER_TB
    query_embed = (queries_per_month * TOKENS_PER_QUERY / 1_000_000) * TITAN_EMBED_PER_1M_TOKENS
    kb_total = s3_stor + s3v_stor + s3v_api + s3v_data + query_embed

    print(f"  S3 storage ({S3_TOTAL_GB:.4f} GB)                  ${s3_stor:.4f}")
    print(f"  S3 Vectors storage ({TOTAL_VECTOR_GB:.4f} GB)      ${s3v_stor:.4f}")
    print(f"  S3 Vectors query API                      ${s3v_api:.4f}")
    print(f"  S3 Vectors query data processing          ${s3v_data:.4f}")
    print(f"  Titan Embed v2 (query embeddings)         ${query_embed:.4f}")
    print("                                          ─────────")
    print(f"  KB subtotal                               ${kb_total:.4f}")

    # --- Section C: Serving layer monthly costs ---
    print("\n🌐 MONTHLY SERVING LAYER COSTS")
    print("-" * 50)

    # AgentCore Runtime
    cpu_per_q = AC_ACTIVE_CPU_SEC * AC_VCPU * (AC_CPU_PER_VCPU_HOUR / 3600)
    mem_per_q = AC_SESSION_SEC * AC_MEMORY_GB * (AC_MEM_PER_GB_HOUR / 3600)
    ac_total = (cpu_per_q + mem_per_q) * queries_per_month

    # WAF
    waf_base = WAF_WEBACL_COUNT * WAF_WEBACL_PER_MONTH + WAF_RULE_COUNT * WAF_RULE_PER_MONTH
    waf_requests = (queries_per_month / 1_000_000) * WAF_REQUEST_PER_1M
    waf_total = waf_base + waf_requests

    # ECR
    ecr_total = ECR_IMAGE_GB * ECR_STORAGE_PER_GB

    # CloudFront (free tier check)
    cf_total = 0.0
    if queries_per_month > CF_FREE_TIER_REQUESTS:
        cf_total = ((queries_per_month - CF_FREE_TIER_REQUESTS) / 10_000) * CF_REQUEST_PER_10K

    # Lambda@Edge (free tier check)
    le_total = 0.0
    if queries_per_month > LE_FREE_TIER_REQUESTS:
        le_total = ((queries_per_month - LE_FREE_TIER_REQUESTS) / 1_000_000) * LE_REQUEST_PER_1M

    serving_total = ac_total + waf_total + ecr_total + cf_total + le_total

    print(f"  AgentCore Runtime                         ${ac_total:.4f}")
    print(f"  WAF WebACL (base: ${waf_base:.2f} + requests)       ${waf_total:.4f}")
    print(f"  ECR container storage                     ${ecr_total:.4f}")
    print(f"  CloudFront {'(free tier)' if cf_total == 0 else ''}                          ${cf_total:.4f}")
    print(f"  Lambda@Edge {'(free tier)' if le_total == 0 else ''}                         ${le_total:.4f}")
    print("                                          ─────────")
    print(f"  Serving subtotal                          ${serving_total:.4f}")

    # --- Section D: Grand total ---
    grand_total = kb_total + serving_total

    print("\n💰 GRAND TOTAL")
    print("-" * 50)
    print(f"  KB layer                                  ${kb_total:.4f}/month")
    print(f"  Serving layer                             ${serving_total:.4f}/month")
    print("                                          ═════════")
    print(f"  TOTAL                                     ${grand_total:.2f}/month")

    # --- Section E: Idle cost ---
    idle = s3_stor + s3v_stor + waf_total + ecr_total
    idle_no_waf = s3_stor + s3v_stor + ecr_total

    print("\n😴 IDLE COST (zero queries)")
    print("-" * 50)
    print(f"  With WAF                                  ${idle:.2f}/month")
    print(f"  Without WAF                               ${idle_no_waf:.4f}/month")

    # --- Section F: Comparison ---
    oss_min_2ocu = 2 * OSS_OCU_PER_HOUR * OSS_HOURS_PER_MONTH
    oss_min_4ocu = 4 * OSS_OCU_PER_HOUR * OSS_HOURS_PER_MONTH

    print("\n📉 COMPARISON: OpenSearch Serverless")
    print("-" * 50)
    print(f"  Our total (S3 Vectors + serving)          ${grand_total:.2f}/month")
    print(f"  OSS minimum (2 OCUs, fractional)          ${oss_min_2ocu:.2f}/month")
    print(f"  OSS minimum (4 OCUs, full HA)             ${oss_min_4ocu:.2f}/month")
    if grand_total > 0:
        savings_2 = ((oss_min_2ocu - grand_total) / oss_min_2ocu) * 100
        print(f"  Savings vs 2-OCU OSS                      {savings_2:.0f}%")

    print()


# ============================================================
# Section 4: Entry Point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate cost estimate for Knowledge Pack")
    parser.add_argument(
        "--queries-per-day",
        type=int,
        default=200,
        help="Expected queries per day (default: 200)",
    )
    args = parser.parse_args()
    calculate_costs(args.queries_per_day)
