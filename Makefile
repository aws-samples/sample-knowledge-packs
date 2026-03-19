# Use finch as Docker runtime for CDK container builds (macOS)
# Falls back to docker if finch is not available
CDK_DOCKER := $(shell which finch 2>/dev/null || echo docker)
export CDK_DOCKER

# Use the venv Python if available, otherwise fall back to python3.
# The venv is at .venv/ per project convention.
PYTHON := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)

# Load .env if it exists (local config, gitignored)
-include .env
export

REGION := eu-west-1

.PHONY: install test test-unit test-e2e lint validate mcp-server \
        cdk-synth cdk-deploy cdk-destroy sync clean cost-estimate \
        deploy-mcp teardown-mcp deploy-domain destroy-domain \
        deploy-all destroy-all check e2e \
        status logs test-runtime ops-check ops-cleanup ops-cleanup-force \
        build-power security-install security-scan security-scan-container \
        publish

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

install:
	pip install -r requirements.txt
	cd infra && npm install

# ---------------------------------------------------------------------------
# Quality gates (no infra required)
# ---------------------------------------------------------------------------

lint:
	$(PYTHON) -m ruff check scripts/ tests/

cdk-synth:
	cd infra && npx cdk synth --quiet

## Run unit + property-based tests only (no AWS credentials needed)
test-unit:
	$(PYTHON) -m pytest tests/ -v --ignore=tests/test_e2e.py

## Run all tests including e2e (requires deployed infra)
test:
	$(PYTHON) -m pytest tests/ -v

## Pre-deploy validation: lint + unit tests + CDK synth
check: lint test-unit cdk-synth
	@echo "All checks passed — safe to deploy."

# ---------------------------------------------------------------------------
# E2E tests (requires deployed KB stack)
# ---------------------------------------------------------------------------

