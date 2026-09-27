# Tessera Contract Specification

**Version:** 0.2.0
**Status:** Draft
**Schema:** [docs/contract-schema.json](docs/contract-schema.json)

## Overview

A Tessera Contract defines what an AI agent is permitted to do on a website, and under what constraints. It is a machine-readable JSON document that the terminal enforces at runtime.

The contract is authored by the website owner. It describes the site's screens (what the agent can see), actions (what the agent can do), trust tiers (who can do what), and limits (rate limits, spending caps, session constraints).

## Positioning

> Stripe ACP and Google/Shopify UCP define how an agent transacts. Tessera defines what an agent is permitted to do on your site, and proves what it did.

Tessera contracts are complementary to transaction protocols, not competitive. A contract governs the *scope* of permitted behavior; ACP/UCP handle the payment rail.

## Contract Structure

A contract is a JSON object with the following top-level fields:

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `contract_id` | string | Unique identifier for this contract |
| `site_name` | string | Human-readable site name |
| `site_url` | string | URL of the website this contract governs |

### Optional Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | string | `"0.1.0"` | Contract format version |
| `description` | string | `""` | Human-readable description |
| `tier` | enum | `"standard"` | Contract tier: `free`, `standard`, `premium`, `enterprise` |
| `expires_at` | string (ISO 8601) | `null` | When this contract expires. Expired contracts refuse all connections |
| `require_identification` | bool | `false` | If true, anonymous agents are rejected |
| `entry_screen` | string | `null` | The screen shown after connecting. Falls back to first screen |
| `screens` | array | `[]` | Screen definitions (see below) |
| `rate_limits` | object | `{}` | Rate limit configuration (see below) |
| `data_terms` | object | `{}` | Data handling terms (see below) |
| `action_trust_requirements` | array | `[]` | Per-action trust tier requirements |
| `required_confirmations` | array | `[]` | Action IDs that require user delegation |
| `default_trust_for_actions` | enum | `"anonymous"` | Default minimum trust for any action |

## Screens

A screen represents a state the agent can be in. Each screen has data fields (what the agent sees) and actions (what the agent can do).

```json
{
  "id": "product_detail",
  "name": "Product Detail",
  "description": "View a single product with price and availability",
  "data_fields": [
    {"name": "product", "type": "object", "description": "Product details"},
    {"name": "price", "type": "number", "description": "Price in USD"},
    {"name": "in_stock", "type": "boolean", "description": "Availability"}
  ],
  "actions": [
    {
      "id": "add_to_cart",
      "name": "Add to Cart",
      "description": "Add this product to the shopping cart",
      "permission": "allowed",
      "parameters": [
        {"name": "quantity", "type": "integer", "required": true, "min_value": 1, "max_value": 10}
      ],
      "api_endpoint": "/api/cart/items",
      "api_method": "POST",
      "transitions_to": "cart"
    }
  ]
}
```

### Screen Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique screen identifier |
| `name` | string | yes | Display name |
| `description` | string | yes | What this screen shows |
| `data_fields` | array | no | Fields visible on this screen |
| `actions` | array | no | Actions available on this screen |

### Action Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique action identifier |
| `name` | string | yes | Display name |
| `description` | string | yes | What this action does |
| `permission` | enum | no | `allowed`, `confirmation_required`, `denied`. Default: `allowed` |
| `parameters` | array | no | Parameters the action accepts |
| `preconditions` | array | no | Conditions that must be true before execution |
| `transitions_to` | string | no | Screen to navigate to after execution |
| `api_endpoint` | string | no | The real API endpoint this action proxies to |
| `api_method` | string | no | HTTP method: `GET`, `POST`, `PUT`, `PATCH`, `DELETE` |

### Parameter Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Parameter name |
| `type` | string | yes | Type: `string`, `integer`, `number`, `boolean`, `array`, `object` |
| `required` | bool | no | Whether this parameter is required. Default: `false` |
| `description` | string | no | Parameter description |
| `enum_values` | array | no | Allowed values (for enum-type parameters) |
| `min_value` | number | no | Minimum value (for numeric parameters) |
| `max_value` | number | no | Maximum value (for numeric parameters) |
| `default` | any | no | Default value if not provided |

## Rate Limits

