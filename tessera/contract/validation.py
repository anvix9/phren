"""
Tessera Contract Validation

Validates a contract for dangerous omissions before it goes live.
A contract that permits transactions but omits spend limits is fail-open —
this validator catches that and similar policy gaps.

Usage:
    from tessera.contract.validation import validate_contract
    errors, warnings = validate_contract(contract)
    if errors:
        raise ValueError(f"Invalid contract: {errors}")
"""
from typing import Optional
from tessera.contract.schema import TesseraContract, AgentTrust


def validate_contract(contract: TesseraContract) -> tuple[list[str], list[str]]:
    """
    Validate a contract for dangerous omissions.

    Returns:
        (errors, warnings) — errors are blockers, warnings are advisory.
    """
    errors = []
    warnings = []

    # ── Spend limits ──
    # If any action can be reached by verified/super_agent AND requires no
    # confirmation, the contract should have spend limits set.
    has_transactable_actions = False
    for req in contract.action_trust_requirements:
        if req.min_trust_level in (AgentTrust.VERIFIED, AgentTrust.SUPER_AGENT):
            has_transactable_actions = True

    # Also: if default trust allows verified+ agents
    if contract.default_trust_for_actions in (AgentTrust.ANONYMOUS, AgentTrust.IDENTIFIED):
        # Low default = most actions accessible to low-trust agents
        # Check if any actions exist that could be transactional
        for screen in contract.screens:
            for action in screen.actions:
                if action.api_method in ("POST", "PUT", "PATCH", "DELETE"):
                    has_transactable_actions = True

    if has_transactable_actions:
        if contract.rate_limits.max_transaction_amount is None:
            errors.append(
                "Contract has transactable actions but no max_transaction_amount. "
                "Set rate_limits.max_transaction_amount to a dollar ceiling, "
                "or no operator-claimed limit will be bounded."
            )
        if contract.rate_limits.max_daily_spend is None:
            warnings.append(
                "Contract has transactable actions but no max_daily_spend. "
                "Consider setting rate_limits.max_daily_spend to cap cumulative spending."
            )

    # ── Rate limits ──
    if (contract.rate_limits.requests_per_minute is None
            and contract.rate_limits.requests_per_hour is None
            and contract.rate_limits.requests_per_day is None):
        warnings.append(
            "No rate limits configured. Consider setting at least requests_per_minute."
        )

    # ── Contract identity ──
    if not contract.contract_id:
        errors.append("Contract must have a contract_id.")

    if not contract.site_name:
        errors.append("Contract must have a site_name.")

    if not contract.site_url:
        errors.append("Contract must have a site_url.")

    # ── Screens ──
    if not contract.screens:
        warnings.append("Contract has no screens defined.")

    if contract.screens and not contract.entry_screen:
        warnings.append(
            "Contract has screens but no entry_screen. "
            "The first screen will be used by default."
        )

    return errors, warnings
