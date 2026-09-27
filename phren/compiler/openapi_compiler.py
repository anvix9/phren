"""
Phren OpenAPI Compiler

Scans a website and generates a Phren contract automatically.

The compiler works in phases:
  1. API Discovery   - Fetch and parse OpenAPI/Swagger spec
  2. HTML Scan       - Crawl the frontend, extract accessibility tree / interactive elements
  3. Flow Inference  - Map UI actions to API calls, infer screen transitions
  4. Contract Gen    - Produce the contract with screens, actions, and default governance

The output is a first-pass contract that the website owner can refine.
"""
import json
import re
import requests
from typing import Optional
from dataclasses import dataclass, field

from phren.contract.schema import (
    PhrenContract, ContractTier,
    ScreenDefinition, DataField,
    ActionDefinition, ActionParameter, ActionPermission,
    RateLimit, DataTerms, DataRetention,
    ActionTrustRequirement, AgentTrust,
)


# ── Phase 1: API Discovery ──

@dataclass
class DiscoveredEndpoint:
    """An API endpoint discovered from the OpenAPI spec."""
    path: str
    method: str
    operation_id: str
    summary: str
    description: str
    parameters: list[dict] = field(default_factory=list)     # Query/path params
    request_body: Optional[dict] = None                       # POST body schema
    response_schema: Optional[dict] = None                    # Response model
    tags: list[str] = field(default_factory=list)


