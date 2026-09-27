"""
Tessera Governance Enforcement

Every contract field must actually bind at runtime.
This module provides enforcement checks that the terminal engine
calls before allowing connections and actions.

Fields enforced:
  - requests_per_minute, requests_per_hour, requests_per_day
  - max_concurrent_sessions
  - max_items_per_action
  - max_transaction_amount (per action)
  - max_daily_spend (rolling 24h accumulator)
  - expires_at (contract expiration)
  - user_consent_token (for actions requiring user delegation)

Each function returns (allowed: bool, reason: str).
"""
from datetime import datetime, timezone, timedelta
from typing import Optional
from tessera.contract.schema import (
    TesseraContract, AgentProfile, ResolvedPermissions, AuditLogEntry,
)


# ── Contract Expiration ──

def check_contract_expiration(contract: TesseraContract) -> tuple[bool, str]:
    """
    Check if the contract has expired.
    Called at connect time — expired contracts refuse all connections.
    """
    if not contract.expires_at:
        return True, ""

    try:
        expires = datetime.fromisoformat(contract.expires_at)
        # Make timezone-aware if naive
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now > expires:
            return False, f"Contract expired at {contract.expires_at}"
    except (ValueError, TypeError):
        # If we can't parse the date, allow (don't break on bad data)
        pass

    return True, ""


# ── Concurrent Sessions ──

def check_max_sessions(
    contract: TesseraContract,
    active_session_count: int,
) -> tuple[bool, str]:
    """
    Check if max concurrent sessions would be exceeded.
    Called at connect time.
    """
    max_sessions = contract.rate_limits.max_concurrent_sessions
    if max_sessions and active_session_count >= max_sessions:
        return False, (
            f"Max concurrent sessions ({max_sessions}) reached. "
            f"Disconnect an existing session first."
        )
    return True, ""


# ── Rate Limits (all three windows) ──

def check_rate_limits(
    contract: TesseraContract,
    audit_log: list[AuditLogEntry],
) -> tuple[bool, str]:
    """
    Check per-minute, per-hour, and per-day rate limits.
    Called before every action execution.
    """
    limits = contract.rate_limits
    now = datetime.now(timezone.utc)

    def count_recent(seconds: int) -> int:
        cutoff = now - timedelta(seconds=seconds)
        count = 0
        for entry in audit_log:
            try:
                ts = datetime.fromisoformat(entry.timestamp)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts > cutoff:
                    count += 1
            except (ValueError, TypeError):
                continue
        return count

    # Per-minute
    if limits.requests_per_minute:
        recent = count_recent(60)
        if recent >= limits.requests_per_minute:
            return False, (
                f"Rate limit exceeded: {limits.requests_per_minute} requests/minute "
                f"({recent} in last 60s)"
            )

    # Per-hour
    if limits.requests_per_hour:
        recent = count_recent(3600)
        if recent >= limits.requests_per_hour:
            return False, (
                f"Rate limit exceeded: {limits.requests_per_hour} requests/hour "
                f"({recent} in last hour)"
            )

    # Per-day
    if limits.requests_per_day:
        recent = count_recent(86400)
        if recent >= limits.requests_per_day:
            return False, (
                f"Rate limit exceeded: {limits.requests_per_day} requests/day "
                f"({recent} in last 24h)"
            )

    return True, ""


# ── Max Items Per Action ──

def check_max_items(
    contract: TesseraContract,
    params: dict,
) -> tuple[bool, str]:
    """
    Check max_items_per_action against quantity-type parameters.
    Called before action execution.
    """
    max_items = contract.rate_limits.max_items_per_action
    if not max_items:
        return True, ""

    # Check common quantity parameter names
    for key in ("quantity", "qty", "count", "amount", "items", "num_items"):
        if key in params:
            try:
                val = int(params[key])
                if val > max_items:
                    return False, (
                        f"Max items per action is {max_items}, "
                        f"but {key}={val} was requested"
                    )
            except (ValueError, TypeError):
                continue

    return True, ""


# ── Transaction Amount ──

