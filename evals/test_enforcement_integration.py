"""
Tessera Phase 2 — Governance Integration Tests

Unit tests (test_enforcement.py) prove each check function works.
These integration tests prove the checks are actually wired into the
terminal engine and fire when agents interact through MCP handlers.

Each test creates a contract with a specific limit, connects an agent,
and verifies the limit actually blocks the action.
"""
import pytest
import json
import asyncio
from datetime import datetime, timezone, timedelta

from tessera.contract.schema import (
    TesseraContract, ScreenDefinition, ActionDefinition,
    ActionParameter, RateLimit, AgentProfile, AgentTrust,
    AgentCapabilities,
)
from tessera.contract.resolver import resolve_permissions
from tessera.terminal.engine import TesseraTerminal


def _make_terminal(contract_overrides: dict) -> TesseraTerminal:
    """Create a terminal with a contract that has one screen and one action."""
    defaults = {
        "contract_id": "enforce-integ",
        "site_name": "Enforcement Integration",
        "site_url": "http://localhost:9999",
        "require_identification": False,
        "screens": [
            ScreenDefinition(
                id="main", name="Main", description="Main screen",
                actions=[
                    ActionDefinition(
                        id="buy", name="Buy", description="Buy something",
                        api_method="POST", api_endpoint="/api/buy",
                        parameters=[
                            ActionParameter(name="quantity", type="integer", required=False),
                            ActionParameter(name="total", type="number", required=False),
                        ],
                    ),
                ],
            ),
        ],
        "entry_screen": "main",
    }
    defaults.update(contract_overrides)
    contract = TesseraContract(**defaults)
    return TesseraTerminal(contract, "http://localhost:9999")


def _connect(terminal: TesseraTerminal, **agent_overrides) -> str:
    """Connect an agent and return session_id."""
    defaults = {
        "provider": "test",
        "agent_name": "test-agent",
        "trust_level": AgentTrust.VERIFIED,
        "purpose": "testing",
        "capabilities": AgentCapabilities(can_transact=True),
    }
    defaults.update(agent_overrides)
    agent = AgentProfile(**defaults)
    result = terminal.connect(agent)
    assert result["status"] == "connected", f"Connect failed: {result}"
    return result["session_id"]


# ═══════════════════════════════════════════════
# CONTRACT EXPIRATION (blocks at connect)
# ═══════════════════════════════════════════════