class APIDiscovery:
    """Phase 1: Discover API endpoints from OpenAPI spec."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.spec: dict = {}
        self.endpoints: list[DiscoveredEndpoint] = []

    def fetch_spec(self) -> dict:
        """Try common OpenAPI spec paths."""
        paths_to_try = ["/openapi.json", "/swagger.json", "/api/docs", "/docs/openapi.json"]
        for path in paths_to_try:
            try:
                resp = requests.get(f"{self.base_url}{path}", timeout=5)
                if resp.status_code == 200:
                    self.spec = resp.json()
                    print(f"[API Discovery] Found OpenAPI spec at {path}")
                    print(f"  Title: {self.spec.get('info', {}).get('title', 'Unknown')}")
                    print(f"  Paths: {len(self.spec.get('paths', {}))}")
                    return self.spec
            except Exception:
                continue
        print("[API Discovery] No OpenAPI spec found — falling back to HTML-only scan")
        return {}

    def parse_endpoints(self) -> list[DiscoveredEndpoint]:
        """Extract structured endpoint data from the spec."""
        if not self.spec:
            return []

        schemas = self.spec.get("components", {}).get("schemas", {})

        for path, methods in self.spec.get("paths", {}).items():
            for method, details in methods.items():
                if method in ("get", "post", "put", "patch", "delete"):
                    endpoint = DiscoveredEndpoint(
                        path=path,
                        method=method.upper(),
                        operation_id=details.get("operationId", ""),
                        summary=details.get("summary", ""),
                        description=details.get("description", ""),
                        tags=details.get("tags", []),
                    )

                    # Parse query/path parameters
                    for param in details.get("parameters", []):
                        endpoint.parameters.append({
                            "name": param["name"],
                            "in": param.get("in", "query"),
                            "required": param.get("required", False),
                            "type": self._resolve_type(param.get("schema", {}), schemas),
                            "description": param.get("description", ""),
                            "enum": self._resolve_enum(param.get("schema", {}), schemas),
                            "minimum": self._resolve_min(param.get("schema", {})),
                            "maximum": self._resolve_max(param.get("schema", {})),
                            "default": self._resolve_default(param.get("schema", {})),
                        })

                    # Parse request body
                    body = details.get("requestBody", {})
                    if body:
                        content = body.get("content", {})
                        json_content = content.get("application/json", {})
                        body_schema = json_content.get("schema", {})
                        if "$ref" in body_schema:
                            ref_name = body_schema["$ref"].split("/")[-1]
                            body_schema = schemas.get(ref_name, {})
                        endpoint.request_body = body_schema

                    # Parse response
                    resp_200 = details.get("responses", {}).get("200", {})
                    resp_content = resp_200.get("content", {}).get("application/json", {})
                    resp_schema = resp_content.get("schema", {})
                    if "$ref" in resp_schema:
                        ref_name = resp_schema["$ref"].split("/")[-1]
                        resp_schema = schemas.get(ref_name, {})
                    endpoint.response_schema = resp_schema

                    self.endpoints.append(endpoint)

        print(f"[API Discovery] Parsed {len(self.endpoints)} endpoints")
        return self.endpoints

    def _resolve_type(self, schema: dict, schemas: dict) -> str:
        """Resolve a JSON Schema type to a simple type string."""
        if "$ref" in schema:
            ref_name = schema["$ref"].split("/")[-1]
            ref_schema = schemas.get(ref_name, {})
            if ref_schema.get("enum"):
                return "enum"
            return ref_schema.get("type", "string")

        # Handle anyOf (nullable types)
        if "anyOf" in schema:
            for option in schema["anyOf"]:
                if option.get("type") != "null":
                    if "$ref" in option:
                        return self._resolve_type(option, schemas)
                    return option.get("type", "string")

        return schema.get("type", "string")

    def _resolve_enum(self, schema: dict, schemas: dict) -> Optional[list[str]]:
        """Extract enum values if present."""
        if "$ref" in schema:
            ref_name = schema["$ref"].split("/")[-1]
            ref_schema = schemas.get(ref_name, {})
            return ref_schema.get("enum")
        if "anyOf" in schema:
            for option in schema["anyOf"]:
                if "$ref" in option:
                    ref_name = option["$ref"].split("/")[-1]
                    ref_schema = schemas.get(ref_name, {})
                    if ref_schema.get("enum"):
                        return ref_schema["enum"]
        return schema.get("enum")

    def _resolve_min(self, schema: dict) -> Optional[float]:
        if "anyOf" in schema:
            for opt in schema["anyOf"]:
                if "minimum" in opt:
                    return opt["minimum"]
        return schema.get("minimum")

    def _resolve_max(self, schema: dict) -> Optional[float]:
        if "anyOf" in schema:
            for opt in schema["anyOf"]:
                if "maximum" in opt:
                    return opt["maximum"]
        return schema.get("maximum")

    def _resolve_default(self, schema: dict) -> Optional[str]:
        val = schema.get("default")
        return str(val) if val is not None else None


# ── Phase 2: HTML Scan ──

class HTMLScanner:
    """
    Phase 2: Scan the website frontend.

    Handles both server-rendered HTML and SPAs:
    - Server-rendered: parse forms, buttons, links directly from HTML
    - SPA detection: if HTML body is mostly empty (React/Vue mount point),
      analyze JavaScript source for fetch()/axios API calls and route definitions
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.html: str = ""
        self.interactive_elements: list[dict] = []
        self.is_spa: bool = False
        self.spa_framework: str = ""

    def fetch_homepage(self) -> str:
        """Fetch the main page HTML."""
        try:
            resp = requests.get(self.base_url, timeout=5)
            self.html = resp.text
            print(f"[HTML Scan] Fetched homepage ({len(self.html)} chars)")
            self._detect_spa()
            return self.html
        except Exception as e:
            print(f"[HTML Scan] Failed to fetch: {e}")
            return ""

    def _detect_spa(self):
        """Detect if the site is a Single Page Application."""
        spa_indicators = [
            (r'<div\s+id=["\']root["\']>\s*(<div[^>]*>.*?</div>)?\s*</div>', 'React'),
            (r'<div\s+id=["\']app["\']>\s*</div>', 'Vue/Generic'),
            (r'<div\s+id=["\']__next["\']', 'Next.js'),
            (r'<div\s+id=["\']__nuxt["\']', 'Nuxt'),
            (r'<app-root[^>]*>\s*</app-root>', 'Angular'),
        ]
        for pattern, framework in spa_indicators:
            if re.search(pattern, self.html, re.DOTALL):
                self.is_spa = True
                self.spa_framework = framework
                break

        # Check: does the body have very little text content vs script size?
        body_match = re.search(r'<body[^>]*>(.*?)</body>', self.html, re.DOTALL)
        if body_match:
            body = body_match.group(1)
            text_only = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL)
            text_only = re.sub(r'<style[^>]*>.*?</style>', '', text_only, flags=re.DOTALL)
            text_only = re.sub(r'<[^>]+>', '', text_only).strip()
            if len(text_only) < 100:
                self.is_spa = True

        # Detect framework from script tags
        if not self.spa_framework:
            html_lower = self.html.lower()
            if 'react' in html_lower:
                self.spa_framework = 'React'
            elif 'vue' in html_lower:
                self.spa_framework = 'Vue'
            elif 'angular' in html_lower:
                self.spa_framework = 'Angular'

        if self.is_spa:
            print(f"[HTML Scan] SPA detected! Framework: {self.spa_framework or 'unknown'}")
            print(f"[HTML Scan] Strategy: analyze JS source for API calls + use OpenAPI spec")
        else:
            print(f"[HTML Scan] Server-rendered HTML detected")

    def extract_elements(self) -> list[dict]:
        """Extract interactive elements from HTML and JavaScript source."""
        elements = []

        # ── Strategy 1: Traditional HTML parsing ──
        for match in re.finditer(r'<button[^>]*onclick=["\']([^"\']+)["\'][^>]*>(.*?)</button>', self.html, re.DOTALL):
            onclick = match.group(1)
            label = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            elements.append({"type": "button", "label": label, "onclick": onclick})

        for match in re.finditer(r'<form[^>]*action=["\']([^"\']*)["\'][^>]*method=["\']([^"\']*)["\']', self.html):
            elements.append({"type": "form", "action": match.group(1), "method": match.group(2)})

        for match in re.finditer(r'<input[^>]*id=["\']([^"\']+)["\'][^>]*placeholder=["\']([^"\']*)["\']', self.html):
            elements.append({"type": "input", "id": match.group(1), "placeholder": match.group(2)})

        for match in re.finditer(r'<select[^>]*id=["\']([^"\']+)["\'][^>]*>(.*?)</select>', self.html, re.DOTALL):
            select_id = match.group(1)
            options = re.findall(r'<option[^>]*value=["\']([^"\']*)["\'][^>]*>(.*?)</option>', match.group(2))
            elements.append({"type": "select", "id": select_id, "options": options})

        # ── Strategy 2: JavaScript source analysis (SPAs) ──
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', self.html, re.DOTALL)
        all_js = '\n'.join(scripts)

        # Find fetch() calls
        seen_urls = set()
        for match in re.finditer(r"""fetch\s*\(\s*[`'"](.*?)[`'"]\s*""", all_js):
            url = match.group(1)
            if url not in seen_urls:
                elements.append({"type": "api_call", "url": url, "source": "fetch"})
                seen_urls.add(url)

        # Find custom API helper calls: api('/endpoint', ...) or api(`/endpoint/${id}`, ...)
        for match in re.finditer(r"""(?<!\w)api\s*\(\s*[`'"]([^`'"]*)[`'"]""", all_js):
            url = match.group(1)
            # Normalize template literals: /cart/${cid}/add → /cart/{id}/add
            normalized = re.sub(r'\$\{[^}]+\}', '{id}', url)
            if normalized not in seen_urls:
                elements.append({"type": "api_call", "url": normalized, "source": "api_helper"})
                seen_urls.add(normalized)

        # Find API path concatenation: API + '/endpoint'
        for match in re.finditer(r"""(?:API|api_url|BASE_URL|baseUrl)\s*\+\s*[`'"](/[^`'"]+)[`'"]""", all_js):
            url = match.group(1)
            if url not in seen_urls:
                elements.append({"type": "api_call", "url": url, "source": "concat"})
                seen_urls.add(url)

        # Find axios calls
        for match in re.finditer(r"""axios\.(get|post|put|delete)\s*\(\s*[`'"](.*?)[`'"]""", all_js):
            url = match.group(2)
            if url not in seen_urls:
                elements.append({"type": "api_call", "url": url, "method": match.group(1).upper(), "source": "axios"})
                seen_urls.add(url)

        # Find API string literals
        for match in re.finditer(r"""['"](/api/[^'"]+)['"]""", all_js):
            url = match.group(1)
            if url not in seen_urls:
                elements.append({"type": "api_call", "url": url, "source": "string_literal"})
                seen_urls.add(url)

        # Find API base URL definition
        for match in re.finditer(r"""(?:const|let|var)\s+(?:API|api_url|BASE_URL|apiBase)\s*=\s*[`'"](.*?)[`'"]""", all_js):
            elements.append({"type": "api_base", "url": match.group(1)})

        # Find client-side route definitions
        for match in re.finditer(r"""(?:path|route)\s*[:=]\s*[`'"](/[^`'"]*)[`'"]""", all_js):
            elements.append({"type": "route", "path": match.group(1)})

        # Find localStorage usage (hints at auth)
        for match in re.finditer(r"""localStorage\.(setItem|getItem|removeItem)\s*\(\s*[`'"](.*?)[`'"]""", all_js):
            elements.append({"type": "storage", "operation": match.group(1), "key": match.group(2)})

        self.interactive_elements = elements

        # Report
        api_calls = [e for e in elements if e["type"] == "api_call"]
        html_els = [e for e in elements if e["type"] in ("button", "form", "input", "select")]
        routes = [e for e in elements if e["type"] == "route"]
        storage = [e for e in elements if e["type"] == "storage"]

        print(f"[HTML Scan] Found {len(elements)} elements:")
        if html_els:
            print(f"  HTML interactive: {len(html_els)}")
        if api_calls:
            print(f"  API calls in JS:  {len(api_calls)}")
            for e in api_calls[:10]:
                print(f"    {e.get('method', 'GET'):6} {e['url']}  ({e.get('source', '')})")
        if routes:
            print(f"  Client routes:    {len(routes)}")
        if storage:
            print(f"  localStorage:     {len(storage)} (auth hint)")

        return elements