def check_transaction_amount(
    permissions: ResolvedPermissions,
    params: dict,
) -> tuple[bool, str]:
    """
    Check if a transaction amount exceeds the per-transaction limit.
    Called before transaction-type actions.
    """
    max_amount = permissions.max_transaction_amount
    if not max_amount:
        return True, ""

    # Look for amount-like parameters
    for key in ("total", "amount", "price", "cost", "value", "subtotal"):
        if key in params:
            try:
                val = float(params[key])
                if val > max_amount:
                    return False, (
                        f"Transaction amount {val} exceeds limit of {max_amount}"
                    )
            except (ValueError, TypeError):
                continue

    return True, ""


# ── Daily Spend Limit ──

def check_daily_spend(
    agent: AgentProfile,
    contract_daily_limit: Optional[float],
    audit_log: list[AuditLogEntry],
    current_amount: float,
) -> tuple[bool, str]:
    """
    Check if the rolling 24h spend would be exceeded.
    The effective limit is the LOWER of the operator's claim and the contract's ceiling.
    """
    # Get operator's claimed limit
    operator_limit = None
    if agent.capabilities:
        operator_limit = agent.capabilities.max_daily_spend

    # Effective limit = min(operator, contract), either can be None
    limits = [v for v in (operator_limit, contract_daily_limit) if v is not None]
    daily_limit = min(limits) if limits else None

    if not daily_limit:
        return True, ""

    # Sum amounts from successful actions in last 24h
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    spent = 0.0

    for entry in audit_log:
        if entry.result != "success":
            continue
        try:
            ts = datetime.fromisoformat(entry.timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts <= cutoff:
                continue
        except (ValueError, TypeError):
            continue

        # Look for amount in params
        params = entry.parameters or {}
        for key in ("total", "amount", "price", "cost", "value"):
            if key in params:
                try:
                    spent += float(params[key])
                    break
                except (ValueError, TypeError):
                    continue

    if spent + current_amount > daily_limit:
        return False, (
            f"Daily spend limit: {daily_limit}. "
            f"Already spent: {spent:.2f}. "
            f"This transaction ({current_amount:.2f}) would exceed the limit."
        )

    return True, ""


# ── User Consent ──

def check_user_consent(
    action_id: str,
    contract: TesseraContract,
    agent: AgentProfile,
) -> tuple[bool, str]:
    """
    Check if actions requiring user delegation have a consent token.
    """
    # Check if this action requires user delegation
    requires_consent = action_id in contract.required_confirmations

    if requires_consent and not agent.delegated_by_user:
        if not agent.user_consent_token:
            return False, (
                f"Action '{action_id}' requires user delegation. "
                f"Provide a user_consent_token or set delegated_by_user=True."
            )

    return True, ""


# ── Aggregate Check ──

def enforce_action(
    contract: TesseraContract,
    agent: AgentProfile,
    permissions: ResolvedPermissions,
    audit_log: list[AuditLogEntry],
    action_id: str,
    params: dict,
) -> tuple[bool, str]:
    """
    Run ALL enforcement checks for an action.
    Returns (allowed, reason).
    Called by the engine before every action execution.
    """
    # Rate limits
    ok, reason = check_rate_limits(contract, audit_log)
    if not ok:
        return False, reason

    # Max items
    ok, reason = check_max_items(contract, params)
    if not ok:
        return False, reason

    # Transaction amount
    ok, reason = check_transaction_amount(permissions, params)
    if not ok:
        return False, reason

    # Daily spend
    current_amount = 0.0
    for key in ("total", "amount", "price", "cost", "value"):
        if key in params:
            try:
                current_amount = float(params[key])
                break
            except (ValueError, TypeError):
                continue
    ok, reason = check_daily_spend(
        agent, contract.rate_limits.max_daily_spend, audit_log, current_amount,
    )
    if not ok:
        return False, reason

    # User consent
    ok, reason = check_user_consent(action_id, contract, agent)
    if not ok:
        return False, reason

    return True, ""


def enforce_connect(
    contract: TesseraContract,
    active_session_count: int,
) -> tuple[bool, str]:
    """
    Run ALL enforcement checks at connect time.
    Returns (allowed, reason).
    """
    # Contract expiration
    ok, reason = check_contract_expiration(contract)
    if not ok:
        return False, reason

    # Max concurrent sessions
    ok, reason = check_max_sessions(contract, active_session_count)
    if not ok:
        return False, reason

    return True, ""
