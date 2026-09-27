"""
Tessera Flat Terminal Engine

An agent-optimized terminal that shows:
  1. A FLOW MAP — paths through the site for different objectives
  2. ALL TOOLS — flat list with preconditions and parameters
  3. CURRENT STATE — what's been done so far (logged_in, cart contents, etc.)

The agent reads the map once, plans its route, then calls tools directly.
No screen-by-screen navigation. The engine validates preconditions before
executing each tool.

Same governance (rate limits, spend caps, trust tiers). Different UX.

Usage:
    terminal = FlatTerminal(contract, site_url)
    session = terminal.connect(agent)
    overview = terminal.get_overview(session_id)  # map + tools + state
    result = terminal.execute(session_id, "login", {"email": "...", "password": "..."})
    result = terminal.execute(session_id, "search", {"q": "laptop"})
    result = terminal.execute(session_id, "add_to_cart", {"product_id": "prod_002"})
"""
import json
import uuid
import requests as http_requests
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass, field

from tessera.contract.schema import (
    TesseraContract, AgentProfile, AgentTrust, AgentCapabilities,
    ActionPermission, AuditLogEntry, ResolvedPermissions,
)
from tessera.contract.resolver import resolve_permissions
from tessera.contract.enforcement import enforce_connect, enforce_action


# ── Session ──

@dataclass
class FlatSession:
    """A flat terminal session with tracked state."""
    session_id: str
    agent: AgentProfile
    permissions: ResolvedPermissions
    state: dict = field(default_factory=dict)      # tracked state: logged_in, cart_id, etc.
    auth_token: Optional[str] = None               # simulation auth token
    audit_log: list = field(default_factory=list)
    created_at: str = ""

    def log(self, action: str, params: dict, result: str, data: dict = None,
            error: str = None, denied_reason: str = None):
        entry = AuditLogEntry(
            session_id=self.session_id,
            contract_id="flat",
            agent_provider=self.agent.provider,
            agent_name=self.agent.agent_name,
            screen="flat",
            action=action,
            parameters=params,
            result=result,
            error_message=str(error) if error else None,
            denied_reason=str(denied_reason) if denied_reason else None,
        )
        self.audit_log.append(entry)


# ── Flow Map Builder ──

def build_flow_map(contract: TesseraContract) -> dict:
    """
    Build a flow map from the contract's screens and actions.
    Returns a dict with:
      - objectives: common paths (buy, browse, book)
      - tools: flat list of all actions with preconditions
      - graph: adjacency list of screen transitions
    """
    tools = []
    graph = {}
    all_actions_by_screen = {}

    for screen in contract.screens:
        screen_actions = []
        for action in screen.actions:
            tool = {
                "id": action.id,
                "name": action.name,
                "description": action.description,
                "role": action.role.value if hasattr(action.role, 'value') else str(action.role),
                "params": [
                    {"name": p.name, "type": p.type, "required": p.required}
                    for p in action.parameters
                ],
                "from_screen": screen.id,
                "transitions_to": action.transitions_to,
                "api_method": action.api_method,
                "api_endpoint": action.api_endpoint,
                "permission": action.permission.value if hasattr(action.permission, 'value') else str(action.permission),
            }
            tools.append(tool)
            screen_actions.append(action.id)

            # Build graph edges
            if action.transitions_to:
                if screen.id not in graph:
                    graph[screen.id] = []
                graph[screen.id].append({
                    "via": action.id,
                    "to": action.transitions_to,
                })

        all_actions_by_screen[screen.id] = screen_actions

    # Infer objectives from primary actions and flow hints
    objectives = []
    for screen in contract.screens:
        if screen.flow_hint:
            objectives.append({
                "from": screen.id,
                "hint": screen.flow_hint,
            })

    # Build objective paths by tracing primary actions through the graph
    paths = _trace_paths(contract)

    return {
        "tools": tools,
        "graph": graph,
        "objectives": objectives,
        "paths": paths,
    }


