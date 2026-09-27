# Tessera — Governed Terminals for AI Agents

Tessera compiles any website into a **terminal** — a governed, navigable layer between AI agents and web APIs. Agents navigate terminals via MCP to complete tasks like purchasing, booking, or requesting documents.

## Why not just give agents the API?

```
┌──────────────────────────────────────────────┐
│              MCP (protocol)                  │
│  How the agent talks to tools                │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │         TERMINAL (governance)          │  │
│  │  What the agent is allowed to do       │  │
│  │  Where it should go next               │  │
│  │  What it already did                   │  │
│  │                                        │  │
│  │  ┌──────────────────────────────────┐  │  │
│  │  │         API (execution)          │  │  │
│  │  │  The actual HTTP calls           │  │  │
│  │  └──────────────────────────────────┘  │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘

MCP is the WIRE.  The API is the DESTINATION.
The terminal is the RULES + MAP + MEMORY in between.
```

| | Raw API | MCP Tools | Browser Agent | **Tessera Terminal** |
|---|---|---|---|---|
| **Governance** | None | None | None | Trust tiers, spend limits, rate limits |
| **Flow guidance** | None | None | None | Roles, hints, path trace |
| **Min model size** | N/A (code) | ~7B | ~70B (vision) | **0.8B** |
| **Speed** | Instant | ~1s/call | 5-30s/action | **0.3-1s/action** |
| **Cost per task** | ¢ | ¢ | $$$ | **¢** |

![Terminal vs Browser Agent](docs/plots/terminal_vs_browser.png)

## Agent eval results

**6 models × 7 tasks × 3 trials = 126/126 (100%)**

| Model | Params | Family | pass@1 | Avg Steps | Avg Invalid | Avg Time |
|-------|--------|--------|--------|-----------|-------------|----------|
| qwen3.5:0.8b | 0.8B | Alibaba | **21/21** | 10.1 | 3.5 | 41s |
| lfm2.5-thinking | 1.2B | Liquid | **21/21** | 10.1 | 3.3 | 60s |
| llama3.2:3b | 3B | Meta | **21/21** | 10.4 | 3.1 | 4.4s |
| granite4.1:3b | 3B | IBM | **21/21** | 10.4 | 1.8 | 6.9s |
| qwen3:4b | 4B | Alibaba | **21/21** | 8.1 | 2.6 | 175s |
| qwen3:8b | 8B | Alibaba | **21/21** | 7.1 | 1.8 | 266s |

![Speed per Task](docs/plots/speed_per_task.png)

![Steps Breakdown](docs/plots/steps_breakdown.png)

**Key finding:** terminal design — not model size — determines agent success. The same models that scored 0% without the terminal's path trace now score 100%.

![Design Iteration Impact](docs/plots/design_iteration.png)

## What the terminal does for agents

**1. Flow map with roles.** Actions are marked as primary (move forward), secondary (optional), or navigation (go back). Each screen carries a flow hint from the contract.

**2. Path trace.** The agent sees where it's been and what it found:

```
PATH SO FAR:
  ✅ [0] login → got auth token
  ✅ [1] search → found 3: Budget Laptop ($399) [id:prod_004]
  ✅ [2] view_product → name=Budget Laptop

SUGGESTED NEXT: add_to_cart (needs: product_id, quantity)
```

**3. Governance enforcement.** Every contract field binds at runtime: trust tiers, spend limits, rate limits, confirmation gates. The terminal checks all constraints before proxying any action to the real API.

## How it works

```
your-website/api/main.py
        │
        ▼  source parser (10 frameworks)
discovered routes
        │
        ▼  terminal compiler
contract.json (governance + flow map + tools)
        │
        ▼  terminal engine
MCP server (JSON-RPC, stdio + HTTP)
        │
        ▼  agent connects, navigates, completes tasks
```

## Install

```bash
pip install -e ".[dev]"
pytest evals/ conformance/ -v        # 180 tests
```

## Run agent evals

```bash
ollama pull llama3.2:3b
python3 -m evals.agent_eval --task all --model llama3.2:3b --trials 3 -v
```

## Trust model

Trust is derived from Ed25519-signed credentials, never self-declared. The terminal verifies the signature, checks expiration, validates the audience, rejects replayed tokens, and caps the trust tier at the operator's registered maximum.

## Supported frameworks

FastAPI, Rails, Go (Chi/Echo), NestJS, Next.js (files/App Router/pages), tRPC, PHP/Laravel. 13 GitHub repos tested: **2,203 routes, 100% precision.**

## Positioning

**Stripe ACP / Google UCP** handle the payment rail. **Tessera** handles what the agent is *permitted* to do, proves what it *did*, and guides it through the *flow*. They are complementary.

## Known limitations

These results cover linear happy paths on 3 hand-written simulations. Not yet tested: complex branching paths, error recovery, real websites, or compiler-generated contracts.

## License

Apache-2.0. Open core never imports the commercial layer.
