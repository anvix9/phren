"""
Tessera Phase 2 — Governance Enforcement Tests

Each test proves a specific contract field actually binds at runtime.
The build plan says: "an enforcement test suite per field, each asserting
the limit actually binds."
"""
import pytest
from datetime import datetime, timezone, timedelta

from tessera.contract.schema import (
    TesseraContract, RateLimit, DataTerms, AgentProfile,
    AgentTrust, AgentCapabilities, ResolvedPermissions, AuditLogEntry,
)
from tessera.contract.enforcement import (
    check_contract_expiration,
    check_max_sessions,
    check_rate_limits,
    check_max_items,
    check_transaction_amount,
    check_daily_spend,
    check_user_consent,
    enforce_connect,
    enforce_action,
)


def _make_contract(**overrides) -> TesseraContract:
    """Helper: create a contract with custom fields."""
    defaults = {
        "contract_id": "test-enforce",
        "site_name": "Enforcement Test",
        "site_url": "http://localhost",
        "require_identification": False,
    }
    defaults.update(overrides)
    return TesseraContract(**defaults)


def _make_log_entries(count: int, seconds_ago: int = 30) -> list[AuditLogEntry]:
    """Helper: create audit log entries within a time window."""
    now = datetime.now(timezone.utc)
    entries = []
    for i in range(count):
        ts = now - timedelta(seconds=seconds_ago - i)
        entries.append(AuditLogEntry(
            session_id="sess_test",
            contract_id="test-enforce",
            agent_provider="test",
            agent_name="test-agent",
            screen="test_screen",
            action=f"action_{i}",
            parameters={},
            result="success",
            timestamp=ts.isoformat(),
        ))
    return entries


# ═══════════════════════════════════════════════
# CONTRACT EXPIRATION
# ═══════════════════════════════════════════════

