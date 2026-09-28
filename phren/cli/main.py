"""
Phren CLI — governed terminals for AI agents.

Commands:
  phren compile <path>          Compile a website into a contract
  phren serve <contract.json>   Start MCP terminal server
  phren verify <contract.json>  Validate a contract
  phren eval <contract.json>    Run agent eval
  phren info <contract.json>    Show contract summary
  phren demo                    Run a quick demo with the shopping simulation
"""
import argparse
import json
import sys
import os
import subprocess
import signal
import time
from pathlib import Path


def cli():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="phren",
        description="Phren — governed terminals for AI agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  phren info simulations/shopping/contract.json
  phren verify simulations/shopping/contract.json
  phren serve simulations/shopping/contract.json --port 8000
  phren compile simulations/shopping/api/main.py --output contract.json
  phren eval simulations/shopping/contract.json --model llama3.2:3b
  phren demo
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── info ──
    info_parser = subparsers.add_parser("info", help="Show contract summary")
    info_parser.add_argument("contract", help="Path to contract JSON")

    # ── verify ──
    verify_parser = subparsers.add_parser("verify", help="Validate a contract")
    verify_parser.add_argument("contract", help="Path to contract JSON")

    # ── serve ──
    serve_parser = subparsers.add_parser("serve", help="Start MCP terminal server")
    serve_parser.add_argument("contract", help="Path to contract JSON")
    serve_parser.add_argument("--site-url", default="http://localhost:8080", help="URL of the website to proxy to")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port for MCP server")
    serve_parser.add_argument("--transport", choices=["http", "stdio"], default="http", help="MCP transport")
    serve_parser.add_argument("--registry", help="Path to operator registry JSON")

    # ── compile ──
    compile_parser = subparsers.add_parser("compile", help="Compile a website into a contract")
    compile_parser.add_argument("source", help="Path to source file or directory")
    compile_parser.add_argument("--output", "-o", default="contract.json", help="Output contract path")
    compile_parser.add_argument("--site-name", default="My Site", help="Site name")
    compile_parser.add_argument("--site-url", default="http://localhost:8080", help="Site URL")
    compile_parser.add_argument("--max-transaction", type=float, default=500.0, help="Max transaction amount")

    # ── eval ──
    eval_parser = subparsers.add_parser("eval", help="Run agent eval against a terminal")
    eval_parser.add_argument("contract", help="Path to contract JSON")
    eval_parser.add_argument("--simulation", required=True, help="Path to simulation api/ directory")
    eval_parser.add_argument("--model", default="llama3.2:3b", help="Ollama model name")
    eval_parser.add_argument("--task", default="browse", help="Task: browse, purchase, or a custom goal")
    eval_parser.add_argument("--trials", type=int, default=3, help="Number of trials")
    eval_parser.add_argument("--port", type=int, default=8100, help="Simulation port")

    # ── demo ──
    demo_parser = subparsers.add_parser("demo", help="Run a quick demo with the shopping simulation")
    demo_parser.add_argument("--model", default="llama3.2:3b", help="Ollama model name")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "info": cmd_info,
        "verify": cmd_verify,
        "serve": cmd_serve,
        "compile": cmd_compile,
        "eval": cmd_eval,
        "demo": cmd_demo,
    }
    commands[args.command](args)


# ═══════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════

def cmd_info(args):
    """Show contract summary."""
    contract = _load_contract(args.contract)
    if not contract:
        return

    print(f"\n  Phren Contract: {contract.contract_id}")
    print(f"  Site: {contract.site_name}")
    print(f"  URL: {contract.site_url}")
    print(f"  Version: {contract.version}")

    if contract.expires_at:
        print(f"  Expires: {contract.expires_at}")

    print(f"\n  Screens: {len(contract.screens)}")
    total_actions = 0
    for screen in contract.screens:
        actions = screen.actions
        total_actions += len(actions)
        primary = [a for a in actions if a.role.value == "primary"]
        print(f"    [{screen.id}] {screen.name} — {len(actions)} actions ({len(primary)} primary)")
        if screen.flow_hint:
            print(f"      hint: {screen.flow_hint}")

    print(f"\n  Total actions: {total_actions}")
    print(f"  Entry screen: {contract.entry_screen or '(first)'}")

    rl = contract.rate_limits
    print(f"\n  Rate limits:")
    if rl.requests_per_minute:
        print(f"    {rl.requests_per_minute} req/min")
    if rl.requests_per_hour:
        print(f"    {rl.requests_per_hour} req/hour")
    if rl.max_concurrent_sessions:
        print(f"    {rl.max_concurrent_sessions} concurrent sessions")
    if rl.max_transaction_amount:
        print(f"    ${rl.max_transaction_amount} max per transaction")
    if rl.max_daily_spend:
        print(f"    ${rl.max_daily_spend} max daily spend")

    print()


