"""
Tessera Registry

A lightweight discovery service for Tessera terminals.
Website owners register their terminals here.
Agents query the registry to find which terminals can help with their goal.

The registry does NOT proxy traffic — agents connect directly to terminals.
Think of it like DNS: it tells you where to go, not what to do.

Usage:
  # Start the registry
  python registry.py --port 8100

  # Register terminals (done by website owners or at startup)
  POST /register { "id": "tesserashop", "url": "http://localhost:8001", ... }

  # Agent discovers terminals
  GET /terminals              → list all
  GET /terminals?q=hotel      → search by keyword
  GET /terminals?category=booking → filter by category
  GET /terminals/tesserastay   → get one terminal's details
"""
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import requests


# ── Models ──

class TerminalRegistration(BaseModel):
    """A terminal registered in the directory."""
    id: str                              # Unique slug: "tesserashop", "tesserastay"
    name: str                            # Display name: "TesseraShop Demo Store"
    url: str                             # MCP server URL: "http://localhost:8001"
    description: str = ""
    category: str = ""                   # "shopping", "booking", "government", etc.
    tags: list[str] = []                 # Searchable tags
    site_url: Optional[str] = None       # The actual website URL
    contract_tier: str = "standard"
    entry_screen: str = ""
    screens: list[str] = []              # Screen IDs for quick reference
    registered_at: str = ""
    healthy: bool = True
    last_health_check: Optional[str] = None


class RegisterRequest(BaseModel):
    id: str
    name: str
    url: str
    description: str = ""
    category: str = ""
    tags: list[str] = []
    site_url: Optional[str] = None


# ── In-Memory Registry ──

TERMINALS: dict[str, TerminalRegistration] = {}


# ── Health Check ──

def check_terminal_health(terminal: TerminalRegistration) -> bool:
    """Ping a terminal to see if it's alive and get its info."""
    try:
        resp = requests.get(terminal.url, timeout=5)
        if resp.status_code == 200:
            info = resp.json()
            terminal.name = info.get("tessera_terminal", terminal.name)
            terminal.contract_tier = info.get("tier", terminal.contract_tier)
            terminal.entry_screen = info.get("entry_screen", terminal.entry_screen)
            terminal.screens = info.get("screens", terminal.screens)
            terminal.healthy = True
            terminal.last_health_check = datetime.now(timezone.utc).isoformat()
            return True
    except Exception:
        pass
    terminal.healthy = False
    terminal.last_health_check = datetime.now(timezone.utc).isoformat()
    return False


# ── Default Terminals ──

DEFAULT_TERMINALS = [
    RegisterRequest(
        id="tesserashop",
        name="TesseraShop Demo Store",
        url="http://localhost:8001",
        description="E-commerce store with electronics, clothing, books, home, and sports products. "
                    "Search, browse, add to cart, and complete purchases.",
        category="shopping",
        tags=["shopping", "ecommerce", "products", "buy", "electronics", "clothing", "books"],
        site_url="http://localhost:8000",
    ),
    RegisterRequest(
        id="tesserastay",
        name="TesseraStay Hotel Booking",
        url="http://localhost:8003",
        description="Hotel booking service across 5 cities (New York, Paris, Tokyo, London, Mexico City). "
                    "Search hotels, check room availability by date, and make reservations.",
        category="booking",
        tags=["hotels", "booking", "travel", "rooms", "reservation", "accommodation"],
        site_url="http://localhost:8002",
    ),
    RegisterRequest(
        id="tesseragov",
        name="TesseraGov Document Library & Services",
        url="http://localhost:8005",
        description="Government document library and service portal. Search permits, certificates, "
                    "forms, and regulations. Submit service requests and track status.",
        category="government",
        tags=["government", "permits", "certificates", "licenses", "forms", "documents", "applications"],
        site_url="http://localhost:8004",
    ),
]


# ── FastAPI App ──

@asynccontextmanager
async def lifespan(app):
    # Register default terminals on startup
    for req in DEFAULT_TERMINALS:
        register_terminal(req)
    print(f"Registry started with {len(TERMINALS)} terminals")
    yield


app = FastAPI(
    title="Tessera Registry",
    description="Discovery service for Tessera terminals. Agents find terminals here, then connect directly.",
    version="0.1.0",
    lifespan=lifespan,
)


def register_terminal(req: RegisterRequest) -> TerminalRegistration:
    """Register a terminal in the directory."""
    terminal = TerminalRegistration(
        id=req.id,
        name=req.name,
        url=req.url,
        description=req.description,
        category=req.category,
        tags=req.tags,
        site_url=req.site_url,
        registered_at=datetime.now(timezone.utc).isoformat(),
    )
    TERMINALS[req.id] = terminal
    return terminal


