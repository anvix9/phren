# Tessera Trust Model

## Overview

The trust model is how Tessera decides what an agent is allowed to do. Instead of agents self-declaring their permissions (the vulnerability in v0.1), trust is **derived from cryptographically verified credentials**.

## How It Works

```
Operator (e.g. Anthropic)          Terminal Owner
        │                                │
        │ 1. Generate Ed25519 keypair    │
        │ 2. Share public key ──────────>│ 3. Register in operator registry
        │                                │    (operator_id, public_key, max_tier)
        │                                │
        │ 4. Sign agent credential       │
        │    (JWT with claims)           │
        │                                │
     Agent                            Terminal
        │                                │
        │ 5. Connect with signed ───────>│ 6. Look up operator in registry
        │    credential                  │ 7. Verify Ed25519 signature
        │                                │ 8. Check expiration
        │                                │ 9. Cap tier at operator's max
        │                                │ 10. Derive permissions
        │<────── session + permissions ──│
```

No step in this flow allows the agent to choose its own trust level.

## Components

### 1. Operator Registry (`tessera/contract/operator_registry.py`)

Stores known agent operators with their public keys and trust tier ceilings.

**Key concepts:**
- **Operator**: An organization that runs AI agents (e.g., Anthropic, a SaaS company, an enterprise)
- **Public key**: Ed25519 — the terminal uses this to verify tokens signed by the operator
- **Max tier**: The highest trust level this operator can issue. Even if the operator signs a token claiming `super_agent`, the terminal caps it at their registered max
- **Revocation**: Immediate. Once revoked, all tokens from that operator are rejected

**Registry file format** (`operators.json`):
```json
{
  "version": "1.0",
  "operators": [
    {
      "operator_id": "anthropic",
      "name": "Anthropic",
      "public_key_b64": "base64-encoded-ed25519-public-key",
      "max_tier": "verified",
      "contact": "security@anthropic.com",
      "revoked": false
    }
  ]
}
```

### 2. Agent Credential (`tessera/contract/credentials.py`)

A signed JWT that an agent presents when connecting to a terminal.

**Token claims:**
```json
{
  "iss": "anthropic",
  "sub": "claude-shopping-agent",
  "tier": "verified",
  "purpose": "purchase",
  "cap": {
    "can_transact": true,
    "max_transaction": 500.0,
    "max_daily_spend": 2000.0
  },
  "iat": 1727280000,
  "exp": 1727366400
}
```

**Fields:**
| Claim | Description |
|-------|-------------|
| `iss` | Operator ID (must match a registered operator) |
| `sub` | Agent name/identifier |
| `tier` | Requested trust tier (capped at operator's max) |
| `purpose` | What the agent intends to do |
| `cap` | Capabilities the operator authorizes |
| `iat` | Issued-at timestamp |
| `exp` | Expiration timestamp |

**Signing:** The operator signs the token with their Ed25519 private key. The terminal verifies with the registered public key. No shared secrets.

### 3. Credential Verifier (`tessera/contract/credentials.py`)

Verifies a credential and returns the derived trust tier.

**Verification steps:**
1. Decode the JWT header to get the operator ID (`iss`)
2. Look up the operator in the registry
3. Verify the Ed25519 signature with the operator's public key
4. Check that the token is not expired
5. Cap the requested tier at the operator's registered max
6. Return the verified agent profile with derived trust

**Failure modes:**
| Condition | Result |
|-----------|--------|
| Unknown operator (`iss` not in registry) | Rejected |
| Revoked operator | Rejected |
| Invalid signature (forged/tampered token) | Rejected |
| Expired token | Rejected |
| Tier exceeds operator's max | Capped to operator's max |
| No credential provided | Falls back to `anonymous` |

### 4. ConnectRequest (updated)

**Before (vulnerable):**
```python
class ConnectRequest(BaseModel):
    provider: str
    agent_name: str
    trust_level: str = "identified"    # ← AGENT CHOOSES THIS
    can_transact: bool = False         # ← AGENT CHOOSES THIS
```

**After (secure):**
```python
class ConnectRequest(BaseModel):
    credential: Optional[str] = None   # Signed JWT from operator
```

The `trust_level` and `can_transact` fields are deleted. Trust is derived from the credential signature, not from the request body.

## Security Properties

1. **No self-declaration**: An agent cannot choose its own trust tier
2. **Cryptographic verification**: Trust is derived from a signature the agent cannot forge
3. **Tier ceiling**: Even a valid credential cannot exceed the operator's registered max
4. **Immediate revocation**: Revoking an operator kills all their agent sessions instantly
5. **Expiration**: Credentials have a TTL; agents must refresh periodically
6. **Audit trail**: Every connection logs the operator, credential hash, derived tier, and verification result

## Interoperability Note

This trust model is deliberately simple and self-contained. The build plan (Phase 1, item 4) calls for interop with emerging standards:

- **IETF `draft-klrc-aiagent-auth`**: Agent authentication draft
- **W3C Agent Identity Registry**: Conventions for agent identity
- **OAuth 2.1 / DCR / OIDC Federation**: Standard auth flows
- **Visa Trusted Agent Protocol**: Payment-specific trust

These are adapter paths in `/interop/`, not replacements. Tessera owns the policy layer *above* whatever identity standard wins.

## MCP Server (Phase 3)

The trust model is enforced through a spec-compliant MCP server using the official MCP Python SDK (v2.2.0).

### Transports

| Transport | Use case | How to run |
|-----------|----------|------------|
| **Streamable HTTP** | Web-accessible terminal | `tessera serve --transport http --port 8001` |
| **stdio** | Claude Desktop, local MCP clients | `tessera serve --transport stdio` |

### Protocol

The server implements JSON-RPC 2.0 over MCP with 5 tools:

| Tool | Description | Auth required |
|------|-------------|---------------|
| `tessera_connect` | Start a session with a signed credential | No (anonymous allowed) |
| `tessera_get_screen` | View current screen + available actions | Session |
| `tessera_execute` | Execute an action on the current screen | Session + trust tier |
| `tessera_audit_log` | View the full session audit log | Session |
| `tessera_disconnect` | End the session | Session |

### Credential Flow Through MCP

```
MCP Client                    Tessera MCP Server
    │                              │
    │ tools/call                   │
    │ { tessera_connect,           │
    │   credential: "eyJ..." }     │
    │ ──────────────────────────>  │
    │                              │ 1. Decode JWT → get operator_id
    │                              │ 2. Look up operator in registry
    │                              │ 3. Verify Ed25519 signature
    │                              │ 4. Check expiration
    │                              │ 5. Cap tier at operator's max
    │                              │ 6. Create session with derived trust
    │  <──────────────────────── │
    │ { session_id, permissions }  │
```

The old REST server is preserved as `mcp/server_legacy.py` for reference.
