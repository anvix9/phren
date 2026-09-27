"""
Phren Phase 5b — LLM Agent Eval Runner

Runs actual LLM agents against simulation servers through the terminal.
Measures pass@1, steps-to-completion, invalid-action rate, and cost.

Architecture:
  1. Start simulation server (FastAPI on localhost)
  2. Create terminal from contract
  3. Connect agent
  4. Loop: show screen to LLM → LLM picks action → execute → repeat
  5. Check ground truth against simulation server state
  6. Collect metrics

Usage:
  python3 -m evals.agent_eval --task shopping-buy-cheapest --model qwen3:8b --trials 3
  python3 -m evals.agent_eval --suite all --model qwen3:8b --trials 5
  python3 -m evals.agent_eval --list  # show available tasks

Requires:
  - Ollama running locally (ollama serve)
  - pip install requests
"""
import json
import time
import uuid
import argparse
import subprocess
import sys
import os
import signal
import requests as http_requests
from dataclasses import dataclass, field
from typing import Optional, Callable
from pathlib import Path
from datetime import datetime, timezone

from phren.contract.schema import (
    PhrenContract, AgentProfile, AgentTrust, AgentCapabilities,
)
from phren.terminal.engine import PhrenTerminal


# ══════════════════════════════════════════════
# TASK DEFINITION
# ══════════════════════════════════════════════

@dataclass
class AgentTask:
    """A task for an LLM agent to complete."""
    id: str
    site: str                               # simulation name (e.g., "shopping")
    goal: str                               # natural language goal for the agent
    contract_path: str                      # path to contract JSON
    simulation_dir: str                     # path to simulation api/ directory
    simulation_port: int = 8100             # port for the simulation server
    max_steps: int = 15                     # max actions before giving up
    ground_truth: Optional[Callable] = None # function(sim_url) → bool
    setup: Optional[Callable] = None        # function(sim_url) to set up state before task
    category: str = "general"
    description: str = ""
    agent_purpose: str = "purchase"          # must match contract's allowed purposes


# ══════════════════════════════════════════════
# AGENT RESULT
# ══════════════════════════════════════════════

@dataclass
class AgentRun:
    """Result of one agent run."""
    task_id: str
    model: str
    trial: int
    passed: bool
    steps: int
    invalid_actions: int
    actions_taken: list[dict] = field(default_factory=list)
    ground_truth_passed: bool = False
    elapsed_seconds: float = 0.0
    error: str = ""
    final_screen: str = ""


@dataclass
class AgentTaskResult:
    """Aggregated result across trials for one task."""
    task_id: str
    model: str
    trials: int
    passed: int                     # pass@1 count
    pass_rate: float                # pass@1 rate
    avg_steps: float
    avg_invalid: float
    avg_elapsed: float
    runs: list[AgentRun] = field(default_factory=list)


# ══════════════════════════════════════════════
# OLLAMA CLIENT
# ══════════════════════════════════════════════

def check_ollama() -> bool:
    """Check if Ollama is running."""
    try:
        r = http_requests.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def list_ollama_models() -> list[str]:
    """List available Ollama models."""
    try:
        r = http_requests.get("http://localhost:11434/api/tags", timeout=3)
        data = r.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def ollama_generate(model: str, prompt: str, system: str = "") -> str:
    """Call Ollama chat API and return the response text.
    Uses chat (not generate) for better instruction following.
    Forces JSON format to avoid free-text responses.
    Strips <think> tags that Qwen3 models emit.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 2048,    # generous — thinking models need room
        },
    }

    try:
        r = http_requests.post(
            "http://localhost:11434/api/chat",
            json=payload,
            timeout=300,
        )
        data = r.json()
        content = data.get("message", {}).get("content", "")

        # Strip <think>...</think> blocks (Qwen3 thinking mode)
        content = _strip_think_tags(content)

        return content.strip()
    except Exception as e:
        return f"ERROR: {e}"


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from model output."""
    import re
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'</?think>', '', text)
    return text.strip()


