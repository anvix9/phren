"""
Tessera MCP Server (spec-compliant)

A real MCP server using the official MCP Python SDK.
Supports both stdio and Streamable HTTP transports.

Protocol: JSON-RPC 2.0 over MCP
Tools:
  tessera_connect       — Connect with a signed credential
  tessera_get_screen    — View current screen (data + actions)
  tessera_execute       — Execute an action
  tessera_audit_log     — View session audit log
  tessera_disconnect    — End session

Usage:
  # Streamable HTTP transport
  tessera serve --contract contract.json --site-url http://localhost:8000

  # stdio transport (for MCP clients like Claude Desktop)
  tessera serve --transport stdio --contract contract.json
"""
import json
import asyncio
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp import Tool, CallToolRequest, ListToolsResult
from mcp.types import TextContent

from tessera.contract.schema import (
    TesseraContract, AgentProfile, AgentTrust, AgentCapabilities,
)
from tessera.terminal.engine import TesseraTerminal
from tessera.contract.credentials import (
    verify_agent_credential,
    VerifiedCredential,
    VerificationError,
)
from tessera.contract.operator_registry import OperatorRegistry


# ── Tool Definitions ──

TOOLS = [
    Tool(
        name="tessera_connect",
        title="Connect to Terminal",
        description=(
            "Connect to the Tessera terminal. Provide a signed credential (JWT) "
            "to authenticate as a verified agent. Without a credential, you connect "
            "as anonymous with read-only access."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "credential": {
                    "type": "string",
                    "description": "Signed JWT from your operator (Ed25519/EdDSA). "
                                   "Omit to connect as anonymous.",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="tessera_get_screen",
        title="View Current Screen",
        description=(
            "View the current screen of the terminal. Returns visible data fields "
            "and available actions. This is how you 'see' the website."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Your session ID from tessera_connect.",
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="tessera_execute",
        title="Execute Action",
        description=(
            "Execute an action on the current screen. The action must be available "
            "on the current screen and within your trust tier's permissions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Your session ID.",
                },
                "action_id": {
                    "type": "string",
                    "description": "ID of the action to execute (from tessera_get_screen).",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the action.",
                    "additionalProperties": True,
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Set to true to confirm a transaction that requires confirmation.",
                    "default": False,
                },
            },
            "required": ["session_id", "action_id"],
        },
    ),
    Tool(
        name="tessera_audit_log",
        title="View Audit Log",
        description="View the full audit log for your session.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Your session ID.",
                },
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="tessera_disconnect",
        title="Disconnect",
        description="End your session and get the final audit summary.",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Your session ID.",
                },
            },
            "required": ["session_id"],
        },
    ),
]


# ── Server Factory ──

def create_tessera_mcp(
    contract_path: str,
    site_url: str,
    registry_path: Optional[str] = None,
) -> Server:
    """
    Create a spec-compliant MCP server for a Tessera terminal.

    Args:
        contract_path: Path to the contract JSON file
        site_url: URL of the actual website the terminal proxies to
        registry_path: Path to operator registry JSON. If None, all agents
                       connect as anonymous.

    Returns:
        An MCP Server instance, ready for stdio or HTTP transport.
    """
    # Load contract
    with open(contract_path) as f:
        contract_data = json.load(f)
    contract = TesseraContract(**contract_data)

    # Create terminal engine
    terminal = TesseraTerminal(contract, site_url)

    # Load operator registry
    registry = OperatorRegistry(registry_path) if registry_path else None

    # Create MCP server
    server = Server(
        name="tessera",
        version="0.2.0",
        title=f"Tessera — {contract.site_name}",
        description=(
            f"Governed agent terminal for {contract.site_name}. "
            f"Contract: {contract.contract_id}. "
            f"Trust is derived from signed credentials, never self-declared."
        ),
        instructions=(
            "To use this terminal:\n"
            "1. Call tessera_connect with your signed credential to start a session\n"
            "2. Call tessera_get_screen to see the current screen and available actions\n"
            "3. Call tessera_execute with an action_id and params to interact\n"
            "4. Call tessera_disconnect when done\n"
            "\n"
            "Without a credential, you connect as anonymous with read-only access."
        ),
        on_list_tools=_make_list_tools_handler(contract),
        on_call_tool=_make_call_tool_handler(terminal, registry),
    )

    return server


# ── Handlers ──

def _make_list_tools_handler(contract: TesseraContract):
    """Create the tools/list handler."""

    async def handle_list_tools(request=None) -> list[Tool]:
        return TOOLS

    return handle_list_tools


