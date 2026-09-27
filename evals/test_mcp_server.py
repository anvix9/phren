"""
Tessera Phase 3 — MCP Server Tests

Tests the real MCP server:
  - Server creation and configuration
  - Tool listing and schema validation
  - Full session lifecycle through MCP handlers
  - Error handling (unknown tools, missing params, invalid sessions)
  - Transport creation (Streamable HTTP, stdio readiness)
  - Multiple concurrent sessions
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
from tessera.contract.schema import TesseraContract
from tessera.terminal.engine import TesseraTerminal
from tessera.mcp.server import (
    create_tessera_mcp, TOOLS,
    _make_call_tool_handler, _make_list_tools_handler,
)


# ── Helpers ──

def _call_sync(handler, tool_name, args=None):
    """Call an async MCP handler synchronously, return parsed JSON."""
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(handler(tool_name, args or {}))
        return json.loads(result[0].text)
    finally:
        loop.close()


def _list_sync(handler):
    """Call the list_tools handler synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(handler())
    finally:
        loop.close()


@pytest.fixture
def mcp_setup(tmp_path):
    """Full MCP setup: contract, registry, terminal, handlers."""
    # Contract with a screen and actions for lifecycle tests
    contract_data = {
        "contract_id": "test-mcp-lifecycle",
        "site_name": "MCP Test Shop",
        "site_url": "http://localhost:9999",
        "require_identification": False,
        "rate_limits": {"max_concurrent_sessions": 10},
        "screens": [],
    }
    contract_path = str(tmp_path / "contract.json")
    with open(contract_path, "w") as f:
        json.dump(contract_data, f)

    # Registry
    reg_path = str(tmp_path / "operators.json")
    registry = OperatorRegistry(reg_path)
    priv_key, pub_key = generate_operator_keypair()
    registry.register("mcp-operator", "MCP Test Op", pub_key, OperatorTier.VERIFIED)

    # Terminal + handlers
    contract = TesseraContract(**contract_data)
    terminal = TesseraTerminal(contract, "http://localhost:9999")
    call_handler = _make_call_tool_handler(terminal, registry)
    list_handler = _make_list_tools_handler(contract)

    return {
        "call": call_handler,
        "list": list_handler,
        "priv_key": priv_key,
        "contract_path": contract_path,
        "reg_path": reg_path,
        "server_factory": lambda: create_tessera_mcp(contract_path, "http://localhost:9999", reg_path),
    }


# ── Server Creation ──

class TestServerCreation:
    """Test that the MCP server creates correctly."""

    def test_create_server(self, mcp_setup):
        server = mcp_setup["server_factory"]()
        assert server is not None

    def test_server_has_tools_capability(self, mcp_setup):
        server = mcp_setup["server_factory"]()
        caps = server.get_capabilities()
        assert caps.tools is not None

    def test_streamable_http_app(self, mcp_setup):
        """Streamable HTTP transport creates a Starlette app."""
        server = mcp_setup["server_factory"]()
        app = server.streamable_http_app(streamable_http_path="/mcp")
        assert app is not None
        routes = [r.path for r in app.routes]
        assert "/mcp" in routes


# ── Tool Listing ──

class TestToolListing:
    """Test tools/list returns correct tools with valid schemas."""

    def test_list_returns_5_tools(self, mcp_setup):
        tools = _list_sync(mcp_setup["list"])
        assert len(tools) == 5

    def test_tool_names(self, mcp_setup):
        tools = _list_sync(mcp_setup["list"])
        names = {t.name for t in tools}
        assert names == {
            "tessera_connect",
            "tessera_get_screen",
            "tessera_execute",
            "tessera_audit_log",
            "tessera_disconnect",
        }

    def test_every_tool_has_input_schema(self, mcp_setup):
        tools = _list_sync(mcp_setup["list"])
        for tool in tools:
            assert tool.input_schema is not None, f"{tool.name} missing input_schema"
            assert tool.input_schema.get("type") == "object", \
                f"{tool.name} input_schema type should be 'object'"
            assert "properties" in tool.input_schema, \
                f"{tool.name} input_schema missing properties"

    def test_every_tool_has_description(self, mcp_setup):
        tools = _list_sync(mcp_setup["list"])
        for tool in tools:
            assert tool.description, f"{tool.name} missing description"

    def test_connect_schema_has_credential(self):
        connect = next(t for t in TOOLS if t.name == "tessera_connect")
        props = connect.input_schema["properties"]
        assert "credential" in props
        assert props["credential"]["type"] == "string"

    def test_execute_schema_has_required_fields(self):
        execute = next(t for t in TOOLS if t.name == "tessera_execute")
        assert "session_id" in execute.input_schema.get("required", [])
        assert "action_id" in execute.input_schema.get("required", [])


# ── Session Lifecycle ──

