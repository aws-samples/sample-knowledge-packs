# COE-001: FastMCP 3.x removed `stateless_http` from constructor

**Date:** 2026-02-27
**Duration of incident:** ~4 hours of wasted debugging
**Impact:** Multiple failed deployments, full teardown of working infrastructure, orphaned resources requiring manual console cleanup

## What happened

After implementing multi-KB support (`query_docs` replacing `query_example_docs`), the Gateway Target creation failed with "Failed to connect and fetch tools from the provided MCP target server." We spent ~4 hours chasing a timing/race condition hypothesis before discovering the actual root cause: the MCP server container was crashing on startup due to a FastMCP 3.x breaking change.

## Timeline

1. **Code changes completed** — multi-KB `query_docs` tool, 67 tests passing, CDK synth clean
2. **First deploy** — `make deploy-mcp` succeeded (CDK UPDATE_COMPLETE), but live endpoint still showed old `query_example_docs` tool
3. **Wrong hypothesis formed** — assumed Gateway was caching stale tools, tried `UpdateGatewayTarget` API → failed
4. **Escalation to full teardown** — destroyed all stacks, attempted fresh deploy → Gateway Target creation failed
5. **5+ consecutive deploy attempts** — all failed with same error, each requiring manual cleanup
6. **Deep dive into wrong direction** — researched similar errors in community forums and support threads, concluded it was a "known timing issue"
7. **Built elaborate workaround** — `skipTarget` CDK flag, `create_gateway_target.py` script with retries, state file tracking
8. **User found Slack thread** — someone with the exact same error fixed it by changing their MCP server code
9. **Checked CloudWatch logs** — found the actual error in 30 seconds: `TypeError: FastMCP() no longer accepts 'stateless_http'`
10. **One-line fix** — moved `stateless_http` from constructor to `run()`, deployed successfully on first try

## Root cause

FastMCP 3.x removed the `stateless_http` parameter from the `FastMCP()` constructor. It must now be passed to `run()` or set via `FASTMCP_STATELESS_HTTP` env var. Our code used the old pattern:

```python
# BROKEN (FastMCP 3.x)
mcp = FastMCP("docs-kb", stateless_http=True)

# FIXED
mcp = FastMCP("docs-kb")
server.run(transport="streamable-http", host="0.0.0.0", port=8000, stateless_http=True)
```

The container crashed immediately on startup with a `TypeError`. The Gateway's tool discovery (`tools/list`) failed because there was no server to connect to — not because of timing.

## Why we missed it

1. **Didn't check logs first.** The CloudWatch logs had the exact error message. We should have looked there within the first 5 minutes, not after 4 hours.
2. **Tested locally but not the container.** We ran `create_server()` locally and it worked — but the local test didn't call `run()` with `stateless_http=True`. The crash only happened in the container where `MCP_TRANSPORT=streamable-http` triggered the broken code path.
3. **Confirmation bias from community reports.** Found forum posts mentioning the same error message. Concluded it was a known service-side timing issue. But those failures had a different root cause (actual service availability). Same symptom, different disease.
4. **Didn't question the hypothesis when evidence contradicted it.** The Runtime control plane said READY, the container image was verified correct locally, the endpoint was READY — all signs pointed to the server being fine. We ignored this evidence and doubled down on the timing theory.
5. **Built workarounds before understanding the problem.** Created `skipTarget`, `create_gateway_target.py` with retries, state file tracking — all before confirming the root cause. This wasted time and added complexity.
6. **Didn't test the deployed container directly.** A simple `curl` to the Runtime with a JWT token would have shown the crash error immediately. We only did this after 4 hours.

## The debugging approach that should have been followed

1. **Check the logs.** Always. First. CloudWatch logs for the Runtime would have shown the `TypeError` in seconds.
2. **Test the deployed artifact, not the source.** Run the container locally with the same env vars and transport mode. Or call the Runtime directly with a token.
3. **Reproduce minimally.** Before building workarounds, confirm the hypothesis. If it's a timing issue, adding a sleep should fix it. We never tested that.
4. **Don't trust "READY" status.** The control plane says READY when it accepts the resource definition. It doesn't mean the container is serving. Always verify with an actual request.
5. **Read error messages literally.** "Failed to connect" means the server isn't responding. The most likely cause is: the server isn't running. Check why.

## Action items

### Immediate
- [x] Fix the `stateless_http` parameter (moved to `run()`)
- [x] Deploy successfully with all three stacks
- [x] Verify `query_docs` tool is live

### Process improvements
- [x] Add a `make logs` target that tails the Runtime CloudWatch logs
- [x] Add a `make test-runtime` target that calls the Runtime directly with a JWT token
- [x] Add the FastMCP 3.x breaking change to `.kiro/steering/cdk.md` as a gotcha
- [x] Pin FastMCP version in `requirements.txt` (`fastmcp>=3.0,<4.0`)
- [ ] Add a container smoke test to the deploy pipeline — run the container locally with the same env vars before pushing to ECR

### Steering file updates
- [x] Add to engineering standards: "When a deployment fails, check CloudWatch logs FIRST."
- [x] Add to engineering standards: "When debugging a 'can't connect' error, verify the server is actually running."
- [x] Add to CDK steering: "FastMCP 3.x removed `stateless_http` from constructor."

## Lessons learned

1. **Logs first, theories later.** The answer was in CloudWatch the entire time. Every minute spent theorizing was a minute not spent reading the error message.
2. **Test the deployed artifact.** Local tests passed because they didn't exercise the failing code path. The container was the unit that mattered.
3. **Community reports are context, not answers.** Finding someone else with the same error message doesn't mean you have the same root cause. Correlation ≠ causation.
4. **Workarounds are not debugging.** Building retry logic and skip flags is engineering around a problem you don't understand. Understand first, then decide if a workaround is needed.
5. **Simple causes are more likely.** "The server crashes on startup" is simpler than "CloudFormation has a race condition with AgentCore Runtime warm-up timing." Occam's razor applies to debugging.