def _trace_paths(contract: TesseraContract) -> list[dict]:
    """Trace common paths through the contract by following primary actions."""
    paths = []

    # Find entry screen
    entry = contract.entry_screen
    if not entry and contract.screens:
        entry = contract.screens[0].id

    if not entry:
        return paths

    # Build screen lookup
    screens = {s.id: s for s in contract.screens}

    # Trace from entry following primary actions
    def trace(screen_id, visited, path_actions):
        if screen_id in visited or screen_id not in screens:
            return
        visited.add(screen_id)
        screen = screens[screen_id]

        for action in screen.actions:
            role = action.role.value if hasattr(action.role, 'value') else str(action.role)
            if role == "primary" and action.transitions_to:
                new_path = path_actions + [action.id]
                if action.transitions_to in screens:
                    trace(action.transitions_to, visited.copy(), new_path)
                else:
                    # Terminal action (no further screen)
                    paths.append({
                        "name": f"path_to_{action.transitions_to}",
                        "steps": new_path,
                    })
            elif role == "primary" and not action.transitions_to:
                # Terminal primary action (like place_order)
                paths.append({
                    "name": f"complete_{action.id}",
                    "steps": path_actions + [action.id],
                })

    trace(entry, set(), [])
    return paths


# ── Flat Terminal ──