# ── Phase 2.5: API Probing ──

class APIProber:
    """
    When no OpenAPI spec is available and JS analysis gives partial results,
    probe the API by trying common endpoint patterns.
    
    Strategy:
    - From discovered endpoints, infer the API base path (e.g., /api)
    - Try common REST patterns: /api/menu, /api/users, /api/auth/login, etc.
    - For each hit, record the HTTP method and response shape
    - Try GET with/without auth, POST with empty body to discover protected endpoints
    """

    COMMON_ENDPOINTS = [
        # Auth
        ("POST", "/auth/login"),
        ("POST", "/auth/register"),
        ("GET", "/auth/profile"),
        ("POST", "/auth/logout"),
        # Common resources
        ("GET", "/menu"),
        ("GET", "/products"),
        ("GET", "/items"),
        ("GET", "/categories"),
        ("GET", "/restaurants"),
        ("GET", "/availability"),
        ("GET", "/reservations"),
        ("GET", "/orders"),
        ("GET", "/bookings"),
        ("GET", "/users/me"),
        ("GET", "/cart"),
        ("GET", "/departments"),
        ("GET", "/documents"),
        ("GET", "/search"),
        # Info
        ("GET", "/restaurant/info"),
        ("GET", "/info"),
        ("GET", "/config"),
        ("GET", "/status"),
    ]

    def __init__(self, base_url: str, api_prefix: str = "/api"):
        self.base_url = base_url.rstrip("/")
        self.api_prefix = api_prefix
        self.discovered: list[dict] = []

    def probe(self, known_endpoints: list[str] = None) -> list[dict]:
        """
        Probe the API for endpoints.
        known_endpoints: URLs already discovered from JS analysis (to skip)
        """
        known = set(known_endpoints or [])
        print(f"[API Probe] Probing {self.base_url}{self.api_prefix} for endpoints...")

        for method, path in self.COMMON_ENDPOINTS:
            full_path = self.api_prefix + path
            if full_path in known or path in known:
                continue

            url = self.base_url + full_path
            try:
                if method == "GET":
                    resp = requests.get(url, timeout=3)
                elif method == "POST":
                    resp = requests.post(url, json={}, timeout=3, headers={"Content-Type": "application/json"})
                else:
                    continue

                if resp.status_code == 404:
                    continue
                if resp.status_code == 405:
                    # Method not allowed — endpoint exists but with different method
                    alt_method = "POST" if method == "GET" else "GET"
                    self.discovered.append({
                        "path": full_path,
                        "method": alt_method,
                        "status": 405,
                        "note": f"Exists but requires {alt_method}",
                    })
                    continue

                # Endpoint exists!
                is_protected = resp.status_code == 401
                is_error = resp.status_code == 422  # Validation error = endpoint exists but needs params
                is_ok = resp.status_code < 400

                response_data = None
                try:
                    response_data = resp.json()
                except Exception:
                    pass

                entry = {
                    "path": full_path,
                    "method": method,
                    "status": resp.status_code,
                    "protected": is_protected,
                    "needs_params": is_error,
                }

                # Infer response shape
                if response_data and isinstance(response_data, dict):
                    entry["response_keys"] = list(response_data.keys())[:10]
                    # Try to detect list endpoints
                    for key in response_data:
                        if isinstance(response_data[key], list):
                            entry["list_field"] = key
                            entry["list_count"] = len(response_data[key])

                self.discovered.append(entry)

            except requests.exceptions.ConnectionError:
                continue
            except requests.exceptions.Timeout:
                continue
            except Exception:
                continue

        # Report
        hits = [d for d in self.discovered if d["status"] != 405]
        protected = [d for d in hits if d.get("protected")]
        public = [d for d in hits if not d.get("protected") and d["status"] < 400]

        print(f"[API Probe] Found {len(hits)} endpoints:")
        if public:
            print(f"  Public ({len(public)}):")
            for e in public:
                keys = f" → keys: {e['response_keys']}" if e.get('response_keys') else ""
                print(f"    {e['method']:6} {e['path']}{keys}")
        if protected:
            print(f"  Protected ({len(protected)}):")
            for e in protected:
                print(f"    {e['method']:6} {e['path']} (requires auth)")

        return self.discovered


