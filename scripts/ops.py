"""Operations toolkit for the Knowledge Pack project.

Provides status checks, cleanup, and diagnostics for all deployed resources.

Usage:
    python scripts/ops.py status          # Full status of all resources
    python scripts/ops.py check           # Pre-deploy health check
    python scripts/ops.py cleanup         # Find and remove orphaned resources
    python scripts/ops.py cleanup --force # Actually delete orphaned resources
"""

import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError

REGION = "eu-west-1"
DOMAIN_REGION = "us-east-1"
STACK_NAMES = {
    "kb": "KnowledgePackStack",
    "mcp": "GatewayMcpStack",
    "domain": "GatewayDomainStack",
}
MCP_ENDPOINT = os.environ.get("MCP_ENDPOINT", "https://knowledge-packs.example.com/mcp")
TARGET_STATE_FILE = ".gateway-target.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfn(region=REGION):
    return boto3.client("cloudformation", region_name=region)


def _agentcore():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def _cognito():
    return boto3.client("cognito-idp", region_name=REGION)


def _stack_status(name, region=REGION):
    """Get stack status or None if it doesn't exist."""
    try:
        r = _cfn(region).describe_stacks(StackName=name)
        return r["Stacks"][0]["StackStatus"]
    except ClientError:
        return None


def _stack_output(name, key, region=REGION):
    """Get a stack output value or None."""
    try:
        r = _cfn(region).describe_stacks(StackName=name)
        for o in r["Stacks"][0].get("Outputs", []):
            if o["OutputKey"] == key:
                return o["OutputValue"]
    except ClientError:
        pass
    return None


def _print_section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _ok(msg):
    print(f"  ✅ {msg}")


def _warn(msg):
    print(f"  ⚠️  {msg}")


def _fail(msg):
    print(f"  ❌ {msg}")


def _info(msg):
    print(f"  ℹ️  {msg}")


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