class FlatTerminal:
    """
    Agent-optimized terminal.
    Shows flow map + all tools. Agent calls tools directly.
    Engine validates preconditions and tracks state.
    """

    def __init__(self, contract: TesseraContract, site_url: str):
        self.contract = contract
        self.site_url = site_url.rstrip("/")
        self.sessions: dict[str, FlatSession] = {}
        self.flow_map = build_flow_map(contract)

        # Build action lookup
        self._actions = {}
        for screen in contract.screens:
            for action in screen.actions:
                self._actions[action.id] = action

    def connect(self, agent: AgentProfile) -> dict:
        """Connect an agent and return session + overview."""
        # Governance check
        active_count = len(self.sessions)
        allowed, reason = enforce_connect(self.contract, active_count)
        if not allowed:
            return {"status": "denied", "reason": reason}

        # Resolve permissions
        permissions = resolve_permissions(self.contract, agent)

        # Create session
        sid = f"flat_{uuid.uuid4().hex[:8]}"
        session = FlatSession(
            session_id=sid,
            agent=agent,
            permissions=permissions,
            state={"logged_in": False, "cart_items": [], "current_step": "start"},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.sessions[sid] = session
        session.log("connect", {}, "success")

        return {
            "status": "connected",
            "session_id": sid,
            "overview": self._build_overview(session),
        }

    def get_overview(self, session_id: str) -> dict:
        """Get the full overview: map + tools + current state."""
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "invalid_session"}
        return self._build_overview(session)

    def execute(self, session_id: str, action_id: str, params: dict = None,
                confirmed: bool = False) -> dict:
        """Execute a tool directly. Engine validates preconditions."""
        session = self.sessions.get(session_id)
        if not session:
            return {"status": "error", "message": "Invalid session"}

        params = params or {}
        action = self._actions.get(action_id)
        if not action:
            session.log(action_id, params, "error", error=f"Unknown action: {action_id}")
            return {"status": "error", "message": f"Unknown action: {action_id}"}

        # Governance enforcement
        allowed, reason = enforce_action(
            self.contract, session.agent, session.permissions,
            session.audit_log, action_id, params,
        )
        if not allowed:
            session.log(action_id, params, "denied", denied_reason=reason)
            return {"status": "denied", "reason": reason}

        # Check confirmation
        if action.permission == ActionPermission.REQUIRES_CONFIRMATION and not confirmed:
            return {
                "status": "confirmation_required",
                "message": f"Action '{action_id}' requires confirmation. Resend with confirmed=true.",
                "action": action_id,
                "params": params,
            }

        # Build API URL
        url = self._build_url(action, params, session)
        headers = {}
        if session.auth_token:
            headers["Authorization"] = f"Bearer {session.auth_token}"

        # Execute API call
        try:
            api_params = {k: v for k, v in params.items()
                         if k not in ("confirmed",) and v is not None and v != ""}

            if action.api_method == "GET":
                resp = http_requests.get(url, params=api_params, headers=headers, timeout=10)
            elif action.api_method == "POST":
                resp = http_requests.post(url, json=api_params, headers=headers, timeout=10)
            elif action.api_method == "PUT":
                resp = http_requests.put(url, json=api_params, headers=headers, timeout=10)
            elif action.api_method == "PATCH":
                resp = http_requests.patch(url, json=api_params, headers=headers, timeout=10)
            elif action.api_method == "DELETE":
                resp = http_requests.delete(url, params=api_params, headers=headers, timeout=10)
            else:
                session.log(action_id, params, "error", error="No API method defined")
                return {"status": "error", "message": "No API method defined for this action"}

            # Parse response
            try:
                data = resp.json()
            except Exception:
                data = {"raw": resp.text[:500]}

            # Update session state
            self._update_state(session, action_id, params, data, resp.status_code)

            if resp.status_code >= 400:
                session.log(action_id, params, "error", error=str(data)[:200])
                return {
                    "status": "error",
                    "http_status": resp.status_code,
                    "message": str(data.get("detail", data.get("message", data)))[:200],
                    "data": data,
                    "state": session.state,
                }

            session.log(action_id, params, "success")
            return {
                "status": "ok",
                "data": data,
                "state": session.state,
            }

        except http_requests.Timeout:
            session.log(action_id, params, "error", error="Request timed out")
            return {"status": "error", "message": "Request timed out"}
        except http_requests.ConnectionError:
            session.log(action_id, params, "error", error="Connection failed")
            return {"status": "error", "message": "Connection to site failed"}
        except Exception as e:
            session.log(action_id, params, "error", error=str(e))
            return {"status": "error", "message": str(e)}

    def disconnect(self, session_id: str) -> dict:
        session = self.sessions.pop(session_id, None)
        if not session:
            return {"status": "error", "message": "Invalid session"}
        return {"status": "disconnected", "steps": len(session.audit_log)}

    # ── Internal ──

    def _build_overview(self, session: FlatSession) -> dict:
        """Build the overview the agent sees."""

        # Build tool list with availability
        tools = []
        for tool in self.flow_map["tools"]:
            tools.append({
                "id": tool["id"],
                "description": tool["description"],
                "role": tool["role"],
                "params": tool["params"],
            })

        # Build paths as readable text
        path_descriptions = []
        for path in self.flow_map.get("paths", []):
            path_descriptions.append(f"{path['name']}: {' → '.join(path['steps'])}")

        # Objectives from flow hints
        objective_hints = []
        for obj in self.flow_map.get("objectives", []):
            objective_hints.append(obj["hint"])

        return {
            "site": self.contract.site_name,
            "flow_map": path_descriptions,
            "hints": objective_hints,
            "tools": tools,
            "state": session.state,
        }

    def _build_url(self, action, params: dict, session: FlatSession) -> str:
        """Build the API URL with path parameter substitution."""
        endpoint = action.api_endpoint or "/"
        url = f"{self.site_url}{endpoint}"

        # Substitute path parameters like {product_id}, {cart_id}
        for key, val in params.items():
            url = url.replace(f"{{{key}}}", str(val))

        # Substitute from session state
        for key, val in session.state.items():
            if isinstance(val, str):
                url = url.replace(f"{{{key}}}", val)

        return url

    def _update_state(self, session: FlatSession, action_id: str, params: dict,
                      data: dict, status_code: int):
        """Update session state based on action results."""
        if status_code >= 400:
            return

        # Track login
        if action_id == "login" and status_code < 300:
            session.state["logged_in"] = True
            token = data.get("token", data.get("access_token", ""))
            if token:
                session.auth_token = token
                session.state["auth_token"] = token

        # Track cart
        if action_id == "add_to_cart":
            product_id = params.get("product_id", "")
            qty = params.get("quantity", 1)
            session.state["cart_items"].append({"product_id": product_id, "quantity": qty})
            # Extract cart_id from response
            if isinstance(data, dict):
                cart_id = data.get("cart_id", data.get("id", ""))
                if cart_id:
                    session.state["cart_id"] = cart_id

        if action_id == "remove_from_cart":
            product_id = params.get("product_id", "")
            session.state["cart_items"] = [
                i for i in session.state.get("cart_items", [])
                if i.get("product_id") != product_id
            ]

        # Track orders/bookings
        if action_id in ("place_order", "book_room", "submit_request"):
            session.state["current_step"] = "completed"
            if isinstance(data, dict):
                order_id = data.get("order_id", data.get("booking_id", data.get("request_id", "")))
                if order_id:
                    session.state["last_order_id"] = order_id

        # Track search results
        if action_id == "search" and isinstance(data, dict):
            results = data.get("products", data.get("rooms", data.get("documents", [])))
            if results and isinstance(results, list):
                session.state["last_search_results"] = [
                    {"id": r.get("id"), "name": r.get("name", r.get("title", "")),
                     "price": r.get("price", r.get("rate", ""))}
                    for r in results[:5]
                ]
