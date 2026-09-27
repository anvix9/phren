"""
Tessera Phase 5 — Governance Boundary Tasks

These tasks test the governance boundary, not agent intelligence.
Each task scripts a sequence that should be denied or allowed based on
the contract, trust tier, and enforcement rules.

Categories:
  - tier_escalation: agent at tier X tries tier Y actions
  - spend_limits: agent tries to exceed transaction/daily limits
  - rate_limits: agent exceeds rate limits
  - contract_expiry: expired contract blocks everything
  - session_limits: max concurrent sessions enforced
  - item_limits: max items per action enforced
  - user_consent: actions requiring delegation
"""
from tessera.contract.schema import RateLimit, ScreenDefinition, ActionDefinition, ActionParameter
from evals.harness import TaskDefinition, TaskStep


# ── Shared screen + action for all governance tests ──

GOVERNANCE_SCREEN = ScreenDefinition(
    id="main",
    name="Main",
    description="Test screen",
    actions=[
        ActionDefinition(
            id="browse", name="Browse", description="Browse items",
            api_method="GET", api_endpoint="/api/items",
        ),
        ActionDefinition(
            id="buy", name="Buy", description="Buy an item",
            api_method="POST", api_endpoint="/api/buy",
            parameters=[
                ActionParameter(name="quantity", type="integer", required=False),
                ActionParameter(name="total", type="number", required=False),
            ],
        ),
        ActionDefinition(
            id="admin_action", name="Admin", description="Admin-only action",
            api_method="POST", api_endpoint="/api/admin",
        ),
    ],
).model_dump()


