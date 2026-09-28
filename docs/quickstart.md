# Phren — Quickstart

Get a governed agent terminal running in 5 minutes.

## Option 1: Local install

```bash
# Clone and install
git clone https://github.com/anvix9/phren.git
cd phren
pip install -e ".[all]"

# Run the demo (starts simulation, connects agent, browses products)
phren demo
```

### Explore the terminal

```bash
# See what's in a contract
phren info simulations/shopping/contract.json

# Validate it
phren verify simulations/shopping/contract.json

# Compile a new contract from source code
phren compile simulations/shopping/api -o my_contract.json --site-name "My Shop"

# Start the MCP server
phren serve simulations/shopping/contract.json --site-url http://localhost:8080 --port 8000
```

### Run agent evals

Requires [Ollama](https://ollama.com/) with a local model:

```bash
ollama pull llama3.2:3b

# Single task
python3 -m evals.agent_eval --task shopping-full-purchase --model llama3.2:3b --trials 3 -v

# All 7 tasks
python3 -m evals.agent_eval --task all --model llama3.2:3b --trials 3 -v

# Try a smaller model
ollama pull qwen3.5:0.8b
python3 -m evals.agent_eval --task all --model qwen3.5:0.8b --trials 3 -v
```

### Run the test suite

```bash
pytest evals/ conformance/ -v    # 183 tests
```

## Option 2: Docker

```bash
# Clone
git clone https://github.com/anvix9/phren.git
cd phren

# Start simulation + Phren terminal server
docker compose up

# In another terminal: the MCP endpoint is at
curl http://localhost:8000/mcp
```

### Docker commands

```bash
# Start everything (simulation on :8080, phren on :8000)
docker compose up

# Run in background
docker compose up -d

# Stop
docker compose down

# Run eval (requires Ollama on host)
docker compose run eval
```

## What happens when you run `phren demo`

```
  ╔══════════════════════════════════════════╗
  ║  Phren Demo — Shopping Terminal          ║
  ╚══════════════════════════════════════════╝

  Contract: phren_shop_v2
  Site: Phren Shop Demo
  Screens: 7
  Actions: 19

  Starting simulation...
  ✅ Connected (session: sess_948999e8931...)

  Screen: Authentication
  Available actions:
    ► login
    ○ register
    ← browse_without_login

  → Executing: browse_without_login
    Status: ok, screen: Product search
  → Executing: search(q='laptop')
    Found 1 products:
      prod_002  Ultra Slim Laptop Stand  $34.99

  ✅ Demo complete. The terminal works.
```

## What happens when you run an agent eval

The agent sees a terminal — screens with actions, roles, and flow hints — not raw API endpoints. The path trace tells it where it's been and what to do next:

```
PATH SO FAR:
  ✅ [0] login → got auth token
  ✅ [1] search → found 3: Budget Laptop ($399) [id:prod_004]
  ✅ [2] view_product → name=Budget Laptop

SUGGESTED NEXT: add_to_cart (needs: product_id, quantity)
```

The agent responds with JSON actions, and the terminal proxies them to the real API after checking governance (trust tiers, spend limits, rate limits):

```json
{"action": "add_to_cart", "params": {"product_id": "prod_004", "quantity": 1}}
```

## Project structure

```
phren/
├── cli/          # CLI: info, verify, compile, serve, eval, demo
├── compiler/     # Route discovery (10 frameworks) + terminal compiler
├── contract/     # Schema, resolver, enforcement, credentials, validation
├── terminal/     # Screen engine, flat engine, presenter
└── mcp/          # MCP server (official SDK, JSON-RPC)
simulations/
├── shopping/     # E-commerce simulation + contract
├── booking/      # Hotel booking simulation + contract
└── library/      # Government document library simulation + contract
evals/
├── agent_eval.py # Agent eval runner (Ollama integration)
├── tasks/        # 7 agent tasks + 15 governance tasks
└── harness.py    # Eval framework
```

## Next steps

Once the demo works:

1. **Read the contract** — `phren info simulations/shopping/contract.json` to understand what governance looks like
2. **Run the evals** — see a 0.8B model complete a purchase in under a minute
3. **Compile your own** — `phren compile your-app/api/ -o contract.json` to generate a terminal from your code
4. **Serve it** — `phren serve contract.json --site-url http://your-app:8080` to expose it as MCP tools