```json
{
  "rate_limits": {
    "requests_per_minute": 60,
    "requests_per_hour": 1000,
    "requests_per_day": 10000,
    "max_concurrent_sessions": 3,
    "max_items_per_action": 10,
    "max_transaction_amount": 500.00,
    "max_daily_spend": 2000.00
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `requests_per_minute` | integer | Max requests per rolling 60-second window |
| `requests_per_hour` | integer | Max requests per rolling 1-hour window |
| `requests_per_day` | integer | Max requests per rolling 24-hour window |
| `max_concurrent_sessions` | integer | Max simultaneous agent sessions. Default: `1` |
| `max_items_per_action` | integer | Max quantity-type parameter value per action |
| `max_transaction_amount` | number | Per-transaction dollar ceiling. **Required if transactions are permitted.** |
| `max_daily_spend` | number | Rolling 24-hour cumulative spend ceiling |

### Contract Validation Rule

A contract that has transactable actions (any `POST`, `PUT`, `PATCH`, `DELETE` action reachable by verified/super_agent trust) but omits `max_transaction_amount` fails validation. This prevents fail-open contracts where the site owner forgets a spend limit.

## Trust Tiers

Agents operate at one of four trust tiers, derived from their credential:

| Tier | Level | Typical permissions |
|------|-------|---------------------|
| `anonymous` | 0 | Read-only: browse, search, view |
| `identified` | 1 | Interactive: add to cart, follow, comment |
| `verified` | 2 | Transactional: purchase, book, submit (with confirmation) |
| `super_agent` | 3 | Autonomous: transact without per-action confirmation |

Trust is never self-declared. It is derived from an Ed25519-signed credential verified against an operator registry. The effective tier is the **minimum** of the credential's claimed tier and the operator's registered maximum.

### Per-Action Trust Requirements

```json
{
  "action_trust_requirements": [
    {"action_id": "place_order", "min_trust_level": "verified"},
    {"action_id": "admin_reset", "min_trust_level": "super_agent"}
  ]
}
```

## Data Terms

```json
{
  "data_terms": {
    "log_all_actions": true,
    "log_data_access": true,
    "retention": "session",
    "allowed_use": ["task_completion"],
    "prohibited_use": ["training", "resale"]
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `log_all_actions` | bool | Whether all actions are logged in the audit trail |
| `log_data_access` | bool | Whether data field reads are logged |
| `retention` | enum | `none`, `session`, `limited`, `permanent` |
| `allowed_use` | array | Permitted uses of data retrieved through the terminal |
| `prohibited_use` | array | Explicitly prohibited uses |

## Minimal Valid Contract

```json
{
  "contract_id": "my-site-001",
  "site_name": "My Site",
  "site_url": "https://mysite.com",
  "rate_limits": {
    "requests_per_minute": 60,
    "max_transaction_amount": 100.00
  }
}
```

## Full Example

```json
{
  "contract_id": "shop-v2",
  "version": "0.2.0",
  "site_name": "Example Shop",
  "site_url": "https://shop.example.com",
  "description": "E-commerce demo with governed agent access",
  "tier": "standard",
  "require_identification": true,
  "entry_screen": "products",
  "rate_limits": {
    "requests_per_minute": 30,
    "requests_per_hour": 500,
    "max_concurrent_sessions": 2,
    "max_items_per_action": 5,
    "max_transaction_amount": 200.00,
    "max_daily_spend": 1000.00
  },
  "screens": [
    {
      "id": "products",
      "name": "Product Catalog",
      "description": "Browse and search products",
      "data_fields": [
        {"name": "products", "type": "array", "description": "Product list"},
        {"name": "total", "type": "integer", "description": "Total count"}
      ],
      "actions": [
        {
          "id": "search",
          "name": "Search",
          "description": "Search products by keyword",
          "parameters": [{"name": "q", "type": "string", "required": true}],
          "api_endpoint": "/api/products/search",
          "api_method": "GET"
        },
        {
          "id": "view_product",
          "name": "View Product",
          "description": "View product details",
          "parameters": [{"name": "id", "type": "string", "required": true}],
          "transitions_to": "product_detail",
          "api_endpoint": "/api/products/{id}",
          "api_method": "GET"
        }
      ]
    }
  ],
  "action_trust_requirements": [
    {"action_id": "place_order", "min_trust_level": "verified"}
  ],
  "required_confirmations": ["place_order"],
  "data_terms": {
    "log_all_actions": true,
    "retention": "session"
  }
}
```

## Versioning

The contract format uses semantic versioning:

- **Major** (1.0 → 2.0): Breaking changes — existing contracts may not validate
- **Minor** (0.2 → 0.3): New fields added — existing contracts remain valid
- **Patch** (0.2.0 → 0.2.1): Clarifications only — no schema changes

The current version is **0.2.0** (draft).

### Deprecation Policy

When a field is deprecated:
1. It is marked `deprecated` in the JSON Schema
2. It continues to function for at least one minor version
3. Validation emits a warning, not an error
4. The next major version removes it

## Validation

Validate a contract against the schema:

```python
from tessera.contract.schema import TesseraContract
from tessera.contract.validation import validate_contract

contract = TesseraContract(**contract_data)
errors, warnings = validate_contract(contract)
if errors:
    raise ValueError(f"Invalid contract: {errors}")
```

Or validate with any JSON Schema validator:

```bash
# Using jsonschema CLI
pip install jsonschema
jsonschema -i my-contract.json docs/contract-schema.json
```