def cmd_verify(args):
    """Validate a contract."""
    contract = _load_contract(args.contract)
    if not contract:
        return

    from phren.contract.validation import validate_contract
    errors, warnings = validate_contract(contract)

    if errors:
        print(f"\n  ❌ Contract has {len(errors)} error(s):")
        for e in errors:
            print(f"    ERROR: {e}")
    if warnings:
        print(f"\n  ⚠ Contract has {len(warnings)} warning(s):")
        for w in warnings:
            print(f"    WARN: {w}")

    if not errors and not warnings:
        print(f"\n  ✅ Contract '{contract.contract_id}' is valid.")
    elif not errors:
        print(f"\n  ✅ Contract '{contract.contract_id}' is valid (with warnings).")
    else:
        print(f"\n  ❌ Contract '{contract.contract_id}' has errors. Fix them before serving.")
        sys.exit(1)

    print()


def cmd_serve(args):
    """Start MCP terminal server."""
    contract = _load_contract(args.contract)
    if not contract:
        return

    # Validate first
    from phren.contract.validation import validate_contract
    errors, _ = validate_contract(contract)
    if errors:
        print(f"  ❌ Contract has errors. Run 'phren verify {args.contract}' to see them.")
        sys.exit(1)

    from phren.mcp.server import create_phren_mcp

    print(f"\n  Phren MCP Server")
    print(f"  Contract: {contract.contract_id}")
    print(f"  Site: {contract.site_name} → {args.site_url}")
    print(f"  Transport: {args.transport}")

    server = create_phren_mcp(
        args.contract,
        args.site_url,
        registry_path=args.registry,
    )

    if args.transport == "stdio":
        print(f"  Mode: stdio (for Claude Desktop, MCP clients)")
        print(f"  Listening on stdin/stdout...\n")
        import asyncio
        from phren.mcp.server import run_stdio
        asyncio.run(run_stdio(server))
    else:
        print(f"  Port: {args.port}")
        print(f"  Endpoint: http://localhost:{args.port}/mcp")
        print(f"\n  Starting...\n")
        app = server.streamable_http_app(streamable_http_path="/mcp")
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


def cmd_compile(args):
    """Compile a website into a contract."""
    source_path = Path(args.source)

    if not source_path.exists():
        print(f"  ❌ Source not found: {args.source}")
        sys.exit(1)

    print(f"\n  Phren Compiler")
    print(f"  Source: {args.source}")

    # Step 1: Parse routes
    from phren.compiler.source_parser import SourceRouteParser

    # Parser expects a directory (project root), not a single file
    parse_path = source_path if source_path.is_dir() else source_path.parent

    parser = SourceRouteParser(str(parse_path))
    routes = parser.parse()
    print(f"  Found {len(routes)} routes in {parse_path}")

    if not routes:
        print(f"  ⚠ No routes found. Check that the source uses a supported framework.")
        sys.exit(1)

    # Step 2: Compile terminal
    from phren.compiler.terminal_compiler import compile_terminal

    contract = compile_terminal(
        routes=routes,
        site_name=args.site_name,
        site_url=args.site_url,
        max_transaction=args.max_transaction,
    )

    # Step 3: Save
    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(contract.model_dump(), f, indent=2, default=str)

    print(f"  Contract saved: {args.output}")
    print(f"  Contract ID: {contract.contract_id}")
    print(f"  Screens: {len(contract.screens)}")
    total_actions = sum(len(s.actions) for s in contract.screens)
    print(f"  Actions: {total_actions}")

    # Validate
    from phren.contract.validation import validate_contract
    errors, warnings = validate_contract(contract)
    if errors:
        print(f"\n  ⚠ Generated contract has {len(errors)} validation error(s):")
        for e in errors:
            print(f"    {e}")
    else:
        print(f"  ✅ Contract is valid.")

    print(f"\n  Next: phren serve {args.output} --site-url {args.site_url}")
    print()


def cmd_eval(args):
    """Run agent eval."""
    contract = _load_contract(args.contract)
    if not contract:
        return

    print(f"\n  Phren Eval")
    print(f"  Contract: {contract.contract_id}")
    print(f"  Simulation: {args.simulation}")
    print(f"  Model: {args.model}")
    print(f"  Task: {args.task}")
    print(f"  Trials: {args.trials}")
    print()

    # Start simulation
    sim_path = Path(args.simulation)
    if not sim_path.exists():
        print(f"  ❌ Simulation not found: {args.simulation}")
        sys.exit(1)

    from phren.terminal.engine import PhrenTerminal
    from phren.contract.schema import AgentProfile, AgentTrust, AgentCapabilities

    sim_url = f"http://localhost:{args.port}"

    # Start simulation server
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(args.port),
         "--log-level", "warning"],
        cwd=str(sim_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )

    # Wait for server
    for _ in range(30):
        try:
            import requests
            r = requests.get(f"{sim_url}/", timeout=1)
            if r.status_code < 500:
                break
        except Exception:
            pass
        time.sleep(0.5)

    try:
        terminal = PhrenTerminal(contract, sim_url)

        for trial in range(1, args.trials + 1):
            print(f"  Trial {trial}/{args.trials}...", end=" ", flush=True)

            agent = AgentProfile(
                provider="phren-cli", agent_name="cli-agent",
                trust_level=AgentTrust.VERIFIED, purpose="purchase",
                capabilities=AgentCapabilities(can_transact=True),
                delegated_by_user=True,
            )

            result = terminal.connect(agent)
            if result["status"] == "connected":
                sid = result["session_id"]
                screen = terminal.get_screen(sid)
                actions = [a["id"] for a in screen.get("actions", [])]
                print(f"✅ connected, screen={screen.get('screen', {}).get('name', '?')}, "
                      f"actions={actions[:3]}{'...' if len(actions) > 3 else ''}")
                terminal.disconnect(sid)
            else:
                print(f"❌ {result.get('reason', 'unknown error')}")

    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)

    print()


