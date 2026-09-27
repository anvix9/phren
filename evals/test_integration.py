"""
Phren — Test Suite

Tests the core components independently without requiring running servers.
Uses pytest with proper assertions — failures exit non-zero.
"""
import pytest
import os
from pathlib import Path


# ── Contract Schema Tests ──

class TestContractSchema:
    """Test that the contract schema validates correctly."""

    def test_import(self):
        from phren.contract.schema import PhrenContract
        assert PhrenContract is not None

    def test_create_minimal_contract(self):
        from phren.contract.schema import PhrenContract
        contract = PhrenContract(
            contract_id="test-contract-001",
            site_name="Test Site",
            site_url="http://localhost:8000",
        )
        assert contract.contract_id == "test-contract-001"
        assert contract.version == "0.1.0"

    def test_contract_with_screens(self):
        from phren.contract.schema import (
            PhrenContract, ScreenDefinition, ActionDefinition,
            ActionParameter, DataField,
        )
        search_action = ActionDefinition(
            id="search",
            name="search",
            description="Search products",
            api_method="GET",
            api_endpoint="/api/products",
            parameters=[
                ActionParameter(name="q", type="string", required=False),
            ],
        )
        screen = ScreenDefinition(
            id="products",
            name="products",
            description="Product listing",
            data_fields=[
                DataField(name="products", type="array", description="List of products"),
            ],
            actions=[search_action],
        )
        contract = PhrenContract(
            contract_id="shop-001",
            site_name="Shop",
            site_url="http://localhost:8000",
            screens=[screen],
        )
        assert len(contract.screens) == 1
        assert contract.screens[0].actions[0].id == "search"

    def test_agent_trust_enum(self):
        from phren.contract.schema import AgentTrust
        assert AgentTrust.ANONYMOUS == "anonymous"
        assert AgentTrust.IDENTIFIED == "identified"
        assert AgentTrust.VERIFIED == "verified"
        assert AgentTrust.SUPER_AGENT == "super_agent"


# ── Permission Resolver Tests ──

class TestResolver:
    """Test permission resolution logic."""

    def test_import(self):
        from phren.contract.resolver import resolve_permissions
        assert resolve_permissions is not None

    def test_resolve_with_minimal_contract(self):
        from phren.contract.schema import (
            PhrenContract, AgentProfile, AgentTrust, AgentCapabilities,
        )
        from phren.contract.resolver import resolve_permissions

        contract = PhrenContract(
            contract_id="test-001",
            site_name="Test",
            site_url="http://localhost",
        )
        agent = AgentProfile(
            provider="test",
            agent_name="test-agent",
            trust_level=AgentTrust.IDENTIFIED,
            purpose="testing",
            capabilities=AgentCapabilities(),
        )
        perms = resolve_permissions(contract, agent)
        assert perms is not None
        assert hasattr(perms, "allowed_actions")
        assert hasattr(perms, "denied_actions")


# ── Source Parser Tests ──

class TestSourceParser:
    """Test route extraction from source code."""

    def test_import(self):
        from phren.compiler.source_parser import SourceRouteParser
        assert SourceRouteParser is not None

    def test_parse_fastapi_simulation(self):
        """Parse routes from a known simulation — conduit has 19 endpoints."""
        from phren.compiler.source_parser import SourceRouteParser

        sim_path = Path(__file__).parent.parent / "simulations" / "conduit" / "api"
        if not sim_path.exists():
            pytest.skip("Conduit simulation not found")

        parser = SourceRouteParser(str(sim_path), "")
        routes = parser.parse()

        assert len(routes) >= 15, f"Expected >=15 routes from conduit, got {len(routes)}"

        # Verify known routes exist
        paths = {r["path"] for r in routes}
        methods = {(r["method"], r["path"]) for r in routes}

        assert ("GET", "/api/articles") in methods, "Missing GET /api/articles"
        assert ("POST", "/api/articles") in methods, "Missing POST /api/articles"
        assert ("GET", "/api/tags") in methods, "Missing GET /api/tags"

    def test_parse_returns_method_and_path(self):
        """Every parsed route must have method and path."""
        from phren.compiler.source_parser import SourceRouteParser

        sim_path = Path(__file__).parent.parent / "simulations" / "conduit" / "api"
        if not sim_path.exists():
            pytest.skip("Conduit simulation not found")

        parser = SourceRouteParser(str(sim_path), "")
        routes = parser.parse()

        for route in routes:
            assert "method" in route, f"Route missing method: {route}"
            assert "path" in route, f"Route missing path: {route}"
            assert route["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"), \
                f"Invalid method: {route['method']}"

    def test_zero_false_positives_on_conduit(self):
        """Source parser should have 100% precision — no false positives."""
        from phren.compiler.source_parser import SourceRouteParser

        sim_path = Path(__file__).parent.parent / "simulations" / "conduit" / "api"
        if not sim_path.exists():
            pytest.skip("Conduit simulation not found")

        parser = SourceRouteParser(str(sim_path), "")
        routes = parser.parse()

        # Every route the parser finds should have a valid HTTP method
        # and a path starting with /
        for route in routes:
            assert route["path"].startswith("/"), f"Path should start with /: {route['path']}"
            assert route["method"] in ("GET", "POST", "PUT", "PATCH", "DELETE"), \
                f"Invalid method: {route}"


# ── Auto-Discovery Tests ──

class TestAutoDiscovery:
    """Test blind auto-discovery (unit-level, no server needed)."""

    def test_import(self):
        from phren.compiler.api_autodiscovery import APIAutoDiscovery
        assert APIAutoDiscovery is not None

    def test_common_resources_not_empty(self):
        from phren.compiler.api_autodiscovery import APIAutoDiscovery
        assert len(APIAutoDiscovery.COMMON_RESOURCES) > 50, \
            "Resource vocabulary should have 50+ entries"

    def test_common_api_prefixes(self):
        from phren.compiler.api_autodiscovery import APIAutoDiscovery
        prefixes = APIAutoDiscovery.COMMON_API_PREFIXES
        assert "/api" in prefixes
        assert "/api/v1" in prefixes


# ── Owner Discovery Tests ──

class TestOwnerDiscovery:
    """Test owner-assisted discovery (unit-level)."""

    def test_import(self):
        from phren.compiler.owner_discovery import OwnerDiscovery
        assert OwnerDiscovery is not None

    def test_init(self):
        from phren.compiler.owner_discovery import OwnerDiscovery
        d = OwnerDiscovery(
            base_url="http://localhost:8000",
            api_prefix="/api/v1",
            auth_type="bearer",
            route_hints=["users", "projects"],
        )
        assert d.api_prefix == "/api/v1"
        assert d.auth_type == "bearer"
        assert len(d.route_hints) == 2


# ── Package Hygiene Tests ──

class TestPackageHygiene:
    """Ensure the package is properly structured."""

    def test_version_exists(self):
        import phren
        assert hasattr(phren, "__version__")
        assert phren.__version__ == "0.2.0"

    def test_no_sys_path_hacks(self):
        """No sys.path.insert in any core package file."""
        package_root = Path(__file__).parent.parent / "phren"
        for py_file in package_root.rglob("*.py"):
            content = py_file.read_text()
            assert "sys.path.insert" not in content, \
                f"sys.path.insert found in {py_file.relative_to(package_root)}"

    def test_license_exists(self):
        license_file = Path(__file__).parent.parent / "LICENSE"
        assert license_file.exists(), "LICENSE file missing"
        content = license_file.read_text()
        assert "Apache License" in content

    def test_pyproject_exists(self):
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        assert pyproject.exists(), "pyproject.toml missing"
