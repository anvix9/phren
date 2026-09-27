"""
Tessera Agent Credentials

Two sides of the same coin:
  - SIGNING (operator side): The operator signs a JWT with their Ed25519 private key,
    encoding the agent's identity, trust tier, capabilities, and expiration.
  - VERIFICATION (terminal side): The terminal verifies the signature using the
    operator's registered public key, checks expiration, caps the tier at the
    operator's max, and returns a verified agent profile.

Token format (JWT with Ed25519 signature):
  Header:  {"alg": "EdDSA", "typ": "JWT"}
  Payload: {
    "iss": "anthropic",              # operator_id (must match registry)
    "sub": "claude-shopping-agent",  # agent name
    "tier": "verified",              # requested trust tier
    "purpose": "purchase",           # what the agent intends to do
    "cap": {                         # capabilities the operator authorizes
      "can_transact": true,
      "max_transaction": 500.0,
      "max_daily_spend": 2000.0
    },
    "iat": 1727280000,               # issued at
    "exp": 1727366400                # expires at
  }
  Signature: Ed25519(private_key, header + "." + payload)

Why Ed25519 + custom JWT (not PyJWT):
  PyJWT supports EdDSA but requires the `cryptography` backend and
  specific key wrapping. We already depend on `cryptography` for the
  operator registry, so we sign/verify directly — fewer moving parts,
  no key format confusion, and the token format is fully transparent.
"""
import json
import base64
import time
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

from tessera.contract.operator_registry import (
    OperatorRegistry,
    OperatorTier,
)


# ── Token Constants ──

# Map between OperatorTier and AgentTrust string values
TIER_HIERARCHY = {
    "anonymous": 0,
    "identified": 1,
    "verified": 2,
    "super_agent": 3,
}


# ── Signing (Operator Side) ──

