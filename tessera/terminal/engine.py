"""
Tessera Terminal Engine

The runtime that sits between agents and websites.
It manages sessions, enforces contracts, translates terminal
commands into real API calls, and logs everything.

Agent -> Terminal -> Contract Check -> Translation Proxy -> Real Website
"""
import json
import re
import uuid
import requests
from datetime import datetime, timezone
from typing import Optional

from tessera.contract.schema import (
    TesseraContract, ScreenDefinition, ActionDefinition,
    AgentProfile, AgentTrust, ActionPermission,
    ContractAcceptance, ResolvedPermissions, AuditLogEntry,
)
from tessera.contract.resolver import resolve_permissions
from tessera.contract.enforcement import enforce_connect, enforce_action


class TerminalSession:
    """A single agent session on a Tessera terminal."""

    def __init__(self, session_id: str, contract: TesseraContract,
                 agent: AgentProfile, permissions: ResolvedPermissions):
        self.session_id = session_id
        self.contract = contract
        self.agent = agent
        self.permissions = permissions
        self.current_screen: str = contract.entry_screen
        self.state: dict = {}           # Runtime state (cart_id, etc.)
        self.audit_log: list[AuditLogEntry] = []
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.action_count = 0

    def log(self, action: str, params: dict, result: str,
            error_message: str = None, denied_reason: str = None):
        """Record an action in the audit log."""
        # Coerce error_message to string (can be a list from Pydantic validation)
        if error_message is not None and not isinstance(error_message, str):
            error_message = str(error_message)
        if denied_reason is not None and not isinstance(denied_reason, str):
            denied_reason = str(denied_reason)
        entry = AuditLogEntry(
            session_id=self.session_id,
            contract_id=self.contract.contract_id,
            agent_provider=self.agent.provider,
            agent_name=self.agent.agent_name,
            agent_trust=self.agent.trust_level,
            screen=self.current_screen,
            action=action,
            parameters=params,
            result=result,
            denied_reason=denied_reason,
            error_message=error_message,
        )
        self.audit_log.append(entry)
        self.action_count += 1


