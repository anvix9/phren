"""
Phren Smart Agent

An upgraded agent that:
1. Queries the Phren Registry to discover available terminals
2. Picks the best terminal for the user's goal
3. Connects and navigates autonomously

Usage:
  # With registry (auto-routing):
  python smart_agent.py "Buy wireless headphones" --model qwen3:8b

  # The agent figures out it needs the shopping terminal and connects.
  # No --mcp-url needed — the registry handles discovery.
"""
import json
import sys
import argparse
import requests
from typing import Optional

# Import the existing agent module
from agent import (
    mcp_call, build_tools_from_screen, format_screen_for_llm,
    build_detailed_flow_map, MAX_STEPS, OLLAMA_URL,
)

REGISTRY_URL = "http://localhost:8100"


def discover_terminal(goal: str, registry_url: str) -> Optional[dict]:
    """
    Query the registry and pick the best terminal for the goal.
    Uses simple keyword matching — in production, this could use an LLM.
    """
    try:
        resp = requests.get(f"{registry_url}/terminals", timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        terminals = data.get("terminals", [])
    except Exception as e:
        print(f"[Discovery] Registry unavailable: {e}")
        return None

    if not terminals:
        print("[Discovery] No terminals registered")
        return None

    print(f"[Discovery] Found {len(terminals)} terminals:")
    for t in terminals:
        status = "✓" if t.get("healthy") else "?"
        print(f"  [{status}] {t['id']:15} {t['name']:40} ({t['category']})")
    print()

    # Score each terminal by keyword overlap with the goal
    goal_lower = goal.lower()
    scores = []
    for t in terminals:
        score = 0
        # Check tags
        for tag in t.get("tags", []):
            if tag in goal_lower:
                score += 3
        # Check description words
        for word in t.get("description", "").lower().split():
            if len(word) > 3 and word in goal_lower:
                score += 1
        # Check category
        if t.get("category", "").lower() in goal_lower:
            score += 5
        # Check name
        if t.get("name", "").lower() in goal_lower:
            score += 5

        # Keyword heuristics for common intents
        shopping_words = ["buy", "purchase", "price", "cart", "checkout", "product", "order",
                          "headphones", "laptop", "shirt", "book", "yoga", "keyboard", "cheapest"]
        booking_words = ["hotel", "room", "book", "reservation", "check-in", "stay", "night",
                         "tokyo", "paris", "london", "new york", "mexico", "travel", "guest"]
        gov_words = ["permit", "license", "certificate", "form", "complaint", "government",
                     "apply", "application", "department", "birth", "death", "marriage",
                     "building", "food truck", "business license", "noise", "zoning"]

        if t.get("category") == "shopping":
            score += sum(2 for w in shopping_words if w in goal_lower)
        elif t.get("category") == "booking":
            score += sum(2 for w in booking_words if w in goal_lower)
        elif t.get("category") == "government":
            score += sum(2 for w in gov_words if w in goal_lower)

        scores.append((score, t))

    # Sort by score descending
    scores.sort(key=lambda x: x[0], reverse=True)

    if scores[0][0] == 0:
        print("[Discovery] No terminal matched the goal. Scores were all 0.")
        print("[Discovery] Available categories:", [t.get("category") for t in terminals])
        return None

    best = scores[0][1]
    print(f"[Discovery] Selected: {best['name']} (score: {scores[0][0]})")
    print(f"[Discovery] URL: {best['url']}")
    return best


def run_smart_agent(goal: str, model: str, trust_level: str, can_transact: bool,
                    registry_url: str):
    """Discover the right terminal, then run the agent."""
    from datetime import date
    today = date.today().isoformat()

    print(f"\n{'='*60}")
    print(f"SUBWAY SMART AGENT")
    print(f"  Model: {model}")
    print(f"  Trust: {trust_level}")
    print(f"  Goal: {goal}")
    print(f"  Registry: {registry_url}")
    print(f"{'='*60}\n")

    # Step 1: Discover terminal
    print("[Discovery] Searching for the right terminal...")
    terminal = discover_terminal(goal, registry_url)
    if not terminal:
        print("[Discovery] Could not find a suitable terminal for this goal.")
        return

    mcp_url = terminal["url"]
    print()

    # Step 2: Connect
    print(f"[Agent] Connecting to {terminal['name']}...")
    import agent as agent_module
    agent_module.MCP_URL = mcp_url

    connect_result = mcp_call("phren_connect", {
        "provider": "ollama",
        "agent_name": f"phren-smart-{model.replace(':', '-')}",
        "trust_level": trust_level,
        "purpose": "purchase" if can_transact else "research",
        "can_transact": can_transact,
        "max_transaction_amount": 500.0 if can_transact else None,
    })

    if connect_result.get("status") != "connected":
        print(f"[Agent] Connection failed: {connect_result}")
        return

    session_id = connect_result["session_id"]
    print(f"[Agent] Connected! Session: {session_id}")
    print(f"[Agent] Allowed actions: {connect_result['permissions']['allowed']}")
    if connect_result["permissions"].get("denied"):
        print(f"[Agent] Denied actions: {connect_result['permissions']['denied']}")
    print()

    # Step 3: Get flow map
    flow_map = build_detailed_flow_map(connect_result, mcp_url)
    if flow_map:
        print("[Agent] Terminal map loaded:")
        for line in flow_map.split("\n")[:20]:  # Print first 20 lines
            print(f"  {line}")
        if flow_map.count("\n") > 20:
            print(f"  ... ({flow_map.count(chr(10)) - 20} more lines)")
        print()

    # Step 4: Agent loop
    system_prompt = f"""You are an AI agent navigating a structured terminal interface.
Today's date: {today}

Your goal: {goal}

You are connected to: {terminal['name']}
Category: {terminal.get('category', 'general')}

{flow_map}

HOW THIS WORKS:
- You navigate between screens by calling tool functions.
- Each screen shows you data and a set of available actions.
- Actions may transition you to a different screen.
- You can ONLY call tools listed as available on the CURRENT screen.
- Read the available tools list carefully at each step — it changes per screen.

PLANNING:
- Plan your full path before starting.
- When dates are needed, use ISO format: YYYY-MM-DD (today is {today})
- When you achieve the goal, respond with a summary. Do NOT keep acting after success.
- Be efficient — take the most direct path.

IMPORTANT:
- If a tool requires parameters you don't have, use reasonable defaults.
- For shipping/payment, fabricate plausible details if not provided by the user.
- For dates not specified, use dates starting from tomorrow."""

    messages = [{"role": "system", "content": system_prompt}]
    cached_screen_data = None

    for step in range(MAX_STEPS):
        # Get screen
        if cached_screen_data:
            screen_data = cached_screen_data
            cached_screen_data = None
        else:
            screen_data = mcp_call("phren_get_screen", {"session_id": session_id})
            if screen_data.get("status") != "ok":
                print(f"[Agent] Screen error: {screen_data}")
                break

        screen_text = format_screen_for_llm(screen_data)
        tools = build_tools_from_screen(screen_data)

        print(f"--- Step {step + 1} ---")
        print(f"Screen: {screen_data['screen']['name']}")
        print(f"Available tools: {[t['function']['name'] for t in tools]}")

        # Trim old messages
        if len(messages) > 13:
            messages = messages[:1] + messages[-12:]

        messages.append({
            "role": "user",
            "content": f"Here is the current terminal screen:\n\n{screen_text}\n\n"
                       f"What action should you take to achieve the goal: {goal}"
        })

        # Call Ollama
        try:
            response = requests.post(f"{OLLAMA_URL}/api/chat", json={
                "model": model,
                "messages": messages,
                "tools": tools if tools else None,
                "stream": False,
                "options": {"temperature": 0.1},
            }, timeout=120)

            if response.status_code != 200:
                print(f"[Agent] Ollama error: {response.status_code}")
                break
            result = response.json()
        except Exception as e:
            print(f"[Agent] Ollama error: {e}")
            break

        message = result.get("message", {})
        messages.append(message)

        tool_calls = message.get("tool_calls", [])

        if tool_calls:
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"].get("arguments", {})
                print(f"[Agent] Calling: {fn_name}({json.dumps(fn_args)})")

                exec_result = mcp_call("phren_execute", {
                    "session_id": session_id,
                    "action_id": fn_name,
                    "params": fn_args,
                    "confirmed": can_transact,
                })

                print(f"[Agent] Result: {exec_result.get('status')}")

                # Cache next screen
                if exec_result.get("next_actions") and exec_result.get("screen"):
                    cached_screen_data = {
                        "status": "ok",
                        "screen": {
                            "id": exec_result["screen"],
                            "name": exec_result.get("screen_name", exec_result["screen"]),
                            "description": "",
                        },
                        "data": exec_result.get("data", {}),
                        "actions": exec_result["next_actions"],
                    }

                # Build summary
                result_summary = _summarize_result(exec_result)
                messages.append({"role": "tool", "content": result_summary})
                print(f"[Agent] -> {result_summary[:200]}")
                print()
        else:
            content = message.get("content", "")
            print(f"[Agent] Response: {content[:500]}")
            print()

            if any(w in content.lower() for w in [
                "complete", "done", "finished", "ordered", "confirmed",
                "purchased", "booked", "reservation", "submitted", "achieved"
            ]):
                break

            describes_action = any(w in content.lower() for w in [
                "next step", "action:", "should", "will now", "let me",
            ])
            if describes_action and step < MAX_STEPS - 1:
                print("[Agent] Nudging to call tool...")
                messages.append({
                    "role": "user",
                    "content": "Don't describe the action — CALL the tool function directly."
                })
                continue

            if step > 0:
                print("[Agent] Model stopped — ending.")
                break

    # Audit log
    print(f"\n{'='*60}")
    print("SESSION SUMMARY")
    print(f"{'='*60}")
    print(f"Terminal: {terminal['name']}")
    log = mcp_call("phren_audit_log", {"session_id": session_id})
    for entry in log.get("audit_log", []):
        emoji = {"success": "+", "denied": "x", "error": "!"}.get(entry["result"], " ")
        print(f"  [{emoji}] {entry['action']:30} ({entry['result']})")

    disc = mcp_call("phren_disconnect", {"session_id": session_id})
    print(f"\nTotal actions: {disc.get('actions_performed', 0)}")


def _summarize_result(exec_result: dict) -> str:
    """Compact result summary."""
    data = exec_result.get("data", {})
    if not data:
        return exec_result.get("message", exec_result.get("reason", json.dumps(exec_result)[:200]))

    if "products" in data:
        s = f"Found {len(data['products'])} products"
        for p in data["products"][:5]:
            s += f"\n  [{p['id']}] {p['name']} — ${p['price']}"
        return s
    if "hotels" in data:
        s = f"Found {len(data['hotels'])} hotels"
        for h in data["hotels"][:5]:
            s += f"\n  [{h['id']}] {h['name']} — from ${h.get('price_min','?')}/night"
        return s
    if "rooms" in data:
        rooms = data["rooms"]
        if not rooms:
            return "No rooms available for your criteria"
        s = f"Found {len(rooms)} rooms"
        for r in rooms[:5]:
            avail = "available" if r.get("available") else "unavailable"
            s += f"\n  [{r['id']}] {r['name']} — ${r['price_per_night']}/night ({avail})"
        return s
    if "reservation" in data:
        res = data["reservation"]
        return f"Reservation {res['id']} — ${res.get('total_price','?')} — {res.get('status','?')}"
    if "documents" in data:
        s = f"Found {len(data['documents'])} documents"
        for d in data["documents"][:5]:
            fee = f"${d['fee']}" if d.get('fee') else "Free"
            s += f"\n  [{d['id']}] {d['title']} — {fee}"
        return s
    if "request" in data:
        req = data["request"]
        return f"Request {req['id']} — status: {req.get('status','?')}, queue: #{req.get('queue_position','?')}"
    if "order" in data:
        order = data["order"]
        return f"Order {order['id']} — ${order['total']} — {order['status']}"
    if "name" in data and "price" in data:
        return f"Product: {data['name']} — ${data['price']}"
    if "name" in data and "price_per_night" in data:
        return f"Room: {data['name']} — ${data['price_per_night']}/night"
    if "items_detail" in data:
        return f"Cart: {len(data.get('items_detail',[]))} items, total: ${data.get('total', 0)}"
    if "departments" in data:
        return f"Found {len(data['departments'])} departments"

    return json.dumps(data)[:300]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phren Smart Agent (auto-discovery)")
    parser.add_argument("goal", nargs="?",
                        default="Find wireless headphones and add them to cart",
                        help="The goal in natural language")
    parser.add_argument("--model", default="qwen3:8b", help="Ollama model")
    parser.add_argument("--buy", action="store_true", help="Enable transactions (super_agent)")
    parser.add_argument("--registry", default="http://localhost:8100", help="Registry URL")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama URL")
    parser.add_argument("--max-steps", type=int, default=15, help="Max steps")
    args = parser.parse_args()

    OLLAMA_URL = args.ollama_url
    MAX_STEPS = args.max_steps

    trust = "super_agent" if args.buy else "identified"
    run_smart_agent(args.goal, args.model, trust, args.buy, args.registry)
