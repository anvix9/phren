"""
Phren Agent — Ollama-powered shopping agent

An AI agent that uses a local Ollama model to navigate the Phren terminal
and complete shopping tasks autonomously.

The agent:
  1. Connects to the Phren MCP server
  2. Reads the current screen (available data + actions)
  3. Sends the screen to the LLM with tool definitions
  4. LLM decides which action to take
  5. Agent executes the action via the MCP server
  6. Repeats until goal is achieved

Usage:
  # Start shopping site (port 8000) and MCP server (port 8001) first, then:
  python agent.py "Find wireless headphones under $100 and buy them"
  python agent.py "Compare the prices of all electronics" --model qwen2.5:3b
  python agent.py "Buy a yoga mat" --trust super_agent --model qwen3:8b
"""
import json
import sys
import argparse
import requests
from typing import Optional


# ── Configuration ──

OLLAMA_URL = "http://localhost:11434"
MCP_URL = "http://localhost:8001"
MAX_STEPS = 15


# ── MCP Client ──

def mcp_call(tool: str, data: dict) -> dict:
    """Call a Phren MCP tool."""
    try:
        resp = requests.post(f"{MCP_URL}/tools/{tool}", json=data, timeout=15)
        if resp.status_code >= 400:
            return {"status": "error", "detail": resp.json().get("detail", f"HTTP {resp.status_code}")}
        return resp.json()
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ── Ollama Tool Definitions ──

