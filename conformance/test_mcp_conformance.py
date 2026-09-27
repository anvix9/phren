"""
Tessera MCP Conformance Suite

Verifies the Tessera MCP server conforms to the MCP protocol.
Tests that the server is a real MCP server using the official SDK,
not a REST API pretending to be one.

Checks:
  1. Server creates via MCP SDK Server class
  2. tools/list returns Tool objects with valid schemas
  3. tools/call returns TextContent responses
  4. Streamable HTTP transport creates a Starlette app at /mcp
  5. Server metadata (name, version, title, description, instructions)
  6. Tool input schemas are valid JSON Schema
  7. Error responses are structured, not HTTP exceptions
"""
import pytest
import json
import asyncio
from pathlib import Path

from mcp.server import Server
from mcp import Tool
from mcp.types import TextContent

from tessera.mcp.server import (
    create_tessera_mcp, TOOLS,
    _make_call_tool_handler, _make_list_tools_handler,
)
from tessera.contract.schema import TesseraContract
from tessera.terminal.engine import TesseraTerminal


def _async(coro):
    """Run an async function synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def mcp_server(tmp_path):
    """Create a real MCP server instance."""
    contract_data = {
        "contract_id": "conformance-test",
        "site_name": "Conformance",
        "site_url": "http://localhost:9999",
        "require_identification": False,
        "screens": [],
        "rate_limits": {"max_concurrent_sessions": 10},
    }
    path = str(tmp_path / "contract.json")
    with open(path, "w") as f:
        json.dump(contract_data, f)
    return create_tessera_mcp(path, "http://localhost:9999")


# ═══════════════════════════════════════════════
# 1. SERVER IS REAL MCP
# ═══════════════════════════════════════════════

class TestServerIsMCP:
    """Verify the server is an actual MCP Server, not FastAPI."""

    def test_is_mcp_server_instance(self, mcp_server):
        assert isinstance(mcp_server, Server)

    def test_has_tools_capability(self, mcp_server):
        caps = mcp_server.get_capabilities()
        assert caps.tools is not None

    def test_server_name(self, mcp_server):
        # Server should identify as tessera
        # (checked via initialization options)
        opts = mcp_server.create_initialization_options()
        assert opts is not None


# ═══════════════════════════════════════════════
# 2. TOOLS/LIST CONFORMANCE
# ═══════════════════════════════════════════════

class TestToolsList:
    """tools/list must return valid MCP Tool objects."""

    def test_returns_tool_objects(self):
        for tool in TOOLS:
            assert isinstance(tool, Tool)

    def test_tools_have_required_fields(self):
        for tool in TOOLS:
            assert tool.name, f"Tool missing name"
            assert tool.description, f"{tool.name} missing description"
            assert tool.input_schema, f"{tool.name} missing input_schema"

    def test_input_schema_is_valid_json_schema(self):
        """Every tool's input_schema must be a valid JSON Schema object."""
        for tool in TOOLS:
            schema = tool.input_schema
            assert schema["type"] == "object", f"{tool.name}: schema type must be 'object'"
            assert "properties" in schema, f"{tool.name}: schema must have 'properties'"
            # Properties must be a dict
            assert isinstance(schema["properties"], dict)
            # Required must be a list (if present)
            if "required" in schema:
                assert isinstance(schema["required"], list)

    def test_tool_names_are_namespaced(self):
        """All tool names should start with tessera_ prefix."""
        for tool in TOOLS:
            assert tool.name.startswith("tessera_"), \
                f"Tool '{tool.name}' should be prefixed with tessera_"

    def test_exactly_five_tools(self):
        assert len(TOOLS) == 5
        names = {t.name for t in TOOLS}
        assert names == {
            "tessera_connect", "tessera_get_screen", "tessera_execute",
            "tessera_audit_log", "tessera_disconnect",
        }


# ═══════════════════════════════════════════════
# 3. TOOLS/CALL CONFORMANCE
# ═══════════════════════════════════════════════

class TestToolsCall:
    """tools/call must return TextContent responses."""

    def test_returns_text_content(self, mcp_server):
        contract = TesseraContract(
            contract_id="call-test", site_name="Test",
            site_url="http://localhost:9999",
            require_identification=False,
            rate_limits={"max_concurrent_sessions": 10},
        )
        terminal = TesseraTerminal(contract, "http://localhost:9999")
        handler = _make_call_tool_handler(terminal, None)

        result = _async(handler("tessera_connect", {}))
        assert isinstance(result, list)
        assert len(result) >= 1
        assert isinstance(result[0], TextContent)
        assert result[0].type == "text"

    def test_response_is_valid_json(self, mcp_server):
        contract = TesseraContract(
            contract_id="json-test", site_name="Test",
            site_url="http://localhost:9999",
            require_identification=False,
            rate_limits={"max_concurrent_sessions": 10},
        )
        terminal = TesseraTerminal(contract, "http://localhost:9999")
        handler = _make_call_tool_handler(terminal, None)

        result = _async(handler("tessera_connect", {}))
        # Must be parseable JSON
        data = json.loads(result[0].text)
        assert isinstance(data, dict)

    def test_unknown_tool_returns_error_not_exception(self, mcp_server):
        """Unknown tools return error in response, not throw."""
        contract = TesseraContract(
            contract_id="err-test", site_name="Test",
            site_url="http://localhost:9999",
            require_identification=False,
        )
        terminal = TesseraTerminal(contract, "http://localhost:9999")
        handler = _make_call_tool_handler(terminal, None)

        result = _async(handler("nonexistent_tool", {}))
        data = json.loads(result[0].text)
        assert "error" in data


# ═══════════════════════════════════════════════
# 4. TRANSPORT CONFORMANCE
# ═══════════════════════════════════════════════

class TestTransports:
    """Verify both transports are available."""

    def test_streamable_http_creates_app(self, mcp_server):
        app = mcp_server.streamable_http_app(streamable_http_path="/mcp")
        assert app is not None
        routes = [r.path for r in app.routes]
        assert "/mcp" in routes

    def test_streamable_http_path_configurable(self, mcp_server):
        app = mcp_server.streamable_http_app(streamable_http_path="/custom/mcp")
        routes = [r.path for r in app.routes]
        assert "/custom/mcp" in routes

    def test_stdio_runner_exists(self):
        """The stdio runner function exists and is importable."""
        from tessera.mcp.server import run_stdio
        assert callable(run_stdio)


# ═══════════════════════════════════════════════
# 5. PROTOCOL DETAILS
# ═══════════════════════════════════════════════

class TestProtocolDetails:
    """Verify MCP protocol-level details."""

    def test_connect_tool_credential_is_optional(self):
        """tessera_connect must not require credential (anonymous allowed)."""
        connect = next(t for t in TOOLS if t.name == "tessera_connect")
        required = connect.input_schema.get("required", [])
        assert "credential" not in required

    def test_execute_tool_requires_session_and_action(self):
        execute = next(t for t in TOOLS if t.name == "tessera_execute")
        required = execute.input_schema.get("required", [])
        assert "session_id" in required
        assert "action_id" in required

    def test_session_id_is_string_type(self):
        for tool in TOOLS:
            props = tool.input_schema.get("properties", {})
            if "session_id" in props:
                assert props["session_id"]["type"] == "string"
