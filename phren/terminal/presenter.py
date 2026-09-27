"""
Phren Terminal Presenter (TTL Format)

Converts engine state into a token-efficient format for LLM agents.
Designed from eval data: fewer tokens = faster inference = same accuracy.

Principles:
  1. Token-first: ~40 tokens per turn, not ~250
  2. Flow-first: the map is the primary artifact
  3. One decision per turn: show only the next step
  4. Progressive disclosure: map on turn 1, state+next on turn 2+
  5. Data is keyed: IDs are explicit, never buried in JSON

Usage:
    presenter = TerminalPresenter(contract)
    turn1 = presenter.first_turn(goal="Buy a laptop")
    turn2 = presenter.turn(last_action="login", result=result, state=state)
"""
import json
from typing import Optional
from phren.contract.schema import PhrenContract, ActionRole


class TerminalPresenter:
    """Converts terminal state into TTL format for LLM agents."""

    def __init__(self, contract: PhrenContract):
        self.contract = contract
        self._actions = {}
        self._primary_chain = []
        self._all_actions = []

        # Build action lookup and primary chain
        for screen in contract.screens:
            for action in screen.actions:
                self._actions[action.id] = action
                self._all_actions.append(action)
                role = action.role.value if hasattr(action.role, 'value') else str(action.role)
                if role == "primary":
                    self._primary_chain.append(action)

    def first_turn(self, goal: str) -> str:
        """Format the first turn: site + goal + map + state + next. ~80 tokens."""
        lines = [
            f"SITE: {self.contract.site_name}",
            f"GOAL: {goal}",
            "",
            "MAP:",
        ]

        for action in self._primary_chain:
            params = self._format_params_short(action)
            output = self._infer_output(action)
            lines.append(f"  {action.id}({params}) → {output}")

        lines.append("")
        lines.append("STATE: start")
        first = self._primary_chain[0].id if self._primary_chain else "?"
        first_params = self._format_params_short(self._primary_chain[0]) if self._primary_chain else ""
        lines.append(f"NEXT: {first}({first_params})")

        return "\n".join(lines)

    def turn(
        self,
        last_action: str,
        status: str,
        result_data: dict = None,
        state: dict = None,
        done_actions: set = None,
    ) -> str:
        """Format a subsequent turn: result + state + next. ~30-50 tokens."""
        lines = []

        # Result of last action
        if status == "ok":
            summary = self._summarize_data(result_data, last_action)
            lines.append(f"OK: {last_action} → {summary}")
        elif status == "confirmation_required":
            lines.append(f"CONFIRM: {last_action} → resend with confirmed=true")
        else:
            reason = ""
            if result_data and isinstance(result_data, dict):
                reason = result_data.get("_message", result_data.get("message", ""))
                if isinstance(reason, list):
                    reason = str(reason[0]) if reason else ""
                reason = str(reason)[:80]
            lines.append(f"FAIL: {last_action} → {reason}")

        # State (compact one-liner)
        if state:
            state_parts = []
            if state.get("logged_in"):
                state_parts.append("logged_in")
            cart = state.get("cart_items", [])
            if cart:
                state_parts.append(f"cart={len(cart)} items")
            results = state.get("last_search_results", [])
            if results:
                state_parts.append(f"results={len(results)}")
            if state.get("current_step") == "completed":
                state_parts.append("COMPLETED")
            lines.append(f"STATE: {', '.join(state_parts) if state_parts else 'start'}")
        else:
            lines.append("STATE: start")

        # Search results (if any, compact table)
        if result_data and isinstance(result_data, dict):
            for key in ("products", "rooms", "documents", "hotels"):
                items = result_data.get(key, [])
                if items and isinstance(items, list):
                    for item in items[:5]:
                        if isinstance(item, dict):
                            item_id = item.get("id", "?")
                            name = item.get("name", item.get("title", "?"))
                            price = item.get("price", item.get("rate", ""))
                            price_str = f"  ${price}" if price else ""
                            lines.append(f"  {item_id}  {name}{price_str}")
                    if len(items) > 5:
                        lines.append(f"  ...and {len(items) - 5} more")
                    break

        # Next action suggestion
        done = done_actions or set()
        next_action = self._suggest_next(done, last_action, status)
        if next_action:
            params = self._format_params_short(next_action)
            lines.append(f"NEXT: {next_action.id}({params})")
        elif state and state.get("current_step") == "completed":
            lines.append("NEXT: DONE")

        return "\n".join(lines)

    # ── Internal helpers ──

    def _format_params_short(self, action) -> str:
        """Format params: name* for required, name for optional."""
        parts = []
        for p in action.parameters:
            if p.required:
                parts.append(f"{p.name}*")
            else:
                parts.append(p.name)
        return ", ".join(parts)

    def _infer_output(self, action) -> str:
        """Infer what this action produces."""
        aid = action.id.lower()
        if "login" in aid:
            return "LOGGED_IN"
        if "search" in aid or "list" in aid or "browse" in aid:
            return "RESULTS[id, name, price]"
        if "view" in aid:
            return "DETAIL"
        if "add" in aid or "cart" in aid:
            return "CART"
        if "checkout" in aid or "proceed" in aid:
            return "CHECKOUT"
        if "order" in aid or "place" in aid or "book" in aid:
            return "DONE"
        if "request" in aid or "submit" in aid:
            return "DONE"
        if action.transitions_to:
            return action.transitions_to.upper()
        return "OK"

    def _summarize_data(self, data: dict, action_id: str) -> str:
        """One-line summary of action result."""
        if not data or not isinstance(data, dict):
            return "done"

        # Login
        if data.get("token") or data.get("access_token"):
            return "token obtained"

        # Search results
        for key in ("products", "rooms", "documents", "hotels"):
            items = data.get(key, [])
            if items and isinstance(items, list):
                return f"{len(items)} results"

        # Cart
        cart = data.get("cart", {})
        if isinstance(cart, dict) and "items" in cart:
            return f"cart has {len(cart['items'])} item(s)"

        # Order/booking/request confirmation
        for key in ("order_id", "reservation_id", "request_id", "booking_id", "id"):
            if data.get(key):
                return f"confirmed: {data[key]}"

        # Nested objects (reservation, request)
        for key in ("reservation", "request", "order", "booking"):
            obj = data.get(key)
            if isinstance(obj, dict) and obj.get("id"):
                return f"confirmed: {obj['id']}"

        return "done"

    def _suggest_next(self, done: set, last_action: str, last_status: str) -> Optional[object]:
        """Find the next primary action the agent hasn't done."""
        if last_status == "confirmation_required":
            return self._actions.get(last_action)

        # Walk the primary chain, find the first not-done
        found_last = False
        for action in self._primary_chain:
            if action.id == last_action:
                found_last = True
                continue
            if found_last and action.id not in done:
                return action

        # If last action was the last primary, we're done
        # Or if we haven't found last_action yet, suggest first undone
        for action in self._primary_chain:
            if action.id not in done:
                return action

        return None
