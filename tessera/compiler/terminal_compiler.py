"""
Tessera Terminal Compiler

Takes raw routes (from source_parser or owner_discovery) and generates
a complete flat-terminal contract with:
  - Tools (one per route, with params and preconditions)
  - Flow map (data dependency chains)
  - Roles (primary/secondary/navigation)
  - Preconditions (what each tool needs to run)

This is the bridge between the compiler and the terminal — the piece
that was missing. Previously, contracts were hand-written.

Usage:
    from tessera.compiler.terminal_compiler import compile_terminal
    
    contract = compile_terminal(
        routes=source_parser.parse(),      # raw routes
        site_name="My Shop",
        site_url="http://localhost:8000",
    )
    
    # Save
    with open("contract.json", "w") as f:
        json.dump(contract.model_dump(), f, indent=2)
"""
import re
from typing import Optional
from tessera.contract.schema import (
    TesseraContract, ScreenDefinition, ActionDefinition,
    ActionParameter, ActionRole, ActionPermission,
    RateLimit, DataField,
)


# ── Route Analysis ──

def _group_routes(routes: list[dict]) -> dict[str, list[dict]]:
    """Group routes by URL prefix (resource name)."""
    groups = {}
    for route in routes:
        path = route.get("path", "")
        # Strip common API prefixes
        clean = re.sub(r'^/api/(v\d+/)?', '', path).strip('/')
        # Take the first segment as group name
        parts = clean.split('/')
        group = parts[0] if parts else "root"
        groups.setdefault(group, []).append(route)
    return groups


def _infer_auth(route: dict) -> bool:
    """Infer whether a route requires authentication."""
    # Check explicit auth flag from source parser
    if route.get("auth"):
        return True
    # Check for auth-related decorators/middleware hints
    if route.get("depends_on_auth"):
        return True
    return False


def _infer_params(route: dict) -> list[ActionParameter]:
    """Infer parameters from the route path and method."""
    params = []
    path = route.get("path", "")

    # Extract path parameters like {product_id}, {cart_id}
    path_params = re.findall(r'\{(\w+)\}', path)
    for pp in path_params:
        params.append(ActionParameter(
            name=pp, type="string", required=True,
            description=f"The {pp.replace('_', ' ')}",
        ))

    # Common query params for GET endpoints
    method = route.get("method", "GET")
    if method == "GET":
        group = _get_group(path)
        if group in ("products", "items", "rooms", "documents", "hotels"):
            # List endpoint — add search params
            if '{' not in path:  # list, not detail
                params.append(ActionParameter(
                    name="q", type="string", required=False,
                    description="Search query",
                ))

    # Common body params for POST endpoints
    if method == "POST":
        if "login" in path:
            params.extend([
                ActionParameter(name="email", type="string", required=True),
                ActionParameter(name="password", type="string", required=True),
            ])
        elif "register" in path:
            params.extend([
                ActionParameter(name="email", type="string", required=True),
                ActionParameter(name="password", type="string", required=True),
                ActionParameter(name="name", type="string", required=True),
            ])
        elif "add" in path:
            # Add-to-cart type
            if "product_id" not in [p.name for p in params]:
                params.append(ActionParameter(
                    name="product_id", type="string", required=True,
                ))
            params.append(ActionParameter(
                name="quantity", type="integer", required=False,
                description="Quantity to add (default: 1)",
            ))
        elif "checkout" in path or "order" in path:
            params.extend([
                ActionParameter(name="shipping_name", type="string", required=True),
                ActionParameter(name="shipping_address", type="string", required=True),
            ])

    return params


def _get_group(path: str) -> str:
    """Get the resource group from a path."""
    clean = re.sub(r'^/api/(v\d+/)?', '', path).strip('/')
    parts = clean.split('/')
    return parts[0] if parts else "root"


def _infer_role(route: dict) -> ActionRole:
    """Infer the action role from HTTP method and path."""
    method = route.get("method", "GET")
    path = route.get("path", "")

    # Auth endpoints are primary (gate everything)
    if "login" in path:
        return ActionRole.PRIMARY

    # POST endpoints that change state are primary
    if method == "POST":
        if any(w in path for w in ("checkout", "order", "book", "request", "add")):
            return ActionRole.PRIMARY

    # GET list/search endpoints are primary (main discovery)
    if method == "GET" and '{' not in path:
        group = _get_group(path)
        if group not in ("auth",):
            return ActionRole.PRIMARY

    # GET detail endpoints are primary
    if method == "GET" and '{' in path:
        return ActionRole.PRIMARY

    # DELETE, update, remove are secondary
    if method in ("DELETE", "PATCH", "PUT"):
        return ActionRole.SECONDARY
    if any(w in path for w in ("remove", "update", "delete")):
        return ActionRole.SECONDARY

    # Profile, history are navigation
    if any(w in path for w in ("profile", "history")):
        return ActionRole.NAVIGATION

    # Register is secondary
    if "register" in path:
        return ActionRole.SECONDARY

    return ActionRole.SECONDARY