def build_tools_from_screen(screen_data: dict) -> list[dict]:
    """
    Convert Phren screen actions into Ollama tool definitions.
    This is where the magic happens — the structured terminal
    maps directly to tool schemas that even small models can use.
    """
    tools = []
    for action in screen_data.get("actions", []):
        properties = {}
        required = []

        for param in action.get("parameters", []):
            prop = {
                "type": param["type"] if param["type"] in ("string", "int", "float", "boolean") else "string",
                "description": param.get("description", ""),
            }
            # Map Phren types to JSON Schema types
            if param["type"] == "int":
                prop["type"] = "integer"
            elif param["type"] == "float":
                prop["type"] = "number"
            elif param["type"] == "enum" and param.get("enum_values"):
                prop["type"] = "string"
                prop["enum"] = param["enum_values"]

            if param.get("min") is not None:
                prop["minimum"] = param["min"]
            if param.get("max") is not None:
                prop["maximum"] = param["max"]

            properties[param["name"]] = prop
            if param.get("required", False):
                required.append(param["name"])

        tools.append({
            "type": "function",
            "function": {
                "name": action["id"],
                "description": action["description"],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })

    return tools


# ── LLM Agent Loop ──

def format_screen_for_llm(screen_data: dict) -> str:
    """Format screen data as compact text for the LLM context."""
    lines = []
    screen = screen_data.get("screen", {})
    lines.append(f"CURRENT SCREEN: {screen.get('name', 'Unknown')}")
    lines.append(f"Description: {screen.get('description', '')}")
    lines.append("")

    # Data
    data = screen_data.get("data", {})
    if data:
        lines.append("VISIBLE DATA:")
        # Handle product lists
        if "products" in data:
            products = data["products"]
            lines.append(f"  Products found: {len(products)}")
            for p in products[:8]:  # Limit to avoid flooding context
                stock = p.get("stock_status", "unknown")
                lines.append(f"  - [{p['id']}] {p['name']} — ${p['price']} "
                             f"(rating: {p.get('rating', 'N/A')}, {stock})")
        # Handle single product
        elif "name" in data and "price" in data:
            lines.append(f"  Product: {data['name']}")
            lines.append(f"  Price: ${data['price']}")
            lines.append(f"  Rating: {data.get('rating', 'N/A')}/5 ({data.get('review_count', 0)} reviews)")
            lines.append(f"  Stock: {data.get('stock_status', 'unknown')}")
            lines.append(f"  Description: {data.get('description', '')}")
        # Handle cart
        elif "items_detail" in data:
            items = data["items_detail"]
            if items:
                lines.append(f"  Cart items: {len(items)}")
                for i in items:
                    lines.append(f"  - {i['name']} x{i['quantity']} — ${i['line_total']}")
                lines.append(f"  Subtotal: ${data.get('subtotal', 0)}")
                lines.append(f"  Tax: ${data.get('tax', 0)}")
                lines.append(f"  Total: ${data.get('total', 0)}")
            else:
                lines.append("  Cart is empty")
        # Handle order
        elif "order" in data:
            order = data["order"]
            lines.append(f"  Order ID: {order.get('id', 'N/A')}")
            lines.append(f"  Status: {order.get('status', 'N/A')}")
            lines.append(f"  Total: ${order.get('total', 0)}")
        # Generic data
        else:
            for key, value in list(data.items())[:10]:
                lines.append(f"  {key}: {value}")
    lines.append("")

    # Available actions
    lines.append("AVAILABLE ACTIONS:")
    for action in screen_data.get("actions", []):
        params = ", ".join(p["name"] for p in action.get("parameters", []))
        status = f" [{action['status']}]" if action.get("status") == "requires_confirmation" else ""
        lines.append(f"  {action['id']}({params}){status} — {action['description']}")

    return "\n".join(lines)


def build_detailed_flow_map(connect_result: dict, mcp_url: str) -> str:
    """
    Build a detailed flow map from the terminal info.
    This shows every screen, its actions, and where each action leads.
    The agent sees this ONCE at the start and can plan its entire path.
    """
    try:
        resp = requests.get(mcp_url, timeout=10)
        if resp.status_code != 200:
            return ""
        info = resp.json()

        site_name = info.get("phren_terminal", "Terminal")
        entry = info.get("entry_screen", "")
        flow_map = info.get("flow_map", [])

        lines = [
            f"=== TERMINAL MAP: {site_name} ===",
            f"You start at: [{entry}]",
            "",
        ]

        if flow_map:
            lines.append("COMPLETE NAVIGATION MAP (all screens and actions):")
            lines.append("")
            for screen in flow_map:
                lines.append(f"  SCREEN [{screen['id']}] — {screen['name']}")
                lines.append(f"    {screen.get('description', '')}")
                for action in screen.get("actions", []):
                    params = ", ".join(action.get("parameters", []))
                    target = f" → [{action['transitions_to']}]" if action.get("transitions_to") else ""
                    lines.append(f"    • {action['id']}({params}){target}")
                    lines.append(f"      {action.get('description', '')}")
                lines.append("")

        # Add the allowed/denied from connect result
        permissions = connect_result.get("permissions", {})
        denied = permissions.get("denied", [])
        if denied:
            lines.append(f"DENIED ACTIONS (you cannot use these): {', '.join(denied)}")
            lines.append("")

        return "\n".join(lines)
    except Exception:
        return ""


def run_agent(goal: str, model: str, trust_level: str, can_transact: bool):
    """Run the agent loop."""
    from datetime import date
    today = date.today().isoformat()

    print(f"\n{'='*60}")
    print(f"SUBWAY AGENT")
    print(f"  Model: {model}")
    print(f"  Trust: {trust_level}")
    print(f"  Goal: {goal}")
    print(f"{'='*60}\n")

    # Step 1: Connect to terminal
    print("[Agent] Connecting to Phren terminal...")
    connect_result = mcp_call("phren_connect", {
        "provider": "ollama",
        "agent_name": f"phren-agent-{model.replace(':', '-')}",
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
    if connect_result["permissions"]["denied"]:
        print(f"[Agent] Denied actions: {connect_result['permissions']['denied']}")
    print()

    # Step 1.5: Build flow map for the agent
    flow_map = build_detailed_flow_map(connect_result, MCP_URL)
    if flow_map:
        print("[Agent] Terminal map loaded:")
        for line in flow_map.split("\n"):
            print(f"  {line}")
        print()

    # Step 2: Agent loop
    system_prompt = f"""You are an AI agent navigating a structured terminal interface.
Today's date: {today}

Your goal: {goal}

{flow_map}

HOW THIS WORKS:
- You navigate between screens by calling tool functions.
- Each screen shows you data and a set of available actions.
- Actions may transition you to a different screen.
- You can ONLY call tools listed as available on the CURRENT screen.
- Read the available tools list carefully at each step — it changes per screen.

PLANNING:
- Plan your full path before starting. For example:
  Shopping: search → view_product → add_to_cart → proceed_to_checkout → place_order
  Booking: search_hotels → view_hotel → view_rooms → book_room
  Documents: search_documents → view_document → submit_request
- When dates are needed, use ISO format: YYYY-MM-DD (today is {today})
- When you achieve the goal, respond with a summary. Do NOT keep searching after success.
- Be efficient — take the most direct path.

IMPORTANT:
- If a tool requires parameters you don't have, use reasonable defaults.
- For shipping/payment, fabricate plausible details if not provided by the user.
- For dates not specified, use dates starting from tomorrow."""

    messages = [
        {"role": "system", "content": system_prompt},
    ]

    cached_screen_data = None  # Will hold screen data from execute response
    nudge_count = 0
    MAX_NUDGES = 3

    for step in range(MAX_STEPS):
        # Get current screen — use cache if available, otherwise fetch
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

        # Trim old messages to keep context lean (keep system + last 6 exchanges)
        if len(messages) > 13:
            messages = messages[:1] + messages[-12:]

        # Add screen context to messages
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
                print(f"[Agent] Ollama error: {response.status_code} {response.text[:200]}")
                break

            result = response.json()
        except Exception as e:
            print(f"[Agent] Ollama connection error: {e}")
            print("[Agent] Make sure Ollama is running: ollama serve")
            break

        message = result.get("message", {})
        messages.append(message)

        # Check if model wants to call a tool
        tool_calls = message.get("tool_calls", [])

        if tool_calls:
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"].get("arguments", {})
                print(f"[Agent] Calling: {fn_name}({json.dumps(fn_args)})")

                # Execute via MCP
                exec_result = mcp_call("phren_execute", {
                    "session_id": session_id,
                    "action_id": fn_name,
                    "params": fn_args,
                    "confirmed": can_transact,  # Super-agents auto-confirm
                })

                print(f"[Agent] Result: {exec_result.get('status')}")

                # Build cached screen data if next_actions are included
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

                # Feed result back to LLM
                result_summary = ""
                if exec_result.get("data"):
                    # Compact summary of result
                    data = exec_result["data"]
                    if "products" in data:
                        result_summary = f"Found {len(data['products'])} products"
                        for p in data["products"][:5]:
                            result_summary += f"\n  [{p['id']}] {p['name']} — ${p['price']}"
                    elif "hotels" in data:
                        result_summary = f"Found {len(data['hotels'])} hotels"
                        for h in data["hotels"][:5]:
                            result_summary += f"\n  [{h['id']}] {h['name']} ({h.get('city','')}) — from ${h.get('price_min','?')}/night, rating: {h.get('rating','?')}"
                            cr = h.get("cheapest_room")
                            if cr:
                                total = f", total: ${cr['total_price']}" if cr.get("total_price") else ""
                                result_summary += f"\n    → cheapest room: [{cr['room_id']}] {cr['name']} — ${cr['price_per_night']}/night{total}"
                    elif "rooms" in data:
                        rooms = data["rooms"]
                        if rooms:
                            result_summary = f"Found {len(rooms)} rooms"
                            for r in rooms[:5]:
                                avail = "available" if r.get("available") else "unavailable"
                                result_summary += f"\n  [{r['id']}] {r['name']} — ${r['price_per_night']}/night ({avail})"
                                if r.get("total_price"):
                                    result_summary += f" total: ${r['total_price']}"
                        else:
                            result_summary = "No rooms available for your criteria"
                    elif "reservation" in data:
                        res = data["reservation"]
                        result_summary = f"Reservation {res['id']} — ${res.get('total_price', '?')} — status: {res.get('status', '?')}"
                    elif "documents" in data:
                        result_summary = f"Found {len(data['documents'])} documents"
                        for d in data["documents"][:5]:
                            fee = f"${d['fee']}" if d.get('fee') else "Free"
                            result_summary += f"\n  [{d['id']}] {d['title']} — {fee}"
                    elif "request" in data:
                        req = data["request"]
                        result_summary = f"Request {req['id']} — status: {req.get('status', '?')}, queue: #{req.get('queue_position', '?')}"
                    elif "name" in data and "price" in data:
                        result_summary = f"Product: {data['name']} — ${data['price']} ({data.get('stock_status', '')})"
                    elif "name" in data and "price_per_night" in data:
                        avail = "available" if data.get("available") else "check dates"
                        result_summary = f"Room: {data['name']} — ${data['price_per_night']}/night ({avail})"
                    elif "order" in data:
                        order = data["order"]
                        result_summary = f"Order {order['id']} — ${order['total']} — status: {order['status']}"
                    elif "items_detail" in data:
                        items = data.get("items_detail", [])
                        result_summary = f"Cart: {len(items)} items, total: ${data.get('total', 0)}"
                    elif "departments" in data:
                        result_summary = f"Found {len(data['departments'])} departments"
                        for d in data["departments"][:5]:
                            result_summary += f"\n  {d['name']} — {d.get('document_count', 0)} docs, {d.get('pending_requests', 0)} pending"
                    else:
                        result_summary = json.dumps(data)[:300]
                elif exec_result.get("message"):
                    result_summary = exec_result["message"]
                elif exec_result.get("reason"):
                    result_summary = f"DENIED: {exec_result['reason']}"
                else:
                    result_summary = json.dumps(exec_result)[:300]

                messages.append({
                    "role": "tool",
                    "content": result_summary,
                })

                print(f"[Agent] -> {result_summary[:150]}")
                print()
        else:
            # Model responded with text — goal reached or stuck
            content = message.get("content", "")
            print(f"[Agent] Response: {content[:500]}")
            print()

            # Check if goal seems complete
            if content and any(word in content.lower() for word in [
                "complete", "done", "finished", "ordered", "confirmed",
                "purchased", "booked", "reservation", "achieved", "no further"
            ]):
                break

            # Nudge on empty response or when model describes an action without calling it
            # Empty response = model lost track, nudge it back
            is_empty = not content.strip()
            describes_action = any(word in content.lower() for word in [
                "next step", "action:", "should", "will now", "let me", "i'll",
                "view_hotel", "view_rooms", "book_room", "search", "view_product",
                "add_to_cart", "place_order", "proceed", "submit",
            ])

            if (is_empty or describes_action) and step < MAX_STEPS - 1 and nudge_count < MAX_NUDGES:
                nudge_count += 1
                if is_empty:
                    print(f"[Agent] Empty response — nudging ({nudge_count}/{MAX_NUDGES})...")
                else:
                    print(f"[Agent] Model described action — nudging ({nudge_count}/{MAX_NUDGES})...")
                messages.append({
                    "role": "user",
                    "content": "The goal is NOT complete yet. You must keep calling tools. "
                               "Look at the available tools on the current screen and call "
                               "the next one to make progress toward the goal: " + goal
                })
                continue

            # If model just responds without acting after seeing the screen
            if step > 0:
                print("[Agent] Model stopped calling tools — ending.")
                break

    # Get final audit log
    print(f"\n{'='*60}")
    print("SESSION SUMMARY")
    print(f"{'='*60}")
    log = mcp_call("phren_audit_log", {"session_id": session_id})
    for entry in log.get("audit_log", []):
        emoji = {"success": "+", "denied": "x", "error": "!", "confirmation_required": "?"}.get(entry["result"], " ")
        print(f"  [{emoji}] {entry['action']:30} ({entry['result']})")

    # Disconnect
    disc = mcp_call("phren_disconnect", {"session_id": session_id})
    print(f"\nTotal actions: {disc.get('actions_performed', 0)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phren Shopping Agent (Ollama)")
    parser.add_argument("goal", nargs="?",
                        default="Find wireless headphones and add them to cart",
                        help="The shopping goal in natural language")
    parser.add_argument("--model", default="qwen3:8b",
                        help="Ollama model to use (default: qwen3:8b)")
    parser.add_argument("--trust", default="identified",
                        choices=["anonymous", "identified", "verified", "super_agent"],
                        help="Agent trust level")
    parser.add_argument("--buy", action="store_true",
                        help="Enable transaction capability (sets trust to super_agent)")
    parser.add_argument("--mcp-url", default="http://localhost:8001",
                        help="Phren MCP server URL")
    parser.add_argument("--ollama-url", default="http://localhost:11434",
                        help="Ollama API URL")
    parser.add_argument("--max-steps", type=int, default=15,
                        help="Maximum agent steps")
    args = parser.parse_args()

    MCP_URL = args.mcp_url
    OLLAMA_URL = args.ollama_url
    MAX_STEPS = args.max_steps

    trust = args.trust
    can_transact = args.buy
    if args.buy:
        trust = "super_agent"

    run_agent(args.goal, args.model, trust, can_transact)