class TestSessionLifecycle:
    """Test connect → get_screen → disconnect through MCP."""

    def test_connect_returns_session_id(self, mcp_setup):
        data = _call_sync(mcp_setup["call"], "tessera_connect", {})
        assert data["status"] == "connected"
        assert "session_id" in data
        assert data["session_id"].startswith("sess_")

    def test_connect_with_credential(self, mcp_setup):
        token = sign_agent_credential(
            mcp_setup["priv_key"], "mcp-operator", "lifecycle-agent",
            tier="verified",
        )
        data = _call_sync(mcp_setup["call"], "tessera_connect", {"credential": token})
        assert data["status"] == "connected"
        assert "permissions" in data

    def test_get_screen_after_connect(self, mcp_setup):
        conn = _call_sync(mcp_setup["call"], "tessera_connect", {})
        sid = conn["session_id"]
        screen = _call_sync(mcp_setup["call"], "tessera_get_screen", {"session_id": sid})
        # Should return something (even if empty contract)
        assert screen is not None

    def test_audit_log_after_connect(self, mcp_setup):
        conn = _call_sync(mcp_setup["call"], "tessera_connect", {})
        sid = conn["session_id"]
        log = _call_sync(mcp_setup["call"], "tessera_audit_log", {"session_id": sid})
        assert "audit_log" in log
        # At least the connect action should be logged
        assert len(log["audit_log"]) >= 1

    def test_disconnect(self, mcp_setup):
        conn = _call_sync(mcp_setup["call"], "tessera_connect", {})
        sid = conn["session_id"]
        disc = _call_sync(mcp_setup["call"], "tessera_disconnect", {"session_id": sid})
        assert disc.get("status") == "disconnected"

    def test_full_lifecycle(self, mcp_setup):
        """Connect → screen → audit → disconnect: no errors at any step."""
        token = sign_agent_credential(
            mcp_setup["priv_key"], "mcp-operator", "full-lifecycle",
            tier="identified",
        )
        # Connect
        conn = _call_sync(mcp_setup["call"], "tessera_connect", {"credential": token})
        assert conn["status"] == "connected"
        sid = conn["session_id"]

        # Get screen
        screen = _call_sync(mcp_setup["call"], "tessera_get_screen", {"session_id": sid})
        assert "error" not in screen or screen.get("error") is None

        # Audit log
        log = _call_sync(mcp_setup["call"], "tessera_audit_log", {"session_id": sid})
        assert len(log["audit_log"]) >= 1

        # Disconnect
        disc = _call_sync(mcp_setup["call"], "tessera_disconnect", {"session_id": sid})
        assert disc["status"] == "disconnected"


# ── Error Handling ──

class TestErrorHandling:
    """Test that errors are returned properly, not thrown."""

    def test_unknown_tool(self, mcp_setup):
        data = _call_sync(mcp_setup["call"], "nonexistent_tool", {})
        assert data["error"] == "unknown_tool"

    def test_missing_session_id_get_screen(self, mcp_setup):
        data = _call_sync(mcp_setup["call"], "tessera_get_screen", {})
        assert data.get("error") == "missing_param"

    def test_missing_session_id_execute(self, mcp_setup):
        data = _call_sync(mcp_setup["call"], "tessera_execute", {"action_id": "test"})
        assert data.get("error") == "missing_param"

    def test_missing_action_id_execute(self, mcp_setup):
        data = _call_sync(mcp_setup["call"], "tessera_execute", {"session_id": "fake"})
        assert data.get("error") == "missing_param"

    def test_invalid_session_id(self, mcp_setup):
        data = _call_sync(mcp_setup["call"], "tessera_get_screen", {"session_id": "invalid_session_xyz"})
        assert "error" in data

    def test_disconnected_session_rejected(self, mcp_setup):
        """After disconnect, the session should be invalid."""
        conn = _call_sync(mcp_setup["call"], "tessera_connect", {})
        sid = conn["session_id"]
        _call_sync(mcp_setup["call"], "tessera_disconnect", {"session_id": sid})

        # Try to use the disconnected session
        data = _call_sync(mcp_setup["call"], "tessera_get_screen", {"session_id": sid})
        assert "error" in data


# ── Multiple Sessions ──

class TestMultipleSessions:
    """Test that multiple sessions can coexist."""

    def test_two_sessions(self, mcp_setup):
        conn1 = _call_sync(mcp_setup["call"], "tessera_connect", {})
        conn2 = _call_sync(mcp_setup["call"], "tessera_connect", {})
        assert conn1["session_id"] != conn2["session_id"]
        assert conn1["status"] == "connected"
        assert conn2["status"] == "connected"

    def test_sessions_independent(self, mcp_setup):
        """Disconnecting one session doesn't affect the other."""
        conn1 = _call_sync(mcp_setup["call"], "tessera_connect", {})
        conn2 = _call_sync(mcp_setup["call"], "tessera_connect", {})

        # Disconnect session 1
        _call_sync(mcp_setup["call"], "tessera_disconnect", {"session_id": conn1["session_id"]})

        # Session 2 should still work
        screen = _call_sync(mcp_setup["call"], "tessera_get_screen", {"session_id": conn2["session_id"]})
        assert "error" not in screen or screen.get("error") is None