def _compact_screen(screen: dict) -> str:
    """Format a screen response for the LLM.
    Shows: screen name, flow hint, key data, actions grouped by role (primary first).
    """
    parts = []

    # Screen name
    screen_info = screen.get("screen", {})
    if isinstance(screen_info, dict):
        name = screen_info.get("name", "Unknown")
    else:
        name = str(screen_info)
    parts.append(f"[{name}]")

    # Flow hint (from contract)
    flow_hint = screen.get("flow_hint", "")
    if not flow_hint and isinstance(screen_info, dict):
        flow_hint = screen_info.get("flow_hint", "")
    if flow_hint:
        parts.append(f"HINT: {flow_hint}")

    # Data (compact — just keys and short values)
    data = screen.get("data", {})
    if data and isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list):
                items = val[:5]
                for item in items:
                    if isinstance(item, dict):
                        compact = {k: v for k, v in item.items()
                                   if k in ("id", "name", "title", "price", "status", "email", "type")}
                        parts.append(f"  {key}: {json.dumps(compact)}")
                    else:
                        parts.append(f"  {key}: {item}")
                if len(val) > 5:
                    parts.append(f"  ... and {len(val) - 5} more")
            elif isinstance(val, dict):
                compact = {k: v for k, v in val.items()
                           if k in ("id", "name", "title", "price", "status", "token", "email")}
                parts.append(f"  {key}: {json.dumps(compact)}")
            else:
                parts.append(f"  {key}: {val}")

    # Actions grouped by role
    actions = screen.get("actions", [])
    if actions:
        # Group by role
        primary = [a for a in actions if a.get("role") == "primary"]
        secondary = [a for a in actions if a.get("role") == "secondary"]
        navigation = [a for a in actions if a.get("role") == "navigation"]
        # Fallback: if no roles assigned, treat all as primary
        if not primary and not secondary and not navigation:
            primary = actions

        if primary:
            parts.append("► Primary actions (use these to progress):")
            for a in primary:
                _append_action(parts, a)

        if secondary:
            parts.append("○ Other actions:")
            for a in secondary:
                _append_action(parts, a)

        if navigation:
            parts.append("← Navigation:")
            for a in navigation:
                _append_action(parts, a)

    return "\n".join(parts)


def _append_action(parts: list, a: dict):
    """Append one action line to the parts list."""
    action_id = a.get("id", "?")
    desc = a.get("description", "")
    params = a.get("parameters", [])
    param_names = [p.get("name", "?") for p in params] if params else []
    param_str = f" (params: {', '.join(param_names)})" if param_names else ""
    parts.append(f"  - {action_id}: {desc}{param_str}")


# ══════════════════════════════════════════════
# SIMULATION SERVER MANAGEMENT
# ══════════════════════════════════════════════

def start_simulation(sim_dir: str, port: int) -> subprocess.Popen:
    """Start a simulation FastAPI server."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=sim_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )

    # Wait for server to be ready
    for _ in range(30):
        # Check if process died
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(
                f"Simulation at {sim_dir} exited immediately.\n"
                f"stderr: {stderr[:500]}"
            )
        try:
            r = http_requests.get(f"http://localhost:{port}/", timeout=1)
            if r.status_code < 500:
                return proc
        except Exception:
            pass
        time.sleep(0.5)

    # Timed out — capture any error output
    stderr = proc.stderr.read().decode() if proc.stderr else ""
    raise RuntimeError(
        f"Simulation at {sim_dir} failed to start on port {port}\n"
        f"stderr: {stderr[:500]}"
    )


def stop_simulation(proc: subprocess.Popen):
    """Stop a simulation server."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ══════════════════════════════════════════════
# AGENT LOOP
# ══════════════════════════════════════════════

SYSTEM_PROMPT = """You navigate a website terminal to complete a task.
Each turn you see: your path so far, the current screen, and available actions.

Reply with ONE JSON object. No other text.

{"action": "action_id", "params": {"key": "value"}}
{"action": "DONE"} when the task is complete.

Rules:
- Use ONLY actions from "Available actions"
- Follow the SUGGESTED NEXT step when shown
- ► Primary actions move you forward. Use them first.
- ← Navigation goes backward. Avoid unless stuck.
- ○ Secondary actions are optional. Skip unless the task requires them.
- Use IDs from your path data in subsequent actions
- If you see _confirmation_needed, resend with "confirmed": true
- Never undo progress (avoid remove/delete/cancel actions)"""


@dataclass
class PathEntry:
    """One step in the agent's path through the terminal."""
    step: int
    action: str
    status: str             # "ok", "error", "denied", "parse_error"
    screen_after: str       # screen name after this action
    data_keys: list[str]    # what data was returned (product ids, token, etc.)
    data_summary: str       # short summary of key findings


def _build_path_trace(path: list[PathEntry], task_goal: str) -> str:
    """Build the path trace for the prompt."""
    if not path:
        return ""

    lines = ["PATH SO FAR:"]
    for entry in path:
        icon = "✅" if entry.status == "ok" else "❌"
        summary = f" → {entry.data_summary}" if entry.data_summary else ""
        lines.append(f"  {icon} [{entry.step}] {entry.action}{summary}")

    return "\n".join(lines)


