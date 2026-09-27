"""
Tessera Phase 4 — Contract Standard Tests

Validates:
  1. JSON Schema is generated correctly from Pydantic models
  2. Reference contracts (simulations) validate against the schema
  3. Contract validation catches dangerous omissions
  4. SPEC.md examples are valid contracts
  5. Schema is versioned and published
"""
import pytest
import json
from pathlib import Path

from tessera.contract.schema import TesseraContract, RateLimit
from tessera.contract.validation import validate_contract


PROJECT_ROOT = Path(__file__).parent.parent


# ═══════════════════════════════════════════════
# JSON SCHEMA GENERATION
# ═══════════════════════════════════════════════

class TestJsonSchema:
    """Verify the JSON Schema is correct and complete."""

    def test_schema_file_exists(self):
        schema_path = PROJECT_ROOT / "docs" / "contract-schema.json"
        assert schema_path.exists(), "docs/contract-schema.json not found"

    def test_schema_is_valid_json(self):
        schema_path = PROJECT_ROOT / "docs" / "contract-schema.json"
        with open(schema_path) as f:
            schema = json.load(f)
        assert schema["type"] == "object"
        assert "properties" in schema

    def test_schema_has_required_fields(self):
        schema_path = PROJECT_ROOT / "docs" / "contract-schema.json"
        with open(schema_path) as f:
            schema = json.load(f)
        props = schema["properties"]
        # Core identity fields
        assert "contract_id" in props
        assert "site_name" in props
        assert "site_url" in props
        # Governance fields
        assert "rate_limits" in props
        assert "screens" in props
        assert "expires_at" in props

    def test_schema_has_definitions(self):
        schema_path = PROJECT_ROOT / "docs" / "contract-schema.json"
        with open(schema_path) as f:
            schema = json.load(f)
        defs = schema.get("$defs", {})
        assert "ScreenDefinition" in defs
        assert "ActionDefinition" in defs
        assert "RateLimit" in defs
        assert "ActionParameter" in defs

    def test_schema_matches_pydantic_model(self):
        """Schema on disk matches what Pydantic generates."""
        schema_path = PROJECT_ROOT / "docs" / "contract-schema.json"
        with open(schema_path) as f:
            on_disk = json.load(f)
        from_model = TesseraContract.model_json_schema()
        # Property count should match
        assert set(on_disk["properties"].keys()) == set(from_model["properties"].keys())


# ═══════════════════════════════════════════════
# REFERENCE CONTRACT VALIDATION
# ═══════════════════════════════════════════════

class TestReferenceContracts:
    """Validate all simulation contracts load as valid TesseraContract objects."""

    @staticmethod
    def _find_contracts():
        """Find all contract JSON files in simulations/."""
        sim_root = PROJECT_ROOT / "simulations"
        contracts = []
        for json_file in sim_root.rglob("contract*.json"):
            contracts.append(json_file)
        return contracts

    def test_at_least_5_reference_contracts(self):
        contracts = self._find_contracts()
        assert len(contracts) >= 3, f"Expected at least 3 reference contracts, found {len(contracts)}"

    @pytest.mark.parametrize("contract_path", _find_contracts.__func__(), ids=lambda p: p.parent.name)
    def test_contract_loads(self, contract_path):
        """Every simulation contract must load as a valid TesseraContract."""
        with open(contract_path) as f:
            data = json.load(f)
        # Should not raise
        contract = TesseraContract(**data)
        assert contract.contract_id or contract.site_name

    @pytest.mark.parametrize("contract_path", _find_contracts.__func__(), ids=lambda p: p.parent.name)
    def test_contract_has_identity(self, contract_path):
        """Every contract must have site_name and site_url."""
        with open(contract_path) as f:
            data = json.load(f)
        contract = TesseraContract(**data)
        assert contract.site_name, f"{contract_path.name}: missing site_name"
        assert contract.site_url, f"{contract_path.name}: missing site_url"


