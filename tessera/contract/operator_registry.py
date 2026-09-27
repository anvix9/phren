"""
Tessera Operator Registry

The trust root for the Tessera terminal. Stores known agent operators
with their public keys and the maximum trust tier they can issue.

How it works:
  1. An operator generates an Ed25519 key pair
  2. They register their public key with the terminal owner
  3. The terminal stores it in the registry (a JSON file)
  4. When an agent connects, it presents a token signed by the operator
  5. The terminal looks up the operator, verifies the signature,
     and derives the trust tier (capped at the operator's max tier)

The registry is deliberately simple — a JSON file on disk.
The commercial layer (tessera-cloud) replaces this with a hosted
registry that adds revocation, audit trails, and federation.
But the interface is the same.

Key design rules:
  - The terminal owner controls who is in the registry
  - An operator cannot grant a tier higher than their registered max
  - Revoked operators are rejected immediately
  - Unknown operators are rejected (no default trust)
"""
import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PublicFormat,
    PrivateFormat,
)
from pydantic import BaseModel, Field
import base64


class OperatorTier(str, Enum):
    """Maximum trust tier an operator can issue to their agents."""
    IDENTIFIED = "identified"
    VERIFIED = "verified"
    SUPER_AGENT = "super_agent"


class OperatorRecord(BaseModel):
    """A registered operator in the trust registry."""
    operator_id: str                          # Unique slug: "anthropic", "acme-corp"
    name: str                                 # Display name: "Anthropic"
    public_key_b64: str                       # Ed25519 public key, base64-encoded
    max_tier: OperatorTier                    # Highest trust tier they can issue
    contact: Optional[str] = None             # Contact email or URL
    registered_at: str = ""                   # ISO timestamp
    revoked: bool = False                     # If True, all tokens from this operator are rejected
    revoked_at: Optional[str] = None
    notes: Optional[str] = None               # Terminal owner's notes


class OperatorRegistry:
    """
    File-backed registry of known agent operators.

    Usage:
        registry = OperatorRegistry("/path/to/operators.json")

        # Register an operator
        registry.register(
            operator_id="anthropic",
            name="Anthropic",
            public_key=public_key,        # Ed25519PublicKey object
            max_tier=OperatorTier.VERIFIED,
        )

        # Look up an operator
        record = registry.get("anthropic")

        # Verify an operator is trusted
        pub_key = registry.get_verified_key("anthropic")
        # Returns the public key if operator exists, is not revoked, else None
    """

    def __init__(self, registry_path: str):
        self.path = Path(registry_path)
        self._operators: dict[str, OperatorRecord] = {}
        if self.path.exists():
            self._load()

    def _load(self):
        """Load operators from the JSON file."""
        with open(self.path) as f:
            data = json.load(f)
        for op_data in data.get("operators", []):
            record = OperatorRecord(**op_data)
            self._operators[record.operator_id] = record

    def _save(self):
        """Persist operators to the JSON file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "operators": [op.model_dump() for op in self._operators.values()],
        }
        with open(self.path, "w") as f:
            json.dump(data, f, indent=2)

    def register(
        self,
        operator_id: str,
        name: str,
        public_key: Ed25519PublicKey,
        max_tier: OperatorTier,
        contact: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> OperatorRecord:
        """
        Register a new operator or update an existing one.
        Only the terminal owner calls this (not agents).
        """
        pub_bytes = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        pub_b64 = base64.b64encode(pub_bytes).decode()

        record = OperatorRecord(
            operator_id=operator_id,
            name=name,
            public_key_b64=pub_b64,
            max_tier=max_tier,
            contact=contact,
            registered_at=datetime.now(timezone.utc).isoformat(),
            notes=notes,
        )
        self._operators[operator_id] = record
        self._save()
        return record

    def get(self, operator_id: str) -> Optional[OperatorRecord]:
        """Get an operator record, or None if not found."""
        return self._operators.get(operator_id)

    def get_verified_key(self, operator_id: str) -> Optional[Ed25519PublicKey]:
        """
        Get the public key for a verified (non-revoked) operator.
        Returns None if operator is unknown, revoked, or has no key.
        This is the main method used during token verification.
        """
        record = self._operators.get(operator_id)
        if not record:
            return None
        if record.revoked:
            return None

        pub_bytes = base64.b64decode(record.public_key_b64)
        return Ed25519PublicKey.from_public_bytes(pub_bytes)

    def get_max_tier(self, operator_id: str) -> Optional[OperatorTier]:
        """Get the maximum trust tier this operator can issue."""
        record = self._operators.get(operator_id)
        if not record or record.revoked:
            return None
        return record.max_tier

    def revoke(self, operator_id: str, reason: str = "") -> bool:
        """
        Revoke an operator. All their tokens will be rejected immediately.
        Returns True if the operator was found and revoked.
        """
        record = self._operators.get(operator_id)
        if not record:
            return False
        record.revoked = True
        record.revoked_at = datetime.now(timezone.utc).isoformat()
        record.notes = f"Revoked: {reason}" if reason else record.notes
        self._save()
        return True

    def list_operators(self, include_revoked: bool = False) -> list[OperatorRecord]:
        """List all registered operators."""
        ops = list(self._operators.values())
        if not include_revoked:
            ops = [o for o in ops if not o.revoked]
        return ops

    def remove(self, operator_id: str) -> bool:
        """Permanently remove an operator from the registry."""
        if operator_id in self._operators:
            del self._operators[operator_id]
            self._save()
            return True
        return False


# ── Key Generation Utility ──

def generate_operator_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """
    Generate a new Ed25519 key pair for an operator.

    The operator keeps the private key (to sign agent tokens).
    The public key is registered with the terminal.

    Returns:
        (private_key, public_key)
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key


def export_private_key_b64(private_key: Ed25519PrivateKey) -> str:
    """Export private key as base64 string (for operator to store securely)."""
    raw = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.b64encode(raw).decode()


def export_public_key_b64(public_key: Ed25519PublicKey) -> str:
    """Export public key as base64 string (for registration)."""
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode()
