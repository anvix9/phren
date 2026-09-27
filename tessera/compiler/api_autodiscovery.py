"""
Tessera API Auto-Discovery

Generates an API specification from a live website by:
1. Probing common endpoint patterns
2. Analyzing response shapes to infer parameters
3. Testing auth flows (login → get token → re-probe with auth)
4. Inferring CRUD patterns from discovered resources
5. Outputting an OpenAPI-compatible spec

This works WITHOUT any documentation or OpenAPI spec on the site.

Usage:
    python3 api_autodiscovery.py http://localhost:8006

Output:
    - Prints discovered API structure
    - Saves OpenAPI spec to discovered_api.json
"""
import json
import re
import requests
import sys
import uuid
from typing import Optional
from datetime import datetime, date, timedelta


class APIAutoDiscovery:
    """Discovers API endpoints from a live website with no documentation."""

    COMMON_API_PREFIXES = ["/api", "/api/v1", "/api/v2", "/v1", "/v2", "/store", "/rest", ""]

    COMMON_RESOURCES = [
        # Shopping/E-commerce (incl. Medusa patterns)
        "menu", "products", "items", "categories", "aisles", "sections",
        "cart", "carts", "checkout", "orders", "wishlist", "favorites",
        "promotions", "deals", "coupons", "discounts",
        "delivery-slots", "shipping", "inventory",
        "collections", "currencies", "regions", "locales",
        "product-categories", "product-tags", "product-types", "product-variants",
        "shipping-options", "return-reasons", "returns",
        "payment-providers", "payment-collections",
        # Music/Media/Entertainment
        "artists", "albums", "tracks", "songs", "genres", "playlists",
        "library", "purchases", "credits", "recommendations",
        "charts", "podcasts", "episodes", "streams", "channels",
        "videos", "movies", "shows", "series",
        "podcast_episodes",  # Forem pattern (underscore)
        # News/Content/Social (incl. Forem/dev.to patterns)
        "articles", "posts", "stories", "feeds", "headlines",
        "authors", "writers", "editors", "contributors",
        "topics", "tags", "sections", "columns",
        "comments", "replies", "threads",
        "likes", "reactions", "votes", "bookmarks", "saves",
        "trending", "popular", "latest", "featured",
        "drafts", "submissions", "publish",
        "newsletters", "subscriptions",
        "badges", "badge_achievements",  # Forem
        "follows", "readinglist",  # Forem
        "profile_images",  # Forem
        "subforems", "surveys",  # Forem
        # Social
        "followers", "following", "friends", "connections",
        "profiles", "activity", "feed", "timeline",
        "shares", "reposts", "mentions",
        "groups", "communities", "forums",
        # Project Management (Taiga patterns)
        "projects", "issues", "tasks", "userstories",
        "epics", "milestones", "sprints", "boards",
        "wiki", "attachments", "resolver",
        # Housing/Real Estate
        "listings", "properties", "rooms", "apartments",
        "roommates", "landlords", "tenants", "leases",
        "universities", "campuses", "neighborhoods",
        # Booking/Hospitality
        "hotels", "bookings", "reservations",
        "availability", "tables", "events", "tickets",
        # Services/Government
        "documents", "departments", "requests", "applications",
        "permits", "licenses", "certificates", "forms",
        # Users/Auth
        "users", "user", "auth", "profile", "account", "me",
        "customers",  # Medusa
        "loyalty", "rewards", "points", "badges",
        "preferences", "settings", "privacy",
        "tags",
        # Communication
        "messages", "inbox", "conversations", "chat",
        "notifications", "alerts",
        # Analytics
        "analytics", "stats", "dashboard",
        # Health/System
        "health_checks", "health", "instance",
        # Common
        "search", "config", "info", "status",
        "restaurants", "stores", "locations", "branches",
        "reviews", "ratings", "reports",
        "payments", "invoices", "transactions",
        "upload", "files", "media", "images", "attachments",
        # Education
        "courses", "lessons", "quizzes", "assignments",
        "students", "teachers", "grades",
        # Jobs
        "jobs", "candidates", "companies", "resumes",
        # Health/Fitness
        "workouts", "exercises", "meals", "nutrition",
        "appointments", "prescriptions", "records",
    ]

    COMMON_NESTED_PATHS = [
        # Auth
        "/auth/login", "/auth/register", "/auth/profile", "/auth/logout",
        "/auth/me", "/auth/refresh", "/auth/forgot-password",
        # Info/Config
        "/store/info", "/restaurant/info", "/site/info", "/site/config",
        "/app/info", "/app/config",
        # Loyalty/Rewards
        "/loyalty/history", "/loyalty/redeem", "/loyalty/balance",
        # Delivery
        "/delivery-slots", "/delivery/zones",
        # Charts/Rankings
        "/charts/top-sellers", "/charts/new-releases", "/charts/trending",
        "/charts/top", "/charts/popular",
        # Social actions
        "/wishlist/add", "/wishlist/remove",
        "/bookmarks/add", "/bookmarks/remove",
        "/favorites/add", "/favorites/remove",
        "/likes/add", "/likes/remove",
        # Purchase/Transaction
        "/purchase", "/checkout",
        # Content sub-paths
        "/articles/featured", "/articles/trending", "/articles/latest",
        "/posts/trending", "/posts/latest", "/posts/popular",
        "/listings/featured", "/listings/nearby", "/listings/trending",
        "/feed/personalized", "/feed/latest",
        # User content
        "/me/articles", "/me/posts", "/me/comments", "/me/drafts",
        "/me/likes", "/me/bookmarks", "/me/activity",
        "/me/listings", "/me/orders", "/me/purchases",
        "/me/playlists", "/me/library", "/me/favorites",
        # RealWorld/Conduit patterns
        "/users/login", "/users/register",
        "/articles/feed",
        # Forem/dev.to patterns
        "/articles/search", "/articles/latest",
        "/articles/me", "/articles/me/published", "/articles/me/unpublished", "/articles/me/all",
        "/follows/tags",
        "/followers/users", "/followers/organizations",
        "/analytics/totals", "/analytics/historical", "/analytics/past_day", "/analytics/referrers",
        "/health_checks/app", "/health_checks/database", "/health_checks/cache",
        # Medusa patterns
        "/customers/me", "/customers/me/addresses",
        # FastAPI Full-Stack patterns
        "/login/access-token", "/login/test-token",
        "/users/me", "/users/me/password", "/users/signup",
        "/password-recovery", "/reset-password",
        "/utils/health-check",
        # Taiga patterns
        "/auth/register",
    ]

    # Sub-actions to probe on discovered resources with IDs/slugs
    COMMON_SUB_ACTIONS_POST = [
        "cancel", "status", "items", "add", "remove", "update", "coupon",
        "like", "unlike", "follow", "unfollow",
        "comments", "reply", "share", "report",
        "approve", "reject", "publish", "archive",
        "favorite",
        # Medusa cart sub-actions
        "complete", "customer", "line-items", "shipping-methods", "taxes", "promotions",
        # Medusa order transfer
        "transfer/request", "transfer/accept", "transfer/decline", "transfer/cancel",
        # Medusa payment
        "payment-sessions",
        # Medusa shipping
        "calculate",
    ]

    COMMON_SUB_ACTIONS_GET = [
        "comments", "reviews", "items", "history", "tracks", "stats",
        # Forem org sub-resources
        "users", "articles",
        # Taiga-style
        "attachments", "voters",
        # Surveys
        "poll_votes", "poll_text_responses",
    ]

    COMMON_SUB_ACTIONS_DELETE = [
        "follow", "favorite",
        "promotions", "line-items",
    ]

    COMMON_AUTH_ENDPOINTS = [
        # Standard pattern
        ("POST", "/auth/login", {"email": "test@test.com", "password": "test123"}),
        ("POST", "/auth/register", {"email": f"probe_{uuid.uuid4().hex[:6]}@test.com", "password": "test123", "name": "Probe User", "phone": "+1-555-0000"}),
        ("POST", "/login", {"email": "test@test.com", "password": "test123"}),
        ("POST", "/register", {"email": f"probe_{uuid.uuid4().hex[:6]}@test.com", "password": "test123", "name": "Probe User"}),
        # RealWorld/Conduit pattern (nested user object)
        ("POST", "/users/login", {"user": {"email": "test@test.com", "password": "test123"}}),
        ("POST", "/users", {"user": {"username": f"probe_{uuid.uuid4().hex[:6]}", "email": f"probe_{uuid.uuid4().hex[:6]}@test.com", "password": "test123"}}),
        # Taiga pattern (direct /auth)
        ("POST", "/auth", {"username": "test", "password": "test123", "type": "normal"}),
        ("POST", "/auth/register", {"username": f"probe_{uuid.uuid4().hex[:6]}", "password": "test123", "email": f"probe_{uuid.uuid4().hex[:6]}@test.com", "full_name": "Probe", "accepted_terms": True}),
        # FastAPI Full-Stack pattern (form-style login)
        ("POST", "/login/access-token", {"username": "test@test.com", "password": "test123"}),
        # Medusa customer registration
        ("POST", "/customers", {"email": f"probe_{uuid.uuid4().hex[:6]}@test.com", "password": "test123", "first_name": "Probe", "last_name": "User"}),
        # Users/signup pattern  
        ("POST", "/users/signup", {"email": f"probe_{uuid.uuid4().hex[:6]}@test.com", "password": "test123", "full_name": "Probe User"}),
    ]

    def __init__(self, base_url: str, timeout: int = 3, safe_mode: bool = False,
                 delay: float = 0, custom_prefixes: list = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.safe_mode = safe_mode  # Skip auth/registration on external sites
        self.delay = delay  # Delay between requests (seconds) to avoid rate limits
        self.custom_prefixes = custom_prefixes  # Override API prefix detection
        self.api_prefix = ""
        self.token: Optional[str] = None
        self.endpoints: list[dict] = []
        self.resources: dict[str, dict] = {}

    def _throttle(self):
        """Respect rate limits on external sites."""
        if self.delay > 0:
            import time
            time.sleep(self.delay)

    def discover(self) -> dict:
        """Run full discovery pipeline."""
        print(f"[AutoDiscovery] Targeting: {self.base_url}")
        if self.safe_mode:
            print(f"[AutoDiscovery] SAFE MODE: no auth/registration, read-only probing")
        if self.delay:
            print(f"[AutoDiscovery] Throttle: {self.delay}s between requests")
        print()

        # Step 1: Find API prefix
        if self.custom_prefixes:
            for prefix in self.custom_prefixes:
                self.api_prefix = prefix
                print(f"[AutoDiscovery] Using custom prefix: {prefix}")
                break
        else:
            self._find_api_prefix()

        # Step 2: Probe common resources
        self._probe_resources()

        # Step 3: Try auth flow (skip in safe mode)
        if not self.safe_mode:
            self._discover_auth()

            # Step 4: Re-probe with auth token
            if self.token:
                self._probe_protected_resources()

        # Step 5: Infer CRUD patterns
        self._infer_crud_patterns()

        # Step 6: Structural inference — discover more endpoints from response shapes
        self._structural_inference()

        # Step 7: Generate spec
        spec = self._generate_spec()

        print()
        print(f"[AutoDiscovery] Complete! Found {len(self.endpoints)} endpoints across {len(self.resources)} resources")
        return spec

    def _find_api_prefix(self):
        """Determine the API prefix. Try all prefixes and pick the best one."""
        print("[AutoDiscovery] Step 1: Finding API prefix...")
        probe_resources = ["products", "articles", "items", "users", "projects", "issues",
                           "tags", "collections", "orders", "listings", "posts", "tasks",
                           "repos", "version", "health", "orgs", "subscribers", "campaigns",
                           "lists", "templates", "settings", "notifications", "bounces", "media"]
        prefix_scores = {}
        for prefix in self.COMMON_API_PREFIXES:
            hits = 0
            for resource in probe_resources:
                url = f"{self.base_url}{prefix}/{resource}"
                try:
                    self._throttle()
                    r = requests.get(url, timeout=self.timeout)
                    if r.status_code < 400 or r.status_code == 401:
                        hits += 1
                        if hits >= 3:
                            break  # Confident enough
                except Exception:
                    continue
            if hits > 0:
                prefix_scores[prefix] = hits
                print(f"  '{prefix or '/'}' → {hits} hits")

        if prefix_scores:
            best = max(prefix_scores, key=lambda p: (prefix_scores[p], len(p)))
            self.api_prefix = best
            print(f"  Selected: '{best or '/'}' ({prefix_scores[best]} hits)")
        else:
            self.api_prefix = "/api"
            print(f"  Using default: /api")

    def _probe_resources(self):
        """Try GET on common resource paths."""
        print(f"[AutoDiscovery] Step 2: Probing resources at {self.api_prefix}/...")
        for resource in self.COMMON_RESOURCES:
            url = f"{self.base_url}{self.api_prefix}/{resource}"
            try:
                r = requests.get(url, timeout=self.timeout)
                if r.status_code == 404:
                    continue

                entry = {
                    "path": f"{self.api_prefix}/{resource}",
                    "method": "GET",
                    "status": r.status_code,
                    "protected": r.status_code == 401,
                }

                if r.status_code < 400:
                    try:
                        data = r.json()
                        entry["response_shape"] = self._describe_shape(data)
                        entry["response_keys"] = list(data.keys()) if isinstance(data, dict) else []

                        # Detect list resources
                        if isinstance(data, dict):
                            for key, val in data.items():
                                if isinstance(val, list) and len(val) > 0:
                                    entry["list_field"] = key
                                    entry["list_count"] = len(val)
                                    if isinstance(val[0], dict):
                                        entry["item_shape"] = list(val[0].keys())
                                        for id_field in ["id", "slug", "uid", "_id", "uuid", "key"]:
                                            if id_field in val[0]:
                                                entry["has_id"] = True
                                                entry["sample_id"] = str(val[0][id_field])
                                                entry["id_field"] = id_field
                                                break
                    except Exception:
                        pass

                self.endpoints.append(entry)
                self.resources[resource] = entry
                status_label = "🔓 public" if r.status_code < 400 else "🔒 protected" if r.status_code == 401 else f"⚠️ {r.status_code}"
                print(f"  {status_label:14} GET {self.api_prefix}/{resource}")
                if entry.get("list_field"):
                    print(f"                  → list of {entry['list_count']} {entry['list_field']}, item keys: {entry.get('item_shape', [])[:6]}")

            except Exception:
                continue

        # Also try nested/compound paths
        for nested in self.COMMON_NESTED_PATHS:
            url = f"{self.base_url}{self.api_prefix}{nested}"
            try:
                r = requests.get(url, timeout=self.timeout)
                if r.status_code == 404:
                    continue
                entry = {
                    "path": f"{self.api_prefix}{nested}",
                    "method": "GET",
                    "status": r.status_code,
                    "protected": r.status_code == 401,
                }
                if r.status_code < 400:
                    try:
                        data = r.json()
                        entry["response_keys"] = list(data.keys()) if isinstance(data, dict) else []
                    except Exception:
                        pass
                self.endpoints.append(entry)
                label = "🔓 public" if r.status_code < 400 else "🔒 protected" if r.status_code == 401 else f"⚠️ {r.status_code}"
                print(f"  {label:14} GET {self.api_prefix}{nested}")
            except Exception:
                continue

    def _discover_auth(self):
        """Try to discover auth endpoints and get a token."""
        print(f"[AutoDiscovery] Step 3: Discovering auth flow...")
        for method, path, body in self.COMMON_AUTH_ENDPOINTS:
            url = f"{self.base_url}{self.api_prefix}{path}"
            try:
                r = requests.post(url, json=body, timeout=self.timeout)
                if r.status_code == 404:
                    continue

                entry = {
                    "path": f"{self.api_prefix}{path}",
                    "method": method,
                    "status": r.status_code,
                    "request_body_keys": list(body.keys()),
                }

                if r.status_code < 400:
                    try:
                        data = r.json()
                        entry["response_keys"] = list(data.keys())
                        # Extract token — check flat and nested patterns
                        token = None
                        if "token" in data:
                            token = data["token"]
                        else:
                            # Check nested: {"user": {"token": ...}}, {"data": {"token": ...}}
                            for wrapper in ["user", "data", "result", "auth"]:
                                if isinstance(data.get(wrapper), dict) and "token" in data[wrapper]:
                                    token = data[wrapper]["token"]
                                    break
                        if token:
                            self.token = token
                            entry["returns_token"] = True
                            print(f"  ✅ {method} {self.api_prefix}{path} → got auth token!")
                        else:
                            print(f"  ✅ {method} {self.api_prefix}{path} → {r.status_code}")
                    except Exception:
                        pass
                elif r.status_code == 401:
                    print(f"  ❌ {method} {self.api_prefix}{path} → wrong credentials (endpoint exists)")
                    entry["note"] = "endpoint exists, needs valid credentials"
                elif r.status_code == 409:
                    print(f"  ⚠️ {method} {self.api_prefix}{path} → conflict (already registered)")
                    entry["note"] = "registration endpoint, user exists"
                elif r.status_code == 422:
                    print(f"  ⚠️ {method} {self.api_prefix}{path} → validation error (endpoint exists)")
                    entry["note"] = "endpoint exists, needs correct params"
                else:
                    print(f"  ⚠️ {method} {self.api_prefix}{path} → {r.status_code}")

                self.endpoints.append(entry)

            except Exception:
                continue

        if not self.token:
            print("  No auth token obtained. Protected endpoints won't be fully discovered.")

    def _probe_protected_resources(self):
        """Re-probe resources that returned 401, now with auth."""
        print(f"[AutoDiscovery] Step 4: Re-probing with auth token...")
        # Try both Bearer and Token formats (RealWorld uses Token)
        headers_bearer = {"Authorization": f"Bearer {self.token}"}
        headers_token = {"Authorization": f"Token {self.token}"}

        protected = [e for e in self.endpoints if e.get("protected")]
        for entry in protected:
            url = f"{self.base_url}{entry['path']}"
            for headers in [headers_bearer, headers_token]:
                try:
                    self._throttle()
                    r = requests.get(url, headers=headers, timeout=self.timeout)
                    if r.status_code < 400:
                        try:
                            data = r.json()
                            entry["status"] = r.status_code
                            entry["protected"] = True
                            entry["response_keys"] = list(data.keys()) if isinstance(data, dict) else []
                            if isinstance(data, dict):
                                for key, val in data.items():
                                    if isinstance(val, list):
                                        entry["list_field"] = key
                                        entry["list_count"] = len(val)
                            print(f"  🔓→ GET {entry['path']} → {r.status_code} (with auth)")
                        except Exception:
                            pass
                        break  # Success, don't try other auth format
                except Exception:
                    continue

    def _infer_crud_patterns(self):
        """For each discovered resource, infer detail/create/update/delete endpoints."""
        print(f"[AutoDiscovery] Step 5: Inferring CRUD patterns...")
        if self.token:
            headers = {"Authorization": f"Bearer {self.token}"}
        else:
            headers = {}

        for resource, info in list(self.resources.items()):
            sample_id = info.get("sample_id")
            base_path = info["path"]

            # Try GET /resource/{id}
            if sample_id:
                url = f"{self.base_url}{base_path}/{sample_id}"
                try:
                    r = requests.get(url, headers=headers, timeout=self.timeout)
                    if r.status_code < 404:
                        self.endpoints.append({
                            "path": f"{base_path}/{{id}}",
                            "method": "GET",
                            "status": r.status_code,
                            "protected": r.status_code == 401,
                            "inferred": True,
                        })
                        print(f"  ✅ GET {base_path}/{{id}} → {r.status_code}")
                except Exception:
                    pass

            # Try POST /resource (create)
            try:
                r = requests.post(f"{self.base_url}{base_path}", json={}, headers=headers, timeout=self.timeout)
                if r.status_code != 404 and r.status_code != 405:
                    self.endpoints.append({
                        "path": base_path,
                        "method": "POST",
                        "status": r.status_code,
                        "protected": r.status_code == 401,
                        "inferred": True,
                    })
                    label = "protected" if r.status_code == 401 else f"{r.status_code}"
                    print(f"  ✅ POST {base_path} → {label}")
            except Exception:
                pass

            # Try PUT /resource/{id} (update)
            if sample_id:
                try:
                    r = requests.put(f"{self.base_url}{base_path}/{sample_id}", json={}, headers=headers, timeout=self.timeout)
                    if r.status_code != 404 and r.status_code != 405:
                        self.endpoints.append({
                            "path": f"{base_path}/{{id}}",
                            "method": "PUT",
                            "status": r.status_code,
                            "protected": r.status_code == 401,
                            "inferred": True,
                        })
                        print(f"  ✅ PUT  {base_path}/{{id}} → {r.status_code}")
                except Exception:
                    pass

                # Try DELETE /resource/{id}
                # Use a non-existent ID to avoid actually deleting data
                try:
                    r = requests.delete(f"{self.base_url}{base_path}/nonexistent_probe_id", headers=headers, timeout=self.timeout)
                    if r.status_code != 404 and r.status_code != 405:
                        self.endpoints.append({
                            "path": f"{base_path}/{{id}}",
                            "method": "DELETE",
                            "status": r.status_code,
                            "protected": r.status_code == 401,
                            "inferred": True,
                        })
                        print(f"  ✅ DEL  {base_path}/{{id}} → {r.status_code}")
                    elif r.status_code == 404:
                        # 404 on non-existent ID means the endpoint exists but ID not found
                        self.endpoints.append({
                            "path": f"{base_path}/{{id}}",
                            "method": "DELETE",
                            "status": 200,
                            "protected": False,
                            "inferred": True,
                            "note": "inferred from 404 on probe ID",
                        })
                        print(f"  ✅ DEL  {base_path}/{{id}} → exists (404 on probe)")
                except Exception:
                    pass

            # Try common sub-resources on /resource/{id}/...
            if sample_id:
                # POST sub-actions
                for sub in self.COMMON_SUB_ACTIONS_POST:
                    url = f"{self.base_url}{base_path}/{sample_id}/{sub}"
                    try:
                        self._throttle()
                        r = requests.post(url, json={}, headers=headers, timeout=self.timeout)
                        if r.status_code != 404 and r.status_code != 405:
                            self.endpoints.append({
                                "path": f"{base_path}/{{id}}/{sub}",
                                "method": "POST",
                                "status": r.status_code,
                                "protected": r.status_code == 401,
                                "inferred": True,
                            })
                            print(f"  ✅ POST {base_path}/{{id}}/{sub} → {r.status_code}")
                    except Exception:
                        pass

                # GET sub-resources
                for sub in self.COMMON_SUB_ACTIONS_GET:
                    url = f"{self.base_url}{base_path}/{sample_id}/{sub}"
                    try:
                        self._throttle()
                        r = requests.get(url, headers=headers, timeout=self.timeout)
                        if r.status_code != 404:
                            self.endpoints.append({
                                "path": f"{base_path}/{{id}}/{sub}",
                                "method": "GET",
                                "status": r.status_code,
                                "protected": r.status_code == 401,
                                "inferred": True,
                            })
                            print(f"  ✅ GET  {base_path}/{{id}}/{sub} → {r.status_code}")
                    except Exception:
                        pass

                # DELETE sub-actions (unfollow, unfavorite)
                for sub in self.COMMON_SUB_ACTIONS_DELETE:
                    url = f"{self.base_url}{base_path}/{sample_id}/{sub}"
                    try:
                        self._throttle()
                        r = requests.delete(url, headers=headers, timeout=self.timeout)
                        if r.status_code != 404 and r.status_code != 405:
                            self.endpoints.append({
                                "path": f"{base_path}/{{id}}/{sub}",
                                "method": "DELETE",
                                "status": r.status_code,
                                "protected": r.status_code == 401,
                                "inferred": True,
                            })
                            print(f"  ✅ DEL  {base_path}/{{id}}/{sub} → {r.status_code}")
                    except Exception:
                        pass

        # Step 5b: For resources that support POST (create), try to create one and probe its sub-resources
        # E.g., POST /cart → get cart_id → probe /cart/{cart_id}/add
        if self.token:
            headers = {"Authorization": f"Bearer {self.token}"}
            for resource in ["cart"]:
                base_path = f"{self.api_prefix}/{resource}"
                try:
                    r = requests.post(f"{self.base_url}{base_path}", json={}, headers=headers, timeout=self.timeout)
                    if r.status_code < 400:
                        data = r.json()
                        # Extract ID from response
                        cart_data = data.get("cart", data)
                        cart_id = cart_data.get("id") if isinstance(cart_data, dict) else None
                        if cart_id:
                            print(f"  Created {resource}: {cart_id}")
                            # Probe GET /cart/{id}
                            r2 = requests.get(f"{self.base_url}{base_path}/{cart_id}", headers=headers, timeout=self.timeout)
                            if r2.status_code < 404:
                                self.endpoints.append({"path": f"{base_path}/{{id}}", "method": "GET",
                                                       "status": r2.status_code, "protected": True, "inferred": True})
                                print(f"  ✅ GET {base_path}/{{id}} → {r2.status_code}")

                            # Probe cart sub-actions
                            for sub in ["add", "update", "remove", "coupon"]:
                                r3 = requests.post(f"{self.base_url}{base_path}/{cart_id}/{sub}",
                                                   json={}, headers=headers, timeout=self.timeout)
                                if r3.status_code != 404:
                                    self.endpoints.append({"path": f"{base_path}/{{id}}/{sub}", "method": "POST",
                                                           "status": r3.status_code, "protected": True, "inferred": True})
                                    print(f"  ✅ POST {base_path}/{{id}}/{sub} → {r3.status_code}")
                except Exception:
                    pass

    def _structural_inference(self):
        """
        Step 6: Discover endpoints from structural patterns in already-discovered data.
        
        Instead of guessing vocabulary, we analyze:
        1. Response shapes → infer related resources from _id fields
        2. Naming conventions → replicate patterns (hyphenated, underscore, etc.)
        3. Sub-path patterns → if /articles/search exists, try /products/search
        4. PATCH probing → many apps use PATCH instead of PUT
        5. /me pattern → if /users exists, try /users/me
        """
        print(f"[AutoDiscovery] Step 6: Structural inference...")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        already_found = set()
        for ep in self.endpoints:
            already_found.add(f"{ep.get('method','GET')} {ep['path']}")

        new_found = 0

        # ── Strategy 1: FK inference from response shapes ──
        # If /products returns items with "category_id", try /categories
        fk_candidates = set()
        for resource, info in self.resources.items():
            item_shape = info.get("item_shape", [])
            for field in item_shape:
                if field.endswith("_id") or field.endswith("Id"):
                    # Extract resource name: "category_id" → "categories", "author_id" → "authors"
                    base = field.replace("_id", "").replace("Id", "")
                    # Pluralize simply
                    if base.endswith("y"):
                        plural = base[:-1] + "ies"
                    elif base.endswith("s"):
                        plural = base + "es"
                    else:
                        plural = base + "s"
                    fk_candidates.add(plural)
                    fk_candidates.add(base)  # Also try singular (e.g., /user)

        for candidate in fk_candidates:
            path = f"{self.api_prefix}/{candidate}"
            if f"GET {path}" in already_found:
                continue
            try:
                self._throttle()
                r = requests.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
                if r.status_code < 400 or r.status_code == 401:
                    entry = {"path": path, "method": "GET", "status": r.status_code,
                             "protected": r.status_code == 401, "inferred": True, "source": "fk_inference"}
                    if r.status_code < 400:
                        try:
                            data = r.json()
                            if isinstance(data, dict):
                                entry["response_keys"] = list(data.keys())[:10]
                                for key, val in data.items():
                                    if isinstance(val, list) and val and isinstance(val[0], dict):
                                        entry["list_field"] = key
                                        entry["item_shape"] = list(val[0].keys())
                                        for id_field in ["id", "slug", "uid", "_id"]:
                                            if id_field in val[0]:
                                                entry["sample_id"] = str(val[0][id_field])
                                                break
                        except: pass
                    self.endpoints.append(entry)
                    self.resources[candidate] = entry
                    already_found.add(f"GET {path}")
                    new_found += 1
                    label = "🔓" if r.status_code < 400 else "🔒"
                    print(f"  {label} FK→ GET {path} (from *_id field)")
            except: pass

        # ── Strategy 2: Naming convention replication ──
        # If we found "product-categories", try "product-tags", "product-types", "product-variants"
        found_names = [r for r in self.resources.keys()]
        hyphenated = [n for n in found_names if "-" in n]
        underscored = [n for n in found_names if "_" in n]

        if hyphenated:
            # Extract prefixes: "product-categories" → "product"
            prefixes = set()
            suffixes = set()
            for name in hyphenated:
                parts = name.split("-")
                if len(parts) >= 2:
                    prefixes.add(parts[0])
                    suffixes.add("-".join(parts[1:]))

            # Try combining known prefixes with common suffixes
            common_suffixes = ["categories", "tags", "types", "variants", "options",
                               "collections", "providers", "reasons", "methods", "sessions"]
            for prefix in prefixes:
                for suffix in common_suffixes:
                    candidate = f"{prefix}-{suffix}"
                    if candidate in self.resources:
                        continue
                    path = f"{self.api_prefix}/{candidate}"
                    if f"GET {path}" in already_found:
                        continue
                    try:
                        self._throttle()
                        r = requests.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
                        if r.status_code < 400 or r.status_code == 401:
                            entry = {"path": path, "method": "GET", "status": r.status_code,
                                     "protected": r.status_code == 401, "inferred": True, "source": "naming_convention"}
                            if r.status_code < 400:
                                try:
                                    data = r.json()
                                    if isinstance(data, dict):
                                        entry["response_keys"] = list(data.keys())[:10]
                                        for key, val in data.items():
                                            if isinstance(val, list) and val and isinstance(val[0], dict):
                                                entry["list_field"] = key
                                                entry["item_shape"] = list(val[0].keys())
                                                for id_field in ["id", "slug"]:
                                                    if id_field in val[0]:
                                                        entry["sample_id"] = str(val[0][id_field])
                                                        break
                                except: pass
                            self.endpoints.append(entry)
                            self.resources[candidate] = entry
                            already_found.add(f"GET {path}")
                            new_found += 1
                            print(f"  🔗 Pattern→ GET {path} (naming convention)")
                    except: pass

        # ── Strategy 3: Sub-path replication (conservative) ──
        # Only replicate sub-paths on resources that returned lists (content resources)
        list_resources = [r for r, info in self.resources.items() if info.get("list_field")]
        replicate_subpaths = ["search", "me", "latest", "featured"]  # Only the most common
        for subpath in replicate_subpaths:
            for resource in list_resources:
                path = f"{self.api_prefix}/{resource}/{subpath}"
                if f"GET {path}" in already_found:
                    continue
                try:
                    self._throttle()
                    r = requests.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
                    if r.status_code < 400:
                        self.endpoints.append({"path": path, "method": "GET", "status": r.status_code,
                                               "inferred": True, "source": "subpath_replication"})
                        already_found.add(f"GET {path}")
                        new_found += 1
                        print(f"  🔗 Sub→ GET {path}")
                except: pass

        # ── Strategy 4: PATCH probing on resources that have PUT ──
        for ep in list(self.endpoints):
            if ep.get("method") == "PUT":
                patch_path = ep["path"]
                if f"PATCH {patch_path}" in already_found:
                    continue
                # Extract a sample_id from the parent resource
                resource_base = "/".join(patch_path.split("/")[:-1])
                parent = self.resources.get(resource_base.split("/")[-1])
                sample_id = parent.get("sample_id") if parent else None
                if sample_id:
                    test_path = patch_path.replace("{id}", sample_id)
                    try:
                        self._throttle()
                        r = requests.patch(f"{self.base_url}{test_path}", json={}, headers=headers, timeout=self.timeout)
                        if r.status_code != 404 and r.status_code != 405:
                            self.endpoints.append({"path": patch_path, "method": "PATCH", "status": r.status_code,
                                                   "protected": r.status_code == 401, "inferred": True, "source": "patch_probe"})
                            already_found.add(f"PATCH {patch_path}")
                            new_found += 1
                            print(f"  ✅ PATCH {patch_path} → {r.status_code}")
                    except: pass

        # ── Strategy 5: /me pattern on user-like resources ──
        for resource in ["users", "customers", "user"]:
            if resource in self.resources:
                for me_path in [f"/{resource}/me", f"/{resource}/me/password"]:
                    path = f"{self.api_prefix}{me_path}"
                    if f"GET {path}" in already_found:
                        continue
                    try:
                        self._throttle()
                        r = requests.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
                        if r.status_code < 400 or r.status_code == 401:
                            self.endpoints.append({"path": path, "method": "GET", "status": r.status_code,
                                                   "protected": r.status_code == 401, "inferred": True, "source": "me_pattern"})
                            already_found.add(f"GET {path}")
                            new_found += 1
                            print(f"  👤 Me→ GET {path}")
                    except: pass

        # ── Strategy 6: Infer CRUD on newly discovered resources ──
        new_resources = {k: v for k, v in self.resources.items() if v.get("source") in ("fk_inference", "naming_convention")}
        for resource, info in new_resources.items():
            sample_id = info.get("sample_id")
            base_path = info["path"]
            if sample_id:
                # GET detail
                if f"GET {base_path}/{{id}}" not in already_found:
                    try:
                        self._throttle()
                        r = requests.get(f"{self.base_url}{base_path}/{sample_id}", headers=headers, timeout=self.timeout)
                        if r.status_code < 404:
                            self.endpoints.append({"path": f"{base_path}/{{id}}", "method": "GET",
                                                   "status": r.status_code, "inferred": True})
                            already_found.add(f"GET {base_path}/{{id}}")
                            new_found += 1
                            print(f"  ✅ GET {base_path}/{{id}} → {r.status_code}")
                    except: pass

        print(f"  Structural inference found {new_found} new endpoints")

    def _generate_spec(self) -> dict:
        """Generate an OpenAPI-like spec from discoveries."""
        paths = {}
        for ep in self.endpoints:
            path = ep["path"]
            method = ep.get("method", "GET").lower()
            if path not in paths:
                paths[path] = {}

            operation = {
                "status": ep.get("status"),
                "protected": ep.get("protected", False),
            }
            if ep.get("response_keys"):
                operation["response_keys"] = ep["response_keys"]
            if ep.get("list_field"):
                operation["returns_list"] = ep["list_field"]
                operation["item_shape"] = ep.get("item_shape", [])
            if ep.get("request_body_keys"):
                operation["request_body"] = ep["request_body_keys"]
            if ep.get("returns_token"):
                operation["returns_token"] = True
            if ep.get("inferred"):
                operation["inferred"] = True
            if ep.get("note"):
                operation["note"] = ep["note"]

            paths[path][method] = operation

        spec = {
            "tessera_autodiscovery": True,
            "base_url": self.base_url,
            "api_prefix": self.api_prefix,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "auth_available": self.token is not None,
            "total_endpoints": len(self.endpoints),
            "total_resources": len(self.resources),
            "paths": paths,
        }
        return spec

    def _describe_shape(self, data) -> str:
        """Describe the shape of a JSON response."""
        if isinstance(data, dict):
            return f"object({', '.join(list(data.keys())[:5])})"
        elif isinstance(data, list):
            if data and isinstance(data[0], dict):
                return f"list[object({', '.join(list(data[0].keys())[:5])})]"
            return f"list[{type(data[0]).__name__}]" if data else "list[]"
        return type(data).__name__


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8006"
    discovery = APIAutoDiscovery(url)
    spec = discovery.discover()

    # Save
    output = "discovered_api.json"
    with open(output, "w") as f:
        json.dump(spec, f, indent=2)
    print(f"\nSpec saved to {output}")

    # Pretty print
    print("\n" + "=" * 60)
    print("DISCOVERED API STRUCTURE")
    print("=" * 60)
    for path, methods in sorted(spec["paths"].items()):
        for method, info in methods.items():
            auth = " 🔒" if info.get("protected") else ""
            inferred = " (inferred)" if info.get("inferred") else ""
            token = " → returns token" if info.get("returns_token") else ""
            list_info = f" → list of {info['returns_list']}" if info.get("returns_list") else ""
            print(f"  {method.upper():6} {path:40}{auth}{inferred}{token}{list_info}")