class TranslationProxy:
    """Translates Tessera terminal commands into real HTTP API calls."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def execute(self, action: ActionDefinition, params: dict,
                session_state: dict) -> dict:
        """Execute an action by calling the real API."""
        if not action.api_endpoint:
            return {"status": "ok", "message": f"Navigation action: {action.id}"}

        url = self.base_url + action.api_endpoint
        method = (action.api_method or "GET").upper()

        # Substitute path parameters from session state and params
        all_state = {**session_state, **params}
        for key, value in all_state.items():
            url = url.replace(f"{{{key}}}", str(value))

        # Restructure flat params into nested objects where needed
        api_params = self._restructure_params(action, params, session_state)

        # Strip empty strings — agent sends "" for optional params but API
        # treats "" as an invalid value, not as "omitted"
        api_params = {k: v for k, v in api_params.items()
                      if v is not None and v != ""}

        # Build headers — include auth token if available
        headers = {"Content-Type": "application/json"}
        auth_token = session_state.get("auth_token")
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"

        try:
            if method == "GET":
                resp = requests.get(url, params=api_params, headers=headers, timeout=10)
            elif method == "POST":
                resp = requests.post(url, json=api_params, headers=headers, timeout=10)
            elif method == "PUT":
                resp = requests.put(url, json=api_params, headers=headers, timeout=10)
            elif method == "DELETE":
                resp = requests.delete(url, params=api_params, headers=headers, timeout=10)
            else:
                return {"status": "error", "message": f"Unsupported method: {method}"}

            if resp.status_code == 401:
                return {
                    "status": "auth_required",
                    "http_status": 401,
                    "message": "Authentication required. Please login first.",
                }

            if resp.status_code >= 400:
                error_data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                return {
                    "status": "error",
                    "http_status": resp.status_code,
                    "message": error_data.get("detail", f"HTTP {resp.status_code}"),
                }

            return {
                "status": "ok",
                "data": resp.json(),
            }
        except requests.Timeout:
            return {"status": "error", "message": "Request timed out"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _restructure_params(self, action: ActionDefinition, params: dict,
                            session_state: dict) -> dict:
        """
        Restructure flat terminal params into the nested format the API expects.
        e.g., {shipping_name: "X", shipping_city: "Y", card_last_four: "Z"}
        becomes {cart_id: "...", shipping: {name: "X", city: "Y"}, payment: {card_last_four: "Z"}}
        """
        # Detect if params have prefixed groups (shipping_*, card_*)
        groups: dict[str, dict] = {}
        ungrouped: dict = {}

        for key, value in params.items():
            if key.startswith("shipping_"):
                inner_key = key[len("shipping_"):]  # Remove "shipping_" prefix
                groups.setdefault("shipping", {})[inner_key] = value
            elif key.startswith("card_"):
                groups.setdefault("payment", {})[key] = value
            else:
                ungrouped[key] = value

        if not groups:
            return params

        # Build restructured payload
        result = dict(ungrouped)
        for group_name, group_params in groups.items():
            result[group_name] = group_params

        # Inject session state (like cart_id) for checkout
        if "cart_id" in session_state and "cart_id" not in result:
            result["cart_id"] = session_state["cart_id"]

        return result


class TesseraTerminal:
    """
    The Tessera Terminal.

    This is the main entry point for agents. It:
    1. Accepts agent profiles and creates sessions
    2. Presents the current screen (data + available actions)
    3. Executes actions with contract enforcement
    4. Translates commands to real API calls
    5. Logs everything

    Usage:
        terminal = TesseraTerminal(contract, "http://localhost:8000")
        session = terminal.connect(agent_profile)
        screen = terminal.get_screen(session.session_id)
        result = terminal.execute_action(session.session_id, "search", {"q": "headphones"})
    """

    def __init__(self, contract: TesseraContract, site_url: str):
        self.contract = contract
        self.proxy = TranslationProxy(site_url)
        self.sessions: dict[str, TerminalSession] = {}

        # Index screens and actions for fast lookup
        self._screens: dict[str, ScreenDefinition] = {s.id: s for s in contract.screens}
        self._actions: dict[str, tuple[ScreenDefinition, ActionDefinition]] = {}
        for screen in contract.screens:
            for action in screen.actions:
                self._actions[action.id] = (screen, action)

    def connect(self, agent: AgentProfile) -> dict:
        """
        Connect an agent to the terminal.
        Returns session info with resolved permissions.
        """
        # Check if provider is blocked
        if agent.provider in self.contract.blocked_providers:
            return {
                "status": "denied",
                "reason": f"Provider '{agent.provider}' is blocked",
            }

        # Check purpose
        if (self.contract.allowed_purposes
                and agent.purpose not in self.contract.allowed_purposes):
            return {
                "status": "denied",
                "reason": f"Purpose '{agent.purpose}' is not allowed. "
                          f"Allowed: {self.contract.allowed_purposes}",
            }

        # Check identification requirement
        if self.contract.require_identification and agent.trust_level == AgentTrust.ANONYMOUS:
            return {
                "status": "denied",
                "reason": "This terminal requires agent identification. "
                          "Provide at minimum an 'identified' trust level.",
            }

        # Phase 2: Governance enforcement at connect
        active_count = len(self.sessions)
        allowed, reason = enforce_connect(self.contract, active_count)
        if not allowed:
            return {"status": "denied", "reason": reason}

        # Resolve permissions
        permissions = resolve_permissions(self.contract, agent)

        # Create session
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        session = TerminalSession(session_id, self.contract, agent, permissions)

        # Initialize session state
        session.state["logged_in"] = False

        self.sessions[session_id] = session

        session.log("connect", {"provider": agent.provider, "trust": agent.trust_level.value},
                    "success")

        return {
            "status": "connected",
            "session_id": session_id,
            "site": self.contract.site_name,
            "current_screen": session.current_screen,
            "permissions": {
                "allowed": permissions.allowed_actions,
                "needs_confirmation": permissions.confirmation_required,
                "denied": permissions.denied_actions,
                "can_transact_autonomously": permissions.can_transact_autonomously,
                "max_transaction": permissions.max_transaction_amount,
            },
            "contract": {
                "tier": self.contract.tier.value,
                "rate_limits": {
                    "per_minute": self.contract.rate_limits.requests_per_minute,
                    "per_hour": self.contract.rate_limits.requests_per_hour,
                },
            },
        }

    def get_screen(self, session_id: str) -> dict:
        """
        Get the current screen for a session.
        Returns visible data fields and available actions.
        """
        session = self._get_session(session_id)
        screen = self._screens.get(session.current_screen)
        if not screen:
            return {"status": "error", "message": f"Unknown screen: {session.current_screen}"}

        # Filter actions based on permissions
        available_actions = []
        for action in screen.actions:
            if action.id in session.permissions.denied_actions:
                continue

            status = "available"
            if action.id in session.permissions.confirmation_required:
                status = "requires_confirmation"

            available_actions.append({
                "id": action.id,
                "name": action.name,
                "description": action.description,
                "role": action.role.value if hasattr(action.role, 'value') else str(action.role),
                "status": status,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "required": p.required,
                        "description": p.description,
                        "enum_values": p.enum_values,
                        "min": p.min_value,
                        "max": p.max_value,
                        "default": p.default,
                    }
                    for p in action.parameters
                ],
            })

        # Fetch live data for the screen
        screen_data = self._fetch_screen_data(screen, session)

        session.log("get_screen", {"screen": screen.id}, "success")

        return {
            "status": "ok",
            "screen": {
                "id": screen.id,
                "name": screen.name,
                "description": screen.description,
                "flow_hint": screen.flow_hint,
            },
            "data": screen_data,
            "actions": available_actions,
            "navigation": {
                "parent": screen.parent,
                "current": screen.id,
            },
        }

    def execute_action(self, session_id: str, action_id: str,
                       params: dict = None, confirmed: bool = False) -> dict:
        """
        Execute an action on the terminal.

        Args:
            session_id: The agent's session
            action_id: Which action to perform
            params: Action parameters
            confirmed: If True, bypasses confirmation requirement (for super_agents or user-confirmed)
        """
        params = params or {}
        session = self._get_session(session_id)

        # Check if action exists
        if action_id not in self._actions:
            session.log(action_id, params, "denied", denied_reason="Action not found")
            return {"status": "error", "message": f"Unknown action: {action_id}"}

        screen_def, action_def = self._actions[action_id]

        # Check if action is on the current screen (or is a global action)
        current_screen = self._screens.get(session.current_screen)
        action_on_screen = any(a.id == action_id for a in current_screen.actions) if current_screen else False
        if not action_on_screen:
            session.log(action_id, params, "denied",
                        denied_reason=f"Action not available on screen '{session.current_screen}'")
            return {
                "status": "error",
                "message": f"Action '{action_id}' is not available on screen '{session.current_screen}'. "
                           f"Available: {[a.id for a in current_screen.actions] if current_screen else []}",
            }

        # Check permission
        if action_id in session.permissions.denied_actions:
            reason = f"Denied for trust level '{session.agent.trust_level.value}'"
            session.log(action_id, params, "denied", denied_reason=reason)
            return {"status": "denied", "reason": reason}

        # Check confirmation requirement
        if action_id in session.permissions.confirmation_required and not confirmed:
            session.log(action_id, params, "confirmation_required")
            return {
                "status": "confirmation_required",
                "message": f"Action '{action_id}' requires confirmation. "
                           "Re-submit with confirmed=True after user approval.",
                "action": action_id,
                "params": params,
            }

        # Phase 2: Full governance enforcement
        allowed, reason = enforce_action(
            self.contract, session.agent, session.permissions,
            session.audit_log, action_id, params,
        )
        if not allowed:
            session.log(action_id, params, "denied", denied_reason=reason)
            return {"status": "denied", "reason": reason}

        # Validate required parameters
        for p_def in action_def.parameters:
            if p_def.required and p_def.name not in params:
                if p_def.default:
                    params[p_def.name] = p_def.default
                else:
                    session.log(action_id, params, "error",
                                error_message=f"Missing required parameter: {p_def.name}")
                    return {
                        "status": "error",
                        "message": f"Missing required parameter: {p_def.name}",
                    }

        # Execute via translation proxy
        result = self.proxy.execute(action_def, params, session.state)

        if result["status"] == "ok":
            # Update session state from result
            self._update_session_state(session, action_def, result)

            # Update current screen if action has a transition
            if action_def.transitions_to:
                session.current_screen = action_def.transitions_to

            session.log(action_id, params, "success")

            # Include next screen info so agent doesn't need a separate get_screen call
            next_screen = self._screens.get(session.current_screen)
            next_actions = []
            if next_screen:
                for a in next_screen.actions:
                    if a.id not in session.permissions.denied_actions:
                        status = "available"
                        if a.id in session.permissions.confirmation_required:
                            status = "requires_confirmation"
                        next_actions.append({
                            "id": a.id,
                            "name": a.name,
                            "description": a.description,
                            "status": status,
                            "parameters": [
                                {"name": p.name, "type": p.type, "required": p.required,
                                 "description": p.description, "enum_values": p.enum_values,
                                 "min": p.min_value, "max": p.max_value, "default": p.default}
                                for p in a.parameters
                            ],
                        })

            return {
                "status": "ok",
                "data": result.get("data"),
                "screen": session.current_screen,
                "screen_name": next_screen.name if next_screen else "",
                "message": f"Action '{action_id}' executed successfully.",
                "next_actions": next_actions,
            }
        else:
            session.log(action_id, params, "error", error_message=result.get("message"))
            return result

    def get_audit_log(self, session_id: str) -> list[dict]:
        """Get the full audit log for a session."""
        session = self._get_session(session_id)
        return [entry.model_dump(mode="json") for entry in session.audit_log]

    def disconnect(self, session_id: str) -> dict:
        """End a session."""
        session = self._get_session(session_id)
        session.log("disconnect", {}, "success")
        log = self.get_audit_log(session_id)
        del self.sessions[session_id]
        return {
            "status": "disconnected",
            "actions_performed": len(log),
            "audit_log": log,
        }

    # ── Private helpers ──

    def _get_session(self, session_id: str) -> TerminalSession:
        if session_id not in self.sessions:
            raise ValueError(f"Session not found: {session_id}")
        return self.sessions[session_id]

    def _fetch_screen_data(self, screen: ScreenDefinition, session: TerminalSession) -> dict:
        """Fetch live data for a screen from the real website."""
        # Find the primary GET action for this screen
        for action in screen.actions:
            if action.api_method == "GET" and action.api_endpoint:
                result = self.proxy.execute(action, {}, session.state)
                if result["status"] == "ok":
                    return result.get("data", {})

        # Try fetching cart data for cart screens
        if "cart" in screen.id and "cart_id" in session.state:
            cart_action = ActionDefinition(
                id="_fetch_cart", name="Fetch", description="",
                api_endpoint=f"/api/cart/{session.state['cart_id']}",
                api_method="GET",
            )
            result = self.proxy.execute(cart_action, {}, session.state)
            if result["status"] == "ok":
                return result.get("data", {})

        return {}

    def _update_session_state(self, session: TerminalSession,
                              action: ActionDefinition, result: dict):
        """Extract useful state from action results."""
        data = result.get("data", {})

        # Track auth token from login/register responses
        if "token" in data:
            session.state["auth_token"] = data["token"]
            session.state["logged_in"] = True
            if "user_id" in data:
                session.state["user_id"] = data["user_id"]
            if "name" in data:
                session.state["user_name"] = data["name"]
            if "email" in data:
                session.state["user_email"] = data["email"]

            # Auto-create cart after login if we don't have one
            if "cart_id" not in session.state:
                cart_result = self.proxy.execute(
                    ActionDefinition(id="_init_cart", name="Init", description="",
                                     api_endpoint="/api/cart", api_method="POST"),
                    {}, session.state
                )
                if cart_result["status"] == "ok" and "data" in cart_result:
                    cart_data = cart_result["data"]
                    if "cart" in cart_data:
                        session.state["cart_id"] = cart_data["cart"]["id"]

        # Track cart_id
        if "cart" in data and "id" in data["cart"]:
            session.state["cart_id"] = data["cart"]["id"]

        # Track order_id
        if "order" in data and "id" in data["order"]:
            session.state["order_id"] = data["order"]["id"]

        # Track reservation_id
        if "reservation" in data and "id" in data["reservation"]:
            session.state["res_id"] = data["reservation"]["id"]

        # Track request_id (gov service)
        if "request" in data and "id" in data["request"]:
            session.state["req_id"] = data["request"]["id"]

    def _check_rate_limit(self, session: TerminalSession) -> bool:
        """Simple rate limit check based on action count."""
        limits = self.contract.rate_limits
        if limits.requests_per_minute:
            # Count actions in last minute
            one_min_ago = datetime.now(timezone.utc).timestamp() - 60
            recent = [e for e in session.audit_log
                      if datetime.fromisoformat(e.timestamp).timestamp() > one_min_ago]
            if len(recent) >= limits.requests_per_minute:
                return False
        return True
