# Tessera — Eval Results

## Headline

**Six models from 0.8B to 8B parameters. Seven tasks across three domains. 126 trials. 100% pass rate.**

A governed terminal with path trace and flow hints enables any model — down to 800 million parameters — to complete purchases, bookings, and document requests on governed web simulations.

## Why a terminal, not a browser?

There are three ways an agent can interact with a website:

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
```

| | Raw API | MCP Tools | Browser Agent | **Tessera Terminal** |
|---|---|---|---|---|
| **Governance** | None | None | None | Trust tiers, spend limits, rate limits |
| **Flow guidance** | None | None | None | Roles, hints, path trace |
| **Min model size** | N/A (code) | ~7B | ~70B (vision) | **0.8B** |
| **Speed** | Instant | ~1s/call | 5-30s/action | **0.3-1s/action** |
| **Site cooperation** | Must build API | Must publish tools | None needed | Publishes contract |
| **Cost per task** | ¢ | ¢ | $$$ | **¢** |

Browser agents need vision models (70B+) to screenshot and click through a checkout flow. Tessera terminals let a 0.8B model do the same thing — **~100x cheaper inference**.

## Model Matrix

All models run locally via Ollama on consumer hardware (Manjaro Linux, external SSD).

| Model | Params | Family | pass@1 | Avg Steps | Avg Invalid | Avg Time/Task |
|-------|--------|--------|--------|-----------|-------------|---------------|
| qwen3.5:0.8b | 0.8B | Alibaba | **21/21 (100%)** | 10.1 | 3.5 | 41s |
| lfm2.5-thinking:1.2b | 1.2B | Liquid | **21/21 (100%)** | 10.1 | 3.3 | 60s |
| llama3.2:3b | 3B | Meta | **21/21 (100%)** | 10.4 | 3.1 | 4.4s |
| granite4.1:3b | 3B | IBM | **21/21 (100%)** | 10.4 | 1.8 | 6.9s |
| qwen3:4b | 4B | Alibaba | **21/21 (100%)** | 8.1 | 2.6 | 175s |
| qwen3:8b | 8B | Alibaba | **21/21 (100%)** | 7.1 | 1.8 | 266s |

### Key Findings

**Fastest:** llama3.2:3b at 4.4s per task. Completes a full purchase in 6.4 seconds.

**Most efficient:** qwen3:8b takes fewest steps (7.1) and fewest invalid actions (1.8), but thinking overhead makes it 60x slower than llama3.2.

**Best balance:** granite4.1:3b — fast (6.9s), near-zero invalid actions, no thinking overhead.

**Smallest:** qwen3.5:0.8b — 800M parameters, 100% pass rate. The terminal design is the capability, not the model.

## Task Breakdown

| Task | Domain | Type | All Models |
|------|--------|------|------------|
| shopping-browse | E-commerce | Navigation | 18/18 ✅ |
| shopping-search-cart | E-commerce | Interaction | 18/18 ✅ |
| shopping-full-purchase | E-commerce | Transaction | 18/18 ✅ |
| booking-search | Hospitality | Navigation | 18/18 ✅ |
| booking-reserve | Hospitality | Transaction | 18/18 ✅ |
| library-browse | Government | Navigation | 18/18 ✅ |
| library-request-doc | Government | Transaction | 18/18 ✅ |

## Design Iteration Impact

Every improvement came from the terminal design, not the model:

| Iteration | Pass Rate | Change |
|-----------|-----------|--------|
| No trace, no roles | 0% | Baseline — all models fail |
| + Action roles & hints | 38% | Agents stop picking wrong actions |
| + Path trace | 67% | Agents stop looping |
| + Data carry-forward | 90% | Agents use IDs from search results |
| + Generic governance | **100%** | Terminal works across all domains |

## Governance Eval

15 governance boundary tasks verify the terminal correctly denies what should be denied (tier escalation, spend limits, rate limits, contract expiry, consent gates). 100% pass rate.

## Known Limitations

These results cover linear happy paths on 3 hand-written simulations. Not yet tested: complex paths, branching flows, error recovery, real websites, compiler-generated contracts, or adversarial inputs.

## Reproducing

```bash
pip install -e ".[dev]"
ollama pull llama3.2:3b
python3 -m evals.agent_eval --task all --model llama3.2:3b --trials 3 -v
pytest evals/ conformance/ -v   # 180 automated tests
```