def _summarize_data(data, action_id: str) -> tuple[list[str], str]:
    """Extract key data and a short summary from an action result."""
    if not data or not isinstance(data, dict):
        return [], ""

    keys = list(data.keys())[:5]
    summary = ""

    # Login → extract token presence
    if action_id == "login" and data.get("token"):
        summary = "got auth token"
        return keys, summary

    # Search → extract product/room/document list
    for list_key in ("products", "rooms", "documents", "items", "results"):
        if list_key in data and isinstance(data[list_key], list):
            items = data[list_key]
            if items:
                names = []
                for item in items[:3]:
                    if isinstance(item, dict):
                        name = item.get("name", item.get("title", ""))
                        price = item.get("price", item.get("rate", ""))
                        item_id = item.get("id", "")
                        if name:
                            entry = f"{name}"
                            if price:
                                entry += f" (${price})"
                            if item_id:
                                entry += f" [id:{item_id}]"
                            names.append(entry)
                summary = f"found {len(items)}: " + ", ".join(names)
                return keys, summary

    # Cart → summarize items
    if "cart" in data or "items" in data:
        cart = data.get("cart", data)
        items = cart.get("items", []) if isinstance(cart, dict) else []
        if items:
            summary = f"cart has {len(items)} item(s)"
        else:
            summary = "cart is empty"
        return keys, summary

    # Order → confirm placement
    if data.get("order_id") or data.get("booking_id") or data.get("request_id"):
        order_id = data.get("order_id", data.get("booking_id", data.get("request_id")))
        summary = f"confirmed! id={order_id}"
        return keys, summary

    # Generic: show first few key-value pairs
    for k, v in data.items():
        if isinstance(v, str) and len(v) < 50:
            summary = f"{k}={v}"
            break
        elif isinstance(v, (int, float)):
            summary = f"{k}={v}"
            break

    return keys, summary


def _suggest_next(path: list[PathEntry], screen: dict, task_goal: str) -> str:
    """Suggest the next action based on roles and flow hints. Never references specific action names."""
    done_actions = {e.action for e in path}

    # Use flow_hint from the contract (already domain-specific)
    flow_hint = ""
    if isinstance(screen.get("screen"), dict):
        flow_hint = screen["screen"].get("flow_hint", "") or ""

    # Find the first primary action the agent hasn't done on this screen
    actions = screen.get("actions", [])
    primary_unused = [a for a in actions
                      if a.get("role") == "primary" and a["id"] not in done_actions]

    if primary_unused:
        next_action = primary_unused[0]
        params = [p["name"] for p in next_action.get("parameters", []) if p.get("required")]
        param_hint = f" (needs: {', '.join(params)})" if params else ""
        suggestion = f"SUGGESTED NEXT: {next_action['id']}{param_hint}"
        if flow_hint:
            suggestion += f"\nHINT: {flow_hint}"
        return suggestion

    # All primaries done — suggest first primary even if done (might need different params)
    primary_any = [a for a in actions if a.get("role") == "primary"]
    if primary_any and flow_hint:
        return f"HINT: {flow_hint}"

    if flow_hint:
        return f"HINT: {flow_hint}"

    return ""


