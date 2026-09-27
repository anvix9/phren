"""
Tessera Phase 1 — Trust Regression Tests (updated for real MCP server)

THE critical test: prove that the self-declared trust vulnerability is fixed.

Before Phase 1:
  {"trust_level": "super_agent", "can_transact": true} → full access

After Phase 1:
  {"credential": "<signed-jwt>"} → trust derived from signature
  No credential → anonymous
"""
import pytest
import json
import os
import asyncio
from pathlib import Path

from tessera.contract.operator_registry import (
    OperatorRegistry, OperatorTier, generate_operator_keypair,
)
from tessera.contract.credentials import sign_agent_credential
from tessera.contract.schema import TesseraContract, AgentTrust
from tessera.terminal.engine import TesseraTerminal
from tessera.mcp.server import (
    create_tessera_mcp, TOOLS,
    _make_call_tool_handler, _make_list_tools_handler,
)


@pytest.fixture
def setup(tmp_path):
    """Create a contract, registry, terminal, and MCP handlers for testing."""
    # Contract
    contract_data = {
        "contract_id": "test-regress-001",
        "site_name": "Regression Test",
        "site_url": "http://localhost:9999",
        "screens": [],
        "require_identification": False,
    }
    contract_path = str(tmp_path / "contract.json")
    with open(contract_path, "w") as f:
        json.dump(contract_data, f)

    # Registry
    reg_path = str(tmp_path / "operators.json")
    registry = OperatorRegistry(reg_path)
    priv_key, pub_key = generate_operator_keypair()
    registry.register("test-op", "Test Operator", pub_key, OperatorTier.VERIFIED)
    evil_priv, _ = generate_operator_keypair()

    # Terminal + handlers
    contract = TesseraContract(**contract_data)
    terminal = TesseraTerminal(contract, "http://localhost:9999")
    call_handler = _make_call_tool_handler(terminal, registry)

    # Also a handler WITHOUT registry (anonymous-only mode)
    call_handler_no_reg = _make_call_tool_handler(terminal, None)

    return {
        "call": call_handler,
        "call_no_reg": call_handler_no_reg,
        "priv_key": priv_key,
        "evil_priv": evil_priv,
        "registry": registry,
    }


def _call_sync(handler, tool_name, args=None):
    """Call an async MCP handler synchronously, return parsed JSON."""
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(handler(tool_name, args or {}))
        return json.loads(result[0].text)
    finally:
        loop.close()


class TestSelfDeclaredTrustRejected:
    """THE REGRESSION TEST — proves Phase 1 vulnerability is fixed."""

    def test_trust_level_field_does_not_exist(self):
        """The MCP tool tessera_connect must NOT accept trust_level."""
        connect_tool = next(t for t in TOOLS if t.name == "tessera_connect")
        props = connect_tool.input_schema.get("properties", {})
        assert "trust_level" not in props, \
            "VULNERABILITY: trust_level is accepted by tessera_connect"

    def test_can_transact_field_does_not_exist(self):
        """The MCP tool tessera_connect must NOT accept can_transact."""
        connect_tool = next(t for t in TOOLS if t.name == "tessera_connect")
        props = connect_tool.input_schema.get("properties", {})
        assert "can_transact" not in props, \
            "VULNERABILITY: can_transact is accepted by tessera_connect"

    def test_old_format_ignored(self, setup):
        """Sending old fields has no effect — extra args are ignored."""
        data = _call_sync(setup["call"], "tessera_connect", {
            "trust_level": "super_agent",
            "can_transact": True,
        })
        # Should connect as anonymous (no credential), NOT as super_agent
        if data.get("status") == "connected":
            perms = data.get("permissions", {})
            assert not perms.get("can_transact_autonomously"), \
                "VULNERABILITY: self-declared trust_level was accepted"

    def test_forged_credential_rejected(self, setup):
        """Token signed with wrong key claiming to be test-op → rejected."""
        token = sign_agent_credential(
            setup["evil_priv"], "test-op", "forged-agent", tier="super_agent",
        )
        data = _call_sync(setup["call"], "tessera_connect", {"credential": token})
        assert data.get("error") == "invalid_signature"

    def test_tier_capped_at_operator_max(self, setup):
        """Operator registered as 'verified' but token claims 'super_agent' → capped."""
        token = sign_agent_credential(
            setup["priv_key"], "test-op", "greedy-agent", tier="super_agent",
        )
        data = _call_sync(setup["call"], "tessera_connect", {"credential": token})
        assert data.get("status") == "connected"
        perms = data.get("permissions", {})
        assert not perms.get("can_transact_autonomously"), \
            "Tier should be capped to verified, not super_agent"


class TestCredentialConnect:
    """Test the credential-based connect flow through real MCP handlers."""

    def test_valid_credential(self, setup):
        token = sign_agent_credential(
            setup["priv_key"], "test-op", "agent-1", tier="verified",
            capabilities={"can_transact": True},
        )
        data = _call_sync(setup["call"], "tessera_connect", {"credential": token})
        assert data["status"] == "connected"
        assert "session_id" in data

    def test_no_credential_anonymous(self, setup):
        data = _call_sync(setup["call"], "tessera_connect", {})
        assert data["status"] == "connected"
        perms = data.get("permissions", {})
        assert not perms.get("can_transact_autonomously")

    def test_unknown_operator(self, setup):
        token = sign_agent_credential(
            setup["evil_priv"], "unknown-corp", "rogue", tier="super_agent",
        )
        data = _call_sync(setup["call"], "tessera_connect", {"credential": token})
        assert data["error"] == "unknown_operator"

    def test_expired_credential(self, setup):
        token = sign_agent_credential(
            setup["priv_key"], "test-op", "old", tier="identified", ttl_seconds=-1,
        )
        data = _call_sync(setup["call"], "tessera_connect", {"credential": token})
        assert data["error"] == "token_expired"

    def test_no_registry_all_anonymous(self, setup):
        """Without a registry, even valid credentials result in anonymous."""
        token = sign_agent_credential(
            setup["priv_key"], "test-op", "agent", tier="super_agent",
        )
        data = _call_sync(setup["call_no_reg"], "tessera_connect", {"credential": token})
        if data.get("status") == "connected":
            perms = data.get("permissions", {})
            assert not perms.get("can_transact_autonomously")