# ── Phase 3: Flow Inference ──

class FlowInference:
    """Phase 3: Infer screens, actions, and transitions from discovered data."""

    def __init__(self, endpoints: list[DiscoveredEndpoint], html_elements: list[dict]):
        self.endpoints = endpoints
        self.html_elements = html_elements
        self.screens: list[ScreenDefinition] = []

    def infer(self) -> list[ScreenDefinition]:
        """
        Map API endpoints to screens and actions.
        
        Heuristics:
        - GET endpoints that return lists → search/browse screens
        - GET endpoints with {id} → detail screens
        - POST endpoints on a resource → actions on that screen
        - Group by URL prefix to cluster related endpoints
        """
        # Group endpoints by resource
        groups: dict[str, list[DiscoveredEndpoint]] = {}
        for ep in self.endpoints:
            # Extract resource from path: /api/products/{id} → "products"
            parts = ep.path.strip("/").split("/")
            resource = parts[1] if len(parts) > 1 else parts[0]
            groups.setdefault(resource, []).append(ep)

        print(f"[Flow Inference] Found {len(groups)} resource groups: {list(groups.keys())}")

        for resource, eps in groups.items():
            self._infer_resource_screens(resource, eps)

        # Infer transitions between screens
        self._infer_transitions()

        print(f"[Flow Inference] Generated {len(self.screens)} screens")
        return self.screens

    def _infer_resource_screens(self, resource: str, endpoints: list[DiscoveredEndpoint]):
        """Generate screens and actions for a resource group."""

        # Separate by pattern
        list_ep = None       # GET /api/resource
        detail_ep = None     # GET /api/resource/{id}
        create_ep = None     # POST /api/resource
        sub_actions = []     # POST /api/resource/{id}/action

        for ep in endpoints:
            path_parts = ep.path.strip("/").split("/")
            has_id_param = any("{" in p for p in path_parts)
            tail = path_parts[-1] if path_parts else ""
            has_sub_action = has_id_param and not tail.startswith("{")

            if ep.method == "GET" and not has_id_param:
                list_ep = ep
            elif ep.method == "GET" and has_id_param and not has_sub_action:
                detail_ep = ep
            elif ep.method == "POST" and not has_id_param:
                create_ep = ep
            elif ep.method == "POST" and has_sub_action:
                sub_actions.append(ep)
            elif ep.method == "POST" and has_id_param:
                sub_actions.append(ep)

        # Build list/search screen
        if list_ep:
            screen = ScreenDefinition(
                id=f"{resource}_list",
                name=f"{resource.replace('_', ' ').title()} list",
                description=list_ep.description or list_ep.summary or f"Browse {resource}",
                data_fields=self._extract_response_fields(list_ep),
                actions=[self._endpoint_to_action(list_ep, f"search_{resource}")],
            )
            # Add "view detail" action if detail endpoint exists
            if detail_ep:
                id_param_name = self._extract_path_param(detail_ep.path)
                screen.actions.append(ActionDefinition(
                    id=f"view_{resource.rstrip('s')}",
                    name=f"View {resource.rstrip('s')} detail",
                    description=detail_ep.description or detail_ep.summary or f"View a single {resource.rstrip('s')}",
                    parameters=[ActionParameter(
                        name=id_param_name, type="string", required=True,
                        description=f"{resource.rstrip('s').title()} ID"
                    )],
                    transitions_to=f"{resource.rstrip('s')}_detail",
                    api_endpoint=detail_ep.path,
                    api_method=detail_ep.method,
                ))
            self.screens.append(screen)

        # Build detail screen
        if detail_ep:
            detail_screen = ScreenDefinition(
                id=f"{resource.rstrip('s')}_detail",
                name=f"{resource.rstrip('s').replace('_', ' ').title()} detail",
                description=detail_ep.description or detail_ep.summary or f"View {resource.rstrip('s')} details",
                parameters=[self._extract_path_param(detail_ep.path)],
                data_fields=self._extract_response_fields(detail_ep),
                actions=[],
                parent=f"{resource}_list",
            )
            self.screens.append(detail_screen)

        # Build create/action screen
        if create_ep:
            # Could be "create cart" or "checkout" — infer from name
            action = self._endpoint_to_action(create_ep, create_ep.operation_id or f"create_{resource}")

            # If it's a standalone creation (like POST /cart), make it an action on a related screen
            # For now, attach to list screen or create a dedicated screen
            if list_ep:
                # Add as action on the list screen
                for screen in self.screens:
                    if screen.id == f"{resource}_list":
                        screen.actions.append(action)
            else:
                # Standalone screen (e.g., checkout)
                screen = ScreenDefinition(
                    id=resource,
                    name=resource.replace("_", " ").title(),
                    description=create_ep.description or create_ep.summary or f"{resource}",
                    data_fields=self._extract_response_fields(create_ep),
                    actions=[action],
                )
                self.screens.append(screen)

        # Sub-actions (POST /api/cart/{id}/add, etc.)
        for ep in sub_actions:
            action = self._endpoint_to_action(ep)
            # Find which screen to attach to
            target_screen = self._find_best_screen(resource, ep)
            if target_screen:
                target_screen.actions.append(action)

    def _endpoint_to_action(self, ep: DiscoveredEndpoint, action_id: str = None) -> ActionDefinition:
        """Convert an endpoint to an ActionDefinition."""
        if not action_id:
            # Derive from operation_id or path
            action_id = ep.operation_id or ep.path.strip("/").replace("/", "_").replace("{", "").replace("}", "")

        # Clean up the action_id
        action_id = re.sub(r'_api_\w+_\w+$', '', action_id)  # Remove FastAPI suffix
        action_id = action_id.replace("__", "_").strip("_")

        params = []

        # Query/path parameters
        for p in ep.parameters:
            if p["name"] in ("page", "per_page"):  # Skip pagination params
                continue
            ap = ActionParameter(
                name=p["name"],
                type=self._map_type(p["type"]),
                required=p["required"],
                description=p["description"],
                enum_values=p.get("enum"),
                min_value=p.get("minimum"),
                max_value=p.get("maximum"),
                default=p.get("default"),
            )
            params.append(ap)

        # Request body parameters
        if ep.request_body:
            props = ep.request_body.get("properties", {})
            required_fields = ep.request_body.get("required", [])
            for name, schema in props.items():
                # Resolve nested objects (like ShippingInfo, PaymentInfo)
                if schema.get("type") == "object" or "$ref" in schema:
                    # Flatten nested object into top-level params
                    nested = schema.get("properties", {})
                    for nname, nschema in nested.items():
                        params.append(ActionParameter(
                            name=f"{name}_{nname}" if nested else nname,
                            type=self._map_type(nschema.get("type", "string")),
                            required=name in required_fields,
                            description=nschema.get("description", ""),
                        ))
                else:
                    params.append(ActionParameter(
                        name=name,
                        type=self._map_type(schema.get("type", "string")),
                        required=name in required_fields,
                        description=schema.get("description", ""),
                        min_value=schema.get("minimum"),
                        max_value=schema.get("maximum"),
                        default=str(schema["default"]) if "default" in schema else None,
                    ))

        # Infer if this is a sensitive/transactional action
        permission = ActionPermission.ALLOWED
        sensitive_keywords = ["checkout", "order", "payment", "purchase", "buy", "delete"]
        if any(kw in action_id.lower() or kw in ep.path.lower() for kw in sensitive_keywords):
            permission = ActionPermission.REQUIRES_CONFIRMATION

        return ActionDefinition(
            id=action_id,
            name=ep.summary or action_id.replace("_", " ").title(),
            description=ep.description or ep.summary or "",
            permission=permission,
            parameters=params,
            api_endpoint=ep.path,
            api_method=ep.method,
        )

    def _extract_response_fields(self, ep: DiscoveredEndpoint) -> list[DataField]:
        """Extract data fields from response schema."""
        fields = []
        schema = ep.response_schema or {}
        props = schema.get("properties", {})

        for name, prop in props.items():
            field_type = prop.get("type", "string")
            if "$ref" in prop:
                field_type = "object"
            if prop.get("type") == "array":
                field_type = "list"
            fields.append(DataField(
                name=name,
                type=field_type,
                description=prop.get("description", prop.get("title", "")),
            ))
        return fields

    def _extract_path_param(self, path: str) -> str:
        """Extract {param} from a URL path."""
        match = re.search(r'\{(\w+)\}', path)
        return match.group(1) if match else "id"

    def _find_best_screen(self, resource: str, ep: DiscoveredEndpoint) -> Optional[ScreenDefinition]:
        """Find the best screen to attach a sub-action to."""
        # Try detail screen first, then list screen
        for suffix in [f"{resource.rstrip('s')}_detail", f"{resource}_list", resource]:
            for screen in self.screens:
                if screen.id == suffix:
                    return screen
        # Fallback: last screen
        return self.screens[-1] if self.screens else None

    def _infer_transitions(self):
        """Add navigation transitions between screens."""
        screen_ids = [s.id for s in self.screens]
        for screen in self.screens:
            # Add "back" action if screen has a parent
            if screen.parent and screen.parent in screen_ids:
                screen.actions.append(ActionDefinition(
                    id=f"back_to_{screen.parent}",
                    name=f"Back to {screen.parent.replace('_', ' ')}",
                    description=f"Return to {screen.parent.replace('_', ' ')}",
                    transitions_to=screen.parent,
                ))

    def _map_type(self, json_type: str) -> str:
        """Map JSON Schema types to Phren types."""
        return {
            "string": "string",
            "integer": "int",
            "number": "float",
            "boolean": "bool",
            "array": "list",
            "object": "object",
        }.get(json_type, "string")