def run_agent(
    task: AgentTask,
    model: str,
    terminal: PhrenTerminal,
    sim_url: str,
    verbose_hint: bool = False,
) -> AgentRun:
    """Run one agent trial against a task."""
    start = time.monotonic()
    steps = 0
    invalid_actions = 0
    actions_taken = []

    # Connect — purpose comes from task config or defaults to 'purchase'
    agent_config = {
        "provider": "eval",
        "agent_name": f"eval-{model}",
        "trust_level": AgentTrust.VERIFIED,
        "purpose": task.agent_purpose if hasattr(task, 'agent_purpose') and task.agent_purpose else "purchase",
        "capabilities": AgentCapabilities(can_transact=True),
        "delegated_by_user": True,  # eval is explicitly delegated by the human running it
    }
    agent_profile = AgentProfile(**agent_config)
    connect_result = terminal.connect(agent_profile)
    if connect_result["status"] != "connected":
        error_msg = f"Connect failed: {connect_result.get('reason', connect_result)}"
        if verbose_hint:
            print(f"\n    ⚠ {error_msg}")
        return AgentRun(
            task_id=task.id, model=model, trial=0, passed=False,
            steps=0, invalid_actions=0, error=error_msg,
        )

    session_id = connect_result["session_id"]
    last_result_data = None
    path: list[PathEntry] = []   # breadcrumb trail

    # Agent loop
    for step in range(task.max_steps):
        steps += 1

        # Get current screen
        screen = terminal.get_screen(session_id)
        screen_compact = _compact_screen(screen)

        # Build path trace
        path_trace = _build_path_trace(path, task.goal)

        # Suggest next action
        suggestion = _suggest_next(path, screen, task.goal)

        # Include key data from last successful action (IDs the agent needs)
        data_context = ""
        if last_result_data:
            data_compact = json.dumps(last_result_data, default=str)
            if len(data_compact) > 800:
                data_compact = data_compact[:800] + "..."
            data_context = f"\nLast result data:\n{data_compact}\n"

        # Build prompt
        prompt = (
            f"TASK: {task.goal}\n\n"
            f"{path_trace}\n"
            f"{data_context}\n"
            f"SCREEN:\n{screen_compact}\n\n"
            f"{suggestion}\n"
            f"Step {step + 1}/{task.max_steps}. Next action:"
        )

        # Ask LLM
        response = ollama_generate(model, prompt, system=SYSTEM_PROMPT)

        # Parse LLM response
        action_data = _parse_agent_response(response)

        if action_data is None:
            invalid_actions += 1
            actions_taken.append({
                "step": step,
                "raw": response[:500] if response else "(EMPTY RESPONSE)",
                "parsed": None,
                "result": "parse_error",
            })
            continue

        if action_data.get("action") == "DONE":
            actions_taken.append({"step": step, "action": "DONE", "result": "agent_declared_done"})
            break

        action_id = action_data.get("action", "")
        params = action_data.get("params", {})

        # Extract confirmed flag from params (agent sends it as a param)
        confirmed = params.pop("confirmed", False)
        if isinstance(confirmed, str):
            confirmed = confirmed.lower() in ("true", "1", "yes")

        # Execute action
        try:
            result = terminal.execute_action(session_id, action_id, params, confirmed=confirmed)
            status = result.get("status", "unknown")
        except Exception as e:
            result = {"status": "error", "message": str(e)}
            status = "error"

        # Capture data for next prompt (continuity)
        if status == "ok" and result.get("data"):
            last_result_data = result["data"]
        elif status == "confirmation_required":
            last_result_data = {
                "_confirmation_needed": True,
                "_message": result.get("message", "Action requires confirmation."),
                "_action_to_confirm": action_id,
                "_params": params,
                "_instruction": f"Resend the same action with confirmed=true: "
                               f'{{"action": "{action_id}", "params": {{...same params..., "confirmed": true}}}}'
            }
        elif status in ("denied", "error", "auth_required"):
            last_result_data = {
                "_error": status,
                "_message": result.get("reason", result.get("message", "")),
                "_hint": "Try a different action or check your parameters.",
            }
            invalid_actions += 1

        # Build path trace entry
        data_for_summary = result.get("data") if status == "ok" else None
        data_keys, data_summary = _summarize_data(data_for_summary, action_id)
        screen_after = ""
        if isinstance(result.get("screen_name"), str):
            screen_after = result["screen_name"]
        elif isinstance(result.get("screen"), str):
            screen_after = result["screen"]

        path.append(PathEntry(
            step=step,
            action=action_id,
            status=status,
            screen_after=screen_after,
            data_keys=data_keys,
            data_summary=data_summary,
        ))

        actions_taken.append({
            "step": step, "action": action_id, "params": params,
            "confirmed": confirmed,
            "result": status,
            "reason": result.get("reason", result.get("message", "")),
            "detail": str(result)[:300],
        })

    elapsed = time.monotonic() - start

    # Check ground truth
    gt_passed = False
    if task.ground_truth:
        try:
            gt_passed = task.ground_truth(sim_url)
        except Exception as e:
            gt_passed = False

    # Disconnect
    try:
        terminal.disconnect(session_id)
    except Exception:
        pass

    return AgentRun(
        task_id=task.id, model=model, trial=0,
        passed=gt_passed, steps=steps, invalid_actions=invalid_actions,
        actions_taken=actions_taken, ground_truth_passed=gt_passed,
        elapsed_seconds=elapsed,
        final_screen=json.dumps(screen, default=str)[:500] if 'screen' in dir() else "",
    )


FLAT_SYSTEM_PROMPT = """You navigate a website using tools. You see a map of the site and all available tools.

Reply with ONE JSON object per turn. No other text.

To act: {"action": "tool_id", "params": {"key": "value"}}
To confirm: {"action": "tool_id", "params": {"...same params...", "confirmed": true}}
When done: {"action": "DONE"}

Rules:
- Follow the path shown in the flow map
- Use tool IDs exactly as listed
- If a tool needs no params: {"action": "tool_id", "params": {}}
- After search results, use the IDs from the results in subsequent actions
- If you see _confirmation_needed, resend with confirmed=true"""