## Run e2e tests only, fail loudly if KB is not deployed
e2e:
	@KB_ID=$$(aws cloudformation describe-stacks \
		--stack-name KnowledgePackStack \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseId'].OutputValue" \
		--output text 2>/dev/null) && \
	test -n "$$KB_ID" || (echo "ERROR: KnowledgePackStack not deployed — cannot run e2e tests" && exit 1) && \
	echo "Running e2e tests against KB $$KB_ID..." && \
	KNOWLEDGE_BASE_ID=$$KB_ID $(PYTHON) -m pytest tests/test_e2e.py -v

# ---------------------------------------------------------------------------
# Validation (requires deployed KB + ingested data)
# ---------------------------------------------------------------------------

validate:
	@KB_ID=$$(aws cloudformation describe-stacks \
		--stack-name KnowledgePackStack \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseId'].OutputValue" \
		--output text) && \
	DS_ID=$$(aws cloudformation describe-stacks \
		--stack-name KnowledgePackStack \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='DataSourceId'].OutputValue" \
		--output text) && \
	$(PYTHON) -m scripts.validate_kb --kb-id "$$KB_ID" --ds-id "$$DS_ID"

# ---------------------------------------------------------------------------
# Data sync (requires deployed KB stack)
# ---------------------------------------------------------------------------

sync:
ifndef REPO_URL
	$(error REPO_URL is required. Usage: make sync REPO_URL=https://github.com/org/docs.git)
endif
	@echo "Reading CDK stack outputs..."
	@BUCKET=$$(aws cloudformation describe-stacks \
		--stack-name KnowledgePackStack \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" \
		--output text) && \
	KB_ID=$$(aws cloudformation describe-stacks \
		--stack-name KnowledgePackStack \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseId'].OutputValue" \
		--output text) && \
	DS_ID=$$(aws cloudformation describe-stacks \
		--stack-name KnowledgePackStack \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='DataSourceId'].OutputValue" \
		--output text) && \
	echo "Bucket=$$BUCKET KB=$$KB_ID DS=$$DS_ID" && \
	$(PYTHON) scripts/sync_docs.py \
		--repo-url "$(REPO_URL)" \
		--bucket "$$BUCKET" \
		--kb-id "$$KB_ID" \
		--ds-id "$$DS_ID"

# ---------------------------------------------------------------------------
# Individual stack deploy/destroy
# ---------------------------------------------------------------------------

cdk-deploy:
	cd infra && npx cdk deploy KnowledgePackStack --require-approval never

cdk-destroy:
	cd infra && npx cdk destroy KnowledgePackStack --force

deploy-mcp:
	@echo "Reading KnowledgeBaseId from KnowledgePackStack outputs..."
	$(eval KB_ID := $(shell aws cloudformation describe-stacks \
		--stack-name KnowledgePackStack \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='KnowledgeBaseId'].OutputValue" \
		--output text 2>/dev/null))
	@test -n "$(KB_ID)" || (echo "ERROR: Could not read KnowledgeBaseId from KnowledgePackStack. Deploy it first: make cdk-deploy" && exit 1)
	@echo "KnowledgeBaseId: $(KB_ID)"
	@echo "Constructing KB allowlist: {\"example\":\"$(KB_ID)\"}"
	cd infra && CDK_DOCKER=$(CDK_DOCKER) npx cdk deploy GatewayMcpStack --require-approval never \
		-c 'kbAllowlist={"example":"$(KB_ID)"}' \
		-c defaultKb=example \
		-c 'kbDescriptions={"example":"Spryker architecture (Yves, Zed, Glue), Glue API development, ACP integrations, Spryker Cloud Commerce OS deployment, Marketplace, PBCs, Oryx frontend, Back Office customization"}'

teardown-mcp:
	cd infra && npx cdk destroy GatewayMcpStack --force \
		-c 'kbAllowlist={"placeholder":"PLACEHOLDER"}' \
		-c defaultKb=placeholder

deploy-domain:
	@echo "Reading GatewayEndpoint from GatewayMcpStack outputs..."
	$(eval GW_EP := $(shell aws cloudformation describe-stacks \
		--stack-name GatewayMcpStack \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='GatewayEndpoint'].OutputValue" \
		--output text 2>/dev/null))
	@test -n "$(GW_EP)" || (echo "ERROR: Could not read GatewayEndpoint from GatewayMcpStack. Deploy it first: make deploy-mcp" && exit 1)
	@echo "GatewayEndpoint: $(GW_EP)"
	cd infra && npx cdk deploy GatewayDomainStack --require-approval never -c gatewayEndpoint=$(GW_EP)

destroy-domain:
	cd infra && npx cdk destroy GatewayDomainStack --force -c gatewayEndpoint=placeholder

# ---------------------------------------------------------------------------
# Full lifecycle: deploy all stacks in dependency order, e2e test, then done
# ---------------------------------------------------------------------------

## Deploy everything: KB → sync → MCP gateway → domain, then e2e test
deploy-all: check cdk-deploy sync deploy-mcp deploy-domain
	@echo ""
	@echo "All stacks deployed. Running e2e tests..."
	@$(MAKE) e2e
	@echo ""
	@echo "Deploy-all complete. All e2e tests passed."

## Destroy everything in reverse dependency order
destroy-all:
	@echo "Destroying all stacks in reverse dependency order..."
	@echo "--- Step 1/3: GatewayDomainStack (CloudFront + WAF) ---"
	-$(MAKE) destroy-domain
	@echo "--- Step 2/3: GatewayMcpStack (Gateway + Runtime + Cognito) ---"
	-$(MAKE) teardown-mcp
	@echo "--- Step 3/3: KnowledgePackStack (KB + S3 + S3 Vectors) ---"
	-$(MAKE) cdk-destroy
	@echo ""
	@echo "All stacks destroyed."

# ---------------------------------------------------------------------------
# Local MCP server
# ---------------------------------------------------------------------------

mcp-server:
	$(PYTHON) scripts/kb_server.py

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

cost-estimate:
	$(PYTHON) scripts/cost_estimate.py

## Build the distributable Kiro Power from .kiro/powers/spryker-docs/
build-power:
	@echo "Building dist/spryker-docs/ from .kiro/powers/spryker-docs/..."
	@mkdir -p dist/spryker-docs
	@cp .kiro/powers/spryker-docs/POWER.md dist/spryker-docs/POWER.md
	@cp .kiro/powers/spryker-docs/mcp.json dist/spryker-docs/mcp.json
	@echo "dist/spryker-docs/ updated."

## Full status of all deployed resources
status:
	$(PYTHON) scripts/ops.py status

## Tail AgentCore Runtime CloudWatch logs (most recent)
logs:
	@RT_ID=$$(aws cloudformation describe-stacks \
		--stack-name GatewayMcpStack \
		--region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='RuntimeArn'].OutputValue" \
		--output text 2>/dev/null | sed 's|.*runtime/||') && \
	test -n "$$RT_ID" || (echo "ERROR: GatewayMcpStack not deployed" && exit 1) && \
	LOG_GROUP="/aws/bedrock-agentcore/runtimes/$${RT_ID}-DEFAULT" && \
	echo "Log group: $$LOG_GROUP" && \
	STREAM=$$(aws logs describe-log-streams \
		--log-group-name "$$LOG_GROUP" \
		--region $(REGION) \
		--order-by LastEventTime --descending --limit 1 \
		--query 'logStreams[0].logStreamName' --output text 2>/dev/null) && \
	test -n "$$STREAM" && test "$$STREAM" != "None" || (echo "No log streams found" && exit 1) && \
	echo "Stream: $$STREAM" && echo "---" && \
	aws logs get-log-events \
		--log-group-name "$$LOG_GROUP" \
		--log-stream-name "$$STREAM" \
		--region $(REGION) \
		--limit 50 \
		--query 'events[*].message' --output text

## Pre-deploy health check (orphaned resources, broken stacks)
ops-check:
	$(PYTHON) scripts/ops.py check

## Test the deployed Runtime directly (gets Cognito token, calls tools/list)
test-runtime:
	@echo "Getting Cognito credentials..."
	@POOL_ID=$$(aws cloudformation describe-stacks \
		--stack-name GatewayMcpStack --region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue" --output text) && \
	CLIENT_ID=$$(aws cloudformation describe-stacks \
		--stack-name GatewayMcpStack --region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='CognitoClientId'].OutputValue" --output text) && \
	TOKEN_URL=$$(aws cloudformation describe-stacks \
		--stack-name GatewayMcpStack --region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='CognitoTokenUrl'].OutputValue" --output text) && \
	RUNTIME_ARN=$$(aws cloudformation describe-stacks \
		--stack-name GatewayMcpStack --region $(REGION) \
		--query "Stacks[0].Outputs[?OutputKey=='RuntimeArn'].OutputValue" --output text) && \
	CLIENT_SECRET=$$(aws cognito-idp describe-user-pool-client \
		--user-pool-id "$$POOL_ID" --client-id "$$CLIENT_ID" --region $(REGION) \
		--query 'UserPoolClient.ClientSecret' --output text) && \
	echo "Getting OAuth token..." && \
	ACCESS_TOKEN=$$(curl -s -X POST "$$TOKEN_URL" \
		-H "Content-Type: application/x-www-form-urlencoded" \
		-u "$$CLIENT_ID:$$CLIENT_SECRET" \
		-d "grant_type=client_credentials&scope=mcp-runtime/invoke" \
		| python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])") && \
	ENCODED_ARN=$$(python3 -c "import urllib.parse; print(urllib.parse.quote('$$RUNTIME_ARN', safe=''))") && \
	echo "Calling Runtime tools/list..." && \
	RESPONSE=$$(curl -s -X POST \
		"https://bedrock-agentcore.$(REGION).amazonaws.com/runtimes/$${ENCODED_ARN}/invocations" \
		-H "Authorization: Bearer $$ACCESS_TOKEN" \
		-H "Content-Type: application/json" \
		-H "Accept: application/json, text/event-stream" \
		-d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}') && \
	echo "$$RESPONSE" | head -c 500 && echo "" && \
	echo "$$RESPONSE" | python3 -c "import sys; data=sys.stdin.read(); exit(0 if 'query_docs' in data else 1)" && \
	echo "✅ Runtime is serving query_docs tool" || \
	(echo "❌ Runtime is NOT serving query_docs — check: make logs" && exit 1)

## Find orphaned resources (dry run)
ops-cleanup:
	$(PYTHON) scripts/ops.py cleanup

## Delete orphaned resources (destructive)
ops-cleanup-force:
	$(PYTHON) scripts/ops.py cleanup --force

# ---------------------------------------------------------------------------
# Security scanning (PCSR evidence)
# ---------------------------------------------------------------------------

SCAN_DIR := security-reports

## Install security scanning tools into the venv
security-install:
	$(PYTHON) -m pip install -q bandit pip-audit detect-secrets pip-licenses
	@echo "Security tools installed."

## Run all security scanners and write reports to security-reports/
security-scan: $(SCAN_DIR)/bandit.txt $(SCAN_DIR)/pip-audit.json $(SCAN_DIR)/detect-secrets.json \
               $(SCAN_DIR)/pip-licenses.json $(SCAN_DIR)/npm-audit-infra.txt \
               $(SCAN_DIR)/npm-audit-custom-resources.txt $(SCAN_DIR)/cdk-nag.txt
	@echo ""
	@echo "=== Security Scan Summary ==="
	@echo "Bandit:          $(SCAN_DIR)/bandit.txt"
	@echo "pip-audit:       $(SCAN_DIR)/pip-audit.json"
	@echo "detect-secrets:  $(SCAN_DIR)/detect-secrets.json"
	@echo "pip-licenses:    $(SCAN_DIR)/pip-licenses.json"
	@echo "npm-audit:       $(SCAN_DIR)/npm-audit-infra.txt, $(SCAN_DIR)/npm-audit-custom-resources.txt"
	@echo "cdk-nag:         $(SCAN_DIR)/cdk-nag.txt"
	@echo ""
	@echo "Optional (requires Docker): make security-scan-container"
	@echo "All reports in $(SCAN_DIR)/ — attach to PCSR ticket."

$(SCAN_DIR)/bandit.txt: scripts/*.py
	@mkdir -p $(SCAN_DIR)
	$(PYTHON) -m pip install -q bandit 2>/dev/null
	$(PYTHON) -m bandit -r scripts/ -f txt -o $@ 2>&1 || true
	@echo "✓ Bandit → $@"

$(SCAN_DIR)/pip-audit.json: requirements.txt
	@mkdir -p $(SCAN_DIR)
	$(PYTHON) -m pip_audit -r requirements.txt --format json --output $@ 2>&1 || true
	@echo "✓ pip-audit → $@"

$(SCAN_DIR)/detect-secrets.json:
	@mkdir -p $(SCAN_DIR)
	$(PYTHON) -m detect_secrets.main scan > $@ 2>&1
	@echo "✓ detect-secrets → $@"

$(SCAN_DIR)/pip-licenses.json:
	@mkdir -p $(SCAN_DIR)
	$(PYTHON) -m piplicenses --format json --output-file $@ 2>&1
	@echo "✓ pip-licenses → $@"

$(SCAN_DIR)/npm-audit-infra.txt: infra/package-lock.json
	@mkdir -p $(SCAN_DIR)
	cd infra && npm audit 2>&1 > ../$(SCAN_DIR)/npm-audit-infra.txt || true
	@echo "✓ npm audit (infra) → $@"

$(SCAN_DIR)/npm-audit-custom-resources.txt: infra/lib/custom-resources/package-lock.json
	@mkdir -p $(SCAN_DIR)
	cd infra/lib/custom-resources && npm audit 2>&1 > ../../../$(SCAN_DIR)/npm-audit-custom-resources.txt || true
	@echo "✓ npm audit (custom-resources) → $@"

$(SCAN_DIR)/cdk-nag.txt:
	@mkdir -p $(SCAN_DIR)
	cd infra && npx cdk synth --quiet 2>&1 > ../$(SCAN_DIR)/cdk-nag.txt || true
	@echo "✓ cdk-nag → $@"

## Container image scan (requires Docker/finch + trivy)
security-scan-container:
	@mkdir -p $(SCAN_DIR)
	$(CDK_DOCKER) build -t knowledge-packs-scan . && \
	trivy image --format table knowledge-packs-scan > $(SCAN_DIR)/trivy-container.txt 2>&1 || true
	@echo "✓ Trivy container → $(SCAN_DIR)/trivy-container.txt"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf infra/dist infra/node_modules

# ---------------------------------------------------------------------------
# Publish to GitHub (aws-samples)
# ---------------------------------------------------------------------------
# Pushes a sanitized copy to the public GitHub repo.
# Internal files (steering, specs, settings, research) are excluded.
# GitLab (origin) remains the complete internal repo.

GITHUB_REMOTE := git@github.com:aws-samples/sample-knowledge-packs.git
GITHUB_BRANCH := main

# Files/dirs to exclude from the public repo
PUBLISH_EXCLUDE := \
	.kiro/steering \
	.kiro/settings \
	.kiro/specs \
	research \
	PCSR-RESPONSE.md

publish:
	@echo "Publishing sanitized copy to GitHub..."
	$(eval TMPDIR := $(shell mktemp -d))
	@# Export current tree (no internal history)
	@git archive HEAD | tar -x -C $(TMPDIR)
	@# Remove internal files
	@cd $(TMPDIR) && rm -rf $(PUBLISH_EXCLUDE)
	@# Try to fetch existing GitHub history, or start fresh
	@cd $(TMPDIR) && git init -b $(GITHUB_BRANCH) && \
		git remote add github $(GITHUB_REMOTE) && \
		(git fetch github $(GITHUB_BRANCH) && \
		 git reset --soft github/$(GITHUB_BRANCH) || true) && \
		git add -A && \
		git diff --cached --quiet && echo "Nothing to publish." || \
		(git commit -m "$(or $(MSG),Update from internal repo)" && \
		 git push github $(GITHUB_BRANCH) --force-with-lease)
	@rm -rf $(TMPDIR)
	@echo "✓ Published to $(GITHUB_REMOTE)"
