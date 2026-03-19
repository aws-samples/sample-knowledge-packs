# COE-002: Unicode LINE SEPARATOR (U+2028) breaks SSE framing in AgentCore Gateway

**Date:** 2026-03-08
**Duration of incident:** ~2 hours of debugging
**Impact:** All queries returning KB chunks containing U+2028 failed through the Gateway. The MCP tool was completely broken for a subset of documentation queries.

## What happened

The `query_docs` MCP tool worked for some queries but failed for others with `McpException - MCP invocation failed: Tool invocation failed`. No useful error detail from the Gateway. The pattern was invisible until we wrote targeted test cases: every failing query returned the same KB chunk (`oryx-technology.md`) which contained a Unicode LINE SEPARATOR character (U+2028).

## Timeline

1. User reported tool failure — `query_docs` returned "Tool invocation failed" for "What is Oryx?"
2. Verified endpoint is up — `tools/list` worked, `curl` to the Gateway returned 200
3. Checked CloudWatch logs (COE-001 lesson applied) — Runtime logs showed `202 Accepted` for tool calls but no errors, no Python tracebacks. Container restarted after each failed tool call.
4. Wrong hypotheses explored:
   - Response size limits (Gateway allows 6 MB per [docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html), our responses are ~5 KB)
   - `structuredContent` field added by FastMCP 3.x
   - MCP protocol version mismatch (Runtime SDK uses `2025-11-25`, Gateway supports `2025-03-26` and `2025-06-18`)
   - ARM64 container issues
   - Sync vs async tool handler differences
5. Wrote parametrized test cases — tested 7 different queries through the Gateway. Found that "Spryker architecture" with 10 results worked, but "What is Oryx?" with 1 result failed. Not about size.
6. Narrowed to specific KB chunk — all failing queries returned `oryx-technology.md`. All passing queries returned other docs.
7. Found the character — `oryx-technology.md` contained U+2028 LINE SEPARATOR. No other top-result docs did.
8. One-line fix — strip U+2028/U+2029 in `format_results()` before returning
9. Generalized the fix — extracted `sanitize_for_sse()` function covering NULL, C0 controls, U+2028, U+2029
10. Deployed and verified — all 18 gateway debug tests pass, all 100 tests pass

## Root cause

MCP Streamable HTTP uses Server-Sent Events (SSE) where newlines delimit event boundaries. The [W3C SSE spec](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream) defines only LF (U+000A), CR (U+000D), and CRLF as line terminators. However, the AgentCore Gateway's SSE parser also treats U+2028 (LINE SEPARATOR) and U+2029 (PARAGRAPH SEPARATOR) as line breaks — likely because these are line terminators in the [ECMAScript specification](https://tc39.es/ecma262/#sec-line-terminators).

When a tool response contained U+2028 in the SSE `data:` field, the parser split the event mid-JSON, corrupting the response. The Gateway returned a generic "Tool invocation failed" error with no indication of what actually failed.

The character came from a source markdown file in the docs repo, was ingested into the Knowledge Base as-is, and passed through the retrieval → formatting → SSE response chain.

```python
# The invisible character in the KB chunk:
"...provided by the web platform and the community.\u2028 It emphasizes..."

# Fix: sanitize before returning
def sanitize_for_sse(text: str) -> str:
    """Replace characters that break SSE event framing with spaces."""
    return text.translate(_SSE_SANITIZE_TABLE)
```

## Known issue across ecosystems

