# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Tessera, please report it responsibly:

- Email: security@tessera.dev (or open a private GitHub security advisory)
- Do not open a public issue for security vulnerabilities
- We will acknowledge receipt within 48 hours

## Scope

Tessera is a governance layer between AI agents and websites. Security issues in the following areas are in scope:

- Trust tier escalation (agent gaining permissions it shouldn't have)
- Contract enforcement bypass (agent exceeding rate limits, spending caps, or action restrictions)
- Session hijacking or token forgery
- Credential replay across terminals
- Injection through terminal actions that reach the underlying website
- Information disclosure through the MCP interface

## Fixed Vulnerabilities

### Trust Tier Self-Declaration (FIXED — Phase 1)

**Issue:** The v0.1 MCP server accepted `trust_level` and `can_transact` as caller-supplied fields. Any agent could self-declare `trust_level=super_agent`.

**Fix:** The fields were deleted from the request model entirely. Trust is now derived from Ed25519-signed credentials verified against an operator registry. Regression tests prove forged trust payloads are rejected.

### Governance Fields Not Enforced (FIXED — Phase 2)

**Issue:** Contract fields like `max_transaction_amount`, `max_daily_spend`, `requests_per_hour` were declared but never checked at runtime.

**Fix:** All fields are now enforced via `tessera/contract/enforcement.py`. Every field has a unit test and an integration test proving it binds.

### Operator-Declared Money Limits (FIXED — Audit)

**Issue:** The operator could sign a credential with `max_transaction=999999` and the terminal honoured it. The contract's own limits were never read as bounds. Same defect class as the original trust_level bug.

**Fix:** The resolver now takes `min(operator_claim, contract_ceiling)`. Contract validation rejects contracts that permit transactions but omit `max_transaction_amount`. Integration tests prove the contract's number governs, not the operator's.

### Credential Hygiene (FIXED — Audit)

**Issue:** No audience claim, 24-hour TTL default, no replay protection.

**Fix:**
- `aud` claim verified when terminal specifies `expected_audience`
- TTL hard-capped at 24 hours, default reduced to 1 hour
- `jti` nonce tracked; same token rejected on second use
- Periodic cleanup of expired jti entries

## Contract Validation

`tessera/contract/validation.py` catches dangerous omissions before a contract goes live:

| Check | Severity | Description |
|-------|----------|-------------|
| Missing `max_transaction_amount` on transactable contracts | Error | Prevents fail-open on forgotten spend limits |
| Missing `max_daily_spend` | Warning | Advisory; cumulative spend ceiling recommended |
| No rate limits configured | Warning | At least `requests_per_minute` recommended |
| Missing `contract_id`, `site_name`, `site_url` | Error | Identity fields required |

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | Yes (current) |
| < 0.2   | No |

## OWASP LLM Top 10 Mapping

| OWASP ID | Risk | Tessera Status |
|----------|------|----------------|
| LLM01 | Prompt Injection | Terminal actions pass through to real APIs — parameter validation is contract-bound |
| LLM06 | Excessive Agency | Fixed. Trust tier self-declaration removed; credential-based, regression-tested |
| LLM07 | Insecure Plugin Design | MCP tools validate all parameters against the contract before execution |
