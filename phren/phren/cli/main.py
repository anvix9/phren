#!/usr/bin/env python3
"""
Phren CLI — compile websites into governed agent terminals.

Commands:
    phren info      Show version and configuration
    phren verify    Validate a terminal contract
    phren compile   Compile API routes into a terminal contract
    phren serve     Start the MCP terminal server
    phren eval      Run agent evaluation suite
    phren demo      Run the bundled simulation demo
"""

import argparse
import json
import sys
from pathlib import Path

from phren import __version__


def cmd_info(args):
    """Show version and configuration."""
    print(f"Phren v{__version__}")
    print(f"  Python:  {sys.version.split()[0]}")
    print(f"  Install: {Path(__file__).resolve().parent.parent}")
    try:
        import pydantic
        print(f"  Pydantic: {pydantic.__version__}")
    except ImportError:
        print("  Pydantic: not installed")
    try:
        import mcp
        print(f"  MCP SDK: {getattr(mcp, '__version__', 'installed')}")
    except ImportError:
        print("  MCP SDK: not installed (install with phren[server])")


def cmd_verify(args):
    """Validate a terminal contract."""
    contract_path = Path(args.contract)
    if not contract_path.exists():
        print(f"Error: contract not found: {contract_path}", file=sys.stderr)
        sys.exit(1)

    from phren.contract.schema import PhrenContract

    try:
        data = json.loads(contract_path.read_text())
        contract = PhrenContract(**data)
        print(f"✅ Contract '{contract.terminal_id}' is valid")
        print(f"   Site:    {contract.site_name}")
        print(f"   Actions: {len(contract.actions)}")
        print(f"   Screens: {len(contract.screens)}")
        if contract.rate_limits:
            rl = contract.rate_limits
            if rl.max_transaction_amount:
                print(f"   Max transaction: {rl.max_transaction_amount}")
        return 0
    except Exception as e:
        print(f"❌ Contract validation failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_compile(args):
    """Compile API routes into a terminal contract."""
    source_path = Path(args.source)
    if not source_path.exists():
        print(f"Error: source not found: {source_path}", file=sys.stderr)
        sys.exit(1)

    from phren.compiler.terminal_compiler import compile_terminal
    from phren.compiler.source_parser import SourceRouteParser

    try:
        parser = SourceRouteParser()
        # If source is a file, parse its parent directory
        parse_path = source_path.parent if source_path.is_file() else source_path
        routes = parser.parse(str(parse_path))

        contract = compile_terminal(
            routes=routes,
            site_name=args.name or source_path.stem,
            site_url=args.url or f"http://localhost:8000",
        )

        output = Path(args.output) if args.output else Path("contract.json")
        output.write_text(json.dumps(contract.model_dump(), indent=2, default=str))
        print(f"✅ Compiled {len(routes)} routes → {output}")
        print(f"   Actions: {len(contract.actions)}")
        print(f"   Screens: {len(contract.screens)}")
        return 0
    except Exception as e:
        print(f"❌ Compilation failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_serve(args):
    """Start the MCP terminal server."""
    contract_path = Path(args.contract)
    if not contract_path.exists():
        print(f"Error: contract not found: {contract_path}", file=sys.stderr)
        sys.exit(1)

    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn not installed. Install with: pip install phren[server]",
              file=sys.stderr)
        sys.exit(1)

    from phren.contract.schema import PhrenContract
    from phren.mcp.server import create_mcp_app

    data = json.loads(contract_path.read_text())
    contract = PhrenContract(**data)

    app = create_mcp_app(
        contract=contract,
        site_url=args.site_url or contract.site_url,
        transport=args.transport,
    )

    print(f"Phren terminal: {contract.terminal_id}")
    print(f"  Site:      {contract.site_name}")
    print(f"  Transport: {args.transport}")
    if args.transport == "http":
        print(f"  Port:      {args.port}")

    if args.transport == "stdio":
        uvicorn.run(app, log_level="warning")
    else:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def cmd_eval(args):
    """Run agent evaluation suite."""
    try:
        from evals.agent_eval import run_eval
        run_eval(
            task=args.task or "all",
            model=args.model,
            trials=args.trials,
            verbose=args.verbose,
        )
    except ImportError:
        print("Error: evals module not found. Run from the project root.",
              file=sys.stderr)
        sys.exit(1)


def cmd_demo(args):
    """Run the bundled simulation demo."""
    import subprocess
    import time

    sim = args.simulation or "shopping"
    sim_dir = Path(__file__).resolve().parent.parent.parent / "simulations" / sim / "api"

    if not sim_dir.exists():
        print(f"Error: simulation '{sim}' not found at {sim_dir}", file=sys.stderr)
        sys.exit(1)

    contract_path = sim_dir.parent / "contract.json"
    if not contract_path.exists():
        print(f"Error: no contract.json in {sim_dir.parent}", file=sys.stderr)
        sys.exit(1)

    print(f"Starting {sim} simulation...")
    sim_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", "8080", "--log-level", "warning"],
        cwd=str(sim_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for simulation to start
    time.sleep(2)
    if sim_proc.poll() is not None:
        stderr = sim_proc.stderr.read().decode() if sim_proc.stderr else ""
        print(f"Error: simulation failed to start\n{stderr}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ Simulation running on :8080")
    print(f"   Contract: {contract_path}")
    print(f"\n   To serve the terminal:")
    print(f"   phren serve {contract_path} --site-url http://localhost:8080")
    print(f"\n   Press Ctrl+C to stop")

    try:
        sim_proc.wait()
    except KeyboardInterrupt:
        sim_proc.terminate()
        sim_proc.wait()
        print("\nStopped.")


def cli():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="phren",
        description="Phren — compile websites into governed agent terminals",
    )
    parser.add_argument("--version", action="version", version=f"phren {__version__}")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # info
    sub.add_parser("info", help="Show version and configuration")

    # verify
    p_verify = sub.add_parser("verify", help="Validate a terminal contract")
    p_verify.add_argument("contract", help="Path to contract.json")

    # compile
    p_compile = sub.add_parser("compile", help="Compile API routes into a contract")
    p_compile.add_argument("source", help="Path to API source directory or file")
    p_compile.add_argument("--name", help="Site name")
    p_compile.add_argument("--url", help="Site URL")
    p_compile.add_argument("-o", "--output", help="Output path (default: contract.json)")

    # serve
    p_serve = sub.add_parser("serve", help="Start the MCP terminal server")
    p_serve.add_argument("contract", help="Path to contract.json")
    p_serve.add_argument("--site-url", help="Backend API URL")
    p_serve.add_argument("--transport", choices=["http", "stdio"], default="http")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)

    # eval
    p_eval = sub.add_parser("eval", help="Run agent evaluation suite")
    p_eval.add_argument("--task", help="Task name or 'all'")
    p_eval.add_argument("--model", default="qwen3:4b", help="Model to evaluate")
    p_eval.add_argument("--trials", type=int, default=3)
    p_eval.add_argument("-v", "--verbose", action="store_true")

    # demo
    p_demo = sub.add_parser("demo", help="Run a bundled simulation demo")
    p_demo.add_argument("--simulation", choices=["shopping", "booking", "library"],
                        default="shopping")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "info": cmd_info,
        "verify": cmd_verify,
        "compile": cmd_compile,
        "serve": cmd_serve,
        "eval": cmd_eval,
        "demo": cmd_demo,
    }

    sys.exit(commands[args.command](args) or 0)


if __name__ == "__main__":
    cli()
