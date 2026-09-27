"""
Phren Source Route Parser

The REAL owner-assisted discovery: parse route definitions directly
from the source code. No HTTP probing needed.

Supported frameworks:
  - FastAPI / Starlette (Python)   → @app.get("/path"), @router.post("/path")
  - Express / Koa (Node.js)        → app.get("/path"), router.post("/path")
  - Django REST Framework (Python) → urlpatterns, ViewSet
  - Ruby on Rails                  → routes.rb resources/get/post
  - Go (Chi/Echo/Gin)              → r.Get("/path"), g.POST("/path"), m.Group("/path")

Usage:
    from source_parser import SourceRouteParser
    parser = SourceRouteParser("/path/to/project")
    routes = parser.parse()
    # Returns: [{"method": "GET", "path": "/api/users", "auth": True}, ...]
"""
import os
import re
from pathlib import Path
from typing import Optional


class SourceRouteParser:
    """Parse API routes directly from source code."""

    def __init__(self, project_path: str, api_prefix: str = ""):
        self.project_path = Path(project_path)
        self.api_prefix = api_prefix
        self.routes: list[dict] = []
        self.framework: str = ""

    def parse(self) -> list[dict]:
        """Auto-detect framework and parse routes."""
        self.framework = self._detect_framework()
        print(f"[SourceParser] Project: {self.project_path}")
        print(f"[SourceParser] Framework: {self.framework}")

        if self.framework == "fastapi":
            self._parse_fastapi()
        elif self.framework == "express":
            self._parse_express()
        elif self.framework == "nextjs":
            self._parse_nextjs_file_routes()
        elif self.framework == "django":
            self._parse_django()
        elif self.framework == "rails":
            self._parse_rails()
        elif self.framework == "go":
            self._parse_go()
        elif self.framework == "nestjs":
            self._parse_nestjs()
        elif self.framework == "trpc":
            self._parse_trpc()
        elif self.framework == "php":
            self._parse_php()
        else:
            print(f"[SourceParser] Unknown framework — trying all parsers")
            self._parse_nextjs_file_routes()
            self._parse_fastapi()
            self._parse_nestjs()
            self._parse_php()
            self._parse_express()
            self._parse_go()

        # Deduplicate
        seen = set()
        unique = []
        for r in self.routes:
            key = f"{r['method']} {r['path']}"
            if key not in seen:
                seen.add(key)
                unique.append(r)
        self.routes = unique

        print(f"[SourceParser] Found {len(self.routes)} routes")
        return self.routes

    def _detect_framework(self) -> str:
        """Detect the web framework from project files."""
        files = set()
        for f in self.project_path.rglob("*"):
            if f.is_file():
                files.add(f.name)

        # NestJS: has .controller.ts files with @Get/@Post decorators
        has_controllers = any(
            f.name.endswith(".controller.ts")
            for f in self.project_path.rglob("*.controller.ts")
        )
        if has_controllers:
            return "nestjs"

        # tRPC: has router.ts with router() calls
        has_trpc = any(
            f.name == "router.ts" and "router(" in (f.read_text(errors="ignore")[:500])
            for f in self.project_path.rglob("router.ts")
        )
        if has_trpc:
            return "trpc"

        # Next.js / Cal.com file-based routing: _get.ts, _post.ts, route.ts
        has_file_routes = any(
            f.name in ("_get.ts", "_post.ts", "_patch.ts", "_delete.ts", "route.ts")
            for f in self.project_path.rglob("*.ts")
        )
        if has_file_routes:
            return "nextjs"

        # Check for framework indicators
        if "requirements.txt" in files or "pyproject.toml" in files:
            for f in self.project_path.rglob("*.py"):
                try:
                    content = f.read_text(errors="ignore")[:2000]
                    if "from fastapi" in content or "import fastapi" in content:
                        return "fastapi"
                    if "from django" in content:
                        return "django"
                    if "from flask" in content:
                        return "flask"
                except:
                    pass

        # PHP: check for .php files with Http:: or Route:: or $app-> patterns
        has_php = any(f.name.endswith(".php") for f in self.project_path.rglob("*.php"))
        if has_php:
            for f in list(self.project_path.rglob("*.php"))[:10]:
                try:
                    content = f.read_text(errors="ignore")[:3000]
                    if "Http::get" in content or "Http::post" in content:
                        return "php"  # Utopia/Appwrite style
                    if "Route::get" in content or "Route::post" in content:
                        return "php"  # Laravel style
                    if "$app->get" in content or "$router->get" in content:
                        return "php"  # Slim/custom style
                except:
                    pass

        if "package.json" in files:
            try:
                pkg = (self.project_path / "package.json").read_text()
                if "express" in pkg:
                    return "express"
                if "koa" in pkg:
                    return "express"  # Similar routing
                if "fastify" in pkg:
                    return "express"
            except:
                pass

        if "Gemfile" in files or "config.ru" in files:
            return "rails"

        if "go.mod" in files:
            return "go"

        return "unknown"

    # ── FastAPI / Starlette / Flask ──

    def _parse_fastapi(self):
        """Parse @app.get("/path") and @router.post("/path") patterns."""
        print("[SourceParser] Parsing FastAPI/Flask routes...")

        # Collect all route prefixes from include_router calls
        prefixes = {}  # router_var -> prefix
        for py_file in self.project_path.rglob("*.py"):
            try:
                content = py_file.read_text(errors="ignore")
            except:
                continue

            # Find include_router(xxx.router, prefix="/items")
            for m in re.finditer(
                r'include_router\s*\(\s*(\w+)(?:\.router)?\s*(?:,\s*prefix\s*=\s*["\']([^"\']*)["\'])?',
                content
            ):
                var = m.group(1)
                prefix = m.group(2) or ""
                prefixes[var] = prefix

            # Find APIRouter(prefix="/items")
            for m in re.finditer(
                r'(\w+)\s*=\s*APIRouter\s*\(\s*(?:prefix\s*=\s*["\']([^"\']*)["\'])?',
                content
            ):
                var = m.group(1)
                prefix = m.group(2) or ""
                prefixes[var] = prefix

        # Now find all route decorators
        for py_file in self.project_path.rglob("*.py"):
            try:
                content = py_file.read_text(errors="ignore")
            except:
                continue

            # Find the router variable used in this file
            file_prefix = ""
            for var, prefix in prefixes.items():
                if f"@{var}." in content or f"{var} = APIRouter" in content:
                    file_prefix = prefix
                    break

            # @app.get("/path") or @router.post("/path")
            for m in re.finditer(
                r'@(\w+)\.(get|post|put|patch|delete|head|options)\s*\(\s*["\']([^"\']*)["\']',
                content
            ):
                router_var = m.group(1)
                method = m.group(2).upper()
                path = m.group(3)

                # Determine if auth is required
                # Look at the function signature for Depends(get_current_user) etc.
                func_start = m.end()
                func_block = content[func_start:func_start + 500]
                has_auth = bool(re.search(
                    r'Depends\s*\(\s*(?:get_current|_auth|require_auth|get_user|check_auth)',
                    func_block
                ))

                full_path = self.api_prefix + file_prefix + path
                self.routes.append({
                    "method": method,
                    "path": full_path,
                    "auth": has_auth,
                    "file": str(py_file.relative_to(self.project_path)),
                })

    # ── Express / Koa / Fastify (Node.js) ──

    def _parse_express(self):
        """Parse app.get("/path") and router.post("/path") patterns."""
        print("[SourceParser] Parsing Express/Node.js routes...")

        for js_file in self.project_path.rglob("*.ts"):
            self._parse_js_file(js_file)
        for js_file in self.project_path.rglob("*.js"):
            self._parse_js_file(js_file)

    def _parse_js_file(self, js_file):
        try:
            content = js_file.read_text(errors="ignore")
        except:
            return

        # app.get("/path", handler) or router.post("/path", handler)
        for m in re.finditer(
            r'(?:app|router|server|r)\.(get|post|put|patch|delete)\s*\(\s*["\']([^"\']*)["\']',
            content, re.IGNORECASE
        ):
            method = m.group(1).upper()
            path = m.group(2)
            self.routes.append({
                "method": method,
                "path": self.api_prefix + path,
                "file": str(js_file.relative_to(self.project_path)),
            })

        # Express file-based routing (Next.js/Medusa style)
        # The file path IS the route
        rel_path = str(js_file.relative_to(self.project_path))
        if "/api/" in rel_path and js_file.name in ("route.ts", "route.js"):
            route_path = "/" + rel_path.rsplit("/route.", 1)[0]
            # Convert [param] to {param}
            route_path = re.sub(r'\[(\w+)\]', r'{\1}', route_path)
            # Check which methods are exported
            for method in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
                if re.search(rf'export\s+(?:async\s+)?(?:function|const)\s+{method}', content):
                    self.routes.append({
                        "method": method,
                        "path": route_path,
                        "file": rel_path,
                    })

    # ── Next.js / Cal.com file-based routing ──

    def _parse_nextjs_file_routes(self):
        """
        Parse file-based API routes (Next.js / Cal.com pattern).
        
        Structure:
          pages/api/bookings/index.ts      → GET /bookings (list)
          pages/api/bookings/_get.ts       → GET /bookings
          pages/api/bookings/_post.ts      → POST /bookings
          pages/api/bookings/[id]/_get.ts  → GET /bookings/{id}
          pages/api/bookings/[id]/_patch.ts → PATCH /bookings/{id}
          pages/api/bookings/[id]/_delete.ts → DELETE /bookings/{id}
          
        Also handles Medusa/Next.js App Router:
          store/products/route.ts          → GET/POST /store/products
          store/products/[id]/route.ts     → GET/PATCH/DELETE /store/products/{id}
        """
        print("[SourceParser] Parsing Next.js file-based routes...")

        method_map = {
            "_get.ts": "GET", "_get.js": "GET",
            "_post.ts": "POST", "_post.js": "POST",
            "_put.ts": "PUT", "_put.js": "PUT",
            "_patch.ts": "PATCH", "_patch.js": "PATCH",
            "_delete.ts": "DELETE", "_delete.js": "DELETE",
        }

        for ts_file in self.project_path.rglob("*.ts"):
            fname = ts_file.name

            # Cal.com style: _get.ts, _post.ts, etc.
            if fname in method_map:
                method = method_map[fname]
                # Build path from directory structure
                rel_dir = ts_file.parent.relative_to(self.project_path)
                path_parts = str(rel_dir).split(os.sep)

                # Strip common prefixes like "pages/api" or "src/pages/api"
                clean_parts = []
                skip = True
                for part in path_parts:
                    if not skip:
                        clean_parts.append(part)
                    if part == "api":
                        skip = False

                if not clean_parts:
                    clean_parts = path_parts

                # Convert [param] to {param}
                route = "/" + "/".join(clean_parts)
                route = re.sub(r'\[(\w+)\]', r'{\1}', route)

                self.routes.append({
                    "method": method,
                    "path": self.api_prefix + route,
                    "file": str(ts_file.relative_to(self.project_path)),
                })

            # Medusa/App Router style: route.ts with exported method functions
            elif fname in ("route.ts", "route.js"):
                try:
                    content = ts_file.read_text(errors="ignore")
                except:
                    continue

                rel_dir = ts_file.parent.relative_to(self.project_path)
                path_parts = str(rel_dir).split(os.sep)

                # Strip common prefixes
                clean_parts = []
                skip = True
                for part in path_parts:
                    if not skip:
                        clean_parts.append(part)
                    if part in ("api", "store", "admin"):
                        skip = False
                        clean_parts.append(part)

                if not clean_parts:
                    clean_parts = path_parts

                route = "/" + "/".join(clean_parts)
                route = re.sub(r'\[(\w+)\]', r'{\1}', route)

                for method in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
                    if re.search(rf'export\s+(?:async\s+)?(?:function|const)\s+{method}', content):
                        self.routes.append({
                            "method": method,
                            "path": route,
                            "file": str(ts_file.relative_to(self.project_path)),
                        })

        # Also check for .js variants
        for js_file in self.project_path.rglob("*.js"):
            fname = js_file.name
            if fname in method_map:
                method = method_map[fname]
                rel_dir = js_file.parent.relative_to(self.project_path)
                route = "/" + str(rel_dir).replace(os.sep, "/")
                route = re.sub(r'\[(\w+)\]', r'{\1}', route)
                # Strip pages/api prefix
                if "/pages/api/" in route:
                    route = route.split("/pages/api")[1]
                self.routes.append({
                    "method": method,
                    "path": self.api_prefix + route,
                    "file": str(js_file.relative_to(self.project_path)),
                })

        # Traditional Next.js pages/api: index.ts with req.method checks
        for ts_file in list(self.project_path.rglob("*.ts")) + list(self.project_path.rglob("*.tsx")):
            rel = str(ts_file.relative_to(self.project_path))
            # Only pages/api files, not _get/_post style (already handled)
            if "pages/api" not in rel:
                continue
            if ts_file.name.startswith("_") and ts_file.name != "__init__.ts":
                continue
            if ts_file.name in method_map:
                continue

            try:
                content = ts_file.read_text(errors="ignore")
            except:
                continue

            if "export default" not in content:
                continue

            # Build route from file path
            route = rel
            if route.endswith("/index.ts") or route.endswith("/index.tsx"):
                route = route.rsplit("/index.", 1)[0]
            else:
                route = route.rsplit(".", 1)[0]
            # Strip pages/api prefix
            if "pages/api/" in route:
                route = "/" + route.split("pages/api/")[1]
            else:
                route = "/" + route
            route = re.sub(r'\[\.\.\.(\w+)\]', r'{...\1}', route)
            route = re.sub(r'\[(\w+)\]', r'{\1}', route)

            # Detect methods from req.method checks
            methods_found = set()
            for method in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
                if re.search(rf'req\.method\s*===?\s*["\']' + method + r'["\']', content):
                    methods_found.add(method)
                elif re.search(rf'method\s*===?\s*["\']' + method + r'["\']', content):
                    methods_found.add(method)

            if not methods_found:
                # Default: assume GET and POST for handlers without explicit checks
                methods_found = {"GET", "POST"}

            has_auth = "getServerSession" in content or "getSession" in content or "auth(" in content

            for method in methods_found:
                self.routes.append({
                    "method": method,
                    "path": self.api_prefix + route,
                    "auth": has_auth,
                    "file": rel,
                })

    # ── Django REST Framework ──

    def _parse_django(self):
        """Parse urlpatterns from urls.py files."""
        print("[SourceParser] Parsing Django routes...")

        for urls_file in self.project_path.rglob("urls.py"):
            try:
                content = urls_file.read_text(errors="ignore")
            except:
                continue

            # path("api/users/", views.UserList.as_view())
            for m in re.finditer(
                r'path\s*\(\s*["\']([^"\']*)["\']',
                content
            ):
                path = "/" + m.group(1).rstrip("/")
                # Infer method from view class name or context
                self.routes.append({"method": "GET", "path": path, "file": str(urls_file)})

    # ── Ruby on Rails ──

    def _parse_rails(self):
        """Parse config/routes.rb."""
        print("[SourceParser] Parsing Rails routes...")

        routes_file = self.project_path / "config" / "routes.rb"
        if not routes_file.exists():
            # Try config/routes/api.rb
            routes_file = self.project_path / "config" / "routes" / "api.rb"

        if not routes_file.exists():
            print("  No routes.rb found")
            return

        content = routes_file.read_text(errors="ignore")
        current_namespace = ""

        # resources :articles, only: [:index, :show, :create, :update]
        for m in re.finditer(
            r'resources\s+:(\w+)(?:\s*,\s*only:\s*%i\[([^\]]*)\]|\s*,\s*only:\s*\[([^\]]*)\])?',
            content
        ):
            resource = m.group(1)
            only = m.group(2) or m.group(3) or "index show create update destroy"

            actions = only.split()
            base = f"{self.api_prefix}/{resource}"

            action_map = {
                "index": ("GET", base),
                "show": ("GET", f"{base}/{{id}}"),
                "create": ("POST", base),
                "update": ("PUT", f"{base}/{{id}}"),
                "destroy": ("DELETE", f"{base}/{{id}}"),
            }

            for action in actions:
                action = action.strip().strip(":").strip(",")
                if action in action_map:
                    method, path = action_map[action]
                    self.routes.append({
                        "method": method,
                        "path": path,
                        "file": str(routes_file),
                    })

        # get "/path", to: "controller#action"
        for m in re.finditer(
            r'(get|post|put|patch|delete)\s+["\']([^"\']+)["\']',
            content
        ):
            method = m.group(1).upper()
            path = m.group(2)
            if not path.startswith("/"):
                path = "/" + path
            self.routes.append({
                "method": method,
                "path": self.api_prefix + path,
                "file": str(routes_file),
            })

    # ── Go (Chi / Echo / Gin / standard) ──

    def _parse_go(self):
        """Parse Go router registrations."""
        print("[SourceParser] Parsing Go routes...")

        for go_file in self.project_path.rglob("*.go"):
            if go_file.name.endswith("_test.go"):
                continue
            try:
                content = go_file.read_text(errors="ignore")
            except:
                continue

            # Chi/Echo style: m.Get("/path", handler) or g.GET("/path", handler)
            for m_match in re.finditer(
                r'(?:m|r|g|e|srv|app|api)\.(Get|Post|Put|Patch|Delete|GET|POST|PUT|PATCH|DELETE)\s*\(\s*["\']([^"\']*)["\']',
                content
            ):
                method = m_match.group(1).upper()
                path = m_match.group(2)
                self.routes.append({
                    "method": method,
                    "path": self.api_prefix + path,
                    "file": str(go_file.relative_to(self.project_path)),
                })

            # m.Combo("/path").Get(h).Post(h).Delete(h)
            for m_match in re.finditer(
                r'\.Combo\s*\(\s*["\']([^"\']*)["\'].*?\)',
                content, re.DOTALL
            ):
                combo_path = m_match.group(1)
                combo_block = content[m_match.start():m_match.start() + 300]
                for method in ["Get", "Post", "Put", "Patch", "Delete"]:
                    if f".{method}(" in combo_block:
                        self.routes.append({
                            "method": method.upper(),
                            "path": self.api_prefix + combo_path,
                            "file": str(go_file.relative_to(self.project_path)),
                        })

            # m.Group("/prefix", func() { ... }) — track groups for prefix
            # This is complex to parse correctly, simplified version
            for m_match in re.finditer(
                r'\.Group\s*\(\s*["\']([^"\']*)["\']',
                content
            ):
                pass  # Group prefix tracking would need AST parsing

    # ── NestJS ──

    def _parse_nestjs(self):
        """
        Parse NestJS controller decorators:
          @Controller('users')
          @Get(':id')
          @Post()
          @Patch(':id')
          @Delete(':id')
        """
        print("[SourceParser] Parsing NestJS controllers...")

        for ctrl_file in self.project_path.rglob("*.controller.ts"):
            try:
                content = ctrl_file.read_text(errors="ignore")
            except:
                continue

            # Extract @Controller('prefix')
            controller_prefix = ""
            m = re.search(r"@Controller\s*\(\s*['\"]([^'\"]*)['\"]", content)
            if m:
                controller_prefix = "/" + m.group(1).strip("/")

            # Extract route decorators: @Get(), @Post(':id'), @Get('search'), etc.
            for m in re.finditer(
                r"@(Get|Post|Put|Patch|Delete|Head)\s*\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)",
                content
            ):
                method = m.group(1).upper()
                path_suffix = m.group(2) or ""

                if path_suffix:
                    # Convert :param to {param}
                    path_suffix = re.sub(r':(\w+)', r'{\1}', path_suffix)
                    if not path_suffix.startswith("/"):
                        path_suffix = "/" + path_suffix

                full_path = self.api_prefix + controller_prefix + path_suffix

                # Check if auth guard is present
                has_auth = bool(re.search(r"@UseGuards|@Auth|JwtAuthGuard|AuthGuard", content))

                self.routes.append({
                    "method": method,
                    "path": full_path,
                    "auth": has_auth,
                    "file": str(ctrl_file.relative_to(self.project_path)),
                })

    # ── tRPC ──

    def _parse_trpc(self):
        """
        Parse tRPC router definitions. Two patterns:
        
        Pattern 1 (inline): getAll: publicProcedure.query(...)
        Pattern 2 (imported): get: getDocumentRoute, create: createDocumentRoute
        """
        print("[SourceParser] Parsing tRPC routers...")

        for ts_file in self.project_path.rglob("router.ts"):
            try:
                content = ts_file.read_text(errors="ignore")
            except:
                continue

            rel_dir = ts_file.parent.relative_to(self.project_path)
            dir_name = str(rel_dir).replace(os.sep, "/")

            router_name = ""
            m = re.search(r'export\s+const\s+(\w+Router)\s*=\s*router', content)
            if m:
                router_name = m.group(1).replace("Router", "").replace("_", "-")

            # Pattern 1: inline procedures
            for m in re.finditer(
                r'(\w+)\s*:\s*(?:public|protected|admin)?Procedure\s*\.(query|mutation)',
                content
            ):
                proc_name = m.group(1)
                proc_type = m.group(2)
                method = "GET" if proc_type == "query" else "POST"
                path = f"/trpc/{router_name}.{proc_name}" if router_name else f"/trpc/{dir_name}/{proc_name}"
                is_protected = "protectedProcedure" in content[max(0, m.start()-50):m.start()+50]
                self.routes.append({"method": method, "path": self.api_prefix + path,
                                    "auth": is_protected, "file": str(ts_file.relative_to(self.project_path))})

            # Pattern 2: named imports — key: importedRoute
            # Match: get: getDocumentRoute, create: createDocumentRoute
            router_block = re.search(r'router\s*\(\s*\{(.*?)\}\s*\)', content, re.DOTALL)
            if router_block:
                block = router_block.group(1)
                for m in re.finditer(r'(\w+)\s*:\s*(\w+Route)\b', block):
                    proc_name = m.group(1)
                    route_var = m.group(2)
                    # Infer method from name: get*, find*, search* → GET, create*, delete*, update* → POST
                    name_lower = route_var.lower()
                    if any(name_lower.startswith(p) for p in ["get", "find", "search", "download", "list"]):
                        method = "GET"
                    elif any(name_lower.startswith(p) for p in ["delete", "remove"]):
                        method = "DELETE"
                    elif any(name_lower.startswith(p) for p in ["update", "edit"]):
                        method = "PATCH"
                    else:
                        method = "POST"
                    path = f"/trpc/{router_name}.{proc_name}" if router_name else f"/trpc/{dir_name}/{proc_name}"
                    self.routes.append({"method": method, "path": self.api_prefix + path,
                                        "file": str(ts_file.relative_to(self.project_path))})

    # ── PHP (Utopia/Appwrite, Laravel, Slim) ──

    def _parse_php(self):
        """
        Parse PHP route definitions. Supports:
        - Utopia/Appwrite: Http::get('/v1/path'), Http::post('/v1/path')
        - Laravel: Route::get('/path'), Route::post('/path')
        - Slim: $app->get('/path'), $app->post('/path')
        """
        print("[SourceParser] Parsing PHP routes...")

        for php_file in self.project_path.rglob("*.php"):
            try:
                content = php_file.read_text(errors="ignore")
            except:
                continue

            # Utopia/Appwrite: Http::get('/v1/account')
            for m in re.finditer(
                r"Http::(get|post|put|patch|delete)\s*\(\s*'(/[^']*)'",
                content, re.IGNORECASE
            ):
                method = m.group(1).upper()
                path = m.group(2)
                # Convert :param to {param}
                path = re.sub(r':(\w+)', r'{\1}', path)

                # Check for auth scope
                # Look ahead for ->label('scope', ...) or ->label('auth.type', ...)
                block_after = content[m.end():m.end() + 500]
                has_auth = bool(re.search(r"label\s*\(\s*'scope'", block_after))

                self.routes.append({
                    "method": method,
                    "path": self.api_prefix + path,
                    "auth": has_auth,
                    "file": str(php_file.relative_to(self.project_path)),
                })

            # Laravel: Route::get('path', ...) — with or without leading slash
            # Handles both Route::get('accounts', [...]) and Route::get('/accounts', ...)
            for m in re.finditer(
                r"Route::(get|post|put|patch|delete)\s*\(\s*'([^']*)'",
                content, re.IGNORECASE
            ):
                method = m.group(1).upper()
                path = m.group(2)
                if not path:
                    path = "/"
                # Ensure leading slash
                if not path.startswith("/"):
                    path = "/" + path
                # Convert Laravel {param} — already correct format
                # Convert :param style just in case
                path = re.sub(r':(\w+)', r'{\1}', path)

                self.routes.append({
                    "method": method,
                    "path": self.api_prefix + path,
                    "file": str(php_file.relative_to(self.project_path)),
                })

            # Laravel: Route::apiResource('name', Controller) → 5 CRUD routes
            for m in re.finditer(
                r"Route::(apiResource|resource)\s*\(\s*'([^']*)'",
                content
            ):
                resource_name = m.group(2)
                parts = resource_name.split(".")
                if len(parts) == 1:
                    base = f"/{parts[0]}"
                    singular = parts[0][:-1] if parts[0].endswith("s") else parts[0]
                    detail = f"/{parts[0]}/{{{singular}}}"
                else:
                    parent = parts[0]
                    child = parts[1]
                    ps = parent[:-1] if parent.endswith("s") else parent
                    cs = child[:-1] if child.endswith("s") else child
                    base = f"/{parent}/{{{ps}}}/{child}"
                    detail = f"/{parent}/{{{ps}}}/{child}/{{{cs}}}"

                after = content[m.end():m.end() + 200]
                excluded = set()
                for ex in re.finditer(r"'(\w+)'", re.search(r"->except\s*\(([^)]*)\)", after).group(1) if re.search(r"->except\s*\(([^)]*)\)", after) else ""):
                    excluded.add(ex.group(1))

                for method, path, action in [("GET",base,"index"),("POST",base,"store"),
                                              ("GET",detail,"show"),("PUT",detail,"update"),("DELETE",detail,"destroy")]:
                    if action not in excluded:
                        self.routes.append({"method": method, "path": self.api_prefix + path,
                                            "file": str(php_file.relative_to(self.project_path))})

            # Slim/custom: $app->get('/path', ...) or $router->post('/path', ...)
            for m in re.finditer(
                r"\$(?:app|router|group)\s*->\s*(get|post|put|patch|delete)\s*\(\s*'(/[^']*)'",
                content, re.IGNORECASE
            ):
                method = m.group(1).upper()
                path = m.group(2)
                path = re.sub(r'\{(\w+)\}', r'{\1}', path)
                self.routes.append({
                    "method": method,
                    "path": self.api_prefix + path,
                    "file": str(php_file.relative_to(self.project_path)),
                })

    def print_routes(self):
        """Pretty-print discovered routes."""
        for r in sorted(self.routes, key=lambda x: (x["path"], x["method"])):
            auth = " 🔒" if r.get("auth") else ""
            print(f"  {r['method']:7} {r['path']:50}{auth}  [{r.get('file', '')}]")


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    prefix = sys.argv[2] if len(sys.argv) > 2 else ""
    parser = SourceRouteParser(path, prefix)
    routes = parser.parse()
    parser.print_routes()