# ── Endpoints ──

@app.get("/")
def registry_info():
    """Registry overview."""
    return {
        "tessera_registry": "Tessera Terminal Registry",
        "version": "0.1.0",
        "total_terminals": len(TERMINALS),
        "healthy_terminals": sum(1 for t in TERMINALS.values() if t.healthy),
        "categories": list(set(t.category for t in TERMINALS.values() if t.category)),
        "usage": {
            "list_all": "GET /terminals",
            "search": "GET /terminals?q=hotel",
            "by_category": "GET /terminals?category=booking",
            "get_one": "GET /terminals/{id}",
            "register": "POST /register",
            "health_check": "POST /health",
        },
    }


@app.get("/terminals")
def list_terminals(
    q: Optional[str] = Query(None, description="Search keyword"),
    category: Optional[str] = Query(None, description="Filter by category"),
    healthy_only: bool = Query(False, description="Only return healthy terminals"),
):
    """
    List available terminals. This is what agents call first.
    Returns enough info for the agent to decide which terminal to connect to.
    """
    results = list(TERMINALS.values())

    if category:
        results = [t for t in results if t.category == category.lower()]

    if q:
        q_lower = q.lower()
        results = [t for t in results if (
            q_lower in t.name.lower()
            or q_lower in t.description.lower()
            or q_lower in t.category.lower()
            or any(q_lower in tag for tag in t.tags)
        )]

    if healthy_only:
        results = [t for t in results if t.healthy]

    return {
        "terminals": [
            {
                "id": t.id,
                "name": t.name,
                "url": t.url,
                "description": t.description,
                "category": t.category,
                "tags": t.tags,
                "entry_screen": t.entry_screen,
                "screens": t.screens,
                "healthy": t.healthy,
            }
            for t in results
        ],
        "total": len(results),
    }


@app.get("/terminals/{terminal_id}")
def get_terminal(terminal_id: str):
    """Get full details of a specific terminal."""
    if terminal_id not in TERMINALS:
        raise HTTPException(status_code=404, detail=f"Terminal '{terminal_id}' not found")

    t = TERMINALS[terminal_id]
    return {
        "id": t.id,
        "name": t.name,
        "url": t.url,
        "description": t.description,
        "category": t.category,
        "tags": t.tags,
        "site_url": t.site_url,
        "contract_tier": t.contract_tier,
        "entry_screen": t.entry_screen,
        "screens": t.screens,
        "healthy": t.healthy,
        "last_health_check": t.last_health_check,
        "registered_at": t.registered_at,
    }


@app.post("/register")
def register(req: RegisterRequest):
    """Register a new terminal in the directory."""
    terminal = register_terminal(req)
    return {
        "status": "registered",
        "id": terminal.id,
        "name": terminal.name,
        "url": terminal.url,
        "message": f"Terminal '{terminal.id}' registered. Run POST /health to verify connectivity.",
    }


@app.delete("/terminals/{terminal_id}")
def unregister(terminal_id: str):
    """Remove a terminal from the directory."""
    if terminal_id not in TERMINALS:
        raise HTTPException(status_code=404, detail=f"Terminal '{terminal_id}' not found")
    del TERMINALS[terminal_id]
    return {"status": "removed", "id": terminal_id}


@app.post("/health")
def health_check_all():
    """Run health checks on all registered terminals."""
    results = {}
    for tid, terminal in TERMINALS.items():
        healthy = check_terminal_health(terminal)
        results[tid] = {"healthy": healthy, "url": terminal.url}
    return {
        "checked": len(results),
        "healthy": sum(1 for r in results.values() if r["healthy"]),
        "results": results,
    }


@app.post("/health/{terminal_id}")
def health_check_one(terminal_id: str):
    """Run health check on a specific terminal."""
    if terminal_id not in TERMINALS:
        raise HTTPException(status_code=404, detail=f"Terminal '{terminal_id}' not found")
    terminal = TERMINALS[terminal_id]
    healthy = check_terminal_health(terminal)
    return {
        "id": terminal_id,
        "healthy": healthy,
        "url": terminal.url,
        "name": terminal.name,
        "screens": terminal.screens,
    }


if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="Tessera Registry")
    parser.add_argument("--port", type=int, default=8100, help="Registry port")
    args = parser.parse_args()

    print(f"Starting Tessera Registry on port {args.port}")
    print(f"  Docs: http://localhost:{args.port}/docs")
    print(f"  Terminals: http://localhost:{args.port}/terminals")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
