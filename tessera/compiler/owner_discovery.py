"""
Tessera Owner-Assisted Auto-Discovery

This is the REALISTIC path for Tessera adoption:
The website owner runs this script to generate their Tessera terminal.

They provide:
  1. base_url        — where the site runs (localhost or deployed)
  2. credentials     — a valid test account (email/password)
  3. auth_type       — how auth works ("bearer", "token", "cookie", "api_key", "basic")
  4. auth_endpoint   — where to login (e.g., "/api/auth/login", "/api/v1/auth", "/users/login")
  5. auth_format     — request body shape (e.g., "flat", "nested:user")
  6. api_prefix      — the API prefix (e.g., "/api", "/api/v1", "/store")
  7. route_hints     — optional list of known top-level resources

With this info, the script can:
  - Authenticate properly on first try
  - Skip blind prefix/auth guessing (saves ~60% of probing time)
  - Discover ALL endpoints including protected ones
  - Use structural inference to find related resources
  - Generate a complete Tessera contract

Usage:
    python3 owner_discovery.py \\
        --url http://localhost:8017 \\
        --prefix /api/v1 \\
        --auth-endpoint /api/v1/auth \\
        --auth-type bearer \\
        --auth-format '{"username":"admin","password":"admin123","type":"normal"}' \\
        --hints repos,users,orgs,issues,notifications

    # Or with a config file:
    python3 owner_discovery.py --config tessera.yaml
"""
import json
import re
import requests
import sys
import uuid
import time
from typing import Optional
from datetime import datetime