def _make_call_tool_handler(terminal: TesseraTerminal, registry: Optional[OperatorRegistry]):
    """Create the tools/call handler."""

    async def handle_call_tool(
        name: str,
        arguments: dict | None = None,
    ) -> list[TextContent]:
        args = arguments or {}

        try:
            if name == "tessera_connect":
                result = _handle_connect(terminal, registry, args)
            elif name == "tessera_get_screen":
                result = _handle_get_screen(terminal, args)
            elif name == "tessera_execute":
                result = _handle_execute(terminal, args)
            elif name == "tessera_audit_log":
                result = _handle_audit_log(terminal, args)
            elif name == "tessera_disconnect":
                result = _handle_disconnect(terminal, args)
            else:
                result = {"error": "unknown_tool", "message": f"No tool named '{name}'"}

        except ValueError as e:
            result = {"error": "invalid_request", "message": str(e)}
        except Exception as e:
            result = {"error": "internal_error", "message": str(e)}

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return handle_call_tool


# ── Tool Implementations ──

def _handle_connect(
    terminal: TesseraTerminal,
    registry: Optional[OperatorRegistry],
    args: dict,
) -> dict:
    """Handle tessera_connect tool call."""
    credential = args.get("credential")

    if credential and registry:
        # Verify the credential
        verification = verify_agent_credential(credential, registry)

        if isinstance(verification, VerificationError):
            return {
                "status": "error",
                "error": verification.code,
                "message": verification.message,
            }

        # Build agent profile from verified credential
        agent = AgentProfile(
            provider=verification.operator_id,
            agent_name=verification.agent_name,
            agent_id=verification.agent_id,
            trust_level=AgentTrust(verification.derived_tier),
            purpose=verification.purpose,
            operator=verification.operator_id,
            credentials=credential,
            capabilities=AgentCapabilities(
                can_transact=verification.capabilities.get("can_transact", False),
                max_transaction_amount=verification.capabilities.get("max_transaction"),
                max_daily_spend=verification.capabilities.get("max_daily_spend"),
            ),
        )
    else:
        # No credential → anonymous
        agent = AgentProfile(
            provider="anonymous",
            agent_name="anonymous-agent",
            trust_level=AgentTrust.ANONYMOUS,
            purpose="browsing",
        )

    return terminal.connect(agent)


def _handle_get_screen(terminal: TesseraTerminal, args: dict) -> dict:
    """Handle tessera_get_screen tool call."""
    session_id = args.get("session_id")
    if not session_id:
        return {"error": "missing_param", "message": "session_id is required"}
    return terminal.get_screen(session_id)


def _handle_execute(terminal: TesseraTerminal, args: dict) -> dict:
    """Handle tessera_execute tool call."""
    session_id = args.get("session_id")
    action_id = args.get("action_id")
    if not session_id or not action_id:
        return {"error": "missing_param", "message": "session_id and action_id are required"}
    params = args.get("params", {})
    confirmed = args.get("confirmed", False)
    return terminal.execute_action(session_id, action_id, params, confirmed)


def _handle_audit_log(terminal: TesseraTerminal, args: dict) -> dict:
    """Handle tessera_audit_log tool call."""
    session_id = args.get("session_id")
    if not session_id:
        return {"error": "missing_param", "message": "session_id is required"}
    return {"audit_log": terminal.get_audit_log(session_id)}


def _handle_disconnect(terminal: TesseraTerminal, args: dict) -> dict:
    """Handle tessera_disconnect tool call."""
    session_id = args.get("session_id")
    if not session_id:
        return {"error": "missing_param", "message": "session_id is required"}
    return terminal.disconnect(session_id)


# ── Transport Runners ──

async def run_stdio(server: Server):
    """Run the MCP server over stdio (for Claude Desktop, etc.)."""
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


def run_http(server: Server, host: str = "127.0.0.1", port: int = 8001):
    """Run the MCP server over Streamable HTTP."""
    import uvicorn

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=False,
    )

    uvicorn.run(app, host=host, port=port)


# ── CLI Entry Point ──

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tessera MCP Server")
    parser.add_argument("--contract", required=True, help="Path to contract JSON")
    parser.add_argument("--site-url", required=True, help="URL of the actual website")
    parser.add_argument("--registry", default=None, help="Path to operator registry JSON")
    parser.add_argument("--transport", choices=["http", "stdio"], default="http",
                        help="Transport: http (Streamable HTTP) or stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host")
    parser.add_argument("--port", type=int, default=8001, help="HTTP port")
    args = parser.parse_args()

    contract_path = str(Path(args.contract).resolve())
    server = create_tessera_mcp(contract_path, args.site_url, args.registry)

    if args.transport == "stdio":
        print("Starting Tessera MCP Server (stdio)", flush=True)
        asyncio.run(run_stdio(server))
    else:
        print(f"Starting Tessera MCP Server (HTTP)")
        print(f"  Contract: {contract_path}")
        print(f"  Site URL: {args.site_url}")
        print(f"  Registry: {args.registry or 'None (anonymous only)'}")
        print(f"  MCP endpoint: http://{args.host}:{args.port}/mcp")
        run_http(server, args.host, args.port)