def cmd_demo(args):
    """Run a quick demo."""
    project_root = _find_project_root()
    if not project_root:
        print("  ❌ Cannot find project root (looking for simulations/ directory)")
        sys.exit(1)

    contract_path = project_root / "simulations" / "shopping" / "contract.json"
    sim_path = project_root / "simulations" / "shopping" / "api"

    if not contract_path.exists():
        print(f"  ❌ Shopping contract not found at {contract_path}")
        sys.exit(1)

    print(f"\n  ╔══════════════════════════════════════════╗")
    print(f"  ║  Phren Demo — Shopping Terminal          ║")
    print(f"  ╚══════════════════════════════════════════╝\n")

    # Show contract info
    contract = _load_contract(str(contract_path))
    print(f"  Contract: {contract.contract_id}")
    print(f"  Site: {contract.site_name}")
    print(f"  Screens: {len(contract.screens)}")
    total_actions = sum(len(s.actions) for s in contract.screens)
    print(f"  Actions: {total_actions}")
    print()

    # Start simulation
    print(f"  Starting simulation...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", "8200",
         "--log-level", "warning"],
        cwd=str(sim_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=os.setsid,
    )

    time.sleep(3)

    try:
        from phren.terminal.engine import PhrenTerminal
        from phren.contract.schema import AgentProfile, AgentTrust, AgentCapabilities

        terminal = PhrenTerminal(contract, "http://localhost:8200")

        agent = AgentProfile(
            provider="phren-demo", agent_name="demo-agent",
            trust_level=AgentTrust.VERIFIED, purpose="purchase",
            capabilities=AgentCapabilities(can_transact=True),
            delegated_by_user=True,
        )

        result = terminal.connect(agent)
        sid = result["session_id"]

        print(f"  ✅ Connected (session: {sid[:16]}...)")
        print()

        # Walk through the terminal
        screen = terminal.get_screen(sid)
        screen_name = screen.get("screen", {}).get("name", "?")
        actions = screen.get("actions", [])
        print(f"  Screen: {screen_name}")
        print(f"  Available actions:")
        for a in actions:
            role = a.get("role", "?")
            marker = "►" if role == "primary" else "○" if role == "secondary" else "←"
            print(f"    {marker} {a['id']}")
        print()

        # Browse without login
        print(f"  → Executing: browse_without_login")
        r = terminal.execute_action(sid, "browse_without_login", {})
        print(f"    Status: {r['status']}, screen: {r.get('screen_name', r.get('screen', '?'))}")

        # Search
        print(f"  → Executing: search(q='laptop')")
        r = terminal.execute_action(sid, "search", {"q": "laptop"})
        if r.get("data") and r["data"].get("products"):
            products = r["data"]["products"]
            print(f"    Found {len(products)} products:")
            for p in products[:3]:
                print(f"      {p['id']}  {p['name']}  ${p.get('price', '?')}")
        else:
            print(f"    Status: {r['status']}")

        print()
        terminal.disconnect(sid)
        print(f"  ✅ Demo complete. The terminal works.\n")
        print(f"  Next steps:")
        print(f"    phren info {contract_path}")
        print(f"    phren verify {contract_path}")
        print(f"    phren serve {contract_path} --site-url http://localhost:8200")
        print()

    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)


# ═══════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════

def _load_contract(path: str):
    """Load and parse a contract JSON file."""
    try:
        with open(path) as f:
            data = json.load(f)
        from phren.contract.schema import PhrenContract
        return PhrenContract(**data)
    except FileNotFoundError:
        print(f"  ❌ File not found: {path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"  ❌ Invalid JSON in {path}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  ❌ Error loading contract: {e}")
        sys.exit(1)


def _find_project_root() -> Path:
    """Find the project root by looking for simulations/ directory."""
    # Check common locations
    for candidate in [
        Path.cwd(),
        Path(__file__).parent.parent.parent,
        Path.home() / "Desktop" / "phren",
    ]:
        if (candidate / "simulations").exists():
            return candidate
    return None


if __name__ == "__main__":
    cli()