def run_agent_flat(
    task: AgentTask,
    model: str,
    terminal,  # FlatTerminal
    sim_url: str,
    verbose_hint: bool = False,
) -> AgentRun:
    """Run an agent using the flat terminal — map shown once, then execute tools."""
    start = time.monotonic()
    steps = 0
    invalid_actions = 0
    actions_taken = []

    # Connect
    agent_profile = AgentProfile(
        provider="eval",
        agent_name=f"eval-{model}",
        trust_level=AgentTrust.VERIFIED,
        purpose=task.agent_purpose if hasattr(task, 'agent_purpose') and task.agent_purpose else "purchase",
        capabilities=AgentCapabilities(can_transact=True),
        delegated_by_user=True,
    )
    connect_result = terminal.connect(agent_profile)
    if connect_result["status"] != "connected":
        return AgentRun(
            task_id=task.id, model=model, trial=0, passed=False,
            steps=0, invalid_actions=0,
            error=f"Connect failed: {connect_result.get('reason', '')}",
        )

    session_id = connect_result["session_id"]
    overview = connect_result.get("overview", terminal.get_overview(session_id))

    # Format the map (shown once at start)
    map_text = _format_overview(overview)
    last_result = None

    # Agent loop
    for step in range(task.max_steps):
        steps += 1

        # Build prompt — map on first turn, then just state + last result
        if step == 0:
            prompt = (
                f"TASK: {task.goal}\n\n"
                f"{map_text}\n\n"
                f"Start now. First action:"
            )
        else:
            state = terminal.get_overview(session_id).get("state", {})
            state_text = json.dumps(state, default=str)
            result_text = ""
            if last_result:
                result_text = f"\nLast result:\n{json.dumps(last_result, default=str)[:600]}\n"

            prompt = (
                f"TASK: {task.goal}\n"
                f"{result_text}\n"
                f"State: {state_text}\n\n"
                f"Step {step + 1}/{task.max_steps}. Next action:"
            )

        # Ask LLM
        response = ollama_generate(model, prompt, system=FLAT_SYSTEM_PROMPT)
        action_data = _parse_agent_response(response)

        if action_data is None:
            invalid_actions += 1
            actions_taken.append({
                "step": step, "raw": response[:500] if response else "(EMPTY RESPONSE)",
                "parsed": None, "result": "parse_error",
            })
            continue

        if action_data.get("action") == "DONE":
            actions_taken.append({"step": step, "action": "DONE", "result": "agent_declared_done"})
            break

        action_id = action_data.get("action", "")
        params = action_data.get("params", {})
        confirmed = params.pop("confirmed", False)
        if isinstance(confirmed, str):
            confirmed = confirmed.lower() in ("true", "1", "yes")

        # Execute
        try:
            result = terminal.execute(session_id, action_id, params, confirmed=confirmed)
            status = result.get("status", "unknown")
        except Exception as e:
            result = {"status": "error", "message": str(e)}
            status = "error"

        last_result = result.get("data") if status == "ok" else result

        if status in ("denied", "error"):
            invalid_actions += 1

        actions_taken.append({
            "step": step, "action": action_id, "params": params,
            "confirmed": confirmed, "result": status,
            "reason": result.get("reason", result.get("message", "")),
            "detail": str(result)[:300],
        })

    elapsed = time.monotonic() - start

    # Check ground truth
    gt_passed = False
    if task.ground_truth:
        try:
            gt_passed = task.ground_truth(sim_url)
        except Exception:
            gt_passed = False

    try:
        terminal.disconnect(session_id)
    except Exception:
        pass

    return AgentRun(
        task_id=task.id, model=model, trial=0,
        passed=gt_passed, steps=steps, invalid_actions=invalid_actions,
        actions_taken=actions_taken, ground_truth_passed=gt_passed,
        elapsed_seconds=elapsed,
    )


def _format_overview(overview: dict) -> str:
    """Format the flat terminal overview for the LLM."""
    parts = [f"SITE: {overview.get('site', '?')}"]

    # Flow map
    paths = overview.get("flow_map", [])
    if paths:
        parts.append("\nFLOW MAP (follow these paths):")
        for p in paths:
            parts.append(f"  {p}")

    # Hints
    hints = overview.get("hints", [])
    if hints:
        parts.append("\nHINTS:")
        for h in hints:
            parts.append(f"  {h}")

    # Tools
    tools = overview.get("tools", [])
    if tools:
        primary = [t for t in tools if t.get("role") == "primary"]
        others = [t for t in tools if t.get("role") != "primary"]

        parts.append("\n► PRIMARY TOOLS:")
        for t in primary:
            params = [f"{p['name']}{'*' if p.get('required') else ''}" for p in t.get("params", [])]
            param_str = f"({', '.join(params)})" if params else "()"
            parts.append(f"  {t['id']}{param_str}: {t['description']}")

        if others:
            parts.append("\n○ OTHER TOOLS:")
            for t in others:
                parts.append(f"  {t['id']}: {t['description']}")

    # State
    state = overview.get("state", {})
    if state:
        parts.append(f"\nCURRENT STATE: {json.dumps(state, default=str)}")

    return "\n".join(parts)