def _infer_preconditions(route: dict) -> list[str]:
    """Infer what must be true before this action can succeed."""
    preconds = []
    path = route.get("path", "")
    method = route.get("method", "GET")

    # Auth required
    if _infer_auth(route):
        preconds.append("logged_in")

    # Path params imply prior data
    if "{cart_id}" in path:
        preconds.append("has_cart")
    if "{product_id}" in path and method != "GET":
        preconds.append("has_product_id")
    if "{order_id}" in path:
        preconds.append("has_order")

    # Checkout implies cart exists and has items
    if "checkout" in path:
        preconds.append("cart_not_empty")

    return preconds


def _make_tool_id(route: dict) -> str:
    """Generate a clean tool ID from the route."""
    method = route.get("method", "GET").lower()
    path = route.get("path", "")

    # Clean path
    clean = re.sub(r'^/api/(v\d+/)?', '', path).strip('/')
    clean = re.sub(r'\{(\w+)\}', '', clean)  # remove path params
    clean = clean.strip('/').replace('/', '_')

    # For GET list vs detail
    has_path_param = '{' in path

    # Special names
    if "login" in clean:
        return "login"
    if "register" in clean:
        return "register"
    if "checkout" in clean:
        return "checkout"
    if "profile" in clean:
        return "view_profile"

    # For CRUD, use descriptive names
    if method == "get":
        if has_path_param:
            return f"view_{clean.rstrip('_')}"
        else:
            return f"list_{clean}" if clean else "list"
    elif method == "post":
        if "add" in clean:
            base = clean.split('_add')[0].rstrip('_')
            return f"add_to_{base}" if base else "add"
        elif "remove" in clean:
            base = clean.split('_remove')[0].rstrip('_')
            return f"remove_from_{base}" if base else "remove"
        elif "update" in clean:
            base = clean.split('_update')[0].rstrip('_')
            return f"update_{base}" if base else "update"
        else:
            return f"create_{clean.rstrip('_')}" if clean else "create"
    elif method == "put" or method == "patch":
        return f"update_{clean.rstrip('_')}"
    elif method == "delete":
        return f"delete_{clean.rstrip('_')}"

    return f"{method}_{clean}".rstrip('_').replace('__', '_')


def _infer_description(route: dict, tool_id: str) -> str:
    """Generate a human-readable description."""
    # Use docstring if available
    if route.get("docstring"):
        return route["docstring"]

    method = route.get("method", "GET")
    path = route.get("path", "")
    group = _get_group(path)

    descriptions = {
        "login": "Login with email and password to get an auth token.",
        "register": "Create a new account.",
        "view_profile": "View your account profile.",
        "checkout": "Place an order from your cart. Requires login and items in cart.",
    }
    if tool_id in descriptions:
        return descriptions[tool_id]

    if method == "GET" and '{' not in path:
        return f"List/search {group}."
    if method == "GET" and '{' in path:
        return f"View details of a specific {group.rstrip('s')}."
    if method == "POST" and "add" in path:
        return f"Add an item to the {group}."
    if method == "POST":
        return f"Create a new {group.rstrip('s')}."
    if method == "DELETE":
        return f"Delete a {group.rstrip('s')}."

    return f"{method} {path}"


# ── Flow Inference ──

def _infer_flow(tools: list[dict]) -> list[dict]:
    """Infer the flow map from tools and their preconditions.
    
    Returns paths like:
      {"name": "purchase", "steps": ["login", "list_products", "add_to_cart", "checkout"]}
    """
    paths = []

    # Find the tool chain by following preconditions
    # Start: tools with no preconditions (entry points)
    # End: tools that produce final state (checkout, book, submit)

    tool_lookup = {t["id"]: t for t in tools}

    # Identify terminal tools (the final actions)
    terminal_ids = [t["id"] for t in tools
                    if any(w in t["id"] for w in ("checkout", "book", "submit", "order", "request"))]

    # Build dependency chains
    for terminal_id in terminal_ids:
        chain = _build_chain(terminal_id, tools, tool_lookup)
        if chain:
            paths.append({
                "name": terminal_id.replace("_", " "),
                "steps": chain,
                "description": f"Complete flow to {terminal_id.replace('_', ' ')}",
            })

    # Add a browse path (GET endpoints with no auth)
    browse_tools = [t["id"] for t in tools
                    if t.get("method") == "GET" and "logged_in" not in t.get("preconditions", [])
                    and t.get("role") == "primary"]
    if browse_tools:
        paths.append({
            "name": "browse",
            "steps": browse_tools[:3],
            "description": "Browse without logging in",
        })

    return paths