# ── Phase 4: Contract Generation ──

class ContractGenerator:
    """Phase 4: Assemble the final PhrenContract."""

    def __init__(self, site_name: str, site_url: str, screens: list[ScreenDefinition]):
        self.site_name = site_name
        self.site_url = site_url
        self.screens = screens

    def generate(self) -> PhrenContract:
        """Generate a contract with sensible defaults."""

        # Determine entry screen (first list/search screen)
        entry = self.screens[0].id if self.screens else ""

        # Auto-detect actions that should require confirmation
        confirmations = []
        for screen in self.screens:
            for action in screen.actions:
                if action.permission == ActionPermission.REQUIRES_CONFIRMATION:
                    confirmations.append(action.id)

        # Auto-detect transactional actions for trust requirements
        trust_reqs = []
        transactional_keywords = ["checkout", "order", "payment", "purchase", "buy"]
        write_keywords = ["add", "update", "remove", "delete", "create"]

        for screen in self.screens:
            for action in screen.actions:
                action_lower = action.id.lower()
                if any(kw in action_lower for kw in transactional_keywords):
                    trust_reqs.append(ActionTrustRequirement(
                        action_id=action.id,
                        min_trust_level=AgentTrust.VERIFIED,
                    ))
                elif any(kw in action_lower for kw in write_keywords):
                    trust_reqs.append(ActionTrustRequirement(
                        action_id=action.id,
                        min_trust_level=AgentTrust.IDENTIFIED,
                    ))

        contract = PhrenContract(
            contract_id=f"phren_{self.site_name.lower().replace(' ', '_')}_auto",
            version="0.1.0",
            site_name=self.site_name,
            site_url=self.site_url,
            description=f"Auto-generated Phren contract for {self.site_name}. "
                        "Review and refine before production use.",
            tier=ContractTier.STANDARD,

            screens=self.screens,
            entry_screen=entry,

            rate_limits=RateLimit(
                requests_per_minute=30,
                requests_per_hour=500,
                requests_per_day=5000,
                max_concurrent_sessions=1,
            ),

            data_terms=DataTerms(
                default_retention=DataRetention.SESSION,
                allow_aggregation=False,
                allow_storage=False,
            ),

            prohibited_actions=[
                "create_account", "delete_account",
                "access_admin", "bulk_scrape",
            ],

            required_confirmations=confirmations,
            action_trust_requirements=trust_reqs,
            default_trust_for_actions=AgentTrust.IDENTIFIED,

            require_identification=True,
            log_all_actions=True,
            log_data_access=True,
        )

        return contract