def _parse_agent_response(response: str) -> Optional[dict]:
    """Parse the LLM's action response. Handles:
    - Direct JSON
    - JSON inside <think>...</think> blocks (strips thinking, finds JSON after)
    - JSON inside markdown ```json ... ```
    - JSON embedded in reasoning text
    - Action names without JSON wrapper
    """
    if not response or not response.strip():
        return None

    # Step 1: Strip <think>...</think> blocks
    import re
    cleaned = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
    cleaned = re.sub(r'</?think>', '', cleaned)
    cleaned = cleaned.strip()

    if not cleaned:
        return None

    # Step 2: Try direct JSON parse
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Step 3: Try extracting from markdown code block
    code_blocks = re.findall(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned, re.DOTALL)
    for block in code_blocks:
        try:
            result = json.loads(block)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            continue

    # Step 4: Find any JSON object in the text
    # Look for { ... } patterns, try each
    brace_depth = 0
    start = -1
    for i, ch in enumerate(cleaned):
        if ch == '{':
            if brace_depth == 0:
                start = i
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
            if brace_depth == 0 and start >= 0:
                candidate = cleaned[start:i+1]
                try:
                    result = json.loads(candidate)
                    if isinstance(result, dict) and "action" in result:
                        return result
                except json.JSONDecodeError:
                    pass
                start = -1

    # Step 5: Try to find action name as plain text
    # e.g., "I'll use view_product with product_id prod_002"
    action_pattern = re.search(
        r'(?:action|use|call|execute)["\s:]*["\s]*(\w+)',
        cleaned, re.IGNORECASE
    )
    if action_pattern:
        action_name = action_pattern.group(1)
        # Try to find params
        param_match = re.search(r'(?:param|with|id)["\s:]*["\s]*(\w+)', cleaned, re.IGNORECASE)
        params = {}
        if param_match:
            # Guess param name from action
            if "product" in action_name:
                params["product_id"] = param_match.group(1)
            elif "search" in action_name:
                params["q"] = param_match.group(1)
        return {"action": action_name, "params": params}

    return None


# ══════════════════════════════════════════════
# TTL MODE AGENT LOOP
# ══════════════════════════════════════════════

TTL_SYSTEM_PROMPT = """You navigate a website terminal. Each turn shows your result and the next step.

Reply with ONE JSON object. No other text.

{"action": "action_id", "params": {"key": "value"}}
{"action": "DONE"} when complete.

Use the IDs shown in results. Follow NEXT. Add confirmed=true when CONFIRM is shown."""


def run_agent_ttl(
    task: AgentTask,
    model: str,
    terminal: PhrenTerminal,
    sim_url: str,
    contract: PhrenContract = None,
    verbose_hint: bool = False,
) -> AgentRun:
    """Run an agent using TTL format — minimal tokens per turn."""
    from phren.terminal.presenter import TerminalPresenter

    start = time.monotonic()
    steps = 0
    invalid_actions = 0
    actions_taken = []

    presenter = TerminalPresenter(contract or terminal.contract)

    # Connect
    agent_profile = AgentProfile(
        provider="eval",
        agent_name=f"eval-{model}",
        trust_level=AgentTrust.VERIFIED,
        purpose=task.agent_purpose if hasattr(task, 'agent_purpose') and task.agent_purpose else "purchase",
        capabilities=AgentCapabilities(can_transact=True),
        delegated_by_user=True,
    )
    connect_result = terminal.connect(agent_profile)
    if connect_result["status"] != "connected":
        return AgentRun(
            task_id=task.id, model=model, trial=0, passed=False,
            steps=0, invalid_actions=0,
            error=f"Connect failed: {connect_result.get('reason', '')}",
        )

    session_id = connect_result["session_id"]
    done_actions = set()
    last_result_data = None
    last_action_id = ""
    last_status = ""

    # State tracking (mirrors what flat_engine does)
    agent_state = {"logged_in": False, "cart_items": [], "last_search_results": [], "current_step": "start"}

    for step_num in range(task.max_steps):
        steps += 1

        # Build prompt
        if step_num == 0:
            prompt = presenter.first_turn(goal=task.goal)
        else:
            prompt = presenter.turn(
                last_action=last_action_id,
                status=last_status,
                result_data=last_result_data,
                state=agent_state,
                done_actions=done_actions,
            )

        # Ask LLM
        response = ollama_generate(model, prompt, system=TTL_SYSTEM_PROMPT)
        action_data = _parse_agent_response(response)

        if action_data is None:
            invalid_actions += 1
            actions_taken.append({
                "step": step_num, "raw": response[:500] if response else "(EMPTY RESPONSE)",
                "parsed": None, "result": "parse_error",
            })
            last_status = "parse_error"
            continue

        if action_data.get("action") == "DONE":
            actions_taken.append({"step": step_num, "action": "DONE", "result": "agent_declared_done"})
            break

        action_id = action_data.get("action", "")
        params = action_data.get("params", {})
        confirmed = params.pop("confirmed", False)
        if isinstance(confirmed, str):
            confirmed = confirmed.lower() in ("true", "1", "yes")

        # Execute
        try:
            result = terminal.execute_action(session_id, action_id, params, confirmed=confirmed)
            status = result.get("status", "unknown")
        except Exception as e:
            result = {"status": "error", "message": str(e)}
            status = "error"

        # Update state
        if status == "ok":
            last_result_data = result.get("data")
            done_actions.add(action_id)
            _update_agent_state(agent_state, action_id, params, result.get("data", {}))
        elif status == "confirmation_required":
            last_result_data = result
        else:
            last_result_data = result
            invalid_actions += 1

        last_action_id = action_id
        last_status = status

        actions_taken.append({
            "step": step_num, "action": action_id, "params": params,
            "confirmed": confirmed, "result": status,
            "reason": result.get("reason", result.get("message", "")),
        })

    elapsed = time.monotonic() - start

    # Ground truth
    gt_passed = False
    if task.ground_truth:
        try:
            gt_passed = task.ground_truth(sim_url)
        except Exception:
            gt_passed = False

    try:
        terminal.disconnect(session_id)
    except Exception:
        pass

    return AgentRun(
        task_id=task.id, model=model, trial=0,
        passed=gt_passed, steps=steps, invalid_actions=invalid_actions,
        actions_taken=actions_taken, ground_truth_passed=gt_passed,
        elapsed_seconds=elapsed,
    )