def _build_chain(target_id: str, tools: list[dict], lookup: dict) -> list[str]:
    """Build a dependency chain to reach a target tool."""
    target = lookup.get(target_id)
    if not target:
        return []

    chain = []
    preconds = target.get("preconditions", [])

    # If needs login, start with login
    if "logged_in" in preconds:
        chain.append("login")

    # If needs product_id, need to search first
    if "has_product_id" in preconds:
        # Find the list endpoint for this resource group
        list_tools = [t for t in tools if t["id"].startswith("list_") and t.get("role") == "primary"]
        if list_tools:
            chain.append(list_tools[0]["id"])

    # If needs cart, need to create cart + add items
    if "has_cart" in preconds or "cart_not_empty" in preconds:
        add_tools = [t for t in tools if "add_to" in t["id"]]
        if add_tools:
            chain.append(add_tools[0]["id"])

    chain.append(target_id)
    return chain


# ── Flow Hints ──

def _generate_hints(groups: dict, paths: list[dict]) -> list[str]:
    """Generate flow hints from the inferred paths."""
    hints = []

    for path in paths:
        steps_str = " → ".join(path["steps"])
        hints.append(f"To {path['name']}: {steps_str}")

    return hints


# ── Main Compiler ──

def compile_terminal(
    routes: list[dict],
    site_name: str,
    site_url: str,
    api_prefix: str = "/api",
    max_transaction: float = 500.0,
    max_daily_spend: float = 2000.0,
    requests_per_minute: int = 60,
) -> TesseraContract:
    """
    Compile raw routes into a complete Tessera contract.

    Args:
        routes: list of route dicts from source_parser.parse()
                Each: {"method": "GET", "path": "/api/products", "auth": True, ...}
        site_name: human-readable site name
        site_url: URL of the site
        api_prefix: API prefix to strip
        max_transaction: per-transaction spend ceiling
        max_daily_spend: daily spend ceiling
        requests_per_minute: rate limit

    Returns:
        A complete TesseraContract ready for the flat terminal.
    """
    # Group routes
    groups = _group_routes(routes)

    # Build tools from routes
    compiled_tools = []
    for route in routes:
        path = route.get("path", "")
        # Skip non-API routes
        if not path.startswith("/api"):
            continue

        tool_id = _make_tool_id(route)
        params = _infer_params(route)
        role = _infer_role(route)
        preconds = _infer_preconditions(route)
        description = _infer_description(route, tool_id)

        # Determine permission
        permission = ActionPermission.ALLOWED
        if any(w in tool_id for w in ("checkout", "order", "book")):
            permission = ActionPermission.REQUIRES_CONFIRMATION

        compiled_tools.append({
            "id": tool_id,
            "route": route,
            "params": params,
            "role": role,
            "preconditions": preconds,
            "description": description,
            "permission": permission,
            "method": route.get("method", "GET"),
        })

    # Infer flow
    flow_paths = _infer_flow(compiled_tools)
    hints = _generate_hints(groups, flow_paths)

    # Build a single "flat" screen with all tools
    actions = []
    for tool in compiled_tools:
        actions.append(ActionDefinition(
            id=tool["id"],
            name=tool["id"].replace("_", " ").title(),
            description=tool["description"],
            role=tool["role"],
            permission=tool["permission"],
            parameters=tool["params"],
            preconditions=tool["preconditions"],
            api_endpoint=tool["route"].get("path"),
            api_method=tool["method"],
        ))

    screen = ScreenDefinition(
        id="main",
        name=site_name,
        description=f"All tools for {site_name}",
        flow_hint=" | ".join(hints) if hints else None,
        actions=actions,
    )

    # Build contract
    contract = TesseraContract(
        contract_id=f"tessera_{site_name.lower().replace(' ', '_')}",
        site_name=site_name,
        site_url=site_url,
        description=f"Auto-compiled terminal for {site_name}",
        entry_screen="main",
        screens=[screen],
        rate_limits=RateLimit(
            requests_per_minute=requests_per_minute,
            max_transaction_amount=max_transaction,
            max_daily_spend=max_daily_spend,
            max_concurrent_sessions=10,
        ),
    )

    # Attach flow metadata (not in schema, but used by flat terminal)
    contract._flow_paths = flow_paths
    contract._flow_hints = hints

    return contract
