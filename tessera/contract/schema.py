"""
Tessera Contract Schema

The contract is the machine-readable agreement between a website owner and AI agents.
It defines what agents can do, what they cannot do, rate limits, data handling terms,
and identification requirements.

The contract is presented to the agent BEFORE any interaction begins.
The agent's planning layer reads the contract and prunes its action space accordingly —
prohibited actions literally don't exist in the agent's environment.
"""
from pydantic import BaseModel, Field, model_validator
from typing import Optional
from enum import Enum
from datetime import datetime, timezone


# ── Enums ──

class ContractTier(str, Enum):
    """Access tiers that website owners can define."""
    PUBLIC = "public"           # Free, read-only browsing
    STANDARD = "standard"       # Free, can transact with limits
    PREMIUM = "premium"         # Paid, higher limits
    PARTNER = "partner"         # Verified partners, full access


class ActionPermission(str, Enum):
    """Permission level for an action."""
    ALLOWED = "allowed"
    REQUIRES_CONFIRMATION = "requires_confirmation"  # Agent must ask user before executing
    PROHIBITED = "prohibited"


class DataRetention(str, Enum):
    """How long agent may retain data from responses."""
    NONE = "none"               # Must not store
    SESSION = "session"         # Current session only
    HOURS_24 = "24h"
    DAYS_7 = "7d"
    DAYS_30 = "30d"
    UNLIMITED = "unlimited"


# ── Action Definition ──

class ActionParameter(BaseModel):
    """A single parameter for an action."""
    name: str
    type: str                                     # string, int, float, bool, enum
    required: bool = True
    description: str = ""
    enum_values: Optional[list[str]] = None       # For enum type
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    default: Optional[str] = None


class ActionRole(str, Enum):
    """Role of an action in the navigation flow."""
    PRIMARY = "primary"           # The main forward step (add_to_cart, checkout, submit)
    SECONDARY = "secondary"       # Useful but not progress (update_qty, apply_coupon, filter)
    NAVIGATION = "navigation"     # Go back or lateral move (back_to_search, view_profile)


class ActionDefinition(BaseModel):
    """
    A single action available in the Tessera terminal.
    Maps to one thing the agent can do.
    """
    id: str                                       # Unique action identifier, e.g. "add_to_cart"
    name: str                                     # Human-readable name
    description: str                              # What this action does
    role: ActionRole = ActionRole.SECONDARY       # Flow role: primary, secondary, navigation
    permission: ActionPermission = ActionPermission.ALLOWED
    parameters: list[ActionParameter] = []
    preconditions: list[str] = []                 # Conditions that must be true, e.g. "stock_status != out_of_stock"
    transitions_to: Optional[str] = None          # Screen this action navigates to
    api_endpoint: Optional[str] = None            # Underlying API call (for translation proxy)
    api_method: Optional[str] = None              # HTTP method


# ── Screen Definition ──

class DataField(BaseModel):
    """A single data field visible on a screen."""
    name: str
    type: str                                     # string, float, int, bool, enum, list
    description: str = ""
    example: Optional[str] = None


class ScreenDefinition(BaseModel):
    """
    A single screen in the Tessera terminal.
    Represents one "state" of the website from the agent's perspective.
    """
    id: str                                       # Unique screen identifier, e.g. "product_detail"
    name: str                                     # Human-readable name
    description: str
    flow_hint: Optional[str] = None               # Plain-text hint: "To buy: add_to_cart → checkout"
    parameters: list[str] = []                    # Required params to reach this screen, e.g. ["product_id"]
    data_fields: list[DataField] = []             # What data is visible
    actions: list[ActionDefinition] = []          # What the agent can do here
    parent: Optional[str] = None                  # Screen to go "back" to


# ── Rate Limits ──

class RateLimit(BaseModel):
    """Rate limiting configuration."""
    requests_per_minute: Optional[int] = None
    requests_per_hour: Optional[int] = None
    requests_per_day: Optional[int] = None
    max_concurrent_sessions: int = 1
    max_items_per_action: Optional[int] = None    # e.g., max 5 items per add_to_cart
    max_transaction_amount: Optional[float] = None  # Site owner's per-transaction ceiling (USD)
    max_daily_spend: Optional[float] = None         # Site owner's daily spend ceiling (USD)


# ── Data Handling Terms ──

class DataTerms(BaseModel):
    """Rules for how the agent may handle data received from the terminal."""
    default_retention: DataRetention = DataRetention.SESSION
    field_overrides: dict[str, DataRetention] = {}  # Per-field overrides, e.g. {"price": "none"}
    allow_aggregation: bool = False                  # Can agent combine data across sessions?
    allow_storage: bool = False                      # Can agent persist data to disk?
    require_attribution: bool = False                # Must agent cite source?
    attribution_text: Optional[str] = None


# ── Agent Trust Tiers ──

class AgentTrust(str, Enum):
    """
    Trust level determines what the agent can do autonomously.
    The website owner defines which actions require which trust level.
    The agent passes its profile; the terminal resolves the effective permissions.
    """
    ANONYMOUS = "anonymous"         # No identity provided — read-only browsing
    IDENTIFIED = "identified"       # Agent identified itself — can interact, but transactions need confirmation
    VERIFIED = "verified"           # Agent operator verified (e.g. signed API key) — can transact with limits
    SUPER_AGENT = "super_agent"     # Fully trusted — can transact autonomously, no confirmation needed


# ── Agent Profile ──