class TestExpirationIntegration:

    def test_expired_contract_denies_connect(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        terminal = _make_terminal({"expires_at": past})
        agent = AgentProfile(provider="test", agent_name="test", trust_level=AgentTrust.IDENTIFIED)
        result = terminal.connect(agent)
        assert result["status"] == "denied"
        assert "expired" in result["reason"].lower()

    def test_valid_contract_allows_connect(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        terminal = _make_terminal({"expires_at": future})
        agent = AgentProfile(provider="test", agent_name="test", trust_level=AgentTrust.IDENTIFIED)
        result = terminal.connect(agent)
        assert result["status"] == "connected"


# ═══════════════════════════════════════════════
# MAX CONCURRENT SESSIONS (blocks at connect)
# ═══════════════════════════════════════════════

class TestMaxSessionsIntegration:

    def test_second_session_denied_when_max_is_1(self):
        terminal = _make_terminal({
            "rate_limits": RateLimit(max_concurrent_sessions=1).model_dump(),
        })
        sid1 = _connect(terminal)
        # Second connect should be denied
        agent2 = AgentProfile(provider="test", agent_name="test2", trust_level=AgentTrust.IDENTIFIED)
        result = terminal.connect(agent2)
        assert result["status"] == "denied"
        assert "concurrent" in result["reason"].lower()

    def test_session_freed_after_disconnect(self):
        terminal = _make_terminal({
            "rate_limits": RateLimit(max_concurrent_sessions=1).model_dump(),
        })
        sid1 = _connect(terminal)
        terminal.disconnect(sid1)
        # Now a new session should be allowed
        sid2 = _connect(terminal)
        assert sid2 != sid1


# ═══════════════════════════════════════════════
# RATE LIMITS (blocks at execute)
# ═══════════════════════════════════════════════

class TestRateLimitIntegration:

    def test_per_minute_blocks_after_limit(self):
        terminal = _make_terminal({
            "rate_limits": RateLimit(
                requests_per_minute=5,  # 5 total: 1 connect + 4 actions allowed
                max_concurrent_sessions=10,
            ).model_dump(),
        })
        sid = _connect(terminal)  # counts as 1 entry in audit log

        # Next 4 should not be rate-limited (total = 5 including connect)
        for i in range(4):
            result = terminal.execute_action(sid, "buy", {"quantity": 1})
            if result.get("status") == "denied":
                assert "rate limit" not in result.get("reason", "").lower(), \
                    f"Action {i+1} was rate-limited too early"

        # 5th action (6th entry total) should be rate-limited
        result = terminal.execute_action(sid, "buy", {"quantity": 1})
        assert result["status"] == "denied"
        assert "rate limit" in result["reason"].lower()


# ═══════════════════════════════════════════════
# MAX ITEMS PER ACTION (blocks at execute)
# ═══════════════════════════════════════════════

class TestMaxItemsIntegration:

    def test_quantity_over_limit_denied(self):
        terminal = _make_terminal({
            "rate_limits": RateLimit(
                max_items_per_action=5,
                max_concurrent_sessions=10,
            ).model_dump(),
        })
        sid = _connect(terminal)
        result = terminal.execute_action(sid, "buy", {"quantity": 20})
        assert result["status"] == "denied"
        assert "max items" in result["reason"].lower()

    def test_quantity_under_limit_allowed(self):
        terminal = _make_terminal({
            "rate_limits": RateLimit(
                max_items_per_action=10,
                max_concurrent_sessions=10,
            ).model_dump(),
        })
        sid = _connect(terminal)
        result = terminal.execute_action(sid, "buy", {"quantity": 3})
        # Should NOT be denied for max items (may fail for other reasons like no server)
        if result.get("status") == "denied":
            assert "max items" not in result.get("reason", "").lower()


# ═══════════════════════════════════════════════
# MAX TRANSACTION AMOUNT (blocks at execute)
# ═══════════════════════════════════════════════

class TestTransactionAmountIntegration:

    def test_amount_over_limit_denied(self):
        terminal = _make_terminal({
            "rate_limits": RateLimit(max_concurrent_sessions=10).model_dump(),
        })
        # Connect with a max_transaction_amount in capabilities
        sid = _connect(terminal, capabilities=AgentCapabilities(
            can_transact=True,
            max_transaction_amount=100.0,
        ))
        result = terminal.execute_action(sid, "buy", {"total": 500.0})
        assert result["status"] == "denied"
        assert "exceeds" in result["reason"].lower()

    def test_amount_under_limit_not_blocked_for_amount(self):
        terminal = _make_terminal({
            "rate_limits": RateLimit(max_concurrent_sessions=10).model_dump(),
        })
        sid = _connect(terminal, capabilities=AgentCapabilities(
            can_transact=True,
            max_transaction_amount=1000.0,
        ))
        result = terminal.execute_action(sid, "buy", {"total": 50.0})
        if result.get("status") == "denied":
            assert "exceeds" not in result.get("reason", "").lower()


# ═══════════════════════════════════════════════
# USER CONSENT (blocks at execute)
# ═══════════════════════════════════════════════

class TestUserConsentIntegration:

    def test_action_requiring_consent_without_token_denied(self):
        terminal = _make_terminal({
            "required_confirmations": ["buy"],
            "rate_limits": RateLimit(max_concurrent_sessions=10).model_dump(),
        })
        # Connect WITHOUT delegation or consent token
        sid = _connect(terminal, delegated_by_user=False)
        result = terminal.execute_action(sid, "buy", {"quantity": 1}, confirmed=True)
        assert result["status"] == "denied"
        assert "user delegation" in result["reason"].lower()

    def test_action_requiring_consent_with_delegation_passes(self):
        terminal = _make_terminal({
            "required_confirmations": ["buy"],
            "rate_limits": RateLimit(max_concurrent_sessions=10).model_dump(),
        })
        sid = _connect(terminal, delegated_by_user=True)
        result = terminal.execute_action(sid, "buy", {"quantity": 1}, confirmed=True)
        # Should NOT be blocked for user consent
        if result.get("status") == "denied":
            assert "user delegation" not in result.get("reason", "").lower()


# ═══════════════════════════════════════════════
# BLOCKER FIX: contract ceiling overrides operator claims
# (end-to-end through terminal engine)
# ═══════════════════════════════════════════════

class TestContractCeilingIntegration:
    """
    THE BLOCKER INTEGRATION TEST.
    Operator signs a token with huge limits. Contract has smaller limits.
    The terminal must deny based on the CONTRACT's number, not the operator's.
    """

    def test_operator_claims_huge_transaction_contract_caps_it(self):
        """Operator: max_transaction=999999. Contract: max_transaction=100. Buy at 200 → denied."""
        terminal = _make_terminal({
            "rate_limits": RateLimit(
                max_concurrent_sessions=10,
                max_transaction_amount=100.0,  # CONTRACT says 100
            ).model_dump(),
        })
        # Connect with operator claiming 999999
        sid = _connect(terminal, capabilities=AgentCapabilities(
            can_transact=True,
            max_transaction_amount=999999.0,  # OPERATOR claims 999999
        ))
        # Try to buy at 200 — should be denied at 100 (contract's limit)
        result = terminal.execute_action(sid, "buy", {"total": 200.0})
        assert result["status"] == "denied", (
            f"BLOCKER: operator claimed 999999, contract says 100, "
            f"but 200 was not denied. Got: {result}"
        )
        assert "100" in result["reason"], (
            f"Denial should quote contract's limit (100), got: {result['reason']}"
        )

    def test_operator_claims_huge_transaction_under_contract_allowed(self):
        """Operator: max_transaction=999999. Contract: max_transaction=100. Buy at 50 → allowed."""
        terminal = _make_terminal({
            "rate_limits": RateLimit(
                max_concurrent_sessions=10,
                max_transaction_amount=100.0,
            ).model_dump(),
        })
        sid = _connect(terminal, capabilities=AgentCapabilities(
            can_transact=True,
            max_transaction_amount=999999.0,
        ))
        result = terminal.execute_action(sid, "buy", {"total": 50.0})
        # Should NOT be denied for transaction amount
        if result.get("status") == "denied":
            assert "exceeds" not in result.get("reason", "").lower(), (
                f"50 should be under contract limit of 100, but was denied: {result['reason']}"
            )

    def test_operator_stricter_than_contract_operator_wins(self):
        """Operator: max_transaction=50. Contract: max_transaction=100. Buy at 75 → denied at 50."""
        terminal = _make_terminal({
            "rate_limits": RateLimit(
                max_concurrent_sessions=10,
                max_transaction_amount=100.0,
            ).model_dump(),
        })
        sid = _connect(terminal, capabilities=AgentCapabilities(
            can_transact=True,
            max_transaction_amount=50.0,  # operator is stricter
        ))
        result = terminal.execute_action(sid, "buy", {"total": 75.0})
        assert result["status"] == "denied"
        assert "50" in result["reason"], (
            f"Denial should quote operator's limit (50), got: {result['reason']}"
        )

    def test_daily_spend_capped_by_contract_end_to_end(self):
        """Operator: daily_spend=99999. Contract: daily_spend=500. Spend 600 → denied."""
        terminal = _make_terminal({
            "rate_limits": RateLimit(
                max_concurrent_sessions=10,
                max_daily_spend=500.0,  # CONTRACT says 500
            ).model_dump(),
        })
        sid = _connect(terminal, capabilities=AgentCapabilities(
            can_transact=True,
            max_daily_spend=99999.0,  # OPERATOR claims 99999
        ))
        result = terminal.execute_action(sid, "buy", {"total": 600.0})
        assert result["status"] == "denied", (
            f"BLOCKER: operator claimed daily 99999, contract says 500, "
            f"but 600 was not denied. Got: {result}"
        )
        assert "500" in result["reason"]
