"""
Phren MCP Server

Exposes the Phren Terminal as MCP-compatible tools that any AI agent can call.

Tools provided:
  phren_connect       - Connect to the terminal with an agent profile
  phren_get_screen    - View the current screen (data + available actions)
  phren_execute       - Execute an action on the terminal
  phren_audit_log     - View the session audit log
  phren_disconnect    - End the session

This can run as a standalone FastAPI server that agents connect to,
or be integrated into an MCP host.
"""
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

from phren.contract.schema import PhrenContract, AgentProfile, AgentTrust, AgentCapabilities
from phren.terminal.engine import PhrenTerminal
from phren.contract.credentials import (
    verify_agent_credential,
    VerifiedCredential,
    VerificationError,
)
from phren.contract.operator_registry import OperatorRegistry


# ── Request Models ──

class ConnectRequest(BaseModel):
    credential: Optional[str] = None   # Signed JWT from operator (EdDSA)
    # If no credential is provided, the agent connects as anonymous (read-only).
    # Trust tier is DERIVED from the credential, never self-declared.


class ExecuteRequest(BaseModel):
    session_id: str
    action_id: str
    params: dict = {}
    confirmed: bool = False


class ScreenRequest(BaseModel):
    session_id: str


class AuditRequest(BaseModel):
    session_id: str


class DisconnectRequest(BaseModel):
    session_id: str


# ── MCP Server App ──

def create_mcp_server(contract_path: str, site_url: str, registry_path: str = None) -> FastAPI:
    """
    Create a FastAPI MCP server for a Phren terminal.

    Args:
        contract_path: Path to the contract JSON file
        site_url: URL of the actual website the terminal proxies to
        registry_path: Path to operator registry JSON. If None, all agents
                       connect as anonymous (no credential verification).
    """

    # Load contract
    with open(contract_path) as f:
        contract_data = json.load(f)
    contract = PhrenContract(**contract_data)

    # Create terminal
    terminal = PhrenTerminal(contract, site_url)

    # Load operator registry (if provided)
    registry = OperatorRegistry(registry_path) if registry_path else None

    app = FastAPI(
        title=f"Phren MCP Server — {contract.site_name}",
        description=f"MCP tools for AI agents to interact with {contract.site_name} "
                    "through the Phren terminal.",
        version="0.2.0",
    )

    # ── Tool: Connect ──

    @app.post("/tools/phren_connect")
    def phren_connect(req: ConnectRequest):
        """
        Connect to the Phren terminal.

        If a signed credential is provided, it is verified against the
        operator registry and the trust tier is derived from the credential.
        If no credential is provided, the agent connects as anonymous.

        Trust tier is NEVER self-declared. It is always derived from:
          - A valid, signed credential → tier from token (capped at operator max)
          - No credential → anonymous
        """
        if req.credential and registry:
            # Verify the credential
            result = verify_agent_credential(req.credential, registry)

            if isinstance(result, VerificationError):
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": result.code,
                        "message": result.message,
                    }
                )

            # Build AgentProfile from verified credential
            agent = AgentProfile(
                provider=result.operator_id,
                agent_name=result.agent_name,
                agent_id=result.agent_id,
                trust_level=AgentTrust(result.derived_tier),
                purpose=result.purpose,
                operator=result.operator_id,
                credentials=req.credential,
                capabilities=AgentCapabilities(
                    can_transact=result.capabilities.get("can_transact", False),
                    max_transaction_amount=result.capabilities.get("max_transaction"),
                    max_daily_spend=result.capabilities.get("max_daily_spend"),
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

        result = terminal.connect(agent)
        if result["status"] == "denied":
            raise HTTPException(status_code=403, detail=result["reason"])
        return result

    # ── Tool: Get Screen ──

    @app.post("/tools/phren_get_screen")
    def phren_get_screen(req: ScreenRequest):
        """
        View the current screen. Returns visible data and available actions.
        This is how the agent "sees" the website.
        """
        try:
            return terminal.get_screen(req.session_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ── Tool: Execute Action ──

    @app.post("/tools/phren_execute")
    def phren_execute(req: ExecuteRequest):
        """
        Execute an action on the terminal.
        The action must be available on the current screen and
        within the agent's permissions.
        """
        try:
            return terminal.execute_action(
                req.session_id, req.action_id, req.params, req.confirmed
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ── Tool: Audit Log ──

    @app.post("/tools/phren_audit_log")
    def phren_audit_log(req: AuditRequest):
        """View the full audit log for this session."""
        try:
            return {"audit_log": terminal.get_audit_log(req.session_id)}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ── Tool: Disconnect ──

    @app.post("/tools/phren_disconnect")
    def phren_disconnect(req: DisconnectRequest):
        """End the session and get the final audit log."""
        try:
            return terminal.disconnect(req.session_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # ── Info Endpoint ──

    @app.get("/")
    def info():
        """Terminal info with full flow map."""
        flow_map = []
        for screen in contract.screens:
            screen_info = {
                "id": screen.id,
                "name": screen.name,
                "description": screen.description,
                "actions": [],
            }
            for action in screen.actions:
                action_info = {
                    "id": action.id,
                    "name": action.name,
                    "description": action.description,
                    "transitions_to": action.transitions_to,
                    "parameters": [p.name for p in action.parameters],
                }
                screen_info["actions"].append(action_info)
            flow_map.append(screen_info)

        return {
            "phren_terminal": contract.site_name,
            "contract_id": contract.contract_id,
            "tier": contract.tier.value,
            "entry_screen": contract.entry_screen,
            "screens": [s.id for s in contract.screens],
            "flow_map": flow_map,
            "tools": [
                {"name": "phren_connect", "description": "Connect with agent profile"},
                {"name": "phren_get_screen", "description": "View current screen"},
                {"name": "phren_execute", "description": "Execute an action"},
                {"name": "phren_audit_log", "description": "View audit log"},
                {"name": "phren_disconnect", "description": "End session"},
            ],
        }

    return app


if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Phren MCP Server")
    parser.add_argument("--contract", default="../../simulations/shopping/contract.json",
                        help="Path to contract JSON")
    parser.add_argument("--site-url", default="http://localhost:8000",
                        help="URL of the actual website")
    parser.add_argument("--registry", default=None,
                        help="Path to operator registry JSON (enables credential verification)")
    parser.add_argument("--port", type=int, default=8001,
                        help="Port for the MCP server")
    args = parser.parse_args()

    contract_path = str(Path(args.contract).resolve())
    print(f"Starting Phren MCP Server")
    print(f"  Contract: {contract_path}")
    print(f"  Site URL: {args.site_url}")
    print(f"  Registry: {args.registry or 'None (all agents connect as anonymous)'}")
    print(f"  MCP Port: {args.port}")
    print(f"  Docs: http://localhost:{args.port}/docs")

    app = create_mcp_server(contract_path, args.site_url, args.registry)
    uvicorn.run(app, host="0.0.0.0", port=args.port)