This is a well-documented cross-ecosystem problem. U+2028/U+2029 are valid in JSON strings but were historically treated as line terminators in JavaScript (fixed in ES2019 via the [JSON superset proposal](https://github.com/tc39/proposal-json-superset)). Multiple projects have encountered this:

- [Express.js #1132](https://github.com/expressjs/express/issues/1132) — JSONP responses broken by U+2028/U+2029
- [Node.js #8221](https://github.com/nodejs/node-v0.x-archive/issues/8221) — `JSON.stringify` doesn't escape these characters
- [browserify #1086](https://github.com/browserify/browserify/issues/1086) — JSON `require()` fails with U+2028
- [Marzipano #167](https://github.com/google/marzipano/issues/167) — tool emits invalid JS when input contains U+2028

The standard fix across all these projects is the same: escape or replace U+2028/U+2029 before embedding JSON in contexts where JavaScript line terminator semantics apply.

## Why we missed it initially

1. **No error detail from the Gateway.** The Gateway returned a generic "Tool invocation failed" with no indication of what failed. No mention of SSE parsing, no character position, no partial response.
2. **CloudWatch logs showed no error.** The Runtime logged `202 Accepted` (SSE stream started) but no Python exception — the tool function completed successfully. The failure happened in the SSE transport layer between Runtime and Gateway, invisible to both.
3. **Tested the wrong variable first.** We assumed response size was the differentiator because short queries worked and long queries failed. But it was the content, not the length.
4. **Didn't write test cases early enough.** We spent time reading CDK code, checking IAM policies, and searching changelogs before writing a single test. The parametrized test that compared multiple queries would have found the pattern in minutes.

## The debugging approach that worked

1. **Write test cases for your hypotheses.** The parametrized `test_oryx_queries` test with 7 different queries immediately showed the pattern: 3 fail, 4 pass, all correlated with which KB chunk was returned.
2. **Check the data, not the infrastructure.** Once we compared the actual content of failing vs passing results, the U+2028 character was obvious.
3. **Make it repeatable.** The `test_gateway_debug.py` file captures every hypothesis as a runnable test. Future regressions will be caught automatically.

## The fix

Extracted a `sanitize_for_sse()` function that strips characters known to break SSE parsers:

| Character(s) | Why stripped |
|---|---|
| U+0000 (NULL) | Can truncate strings in C-based parsers |
| U+0001–U+0008, U+000B, U+000C, U+000E–U+001F | C0 control characters — no business in readable text |
| U+2028 (LINE SEPARATOR) | **Confirmed root cause** — breaks AgentCore Gateway SSE parser |
| U+2029 (PARAGRAPH SEPARATOR) | Same risk as U+2028 per ECMAScript spec |

Characters deliberately preserved: tab (U+0009), LF (U+000A), CR (U+000D) — these are meaningful in text and handled correctly by SSE.

Uses `str.maketrans()` for O(n) single-pass performance.

## Action items

- [x] Strip U+2028 and U+2029 in `format_results()` before returning
- [x] Generalize to `sanitize_for_sse()` covering all risky characters
- [x] Add unit tests for the fix (3 tests for U+2028/U+2029, 11 tests for `sanitize_for_sse`)
- [x] Add gateway debug e2e tests (18 tests covering all hypotheses)
- [x] Deploy and verify all tests pass
- [x] Add gotcha to steering docs
- [x] Research known issues across ecosystems
- [ ] Add preprocessing step in sync pipeline to strip U+2028/U+2029 from source docs before uploading to S3
- [ ] Consider filing a bug report with the AgentCore Gateway team — their SSE parser should follow the W3C spec

## Open questions

- **Is this a Gateway bug or intended behavior?** The W3C SSE spec only defines LF/CR/CRLF as line terminators. U+2028/U+2029 should be valid in `data:` fields. The Gateway's parser appears to use JavaScript line terminator semantics instead of SSE semantics.
- **Should FastMCP sanitize SSE event data?** FastMCP's Streamable HTTP transport sends tool results as SSE events. It could escape U+2028/U+2029 in the `data:` field to protect against non-compliant parsers. Worth filing as a [FastMCP issue](https://github.com/jlowin/fastmcp/issues).

## Lessons learned

1. **Write tests before theorizing.** A parametrized test across multiple queries found the pattern faster than reading source code, checking IAM policies, or searching changelogs.
2. **When the error is content-dependent, compare the content.** "Works for some queries, fails for others" means the differentiator is in the data, not the infrastructure.
3. **Invisible characters cause visible failures.** U+2028 looks like whitespace in most editors and terminals. Always check for non-ASCII characters when debugging content-dependent failures.
4. **Generic error messages are the real enemy.** The Gateway's "Tool invocation failed" message cost us most of the debugging time. If it had said "SSE parse error at byte offset N" we'd have found it in seconds.
5. **COE-001's lesson still applies: logs first.** But this time the logs were clean — the failure was in the transport layer between two services, invisible to both. When logs are clean but the tool fails, the problem is in the wire format.