class TestContractExpiration:

    def test_no_expiry_is_valid(self):
        contract = _make_contract()
        ok, reason = check_contract_expiration(contract)
        assert ok is True

    def test_future_expiry_is_valid(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        contract = _make_contract(expires_at=future)
        ok, reason = check_contract_expiration(contract)
        assert ok is True

    def test_past_expiry_is_rejected(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        contract = _make_contract(expires_at=past)
        ok, reason = check_contract_expiration(contract)
        assert ok is False
        assert "expired" in reason.lower()


# ═══════════════════════════════════════════════
# MAX CONCURRENT SESSIONS
# ═══════════════════════════════════════════════

class TestMaxSessions:

    def test_under_limit_allowed(self):
        contract = _make_contract(rate_limits=RateLimit(max_concurrent_sessions=5))
        ok, reason = check_max_sessions(contract, active_session_count=3)
        assert ok is True

    def test_at_limit_rejected(self):
        contract = _make_contract(rate_limits=RateLimit(max_concurrent_sessions=2))
        ok, reason = check_max_sessions(contract, active_session_count=2)
        assert ok is False
        assert "concurrent" in reason.lower()

    def test_over_limit_rejected(self):
        contract = _make_contract(rate_limits=RateLimit(max_concurrent_sessions=1))
        ok, reason = check_max_sessions(contract, active_session_count=5)
        assert ok is False


# ═══════════════════════════════════════════════
# RATE LIMITS (all three windows)
# ═══════════════════════════════════════════════

class TestRateLimits:

    def test_per_minute_under_limit(self):
        contract = _make_contract(rate_limits=RateLimit(requests_per_minute=10))
        log = _make_log_entries(5, seconds_ago=30)
        ok, reason = check_rate_limits(contract, log)
        assert ok is True

    def test_per_minute_exceeded(self):
        contract = _make_contract(rate_limits=RateLimit(requests_per_minute=5))
        log = _make_log_entries(6, seconds_ago=30)
        ok, reason = check_rate_limits(contract, log)
        assert ok is False
        assert "minute" in reason.lower()

    def test_per_hour_exceeded(self):
        contract = _make_contract(rate_limits=RateLimit(requests_per_hour=10))
        log = _make_log_entries(11, seconds_ago=1800)  # within last hour
        ok, reason = check_rate_limits(contract, log)
        assert ok is False
        assert "hour" in reason.lower()

    def test_per_day_exceeded(self):
        contract = _make_contract(rate_limits=RateLimit(requests_per_day=20))
        log = _make_log_entries(21, seconds_ago=43200)  # within last 24h
        ok, reason = check_rate_limits(contract, log)
        assert ok is False
        assert "day" in reason.lower()

    def test_no_limits_always_passes(self):
        contract = _make_contract()
        log = _make_log_entries(1000, seconds_ago=10)
        ok, reason = check_rate_limits(contract, log)
        assert ok is True


# ═══════════════════════════════════════════════
# MAX ITEMS PER ACTION
# ═══════════════════════════════════════════════

class TestMaxItems:

    def test_under_limit_allowed(self):
        contract = _make_contract(rate_limits=RateLimit(max_items_per_action=10))
        ok, reason = check_max_items(contract, {"quantity": 5})
        assert ok is True

    def test_over_limit_rejected(self):
        contract = _make_contract(rate_limits=RateLimit(max_items_per_action=3))
        ok, reason = check_max_items(contract, {"quantity": 10})
        assert ok is False
        assert "max items" in reason.lower()

    def test_checks_qty_alias(self):
        contract = _make_contract(rate_limits=RateLimit(max_items_per_action=2))
        ok, reason = check_max_items(contract, {"qty": 5})
        assert ok is False

    def test_no_quantity_param_passes(self):
        contract = _make_contract(rate_limits=RateLimit(max_items_per_action=3))
        ok, reason = check_max_items(contract, {"product_id": "abc"})
        assert ok is True


# ═══════════════════════════════════════════════
# TRANSACTION AMOUNT
# ═══════════════════════════════════════════════

class TestTransactionAmount:

    def test_under_limit_allowed(self):
        perms = ResolvedPermissions(
            allowed_actions=[], confirmation_required=[], denied_actions=[],
            max_transaction_amount=500.0,
        )
        ok, reason = check_transaction_amount(perms, {"total": 100.0})
        assert ok is True

    def test_over_limit_rejected(self):
        perms = ResolvedPermissions(
            allowed_actions=[], confirmation_required=[], denied_actions=[],
            max_transaction_amount=200.0,
        )
        ok, reason = check_transaction_amount(perms, {"total": 500.0})
        assert ok is False
        assert "exceeds limit" in reason.lower()

    def test_checks_amount_alias(self):
        perms = ResolvedPermissions(
            allowed_actions=[], confirmation_required=[], denied_actions=[],
            max_transaction_amount=100.0,
        )
        ok, reason = check_transaction_amount(perms, {"amount": 150.0})
        assert ok is False

    def test_no_limit_always_passes(self):
        perms = ResolvedPermissions(
            allowed_actions=[], confirmation_required=[], denied_actions=[],
        )
        ok, reason = check_transaction_amount(perms, {"total": 999999.0})
        assert ok is True


# ═══════════════════════════════════════════════
# DAILY SPEND
# ═══════════════════════════════════════════════

class TestDailySpend:

    def test_under_limit_allowed(self):
        agent = AgentProfile(
            provider="test", agent_name="test",
            trust_level=AgentTrust.VERIFIED,
            capabilities=AgentCapabilities(max_daily_spend=1000.0),
        )
        ok, reason = check_daily_spend(agent, None, [], 100.0)
        assert ok is True

    def test_accumulated_over_limit_rejected(self):
        agent = AgentProfile(
            provider="test", agent_name="test",
            trust_level=AgentTrust.VERIFIED,
            capabilities=AgentCapabilities(max_daily_spend=500.0),
        )
        # Previous spend: 3 transactions of 150 each = 450
        now = datetime.now(timezone.utc)
        log = [
            AuditLogEntry(
                session_id="sess_test", contract_id="test", agent_provider="test",
                agent_name="test", screen="checkout",
                action="place_order", parameters={"total": 150.0},
                result="success",
                timestamp=(now - timedelta(hours=i)).isoformat(),
            )
            for i in range(3)
        ]
        # This 100 would bring total to 550 > 500
        ok, reason = check_daily_spend(agent, None, log, 100.0)
        assert ok is False
        assert "daily spend" in reason.lower()

    def test_old_transactions_not_counted(self):
        agent = AgentProfile(
            provider="test", agent_name="test",
            trust_level=AgentTrust.VERIFIED,
            capabilities=AgentCapabilities(max_daily_spend=500.0),
        )
        # Old transactions (>24h ago) should not count
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        log = [
            AuditLogEntry(
                session_id="sess_test", contract_id="test", agent_provider="test",
                agent_name="test", screen="checkout",
                action="place_order", parameters={"total": 400.0},
                result="success",
                timestamp=old.isoformat(),
            )
        ]
        ok, reason = check_daily_spend(agent, None, log, 100.0)
        assert ok is True  # old spend doesn't count


# ═══════════════════════════════════════════════
# USER CONSENT
# ═══════════════════════════════════════════════

class TestUserConsent:

    def test_no_consent_required_passes(self):
        contract = _make_contract()
        agent = AgentProfile(provider="test", agent_name="test")
        ok, reason = check_user_consent("search", contract, agent)
        assert ok is True

    def test_consent_required_without_token_rejected(self):
        contract = _make_contract(required_confirmations=["place_order"])
        agent = AgentProfile(
            provider="test", agent_name="test",
            delegated_by_user=False,
            user_consent_token=None,
        )
        ok, reason = check_user_consent("place_order", contract, agent)
        assert ok is False
        assert "user delegation" in reason.lower()

    def test_consent_required_with_delegation_passes(self):
        contract = _make_contract(required_confirmations=["place_order"])
        agent = AgentProfile(
            provider="test", agent_name="test",
            delegated_by_user=True,
        )
        ok, reason = check_user_consent("place_order", contract, agent)
        assert ok is True

    def test_consent_required_with_token_passes(self):
        contract = _make_contract(required_confirmations=["place_order"])
        agent = AgentProfile(
            provider="test", agent_name="test",
            user_consent_token="valid-consent-abc",
        )
        ok, reason = check_user_consent("place_order", contract, agent)
        assert ok is True


# ═══════════════════════════════════════════════
# AGGREGATE: enforce_connect
# ═══════════════════════════════════════════════

class TestEnforceConnect:

    def test_valid_contract_allows(self):
        contract = _make_contract()
        ok, reason = enforce_connect(contract, active_session_count=0)
        assert ok is True

    def test_expired_contract_blocks(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        contract = _make_contract(expires_at=past)
        ok, reason = enforce_connect(contract, active_session_count=0)
        assert ok is False
        assert "expired" in reason.lower()

    def test_max_sessions_blocks(self):
        contract = _make_contract(rate_limits=RateLimit(max_concurrent_sessions=1))
        ok, reason = enforce_connect(contract, active_session_count=1)
        assert ok is False
        assert "concurrent" in reason.lower()


# ═══════════════════════════════════════════════
# AGGREGATE: enforce_action
# ═══════════════════════════════════════════════

class TestEnforceAction:

    def test_all_clear_passes(self):
        contract = _make_contract()
        agent = AgentProfile(provider="test", agent_name="test")
        perms = ResolvedPermissions(
            allowed_actions=[], confirmation_required=[], denied_actions=[],
        )
        ok, reason = enforce_action(contract, agent, perms, [], "search", {})
        assert ok is True

    def test_rate_limit_blocks_action(self):
        contract = _make_contract(rate_limits=RateLimit(requests_per_minute=3))
        agent = AgentProfile(provider="test", agent_name="test")
        perms = ResolvedPermissions(
            allowed_actions=[], confirmation_required=[], denied_actions=[],
        )
        log = _make_log_entries(5, seconds_ago=30)
        ok, reason = enforce_action(contract, agent, perms, log, "search", {})
        assert ok is False

    def test_transaction_limit_blocks_action(self):
        contract = _make_contract()
        agent = AgentProfile(provider="test", agent_name="test")
        perms = ResolvedPermissions(
            allowed_actions=[], confirmation_required=[], denied_actions=[],
            max_transaction_amount=100.0,
        )
        ok, reason = enforce_action(
            contract, agent, perms, [], "place_order", {"total": 500.0}
        )
        assert ok is False
        assert "exceeds" in reason.lower()


# ═══════════════════════════════════════════════
# BLOCKER FIX: contract ceiling overrides operator claim
# ═══════════════════════════════════════════════

class TestContractCeilsOperatorClaims:
    """
    THE BLOCKER TEST.
    An operator signs max_transaction=999999 but the contract says 100.
    The effective limit must be 100 (the contract's number), not 999999.
    Same for daily spend.
    """

    def test_transaction_amount_capped_by_contract(self):
        """Operator claims 999999, contract says 100 → 100 wins."""
        from tessera.contract.resolver import resolve_permissions

        contract = _make_contract(
            rate_limits=RateLimit(max_transaction_amount=100.0),
        )
        agent = AgentProfile(
            provider="test", agent_name="greedy-agent",
            trust_level=AgentTrust.VERIFIED,
            capabilities=AgentCapabilities(
                can_transact=True,
                max_transaction_amount=999999.0,  # operator claims huge limit
            ),
        )
        perms = resolve_permissions(contract, agent)
        assert perms.max_transaction_amount == 100.0, (
            f"BLOCKER: operator claimed 999999 but contract says 100. "
            f"Effective limit should be 100, got {perms.max_transaction_amount}"
        )

    def test_transaction_amount_operator_lower_wins(self):
        """Operator claims 50, contract says 100 → 50 wins (operator is stricter)."""
        from tessera.contract.resolver import resolve_permissions

        contract = _make_contract(
            rate_limits=RateLimit(max_transaction_amount=100.0),
        )
        agent = AgentProfile(
            provider="test", agent_name="modest-agent",
            trust_level=AgentTrust.VERIFIED,
            capabilities=AgentCapabilities(
                can_transact=True,
                max_transaction_amount=50.0,
            ),
        )
        perms = resolve_permissions(contract, agent)
        assert perms.max_transaction_amount == 50.0

    def test_contract_only_limit_used_when_operator_has_none(self):
        """Operator sets no limit, contract says 200 → 200."""
        from tessera.contract.resolver import resolve_permissions

        contract = _make_contract(
            rate_limits=RateLimit(max_transaction_amount=200.0),
        )
        agent = AgentProfile(
            provider="test", agent_name="no-limit-agent",
            trust_level=AgentTrust.VERIFIED,
            capabilities=AgentCapabilities(
                can_transact=True,
                max_transaction_amount=None,
            ),
        )
        perms = resolve_permissions(contract, agent)
        assert perms.max_transaction_amount == 200.0

    def test_daily_spend_capped_by_contract(self):
        """Operator claims daily_spend=99999, contract says 500 → 500 wins."""
        contract = _make_contract(
            rate_limits=RateLimit(max_daily_spend=500.0),
        )
        agent = AgentProfile(
            provider="test", agent_name="big-spender",
            trust_level=AgentTrust.VERIFIED,
            capabilities=AgentCapabilities(
                can_transact=True,
                max_daily_spend=99999.0,
            ),
        )
        # check_daily_spend should use 500, not 99999
        from tessera.contract.enforcement import check_daily_spend
        ok, reason = check_daily_spend(agent, 500.0, [], 600.0)
        assert ok is False, (
            f"BLOCKER: operator claimed daily_spend=99999 but contract says 500. "
            f"A 600 transaction should be denied."
        )
        assert "500" in reason  # denial should quote the contract's number

    def test_daily_spend_operator_lower_wins(self):
        """Operator claims 200, contract says 500 → 200 wins."""
        from tessera.contract.enforcement import check_daily_spend
        agent = AgentProfile(
            provider="test", agent_name="modest",
            trust_level=AgentTrust.VERIFIED,
            capabilities=AgentCapabilities(can_transact=True, max_daily_spend=200.0),
        )
        ok, reason = check_daily_spend(agent, 500.0, [], 300.0)
        assert ok is False  # 300 > 200 (operator's limit)