class AgentCapabilities(BaseModel):
    """What this agent is authorized to do, declared by its operator."""
    can_transact: bool = False               # Can the agent spend money?
    max_transaction_amount: Optional[float] = None  # Per-transaction limit in USD
    max_daily_spend: Optional[float] = None  # Daily spending cap
    can_create_accounts: bool = False
    can_modify_data: bool = False             # Can it edit profiles, reviews, etc.?
    can_access_pii: bool = False              # Can it handle personal data?


class AgentProfile(BaseModel):
    """
    Full agent profile passed when connecting to a Tessera terminal.
    The terminal evaluates the profile against the contract to determine
    effective permissions. A super_agent with can_transact=True can place
    orders autonomously; an identified agent cannot.
    """
    # Identity
    provider: str                            # e.g. "anthropic", "openai", "custom"
    agent_name: str                          # e.g. "claude-shopping-agent"
    agent_id: Optional[str] = None           # Unique agent instance ID
    version: Optional[str] = None
    contact: Optional[str] = None            # Contact for the agent operator

    # Trust & purpose
    trust_level: AgentTrust = AgentTrust.ANONYMOUS
    purpose: str = ""                        # e.g. "product_comparison", "purchase", "research"
    operator: Optional[str] = None           # Organization operating the agent
    credentials: Optional[str] = None        # Signed token / API key for verification

    # Capabilities declared by operator
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)

    # User delegation (if agent acts on behalf of a human)
    delegated_by_user: bool = False          # Is a human explicitly behind this session?
    user_consent_token: Optional[str] = None # Proof of user consent for transactions


# ── Action Trust Requirements ──

class ActionTrustRequirement(BaseModel):
    """Per-action trust requirements set by the website owner."""
    action_id: str
    min_trust_level: AgentTrust = AgentTrust.IDENTIFIED
    requires_user_delegation: bool = False   # Must a human be behind this?
    max_amount: Optional[float] = None       # Transaction cap for this action


# ── The Contract ──

class TesseraContract(BaseModel):
    """
    The complete contract for a Tessera terminal.

    This is presented to agents before any interaction.
    The agent must accept the contract to proceed.
    All interactions are logged against the accepted contract.
    """
    # Metadata
    contract_id: str
    version: str = "0.1.0"
    site_name: str
    site_url: str
    description: str = ""
    tier: ContractTier = ContractTier.STANDARD
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: Optional[str] = None

    # The terminal structure
    screens: list[ScreenDefinition] = []
    entry_screen: str = ""                        # Which screen the agent starts on

    # Governance
    rate_limits: RateLimit = Field(default_factory=RateLimit)
    data_terms: DataTerms = Field(default_factory=DataTerms)
    prohibited_actions: list[str] = []            # Explicit list of forbidden action IDs
    required_confirmations: list[str] = []        # Actions requiring human confirmation

    # Trust-based access control
    action_trust_requirements: list[ActionTrustRequirement] = []  # Per-action trust levels
    default_trust_for_actions: AgentTrust = AgentTrust.IDENTIFIED  # Default min trust for any action
    trust_overrides: dict[str, AgentTrust] = {}   # Quick overrides: {"place_order": "super_agent"}

    # Agent requirements
    require_identification: bool = True
    allowed_purposes: list[str] = []              # Empty = any purpose allowed
    blocked_providers: list[str] = []             # Providers that are denied access

    # Audit
    log_all_actions: bool = True
    log_data_access: bool = True

    # Validation: deny-by-default for spend paths
    @model_validator(mode="after")
    def validate_spend_limits(self):
        """
        If the contract allows transaction-level trust (verified or super_agent),
        it MUST specify spend limits. A contract that permits transactions but
        omits max_transaction_amount is fail-open — the omission is dangerous,
        not harmless. This makes it impossible rather than just bad practice.
        """
        allows_transactions = False

        # Check if any trust override or default allows verified/super_agent
        for req in self.action_trust_requirements:
            if req.min_trust_level in (AgentTrust.VERIFIED, AgentTrust.SUPER_AGENT):
                allows_transactions = True
                break
        for action_id, tier in self.trust_overrides.items():
            if tier in (AgentTrust.VERIFIED, AgentTrust.SUPER_AGENT):
                allows_transactions = True
                break

        if allows_transactions:
            if self.rate_limits.max_transaction_amount is None:
                import warnings
                warnings.warn(
                    f"Contract '{self.contract_id}' allows verified/super_agent trust "
                    f"but has no max_transaction_amount. Set rate_limits.max_transaction_amount "
                    f"to establish a per-transaction ceiling.",
                    UserWarning,
                    stacklevel=2,
                )

        return self


# ── Contract Acceptance ──

class ResolvedPermissions(BaseModel):
    """
    The effective permissions for this agent session,
    computed from the contract + agent profile.
    """
    allowed_actions: list[str] = []               # Actions this agent can perform
    confirmation_required: list[str] = []          # Actions that need human confirmation
    denied_actions: list[str] = []                 # Actions denied at this trust level
    max_transaction_amount: Optional[float] = None
    can_transact_autonomously: bool = False


class ContractAcceptance(BaseModel):
    """Record of an agent accepting a contract."""
    contract_id: str
    agent: AgentProfile
    accepted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str = ""
    resolved_permissions: Optional[ResolvedPermissions] = None


# ── Audit Log Entry ──

class AuditLogEntry(BaseModel):
    """A single logged action in a Tessera session."""
    session_id: str
    contract_id: str
    agent_provider: str
    agent_name: str
    agent_trust: AgentTrust = AgentTrust.ANONYMOUS
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    screen: str
    action: str
    parameters: dict = {}
    result: str = ""                              # "success", "denied", "confirmation_required", "error"
    denied_reason: Optional[str] = None           # Why action was denied
    error_message: Optional[str] = None