def _b64url_encode(data: bytes) -> str:
    """Base64url encode without padding (JWT standard)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    """Base64url decode with padding restoration."""
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def sign_agent_credential(
    private_key: Ed25519PrivateKey,
    operator_id: str,
    agent_name: str,
    tier: str = "identified",
    purpose: str = "general",
    capabilities: Optional[dict] = None,
    ttl_seconds: int = 3600,
    agent_id: Optional[str] = None,
    audience: Optional[str] = None,
) -> str:
    """
    Sign an agent credential (operator side).

    The operator calls this with their private key to create a JWT
    that the agent presents to terminals.

    Args:
        private_key: The operator's Ed25519 private key
        operator_id: Must match the operator's registration in the terminal
        agent_name: Identifier for this agent
        tier: Requested trust tier ("identified", "verified", "super_agent")
        purpose: What the agent intends to do
        capabilities: Dict of capabilities (can_transact, max_transaction, etc.)
        ttl_seconds: Token lifetime in seconds (default 1 hour, max 24 hours)
        agent_id: Optional unique agent instance ID
        audience: Terminal identity this token is bound to (recommended)

    Returns:
        A signed JWT string (header.payload.signature)
    """
    import uuid as _uuid

    # Cap TTL at 24 hours
    ttl_seconds = min(ttl_seconds, 86400)
    now = int(time.time())

    # Header
    header = {"alg": "EdDSA", "typ": "JWT"}

    # Payload
    payload = {
        "iss": operator_id,
        "sub": agent_name,
        "tier": tier,
        "purpose": purpose,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": _uuid.uuid4().hex,  # unique nonce for replay protection
    }
    if audience:
        payload["aud"] = audience
    if capabilities:
        payload["cap"] = capabilities
    if agent_id:
        payload["jti"] = agent_id

    # Encode
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()

    # Sign
    signature = private_key.sign(signing_input)
    sig_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{sig_b64}"


# ── Verification Result ──

@dataclass
class VerifiedCredential:
    """Result of successful credential verification."""
    operator_id: str
    agent_name: str
    derived_tier: str         # The ACTUAL tier after capping at operator's max
    requested_tier: str       # What the token asked for (may be higher)
    purpose: str
    capabilities: dict
    issued_at: int
    expires_at: int
    agent_id: Optional[str]
    was_capped: bool          # True if requested_tier was higher than operator's max


@dataclass
class VerificationError:
    """Result of failed credential verification."""
    code: str                 # Machine-readable error code
    message: str              # Human-readable explanation


# ── Verification (Terminal Side) ──

# Replay protection: track seen jti nonces
_SEEN_JTIS: dict[str, int] = {}  # jti → expiry timestamp
_JTI_CLEANUP_INTERVAL = 1000  # clean up every N verifications
_JTI_VERIFY_COUNT = 0


def _cleanup_jtis():
    """Remove expired jti entries to prevent unbounded growth."""
    now = int(time.time())
    expired = [jti for jti, exp in _SEEN_JTIS.items() if now > exp]
    for jti in expired:
        del _SEEN_JTIS[jti]


def verify_agent_credential(
    token: str,
    registry: OperatorRegistry,
    expected_audience: Optional[str] = None,
) -> VerifiedCredential | VerificationError:
    """
    Verify an agent credential (terminal side).

    Steps:
      1. Decode the JWT without verifying (to read the operator ID)
      2. Look up the operator in the registry
      3. Verify the Ed25519 signature with the operator's public key
      4. Check expiration
      5. Check audience (if expected_audience is set)
      6. Check replay (jti nonce)
      7. Cap the requested tier at the operator's registered max
      8. Return the verified credential or an error

    Args:
        token: The JWT string from the agent
        registry: The operator registry to verify against
        expected_audience: Terminal identity to check against aud claim (optional)

    Returns:
        VerifiedCredential on success, VerificationError on failure
    """
    global _JTI_VERIFY_COUNT
    _JTI_VERIFY_COUNT += 1
    if _JTI_VERIFY_COUNT % _JTI_CLEANUP_INTERVAL == 0:
        _cleanup_jtis()
    # Step 1: Decode without verification
    parts = token.split(".")
    if len(parts) != 3:
        return VerificationError("invalid_format", "Token must have 3 parts (header.payload.signature)")

    try:
        header_b64, payload_b64, sig_b64 = parts
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return VerificationError("decode_error", "Failed to decode token")

    # Check algorithm
    if header.get("alg") != "EdDSA":
        return VerificationError("unsupported_algorithm", f"Expected EdDSA, got {header.get('alg')}")

    # Step 2: Look up operator
    operator_id = payload.get("iss")
    if not operator_id:
        return VerificationError("missing_issuer", "Token has no 'iss' (operator ID) claim")

    public_key = registry.get_verified_key(operator_id)
    if public_key is None:
        # Check if revoked vs unknown
        record = registry.get(operator_id)
        if record and record.revoked:
            return VerificationError("operator_revoked", f"Operator '{operator_id}' has been revoked")
        return VerificationError("unknown_operator", f"Operator '{operator_id}' is not registered")

    # Step 3: Verify signature
    try:
        signing_input = f"{header_b64}.{payload_b64}".encode()
        signature = _b64url_decode(sig_b64)
        public_key.verify(signature, signing_input)
    except InvalidSignature:
        return VerificationError("invalid_signature", "Token signature verification failed")
    except Exception as e:
        return VerificationError("signature_error", f"Signature check error: {e}")

    # Step 4: Check expiration
    now = int(time.time())
    exp = payload.get("exp")
    if exp is not None and now > exp:
        return VerificationError("token_expired", f"Token expired at {exp}, current time is {now}")

    iat = payload.get("iat", now)
    if iat > now + 60:  # Allow 60s clock skew
        return VerificationError("token_not_yet_valid", "Token issued-at is in the future")

    # Step 5: Check audience (if terminal specifies expected audience)
    token_aud = payload.get("aud")
    if expected_audience:
        if not token_aud:
            return VerificationError(
                "missing_audience",
                f"Token has no 'aud' claim but terminal requires audience '{expected_audience}'"
            )
        # aud can be a string or list
        aud_list = token_aud if isinstance(token_aud, list) else [token_aud]
        if expected_audience not in aud_list:
            return VerificationError(
                "audience_mismatch",
                f"Token audience {aud_list} does not include this terminal '{expected_audience}'"
            )

    # Step 6: Replay protection (jti nonce)
    jti = payload.get("jti")
    if jti:
        if jti in _SEEN_JTIS:
            return VerificationError(
                "token_replayed",
                f"Token with jti '{jti[:12]}...' has already been used"
            )
        # Record this jti with its expiry for cleanup
        _SEEN_JTIS[jti] = exp or (iat + 86400)

    # Step 7: Cap tier at operator's max
    requested_tier = payload.get("tier", "identified")
    max_tier = registry.get_max_tier(operator_id)

    requested_level = TIER_HIERARCHY.get(requested_tier, 1)
    max_level = TIER_HIERARCHY.get(max_tier.value if max_tier else "identified", 1)

    if requested_level > max_level:
        derived_tier = max_tier.value
        was_capped = True
    else:
        derived_tier = requested_tier
        was_capped = False

    # Step 6: Return verified credential
    return VerifiedCredential(
        operator_id=operator_id,
        agent_name=payload.get("sub", "unknown"),
        derived_tier=derived_tier,
        requested_tier=requested_tier,
        purpose=payload.get("purpose", "general"),
        capabilities=payload.get("cap", {}),
        issued_at=iat,
        expires_at=exp or (iat + 86400),
        agent_id=payload.get("jti"),
        was_capped=was_capped,
    )