def _update_agent_state(state: dict, action_id: str, params: dict, data: dict):
    """Update tracked state after a successful action."""
    if not isinstance(data, dict):
        return
    if "login" in action_id and (data.get("token") or data.get("access_token")):
        state["logged_in"] = True
    if "add" in action_id and "cart" in action_id:
        pid = params.get("product_id", "")
        state["cart_items"].append(pid)
    if "remove" in action_id:
        pid = params.get("product_id", "")
        state["cart_items"] = [i for i in state["cart_items"] if i != pid]
    for key in ("products", "rooms", "documents", "hotels"):
        items = data.get(key, [])
        if items and isinstance(items, list):
            state["last_search_results"] = [
                {"id": i.get("id"), "name": i.get("name", i.get("title", "")), "price": i.get("price", i.get("rate", ""))}
                for i in items[:5] if isinstance(i, dict)
            ]
    for key in ("order_id", "reservation_id", "request_id"):
        if data.get(key):
            state["current_step"] = "completed"
    for key in ("reservation", "request", "order"):
        if isinstance(data.get(key), dict) and data[key].get("id"):
            state["current_step"] = "completed"


# ══════════════════════════════════════════════
# EVAL RUNNER
# ══════════════════════════════════════════════

def run_eval(
    task: AgentTask,
    model: str,
    trials: int = 3,
    verbose: bool = False,
    flat_mode: bool = False,
    ttl_mode: bool = False,
) -> AgentTaskResult:
    """Run an agent eval: start server, run N trials, collect metrics."""

    # Start simulation
    if verbose:
        print(f"  Starting simulation: {task.simulation_dir}")

    sim_proc = None
    sim_url = f"http://localhost:{task.simulation_port}"

    try:
        runs = []
        for trial in range(trials):
            if verbose:
                print(f"  Trial {trial + 1}/{trials}...", end=" ", flush=True)

            # Restart simulation each trial for fresh state
            if sim_proc:
                stop_simulation(sim_proc)
            sim_proc = start_simulation(task.simulation_dir, task.simulation_port)

            # Load contract and create fresh terminal per trial
            with open(task.contract_path) as f:
                contract_data = json.load(f)
            contract = PhrenContract(**contract_data)

            if flat_mode:
                from phren.terminal.flat_engine import FlatTerminal
                terminal = FlatTerminal(contract, sim_url)
            else:
                terminal = PhrenTerminal(contract, sim_url)

            # Run setup if provided
            if task.setup:
                task.setup(sim_url)

            # Run agent
            if flat_mode:
                run = run_agent_flat(task, model, terminal, sim_url, verbose_hint=verbose)
            elif ttl_mode:
                run = run_agent_ttl(task, model, terminal, sim_url, contract, verbose_hint=verbose)
            else:
                run = run_agent(task, model, terminal, sim_url, verbose_hint=verbose)
            run.trial = trial + 1
            runs.append(run)

            if verbose:
                icon = "✅" if run.passed else "❌"
                print(f"{icon} steps={run.steps} invalid={run.invalid_actions} "
                      f"time={run.elapsed_seconds:.1f}s")

    finally:
        if sim_proc:
            stop_simulation(sim_proc)

    # Aggregate
    passed = sum(1 for r in runs if r.passed)
    avg_steps = sum(r.steps for r in runs) / len(runs) if runs else 0
    avg_invalid = sum(r.invalid_actions for r in runs) / len(runs) if runs else 0
    avg_elapsed = sum(r.elapsed_seconds for r in runs) / len(runs) if runs else 0

    return AgentTaskResult(
        task_id=task.id, model=model, trials=trials,
        passed=passed, pass_rate=passed / trials if trials > 0 else 0,
        avg_steps=avg_steps, avg_invalid=avg_invalid, avg_elapsed=avg_elapsed,
        runs=runs,
    )


