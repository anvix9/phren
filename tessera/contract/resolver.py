"""
Tessera Permission Resolver

Takes a contract and an agent profile, and computes the effective permissions
for that agent session. This is the core governance logic.

The key rule:
  - anonymous: can only read (search, view)
  - identified: can interact (add to cart, modify cart) but transactions need confirmation
  - verified: can transact with limits
  - super_agent: can transact autonomously, no confirmation needed
"""
from .schema import (
    TesseraContract, AgentProfile, AgentTrust,
    ActionPermission, ActionTrustRequirement,
    ResolvedPermissions,
)


# Actions that are inherently read-only (never need trust above anonymous)
READ_ONLY_ACTIONS = {
    "search", "view_product", "view_cart", "check_order_status",
    "back_to_search", "continue_shopping", "back_to_cart", "new_shopping_session",
}


def resolve_permissions(contract: TesseraContract, agent: AgentProfile) -> ResolvedPermissions:
    """
    Compute the effective permissions for an agent given a contract.

    Returns a ResolvedPermissions object listing exactly which actions
    the agent can perform, which need confirmation, and which are denied.
    """
    allowed = []
    confirmation_required = []
    denied = []

    # Build a trust requirement lookup from the contract
    trust_map: dict[str, AgentTrust] = {}

    # First, apply explicit trust overrides
    for action_id, trust_level in contract.trust_overrides.items():
        trust_map[action_id] = AgentTrust(trust_level)

    # Then apply detailed requirements (these take precedence)
    for req in contract.action_trust_requirements:
        trust_map[req.action_id] = req.min_trust_level

    # Process every action across all screens
    for screen in contract.screens:
        for action in screen.actions:
            action_id = action.id

            # Check if action is globally prohibited
            if action_id in contract.prohibited_actions:
                denied.append(action_id)
                continue

            # Check if action is explicitly set to prohibited in the contract
            if action.permission == ActionPermission.PROHIBITED:
                denied.append(action_id)
                continue

            # Read-only actions are always allowed for any trust level
            if action_id in READ_ONLY_ACTIONS:
                allowed.append(action_id)
                continue

            # Determine the minimum trust required for this action
            min_trust = trust_map.get(action_id, contract.default_trust_for_actions)

            # Trust level hierarchy: anonymous < identified < verified < super_agent
            trust_hierarchy = {
                AgentTrust.ANONYMOUS: 0,
                AgentTrust.IDENTIFIED: 1,
                AgentTrust.VERIFIED: 2,
                AgentTrust.SUPER_AGENT: 3,
            }

            agent_level = trust_hierarchy.get(agent.trust_level, 0)
            required_level = trust_hierarchy.get(min_trust, 1)

            if agent_level < required_level:
                denied.append(action_id)
                continue

            # Check if action requires user delegation
            req = next((r for r in contract.action_trust_requirements if r.action_id == action_id), None)
            if req and req.requires_user_delegation and not agent.delegated_by_user:
                denied.append(action_id)
                continue

            # Determine if confirmation is needed
            needs_confirmation = False

            # Contract says this action always needs confirmation
            if action_id in contract.required_confirmations:
                needs_confirmation = True

            # Action definition says confirmation required
            if action.permission == ActionPermission.REQUIRES_CONFIRMATION:
                needs_confirmation = True

            # SUPER_AGENT with can_transact bypasses confirmation
            if (agent.trust_level == AgentTrust.SUPER_AGENT
                    and agent.capabilities.can_transact):
                needs_confirmation = False

            # VERIFIED agents still need confirmation for transactions
            # unless they have explicit user delegation
            if (agent.trust_level == AgentTrust.VERIFIED
                    and needs_confirmation
                    and agent.delegated_by_user):
                needs_confirmation = False

            if needs_confirmation:
                confirmation_required.append(action_id)
            else:
                allowed.append(action_id)

    # Compute transaction limits — contract ceiling is the bound, not operator claim
    def _min_opt(a, b):
        """Return the minimum of two optional values (None means no limit)."""
        vals = [v for v in (a, b) if v is not None]
        return min(vals) if vals else None

    max_amount = None
    can_transact = False

    if agent.trust_level in (AgentTrust.SUPER_AGENT, AgentTrust.VERIFIED):
        if agent.capabilities.can_transact:
            can_transact = True
            # The effective limit is the LOWER of the operator's claim and the contract's ceiling
            max_amount = _min_opt(
                agent.capabilities.max_transaction_amount,
                contract.rate_limits.max_transaction_amount,
            )

    return ResolvedPermissions(
        allowed_actions=allowed,
        confirmation_required=confirmation_required,
        denied_actions=denied,
        max_transaction_amount=max_amount,
        can_transact_autonomously=can_transact and agent.trust_level == AgentTrust.SUPER_AGENT,
    )


def print_permissions(perms: ResolvedPermissions, agent: AgentProfile):
    """Pretty-print resolved permissions."""
    print(f"=== Permissions for {agent.agent_name} ({agent.trust_level.value}) ===")
    print(f"  Provider: {agent.provider}")
    print(f"  Purpose: {agent.purpose}")
    print(f"  Can transact: {perms.can_transact_autonomously}")
    if perms.max_transaction_amount:
        print(f"  Max transaction: ${perms.max_transaction_amount}")
    print()
    print(f"  ALLOWED ({len(perms.allowed_actions)}):")
    for a in perms.allowed_actions:
        print(f"    + {a}")
    print(f"  NEEDS CONFIRMATION ({len(perms.confirmation_required)}):")
    for a in perms.confirmation_required:
        print(f"    ? {a}")
    print(f"  DENIED ({len(perms.denied_actions)}):")
    for a in perms.denied_actions:
        print(f"    x {a}")


if __name__ == "__main__":
    from shopping_contract import build_shopping_contract
    from schema import AgentCapabilities

    contract = build_shopping_contract()

    print("=" * 60)
    print("TEST 1: Anonymous agent (no identity)")
    print("=" * 60)
    anon = AgentProfile(
        provider="unknown", agent_name="random-bot",
        trust_level=AgentTrust.ANONYMOUS, purpose="browsing",
    )
    perms = resolve_permissions(contract, anon)
    print_permissions(perms, anon)

    print()
    print("=" * 60)
    print("TEST 2: Identified agent (standard use)")
    print("=" * 60)
    identified = AgentProfile(
        provider="anthropic", agent_name="claude-shopper",
        trust_level=AgentTrust.IDENTIFIED, purpose="product_comparison",
    )
    perms = resolve_permissions(contract, identified)
    print_permissions(perms, identified)

    print()
    print("=" * 60)
    print("TEST 3: Super-agent with transaction rights")
    print("=" * 60)
    super_agent = AgentProfile(
        provider="anthropic", agent_name="claude-buyer-pro",
        trust_level=AgentTrust.SUPER_AGENT, purpose="purchase",
        operator="Acme Corp",
        capabilities=AgentCapabilities(
            can_transact=True,
            max_transaction_amount=500.00,
            max_daily_spend=2000.00,
        ),
    )
    perms = resolve_permissions(contract, super_agent)
    print_permissions(perms, super_agent)