# ── Main Compiler ──

class PhrenCompiler:
    """
    The Phren Compiler.

    Usage:
        compiler = PhrenCompiler("http://localhost:8000")
        contract = compiler.compile()
        compiler.export("output/contract.json")
    """

    def __init__(self, base_url: str, site_name: str = None):
        self.base_url = base_url.rstrip("/")
        self.site_name = site_name or "Unknown Site"
        self.contract: Optional[PhrenContract] = None

    def compile(self) -> PhrenContract:
        """Run the full compilation pipeline."""
        print(f"\n{'='*60}")
        print(f"SUBWAY COMPILER — Scanning {self.base_url}")
        print(f"{'='*60}\n")

        # Phase 1: API Discovery
        api = APIDiscovery(self.base_url)
        spec = api.fetch_spec()
        endpoints = api.parse_endpoints()

        if spec:
            self.site_name = spec.get("info", {}).get("title", self.site_name)

        # Phase 2: HTML Scan
        scanner = HTMLScanner(self.base_url)
        scanner.fetch_homepage()
        html_elements = scanner.extract_elements()

        # Phase 3: Flow Inference
        flow = FlowInference(endpoints, html_elements)
        screens = flow.infer()

        # Phase 4: Contract Generation
        gen = ContractGenerator(self.site_name, self.base_url, screens)
        self.contract = gen.generate()

        self._print_summary()
        return self.contract

    def export(self, output_path: str):
        """Export the compiled contract as JSON."""
        if not self.contract:
            raise RuntimeError("Must call compile() first")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(self.contract.model_dump(mode="json"), f, indent=2)
        print(f"\nContract exported to {output_path}")

    def _print_summary(self):
        """Print a summary of the compiled contract."""
        c = self.contract
        print(f"\n{'='*60}")
        print(f"COMPILATION COMPLETE")
        print(f"{'='*60}")
        print(f"Site: {c.site_name}")
        print(f"Screens: {len(c.screens)}")
        total_actions = sum(len(s.actions) for s in c.screens)
        print(f"Actions: {total_actions}")
        print(f"Entry: {c.entry_screen}")
        print()
        print("Flow graph:")
        for screen in c.screens:
            print(f"  [{screen.id}]")
            for action in screen.actions:
                arrow = f" -> [{action.transitions_to}]" if action.transitions_to else ""
                perm = " [CONFIRM]" if action.permission == ActionPermission.REQUIRES_CONFIRMATION else ""
                print(f"    {action.id}{arrow}{perm}")
        print()
        print(f"Trust requirements: {len(c.action_trust_requirements)}")
        for req in c.action_trust_requirements:
            print(f"  {req.action_id}: min {req.min_trust_level.value}")
        print(f"Required confirmations: {c.required_confirmations}")


if __name__ == "__main__":
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    output = sys.argv[2] if len(sys.argv) > 2 else "compiled_contract.json"

    compiler = PhrenCompiler(url)
    compiler.compile()
    compiler.export(output)