def governance_tasks() -> list[TaskDefinition]:
    """Return all governance boundary tasks."""
    tasks = []

    # ═══════════════════════════════════════════
    # TIER ESCALATION
    # ═══════════════════════════════════════════

    tasks.append(TaskDefinition(
        id="tier-anon-browse-allowed",
        site="governance",
        description="Anonymous agent CAN browse (read-only action)",
        category="tier_escalation",
        agent_config={"trust_level": "anonymous"},
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "default_trust_for_actions": "anonymous",
        },
        steps=[
            TaskStep(action="browse", params={}, expect="error"),
            # expect error because no real server, but NOT "denied" for trust
        ],
    ))

    tasks.append(TaskDefinition(
        id="tier-anon-buy-denied",
        site="governance",
        description="Anonymous agent CANNOT buy (requires identified+)",
        category="tier_escalation",
        agent_config={"trust_level": "anonymous"},
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "action_trust_requirements": [
                {"action_id": "buy", "min_trust_level": "verified"},
            ],
        },
        steps=[
            TaskStep(action="buy", params={"total": 10.0}, expect="denied"),
        ],
    ))

    tasks.append(TaskDefinition(
        id="tier-identified-buy-denied",
        site="governance",
        description="Identified agent CANNOT buy when verified is required",
        category="tier_escalation",
        agent_config={"trust_level": "identified"},
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "action_trust_requirements": [
                {"action_id": "buy", "min_trust_level": "verified"},
            ],
        },
        steps=[
            TaskStep(action="buy", params={"total": 10.0}, expect="denied"),
        ],
    ))

    tasks.append(TaskDefinition(
        id="tier-verified-buy-allowed",
        site="governance",
        description="Verified agent CAN buy when verified is required",
        category="tier_escalation",
        agent_config={
            "trust_level": "verified",
            "capabilities": {"can_transact": True},
        },
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "action_trust_requirements": [
                {"action_id": "buy", "min_trust_level": "verified"},
            ],
        },
        steps=[
            # Not denied for trust (may error because no server, but that's OK)
            TaskStep(action="buy", params={"total": 10.0}, expect="error"),
        ],
    ))

    # ═══════════════════════════════════════════
    # SPEND LIMITS
    # ═══════════════════════════════════════════

    tasks.append(TaskDefinition(
        id="spend-over-transaction-limit",
        site="governance",
        description="Transaction exceeding contract limit is denied",
        category="spend_limits",
        agent_config={
            "trust_level": "verified",
            "capabilities": {"can_transact": True, "max_transaction_amount": 999999.0},
        },
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "rate_limits": {"max_concurrent_sessions": 10, "max_transaction_amount": 100.0},
        },
        steps=[
            TaskStep(
                action="buy", params={"total": 200.0}, expect="denied",
                expect_reason_contains="100",
            ),
        ],
    ))

    tasks.append(TaskDefinition(
        id="spend-under-transaction-limit",
        site="governance",
        description="Transaction under contract limit is allowed",
        category="spend_limits",
        agent_config={
            "trust_level": "verified",
            "capabilities": {"can_transact": True, "max_transaction_amount": 999999.0},
        },
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "rate_limits": {"max_concurrent_sessions": 10, "max_transaction_amount": 100.0},
        },
        steps=[
            TaskStep(action="buy", params={"total": 50.0}, expect="error"),
            # expect "error" (no real server) but NOT "denied" for spend
        ],
    ))

    tasks.append(TaskDefinition(
        id="spend-contract-caps-operator",
        site="governance",
        description="Operator claims 999999 but contract says 100 — contract wins",
        category="spend_limits",
        agent_config={
            "trust_level": "verified",
            "capabilities": {"can_transact": True, "max_transaction_amount": 999999.0},
        },
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "rate_limits": {"max_concurrent_sessions": 10, "max_transaction_amount": 100.0},
        },
        steps=[
            TaskStep(
                action="buy", params={"total": 150.0}, expect="denied",
                expect_reason_contains="100",
            ),
        ],
    ))

    tasks.append(TaskDefinition(
        id="spend-daily-limit-exceeded",
        site="governance",
        description="Daily spend exceeding contract limit is denied",
        category="spend_limits",
        agent_config={
            "trust_level": "verified",
            "capabilities": {"can_transact": True, "max_daily_spend": 999999.0},
        },
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "rate_limits": {"max_concurrent_sessions": 10, "max_daily_spend": 500.0},
        },
        steps=[
            TaskStep(
                action="buy", params={"total": 600.0}, expect="denied",
                expect_reason_contains="500",
            ),
        ],
    ))

    # ═══════════════════════════════════════════
    # RATE LIMITS
    # ═══════════════════════════════════════════

    tasks.append(TaskDefinition(
        id="rate-per-minute-exceeded",
        site="governance",
        description="Actions beyond per-minute limit are denied",
        category="rate_limits",
        agent_config={"trust_level": "identified"},
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "rate_limits": {"requests_per_minute": 4, "max_concurrent_sessions": 10},
        },
        steps=[
            # 1 connect (implicit) + 3 browse = 4 total → limit hit
            TaskStep(action="browse", params={}, expect="error"),  # 2nd total
            TaskStep(action="browse", params={}, expect="error"),  # 3rd total
            TaskStep(action="browse", params={}, expect="error"),  # 4th total = limit
            TaskStep(action="browse", params={}, expect="denied",
                     expect_reason_contains="rate limit"),  # 5th = denied
        ],
    ))

    # ═══════════════════════════════════════════
    # CONTRACT EXPIRATION
    # ═══════════════════════════════════════════

    tasks.append(TaskDefinition(
        id="expired-contract-blocks-connect",
        site="governance",
        description="Expired contract denies all connections",
        category="contract_expiry",
        agent_config={"trust_level": "verified"},
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "expires_at": "2020-01-01T00:00:00+00:00",
        },
        steps=[
            TaskStep(action="connect", params={}, expect="connect_denied"),
        ],
    ))

    # ═══════════════════════════════════════════
    # SESSION LIMITS
    # ═══════════════════════════════════════════

    tasks.append(TaskDefinition(
        id="max-sessions-blocks-second",
        site="governance",
        description="Second connection denied when max_concurrent_sessions=1",
        category="session_limits",
        agent_config={"trust_level": "identified"},
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "rate_limits": {"max_concurrent_sessions": 1},
        },
        # Note: this task connects once (the harness connect), then we can't
        # test a second connect through steps. Tested in integration tests instead.
        steps=[
            TaskStep(action="browse", params={}, expect="error"),  # first action works
        ],
    ))

    # ═══════════════════════════════════════════
    # ITEM LIMITS
    # ═══════════════════════════════════════════

    tasks.append(TaskDefinition(
        id="max-items-exceeded",
        site="governance",
        description="Quantity exceeding max_items_per_action is denied",
        category="item_limits",
        agent_config={"trust_level": "verified", "capabilities": {"can_transact": True}},
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "rate_limits": {"max_items_per_action": 5, "max_concurrent_sessions": 10},
        },
        steps=[
            TaskStep(
                action="buy", params={"quantity": 20}, expect="denied",
                expect_reason_contains="max items",
            ),
        ],
    ))

    tasks.append(TaskDefinition(
        id="max-items-under-limit",
        site="governance",
        description="Quantity under max_items_per_action is allowed",
        category="item_limits",
        agent_config={"trust_level": "verified", "capabilities": {"can_transact": True}},
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "rate_limits": {"max_items_per_action": 10, "max_concurrent_sessions": 10},
        },
        steps=[
            TaskStep(action="buy", params={"quantity": 3}, expect="error"),
            # "error" because no server, but NOT "denied" for items
        ],
    ))

    # ═══════════════════════════════════════════
    # USER CONSENT
    # ═══════════════════════════════════════════

    tasks.append(TaskDefinition(
        id="consent-required-without-token",
        site="governance",
        description="Action requiring user delegation denied without consent",
        category="user_consent",
        agent_config={
            "trust_level": "verified",
            "capabilities": {"can_transact": True},
            "delegated_by_user": False,
        },
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "required_confirmations": ["buy"],
            "rate_limits": {"max_concurrent_sessions": 10},
        },
        steps=[
            TaskStep(
                action="buy", params={"total": 10.0, "confirmed": True}, expect="denied",
                expect_reason_contains="user delegation",
            ),
        ],
    ))

    tasks.append(TaskDefinition(
        id="consent-required-with-delegation",
        site="governance",
        description="Action requiring user delegation allowed with delegated_by_user",
        category="user_consent",
        agent_config={
            "trust_level": "verified",
            "capabilities": {"can_transact": True},
            "delegated_by_user": True,
        },
        contract_overrides={
            "screens": [GOVERNANCE_SCREEN],
            "entry_screen": "main",
            "required_confirmations": ["buy"],
            "rate_limits": {"max_concurrent_sessions": 10},
        },
        steps=[
            TaskStep(action="buy", params={"total": 10.0, "confirmed": True}, expect="error"),
            # "error" (no server) but NOT "denied" for consent
        ],
    ))

    return tasks