def cmd_status():
    """Print full status of all deployed resources."""
    _print_section("CloudFormation Stacks")

    for label, name in STACK_NAMES.items():
        region = DOMAIN_REGION if label == "domain" else REGION
        status = _stack_status(name, region)
        if status is None:
            _info(f"{name} ({region}): NOT DEPLOYED")
        elif "COMPLETE" in status and "ROLLBACK" not in status and "DELETE" not in status:
            _ok(f"{name} ({region}): {status}")
        elif "ROLLBACK" in status or "FAILED" in status:
            _fail(f"{name} ({region}): {status}")
        else:
            _warn(f"{name} ({region}): {status}")

    # KB details
    _print_section("Knowledge Base")
    kb_id = _stack_output(STACK_NAMES["kb"], "KnowledgeBaseId")
    ds_id = _stack_output(STACK_NAMES["kb"], "DataSourceId")
    bucket = _stack_output(STACK_NAMES["kb"], "DataBucketName")
    if kb_id:
        _ok(f"KB ID: {kb_id}")
        _info(f"Data Source ID: {ds_id}")
        _info(f"Bucket: {bucket}")
    else:
        _info("KB stack not deployed")

    # AgentCore resources
    _print_section("AgentCore Resources")
    ac = _agentcore()

    runtimes = ac.list_agent_runtimes().get("agentRuntimes", [])
    if runtimes:
        for rt in runtimes:
            _info(f"Runtime: {rt['agentRuntimeName']} ({rt['agentRuntimeId']}) - {rt['status']}")
            try:
                ep = ac.get_agent_runtime_endpoint(
                    agentRuntimeId=rt["agentRuntimeId"], endpointName="DEFAULT"
                )
                _info(f"  Endpoint: {ep['status']}, liveVersion: {ep.get('liveVersion', '?')}")
            except ClientError:
                _warn("  Endpoint: not available")
    else:
        _info("No runtimes found")

    gateways = ac.list_gateways().get("gateways", [])
    if gateways:
        for gw in gateways:
            _info(f"Gateway: {gw['name']} ({gw['gatewayId']}) - {gw['status']}")
            try:
                targets = ac.list_gateway_targets(
                    gatewayIdentifier=gw["gatewayId"], maxResults=50
                ).get("targets", [])
                for t in targets:
                    status_str = t.get("status", "?")
                    if status_str == "READY":
                        _ok(f"  Target: {t['name']} ({t['targetId']}) - {status_str}")
                    elif "FAIL" in status_str:
                        _fail(f"  Target: {t['name']} ({t['targetId']}) - {status_str}")
                    else:
                        _info(f"  Target: {t['name']} ({t['targetId']}) - {status_str}")
                if not targets:
                    _warn("  No targets registered")
            except ClientError as e:
                _warn(f"  Could not list targets: {e}")
    else:
        _info("No gateways found")

    # Cognito
    _print_section("Cognito")
    pools = _cognito().list_user_pools(MaxResults=10).get("UserPools", [])
    mcp_pools = [p for p in pools if "mcp" in p["Name"].lower() or "example" in p["Name"].lower()]
    if mcp_pools:
        for p in mcp_pools:
            _info(f"User Pool: {p['Name']} ({p['Id']})")
    else:
        _info("No MCP-related user pools found")

    # Stack outputs
    _print_section("Key Outputs")
    gw_ep = _stack_output(STACK_NAMES["mcp"], "GatewayEndpoint")
    mcp_url = _stack_output(STACK_NAMES["domain"], "McpEndpointUrl", DOMAIN_REGION)
    rt_arn = _stack_output(STACK_NAMES["mcp"], "RuntimeArn")
    if gw_ep:
        _info(f"Gateway Endpoint: {gw_ep}")
    if mcp_url:
        _info(f"MCP Endpoint: {mcp_url}")
    if rt_arn:
        _info(f"Runtime ARN: {rt_arn}")

    # Live endpoint test
    _print_section("Live Endpoint Test")
    try:
        import urllib.request
        req = urllib.request.Request(
            MCP_ENDPOINT,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            if "query_docs" in body:
                _ok("Endpoint live — query_docs tool available")
            elif "query_example_docs" in body:
                _warn("Endpoint live — OLD query_example_docs tool (needs sync)")
            else:
                _warn(f"Endpoint live — unexpected response: {body[:200]}")
    except Exception as e:
        _fail(f"Endpoint not reachable: {e}")


# ---------------------------------------------------------------------------
# Check command (pre-deploy health check)
# ---------------------------------------------------------------------------

def cmd_check():
    """Pre-deploy health check — verify no orphaned resources or broken stacks."""
    issues = []

    _print_section("Pre-Deploy Health Check")

    # Check for ROLLBACK_COMPLETE stacks
    for label, name in STACK_NAMES.items():
        region = DOMAIN_REGION if label == "domain" else REGION
        status = _stack_status(name, region)
        if status and "ROLLBACK" in status:
            _fail(f"{name}: {status} — must be deleted before redeploying")
            issues.append(f"Delete stack: aws cloudformation delete-stack --stack-name {name} --region {region}")
        elif status and "FAILED" in status:
            _fail(f"{name}: {status} — stuck, needs manual cleanup")
            issues.append(f"Fix stack: {name} in {region}")
        elif status:
            _ok(f"{name}: {status}")
        else:
            _info(f"{name}: not deployed")

    # Check for orphaned AgentCore resources
    ac = _agentcore()

    runtimes = ac.list_agent_runtimes().get("agentRuntimes", [])
    # Check if any runtimes exist that aren't managed by our stack
    rt_arn = _stack_output(STACK_NAMES["mcp"], "RuntimeArn")
    for rt in runtimes:
        managed = rt_arn and rt["agentRuntimeId"] in (rt_arn or "")
        if not managed:
            _warn(f"Orphaned runtime: {rt['agentRuntimeName']} ({rt['agentRuntimeId']})")
            issues.append(f"Delete orphaned runtime: {rt['agentRuntimeId']}")
        else:
            _ok(f"Runtime {rt['agentRuntimeName']} managed by stack")

    gateways = ac.list_gateways().get("gateways", [])
    gw_id = _stack_output(STACK_NAMES["mcp"], "GatewayId")
    for gw in gateways:
        managed = gw_id and gw["gatewayId"] == gw_id
        if not managed:
            _warn(f"Orphaned gateway: {gw['name']} ({gw['gatewayId']})")
            issues.append(f"Delete orphaned gateway: {gw['gatewayId']}")

            # Check for targets on orphaned gateway
            try:
                targets = ac.list_gateway_targets(
                    gatewayIdentifier=gw["gatewayId"], maxResults=50
                ).get("targets", [])
                for t in targets:
                    _warn(f"  Orphaned target: {t['targetId']} - {t.get('status', '?')}")
                    issues.append(f"Delete target {t['targetId']} on gateway {gw['gatewayId']}")
            except ClientError:
                _warn("  Could not list targets (check console for FAILED targets)")
                issues.append(
                    f"Check console for targets on gateway {gw['gatewayId']}: "
                    f"https://{REGION}.console.aws.amazon.com/bedrock-agentcore/home?region={REGION}#/gateways/{gw['gatewayId']}"
                )
        else:
            _ok(f"Gateway {gw['name']} managed by stack")

    # Summary
    _print_section("Summary")
    if issues:
        _fail(f"{len(issues)} issue(s) found — fix before deploying:")
        for i, issue in enumerate(issues, 1):
            print(f"    {i}. {issue}")
        return 1
    else:
        _ok("All clear — safe to deploy")
        return 0


# ---------------------------------------------------------------------------
# Cleanup command
# ---------------------------------------------------------------------------

def cmd_cleanup(force=False):
    """Find and optionally remove orphaned AgentCore resources and broken stacks."""
    _print_section("Cleanup" + (" (DRY RUN)" if not force else " (FORCE)"))

    ac = _agentcore()
    cleaned = 0

    # 1. Delete ROLLBACK_COMPLETE stacks
    for label, name in STACK_NAMES.items():
        region = DOMAIN_REGION if label == "domain" else REGION
        status = _stack_status(name, region)
        if status and "ROLLBACK_COMPLETE" in status:
            if force:
                _warn(f"Deleting {name} (ROLLBACK_COMPLETE)...")
                _cfn(region).delete_stack(StackName=name)
                _info("  Delete initiated")
                cleaned += 1
            else:
                _warn(f"Would delete {name} (ROLLBACK_COMPLETE)")
                cleaned += 1

    # 2. Find orphaned gateways and their targets
    gw_id = _stack_output(STACK_NAMES["mcp"], "GatewayId")
    gateways = ac.list_gateways().get("gateways", [])

    # Also check managed gateways for failed targets
    if gw_id:
        try:
            targets = ac.list_gateway_targets(
                gatewayIdentifier=gw_id, maxResults=50
            ).get("targets", [])
            for t in targets:
                if t.get("status") in ("FAILED", "CREATE_UNSUCCESSFUL", "UPDATE_UNSUCCESSFUL"):
                    _warn(f"Failed target on managed gateway: {t['targetId']} - {t['status']}")
                    if force:
                        _warn(f"  Deleting failed target {t['targetId']}...")
                        ac.delete_gateway_target(
                            gatewayIdentifier=gw_id, targetId=t["targetId"]
                        )
                        cleaned += 1
                    else:
                        _warn(f"  Would delete failed target {t['targetId']}")
                        cleaned += 1
        except ClientError:
            pass

        # Check state file for targets the API doesn't list (FAILED targets are invisible)
        if os.path.exists(TARGET_STATE_FILE):
            with open(TARGET_STATE_FILE) as f:
                state = json.load(f)
            if state.get("gatewayId") == gw_id and state.get("status") != "READY":
                tid = state["targetId"]
                _warn(f"Tracked target from state file: {tid} ({state.get('status')})")
                if force:
                    try:
                        ac.delete_gateway_target(
                            gatewayIdentifier=gw_id, targetId=tid
                        )
                        _warn(f"  Deleted tracked target {tid}")
                    except ClientError:
                        _info(f"  Target {tid} already gone")
                    os.remove(TARGET_STATE_FILE)
                    cleaned += 1
                else:
                    _warn(f"  Would delete tracked target {tid}")
                    cleaned += 1

    for gw in gateways:
        if gw_id and gw["gatewayId"] == gw_id:
            continue  # Managed by stack

        _warn(f"Orphaned gateway: {gw['name']} ({gw['gatewayId']})")

        # Delete targets first
        try:
            targets = ac.list_gateway_targets(
                gatewayIdentifier=gw["gatewayId"], maxResults=50
            ).get("targets", [])
            for t in targets:
                if force:
                    _warn(f"  Deleting target {t['targetId']}...")
                    ac.delete_gateway_target(
                        gatewayIdentifier=gw["gatewayId"], targetId=t["targetId"]
                    )
                    cleaned += 1
                else:
                    _warn(f"  Would delete target {t['targetId']}")
                    cleaned += 1
        except ClientError:
            _fail("  Cannot list targets — check console manually:")
            _info(
                f"  https://{REGION}.console.aws.amazon.com/bedrock-agentcore/"
                f"home?region={REGION}#/gateways/{gw['gatewayId']}"
            )

        # Delete gateway
        if force:
            try:
                _warn(f"  Deleting gateway {gw['gatewayId']}...")
                ac.delete_gateway(gatewayIdentifier=gw["gatewayId"])
                cleaned += 1
            except ClientError as e:
                _fail(f"  Cannot delete gateway: {e}")
                _info("  Delete targets first via console, then retry")
        else:
            _warn(f"  Would delete gateway {gw['gatewayId']}")
            cleaned += 1

    # 3. Find orphaned runtimes
    rt_arn = _stack_output(STACK_NAMES["mcp"], "RuntimeArn")
    runtimes = ac.list_agent_runtimes().get("agentRuntimes", [])
    for rt in runtimes:
        if rt_arn and rt["agentRuntimeId"] in rt_arn:
            continue  # Managed by stack
        _warn(f"Orphaned runtime: {rt['agentRuntimeName']} ({rt['agentRuntimeId']})")
        if force:
            _warn(f"  Deleting runtime {rt['agentRuntimeId']}...")
            ac.delete_agent_runtime(agentRuntimeId=rt["agentRuntimeId"])
            cleaned += 1
        else:
            _warn(f"  Would delete runtime {rt['agentRuntimeId']}")
            cleaned += 1

    # Summary
    _print_section("Summary")
    if cleaned == 0:
        _ok("Nothing to clean up")
    elif force:
        _ok(f"Cleaned up {cleaned} resource(s)")
    else:
        _warn(f"Found {cleaned} resource(s) to clean — run with --force to delete")

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Operations toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Full status of all resources")
    sub.add_parser("check", help="Pre-deploy health check")

    cleanup_parser = sub.add_parser("cleanup", help="Find/remove orphaned resources")
    cleanup_parser.add_argument(
        "--force", action="store_true", help="Actually delete (default is dry run)"
    )

    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "check":
        sys.exit(cmd_check())
    elif args.command == "cleanup":
        sys.exit(cmd_cleanup(force=args.force))


if __name__ == "__main__":
    main()