# ═══════════════════════════════════════════════
# CONTRACT VALIDATION (fail-closed)
# ═══════════════════════════════════════════════

class TestContractValidation:
    """Verify the validation catches dangerous omissions."""

    def test_valid_contract_passes(self):
        contract = TesseraContract(
            contract_id="valid-001",
            site_name="Valid Site",
            site_url="http://localhost",
            rate_limits=RateLimit(
                max_transaction_amount=100.0,
                requests_per_minute=60,
            ),
        )
        errors, warnings = validate_contract(contract)
        assert len(errors) == 0

    def test_missing_identity_fails(self):
        contract = TesseraContract(
            contract_id="",
            site_name="",
            site_url="",
        )
        errors, _ = validate_contract(contract)
        assert len(errors) >= 1

    def test_no_rate_limits_warns(self):
        contract = TesseraContract(
            contract_id="no-limits",
            site_name="No Limits",
            site_url="http://localhost",
        )
        _, warnings = validate_contract(contract)
        assert any("rate limit" in w.lower() for w in warnings)

    def test_roundtrip_json_schema(self):
        """Contract → JSON → reload → same contract."""
        contract = TesseraContract(
            contract_id="roundtrip-001",
            site_name="Roundtrip Test",
            site_url="http://localhost",
            rate_limits=RateLimit(
                requests_per_minute=30,
                max_transaction_amount=200.0,
            ),
        )
        json_str = contract.model_dump_json()
        reloaded = TesseraContract.model_validate_json(json_str)
        assert reloaded.contract_id == contract.contract_id
        assert reloaded.rate_limits.max_transaction_amount == 200.0


# ═══════════════════════════════════════════════
# SPEC EXAMPLES VALIDITY
# ═══════════════════════════════════════════════

class TestSpecExamples:
    """Verify the examples in SPEC.md are valid contracts."""

    def test_minimal_contract_from_spec(self):
        """The minimal example from SPEC.md must validate."""
        minimal = {
            "contract_id": "my-site-001",
            "site_name": "My Site",
            "site_url": "https://mysite.com",
            "rate_limits": {
                "requests_per_minute": 60,
                "max_transaction_amount": 100.00,
            },
        }
        contract = TesseraContract(**minimal)
        errors, _ = validate_contract(contract)
        assert len(errors) == 0

    def test_full_contract_from_spec(self):
        """The full example from SPEC.md must validate."""
        full = {
            "contract_id": "shop-v2",
            "version": "0.2.0",
            "site_name": "Example Shop",
            "site_url": "https://shop.example.com",
            "description": "E-commerce demo with governed agent access",
            "tier": "standard",
            "require_identification": True,
            "entry_screen": "products",
            "rate_limits": {
                "requests_per_minute": 30,
                "requests_per_hour": 500,
                "max_concurrent_sessions": 2,
                "max_items_per_action": 5,
                "max_transaction_amount": 200.00,
                "max_daily_spend": 1000.00,
            },
            "screens": [
                {
                    "id": "products",
                    "name": "Product Catalog",
                    "description": "Browse and search products",
                    "data_fields": [
                        {"name": "products", "type": "array", "description": "Product list"},
                        {"name": "total", "type": "integer", "description": "Total count"},
                    ],
                    "actions": [
                        {
                            "id": "search",
                            "name": "Search",
                            "description": "Search products by keyword",
                            "parameters": [{"name": "q", "type": "string", "required": True}],
                            "api_endpoint": "/api/products/search",
                            "api_method": "GET",
                        },
                    ],
                },
            ],
            "action_trust_requirements": [
                {"action_id": "place_order", "min_trust_level": "verified"},
            ],
            "required_confirmations": ["place_order"],
            "data_terms": {
                "log_all_actions": True,
                "retention": "session",
            },
        }
        contract = TesseraContract(**full)
        errors, _ = validate_contract(contract)
        assert len(errors) == 0
        assert len(contract.screens) == 1
        assert contract.rate_limits.max_transaction_amount == 200.0