# ══════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════

def print_results(results: list[AgentTaskResult]):
    """Print a summary table."""
    print(f"\n{'='*75}")
    print(f"  PHREN AGENT EVAL RESULTS")
    print(f"{'='*75}")
    print(f"  {'Task':<35} {'Model':<15} {'pass@1':>8} {'Steps':>7} {'Invalid':>8} {'Time':>7}")
    print(f"  {'-'*35} {'-'*15} {'-'*8} {'-'*7} {'-'*8} {'-'*7}")

    for r in results:
        rate = f"{r.passed}/{r.trials}"
        print(f"  {r.task_id:<35} {r.model:<15} {rate:>8} {r.avg_steps:>7.1f} "
              f"{r.avg_invalid:>8.1f} {r.avg_elapsed:>6.1f}s")

    print(f"{'='*75}")

    # Overall stats
    total_trials = sum(r.trials for r in results)
    total_passed = sum(r.passed for r in results)
    overall_rate = total_passed / total_trials if total_trials > 0 else 0
    print(f"  Overall: {total_passed}/{total_trials} ({overall_rate*100:.0f}%)")
    print()


def results_to_json(results: list[AgentTaskResult]) -> str:
    """Export results as JSON."""
    return json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": [
            {
                "task_id": r.task_id,
                "model": r.model,
                "trials": r.trials,
                "passed": r.passed,
                "pass_rate": r.pass_rate,
                "avg_steps": r.avg_steps,
                "avg_invalid": r.avg_invalid,
                "avg_elapsed": r.avg_elapsed,
                "runs": [
                    {
                        "trial": run.trial,
                        "passed": run.passed,
                        "steps": run.steps,
                        "invalid_actions": run.invalid_actions,
                        "elapsed_seconds": run.elapsed_seconds,
                        "actions": run.actions_taken,
                    }
                    for run in r.runs
                ],
            }
            for r in results
        ],
    }, indent=2)


# ══════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Phren Agent Eval")
    parser.add_argument("--task", help="Task ID to run (or 'all')")
    parser.add_argument("--model", default="qwen3:8b", help="Ollama model name")
    parser.add_argument("--trials", type=int, default=3, help="Number of trials per task")
    parser.add_argument("--list", action="store_true", help="List available tasks")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--flat", action="store_true", help="Use flat terminal (agent sees map + all tools at once)")
    parser.add_argument("--ttl", action="store_true", help="Use TTL format (token-efficient, ~6x fewer tokens per turn)")
    parser.add_argument("--output", help="Save results JSON to file")
    args = parser.parse_args()

    # Import tasks
    from evals.tasks.agent_tasks import get_all_tasks, get_task

    if args.list:
        tasks = get_all_tasks()
        print(f"\nAvailable tasks ({len(tasks)}):")
        for t in tasks:
            print(f"  {t.id:<35} {t.description}")
        return

    if not check_ollama():
        print("ERROR: Ollama is not running. Start it with: ollama serve")
        sys.exit(1)

    models = list_ollama_models()
    if args.model not in [m.split(":")[0] + ":" + m.split(":")[-1] if ":" in m else m for m in models]:
        # Try partial match
        if not any(args.model in m for m in models):
            print(f"WARNING: Model '{args.model}' may not be available. Installed: {models}")

    if args.task == "all":
        tasks = get_all_tasks()
    else:
        task = get_task(args.task)
        if not task:
            print(f"ERROR: Unknown task '{args.task}'. Use --list to see available tasks.")
            sys.exit(1)
        tasks = [task]

    print(f"\nPhren Agent Eval")
    print(f"  Model: {args.model}")
    print(f"  Tasks: {len(tasks)}")
    print(f"  Trials: {args.trials}")
    print()

    results = []
    for task in tasks:
        print(f"[{task.id}] {task.description}")
        result = run_eval(task, args.model, args.trials, verbose=args.verbose,
                         flat_mode=args.flat, ttl_mode=args.ttl)
        results.append(result)
        icon = "✅" if result.pass_rate > 0 else "❌"
        print(f"  {icon} pass@1: {result.passed}/{result.trials} "
              f"({result.pass_rate*100:.0f}%)")
        print()

    print_results(results)

    if args.output:
        with open(args.output, "w") as f:
            f.write(results_to_json(results))
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