class OwnerDiscovery:
    """
    Auto-discovery with owner-provided context.
    
    The owner knows their own site — they provide the minimum info
    needed for the script to discover everything else automatically.
    """

    # Standard sub-actions to probe on every resource with IDs
    SUB_ACTIONS_POST = [
        "cancel", "status", "add", "remove", "update", "coupon",
        "like", "unlike", "follow", "unfollow",
        "comments", "reply", "share", "report",
        "favorite", "star", "watch",
        "complete", "archive", "publish",
        "test", "preview", "content",
        "optin", "blocklist",
    ]

    SUB_ACTIONS_GET = [
        "comments", "reviews", "items", "history", "stats",
        "activity", "export", "bounces", "preview",
        "branches", "labels", "milestones", "topics",
        "issues", "pulls", "stargazers",
        "members", "repos", "followers", "following", "starred",
    ]

    SUB_ACTIONS_DELETE = [
        "follow", "favorite", "star", "watch",
        "bounces",
    ]

    def __init__(
        self,
        base_url: str,
        api_prefix: str = "/api",
        credentials: dict = None,
        auth_endpoint: str = None,
        auth_type: str = "bearer",       # bearer, token, cookie, api_key, basic
        auth_format: str = "flat",        # flat, nested:user, nested:data, form
        route_hints: list = None,         # known top-level resources
        timeout: int = 3,
        delay: float = 0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_prefix = api_prefix
        self.credentials = credentials or {}
        self.auth_endpoint = auth_endpoint
        self.auth_type = auth_type
        self.auth_format = auth_format
        self.route_hints = route_hints or []
        self.timeout = timeout
        self.delay = delay

        self.token: Optional[str] = None
        self.cookies: dict = {}
        self.headers: dict = {}
        self.endpoints: list[dict] = []
        self.resources: dict[str, dict] = {}

    def _throttle(self):
        if self.delay > 0:
            time.sleep(self.delay)

    def _request(self, method, url, **kwargs):
        """Make a request with current auth."""
        headers = {**self.headers, **kwargs.pop("headers", {})}
        cookies = {**self.cookies, **kwargs.pop("cookies", {})}
        self._throttle()
        return getattr(requests, method)(
            url, headers=headers, cookies=cookies, timeout=self.timeout, **kwargs
        )

    def discover(self) -> dict:
        """Run owner-assisted discovery."""
        print(f"[OwnerDiscovery] Target: {self.base_url}")
        print(f"[OwnerDiscovery] Prefix: {self.api_prefix}")
        print(f"[OwnerDiscovery] Auth: {self.auth_type} via {self.auth_endpoint or 'none'}")
        if self.route_hints:
            print(f"[OwnerDiscovery] Hints: {', '.join(self.route_hints)}")
        print()

        # Step 1: Authenticate
        if self.auth_endpoint and self.credentials:
            self._authenticate()

        # Step 2: Discover top-level resources
        self._discover_resources()

        # Step 3: Probe detail endpoints (/{id}) and sub-actions
        self._probe_details_and_actions()

        # Step 4: Structural inference (FK fields, naming patterns)
        self._structural_inference()

        # Step 5: Generate spec
        spec = self._generate_spec()

        total = sum(len(m) for m in spec["paths"].values())
        print()
        print(f"[OwnerDiscovery] Complete! {total} endpoints across {len(self.resources)} resources")
        return spec

    def _authenticate(self):
        """Authenticate using owner-provided credentials."""
        print("[OwnerDiscovery] Step 1: Authenticating...")
        url = f"{self.base_url}{self.api_prefix}{self.auth_endpoint}"

        # Build request body based on format
        if self.auth_format == "flat":
            body = self.credentials
        elif self.auth_format.startswith("nested:"):
            wrapper = self.auth_format.split(":")[1]
            body = {wrapper: self.credentials}
        elif self.auth_format == "form":
            # Form-encoded (e.g., OAuth2 password grant)
            r = self._request("post", url, data=self.credentials)
        else:
            body = self.credentials

        if self.auth_format != "form":
            r = self._request("post", url, json=body)

        if r.status_code >= 400:
            print(f"  ❌ Auth failed: {r.status_code} → {r.text[:100]}")
            return

        # Extract token from response
        data = r.json()
        token = self._extract_token(data)

        if token:
            self.token = token
            if self.auth_type == "bearer":
                self.headers["Authorization"] = f"Bearer {token}"
            elif self.auth_type == "token":
                self.headers["Authorization"] = f"Token {token}"
            elif self.auth_type == "api_key":
                self.headers["X-API-Key"] = token
            print(f"  ✅ Authenticated ({self.auth_type})")
        else:
            # Try cookie-based auth
            if r.cookies:
                self.cookies = dict(r.cookies)
                print(f"  ✅ Authenticated (cookies: {list(r.cookies.keys())})")
            else:
                print(f"  ⚠️ No token or cookies found in response")

    def _extract_token(self, data, depth=0):
        """Recursively find a token in the response."""
        if depth > 3:
            return None
        if isinstance(data, dict):
            for key in ["token", "access_token", "auth_token", "jwt", "session_token"]:
                if key in data:
                    return data[key]
            # Check nested objects
            for val in data.values():
                if isinstance(val, dict):
                    t = self._extract_token(val, depth + 1)
                    if t:
                        return t
        return None

    def _discover_resources(self):
        """Discover top-level resources using hints + broad probing."""
        print("[OwnerDiscovery] Step 2: Discovering resources...")

        # Start with owner-provided hints
        resources_to_try = list(self.route_hints)

        # Add standard resources if no hints
        if not resources_to_try:
            resources_to_try = [
                "products", "articles", "items", "users", "user", "projects",
                "issues", "tags", "collections", "orders", "listings", "posts",
                "tasks", "repos", "orgs", "subscribers", "campaigns", "lists",
                "templates", "bounces", "media", "settings", "notifications",
                "comments", "categories", "profiles", "favorites", "bookmarks",
                "search", "feed", "health", "version", "config", "about",
                "roles", "permissions", "teams", "members", "pulls",
            ]

        for resource in resources_to_try:
            path = f"{self.api_prefix}/{resource}"
            url = f"{self.base_url}{path}"
            try:
                r = self._request("get", url)
                if r.status_code == 404:
                    continue

                entry = {
                    "path": path,
                    "method": "GET",
                    "status": r.status_code,
                    "protected": r.status_code == 401,
                }

                if r.status_code < 400:
                    try:
                        data = r.json()
                        entry["response_keys"] = list(data.keys()) if isinstance(data, dict) else []

                        # Detect list resources and extract sample IDs + item shapes
                        items_list = None
                        if isinstance(data, list) and data:
                            items_list = data
                        elif isinstance(data, dict):
                            for key, val in data.items():
                                if isinstance(val, list) and val and isinstance(val[0], dict):
                                    items_list = val
                                    entry["list_field"] = key
                                    entry["list_count"] = len(val)
                                    break

                        if items_list and isinstance(items_list[0], dict):
                            entry["item_shape"] = list(items_list[0].keys())
                            # Extract ID (try multiple field names)
                            for id_field in ["id", "slug", "uid", "_id", "uuid", "login", "username", "name", "code"]:
                                if id_field in items_list[0]:
                                    entry["sample_id"] = str(items_list[0][id_field])
                                    entry["id_field"] = id_field
                                    break
                    except Exception:
                        pass

                self.endpoints.append(entry)
                self.resources[resource] = entry

                label = "🔓" if r.status_code < 400 else "🔒" if r.status_code == 401 else f"⚠️ {r.status_code}"
                id_info = f" (id_field={entry.get('id_field','?')}, sample={entry.get('sample_id','-')[:20]})" if entry.get("sample_id") else ""
                items_info = f" → {entry.get('list_count', '?')} items" if entry.get("list_field") else ""
                print(f"  {label} {path}{items_info}{id_info}")

            except Exception:
                continue

        # Also probe nested paths from COMMON_NESTED_PATHS-style patterns
        nested = [
            "/users/search", "/repos/search",
            "/users/me", "/customers/me",
            "/dashboard/charts", "/dashboard/counts",
            "/import/subscribers", "/import/subscribers/logs",
            "/campaigns/running/stats",
            "/notifications/new",
            "/settings/api", "/settings/repository", "/settings/ui",
        ]
        for nested_path in nested:
            path = f"{self.api_prefix}{nested_path}"
            url = f"{self.base_url}{path}"
            try:
                r = self._request("get", url)
                if r.status_code != 404 and r.status_code != 405:
                    self.endpoints.append({
                        "path": path, "method": "GET",
                        "status": r.status_code,
                        "protected": r.status_code == 401,
                    })
                    label = "🔓" if r.status_code < 400 else "🔒"
                    print(f"  {label} {path} (nested)")
            except:
                continue

    def _probe_details_and_actions(self):
        """For each resource with a sample ID, probe detail + CRUD + sub-actions."""
        print("[OwnerDiscovery] Step 3: Probing details & actions...")
        already = {f"{e['method']} {e['path']}" for e in self.endpoints}

        for resource, info in list(self.resources.items()):
            sample_id = info.get("sample_id")
            base_path = info["path"]
            if not sample_id:
                continue

            # GET /{id}
            detail_path = f"{base_path}/{sample_id}"
            if f"GET {base_path}/{{id}}" not in already:
                try:
                    r = self._request("get", f"{self.base_url}{detail_path}")
                    if r.status_code < 400:
                        entry = {"path": f"{base_path}/{{id}}", "method": "GET",
                                 "status": r.status_code, "inferred": True}
                        # Parse detail response for nested resource hints
                        try:
                            detail_data = r.json()
                            if isinstance(detail_data, dict):
                                entry["detail_keys"] = list(detail_data.keys())
                        except: pass
                        self.endpoints.append(entry)
                        already.add(f"GET {base_path}/{{id}}")
                        print(f"  ✅ GET {base_path}/{{id}}")
                except: pass

            # POST (create)
            if f"POST {base_path}" not in already:
                try:
                    r = self._request("post", f"{self.base_url}{base_path}", json={})
                    if r.status_code != 404 and r.status_code != 405:
                        self.endpoints.append({"path": base_path, "method": "POST",
                                               "status": r.status_code, "inferred": True})
                        already.add(f"POST {base_path}")
                        print(f"  ✅ POST {base_path} → {r.status_code}")
                except: pass

            # PUT /{id}
            if f"PUT {base_path}/{{id}}" not in already:
                try:
                    r = self._request("put", f"{self.base_url}{detail_path}", json={})
                    if r.status_code != 404 and r.status_code != 405:
                        self.endpoints.append({"path": f"{base_path}/{{id}}", "method": "PUT",
                                               "status": r.status_code, "inferred": True})
                        already.add(f"PUT {base_path}/{{id}}")
                        print(f"  ✅ PUT {base_path}/{{id}} → {r.status_code}")
                except: pass

            # PATCH /{id}
            if f"PATCH {base_path}/{{id}}" not in already:
                try:
                    r = self._request("patch", f"{self.base_url}{detail_path}", json={})
                    if r.status_code != 404 and r.status_code != 405:
                        self.endpoints.append({"path": f"{base_path}/{{id}}", "method": "PATCH",
                                               "status": r.status_code, "inferred": True})
                        already.add(f"PATCH {base_path}/{{id}}")
                        print(f"  ✅ PATCH {base_path}/{{id}} → {r.status_code}")
                except: pass

            # DELETE /{id} (probe with fake ID to avoid damage)
            if f"DELETE {base_path}/{{id}}" not in already:
                try:
                    r = self._request("delete", f"{self.base_url}{base_path}/__probe_nonexistent__")
                    if r.status_code == 404 or (r.status_code != 405 and r.status_code < 500):
                        self.endpoints.append({"path": f"{base_path}/{{id}}", "method": "DELETE",
                                               "status": r.status_code, "inferred": True})
                        already.add(f"DELETE {base_path}/{{id}}")
                        print(f"  ✅ DEL {base_path}/{{id}}")
                except: pass

            # POST sub-actions
            for sub in self.SUB_ACTIONS_POST:
                key = f"POST {base_path}/{{id}}/{sub}"
                if key in already:
                    continue
                try:
                    r = self._request("post", f"{self.base_url}{detail_path}/{sub}", json={})
                    if r.status_code != 404 and r.status_code != 405:
                        self.endpoints.append({"path": f"{base_path}/{{id}}/{sub}",
                                               "method": "POST", "status": r.status_code, "inferred": True})
                        already.add(key)
                        print(f"  ✅ POST {base_path}/{{id}}/{sub} → {r.status_code}")
                except: pass

            # GET sub-resources
            for sub in self.SUB_ACTIONS_GET:
                key = f"GET {base_path}/{{id}}/{sub}"
                if key in already:
                    continue
                try:
                    r = self._request("get", f"{self.base_url}{detail_path}/{sub}")
                    if r.status_code != 404:
                        self.endpoints.append({"path": f"{base_path}/{{id}}/{sub}",
                                               "method": "GET", "status": r.status_code, "inferred": True})
                        already.add(key)
                        print(f"  ✅ GET {base_path}/{{id}}/{sub} → {r.status_code}")
                except: pass

            # DELETE sub-actions
            for sub in self.SUB_ACTIONS_DELETE:
                key = f"DELETE {base_path}/{{id}}/{sub}"
                if key in already:
                    continue
                try:
                    r = self._request("delete", f"{self.base_url}{detail_path}/{sub}")
                    if r.status_code != 404 and r.status_code != 405:
                        self.endpoints.append({"path": f"{base_path}/{{id}}/{sub}",
                                               "method": "DELETE", "status": r.status_code, "inferred": True})
                        already.add(key)
                        print(f"  ✅ DEL {base_path}/{{id}}/{sub} → {r.status_code}")
                except: pass

    def _structural_inference(self):
        """Discover more from response shapes — FK fields, naming patterns."""
        print("[OwnerDiscovery] Step 4: Structural inference...")
        already = {f"{e['method']} {e['path']}" for e in self.endpoints}
        new_found = 0

        # FK inference: fields ending in _id point to other resources
        fk_candidates = set()
        for info in self.resources.values():
            for field in info.get("item_shape", []):
                if field.endswith("_id") or field.endswith("Id"):
                    base = field.replace("_id", "").replace("Id", "")
                    if base.endswith("y"):
                        fk_candidates.add(base[:-1] + "ies")
                    elif not base.endswith("s"):
                        fk_candidates.add(base + "s")
                    fk_candidates.add(base)

        for candidate in fk_candidates:
            path = f"{self.api_prefix}/{candidate}"
            if f"GET {path}" in already or candidate in self.resources:
                continue
            try:
                r = self._request("get", f"{self.base_url}{path}")
                if r.status_code < 400 or r.status_code == 401:
                    entry = {"path": path, "method": "GET", "status": r.status_code,
                             "protected": r.status_code == 401, "source": "fk"}
                    if r.status_code < 400:
                        try:
                            data = r.json()
                            if isinstance(data, dict):
                                for key, val in data.items():
                                    if isinstance(val, list) and val and isinstance(val[0], dict):
                                        entry["list_field"] = key
                                        entry["item_shape"] = list(val[0].keys())
                                        for idf in ["id", "slug", "uid", "_id"]:
                                            if idf in val[0]:
                                                entry["sample_id"] = str(val[0][idf])
                                                break
                        except: pass
                    self.endpoints.append(entry)
                    self.resources[candidate] = entry
                    already.add(f"GET {path}")
                    new_found += 1
                    print(f"  🔗 FK→ {path}")
            except: pass

        # Naming pattern: if "product-categories" exists, try "product-tags", etc.
        hyphenated = [r for r in self.resources if "-" in r]
        if hyphenated:
            prefixes = set()
            for name in hyphenated:
                parts = name.split("-")
                if len(parts) >= 2:
                    prefixes.add(parts[0])
            suffixes = ["categories", "tags", "types", "variants", "options",
                        "collections", "providers", "reasons", "methods", "sessions"]
            for prefix in prefixes:
                for suffix in suffixes:
                    candidate = f"{prefix}-{suffix}"
                    path = f"{self.api_prefix}/{candidate}"
                    if f"GET {path}" in already:
                        continue
                    try:
                        r = self._request("get", f"{self.base_url}{path}")
                        if r.status_code < 400:
                            self.endpoints.append({"path": path, "method": "GET",
                                                   "status": r.status_code, "source": "naming"})
                            already.add(f"GET {path}")
                            new_found += 1
                            print(f"  🔗 Naming→ {path}")
                    except: pass

        print(f"  Found {new_found} new endpoints")

    def _generate_spec(self) -> dict:
        paths = {}
        for ep in self.endpoints:
            path = ep["path"]
            method = ep.get("method", "GET").lower()
            if path not in paths:
                paths[path] = {}
            paths[path][method] = {
                "status": ep.get("status"),
                "protected": ep.get("protected", False),
                "inferred": ep.get("inferred", False),
            }
            if ep.get("response_keys"):
                paths[path][method]["response_keys"] = ep["response_keys"]
            if ep.get("list_field"):
                paths[path][method]["returns_list"] = ep["list_field"]
            if ep.get("item_shape"):
                paths[path][method]["item_shape"] = ep["item_shape"]

        return {
            "tessera_discovery": True,
            "mode": "owner_assisted",
            "base_url": self.base_url,
            "api_prefix": self.api_prefix,
            "auth_type": self.auth_type,
            "total_endpoints": len(self.endpoints),
            "total_resources": len(self.resources),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "paths": paths,
        }
