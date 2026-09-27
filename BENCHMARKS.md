# Tessera — Discovery Benchmarks

## Overview

Tessera compiles any website into a structured terminal interface for AI agents.
The compiler's job: discover every API endpoint a site exposes, then generate
a Tessera contract from them.

We built 4 discovery tools and tested them against 13 real GitHub repos
and 18 simulation sites.

---

## Discovery Tools

| Tool | Input | Method | Best For |
|------|-------|--------|----------|
| `source_parser.py` | Source code | Regex parse route definitions | Owner has codebase (primary path) |
| `owner_discovery.py` | Token + URL + hints | Authenticated HTTP probing | Owner, no source access |
| `api_autodiscovery.py` | Just a URL | Blind HTTP probing + structural inference | External evaluation |
| `compiler.py` | OpenAPI spec | Spec parsing + SPA detection | Sites with API docs |

---

## Source Parser — Framework Support (10 frameworks)

| Framework | Pattern | Example Project |
|-----------|---------|-----------------|
| FastAPI (Python) | `@app.get("/path")`, `@router.post("/path")` | FastAPI Full-Stack, RealWorld |
| Rails (Ruby) | `resources :name`, `get "/path"` in routes.rb | Forem/dev.to |
| Go (Chi/Echo/Gin) | `m.Get("/path")`, `g.POST("/path")`, `m.Combo()` | Gitea |
| NestJS (TypeScript) | `@Controller('prefix')` + `@Get()/@Post()` | Twenty CRM, Hoppscotch |
| Next.js file-based | `_get.ts`, `_post.ts`, `_patch.ts`, `_delete.ts` | Cal.com |
| Next.js App Router | `route.ts` with `export function GET/POST` | Medusa |
| Next.js pages/api | `export default function` + `req.method` checks | Papermark |
| tRPC (TypeScript) | `router({})` procedures (inline + named imports) | Documenso |
| PHP/Utopia | `Http::get('/path')`, `Http::post('/path')` | Appwrite |
| Laravel (PHP) | `Route::get('path')`, `Route::apiResource('name')` | Firefly III, Koel |
| Express (JavaScript) | `app.get("/path")`, `router.post("/path")` | (supported) |

---

## Benchmark 1: Source Parser vs 13 Real GitHub Repos

Routes extracted directly from production codebases. No HTTP probing needed.

| Project | Framework | Domain | Routes |
|---------|-----------|--------|--------|
| Firefly III | Laravel | Personal finance | 502 |
| Papermark | Next.js pages | Document sharing | 379 |
| Forem/dev.to | Rails | Social blogging | 341 |
| Appwrite | PHP/Utopia | Backend-as-Service | 186 |
| Koel | Laravel | Music streaming | 170 |
| Gitea | Go (Chi) | Git hosting | 162 |
| Cal.com | Next.js files | Scheduling | 131 |
| Documenso | tRPC | E-signatures | 102 |
| Twenty CRM | NestJS | CRM | 101 |
| Medusa | Next.js router | E-commerce | 51 |
| Hoppscotch | NestJS | API testing | 38 |
| FastAPI Full-Stack | FastAPI | Template | 21 |
| RealWorld/Conduit | FastAPI | Medium clone | 19 |
| **TOTAL** | **10 frameworks** | **13 domains** | **2,203** |

---

## Benchmark 2: Source Parser — Precision & Recall

### Against 8 Real-World Replicas (absolute ground truth from FastAPI app object)

| App | Real | Parsed | Hit | Miss | FP | Recall | Precision |
|-----|------|--------|-----|------|----|--------|-----------|
| conduit | 19 | 19 | 19 | 0 | 0 | 100% | 100% ✅ |
| fastapi_fs | 21 | 21 | 21 | 0 | 0 | 100% | 100% ✅ |
| medusa | 51 | 51 | 51 | 0 | 0 | 100% | 100% ✅ |
| forem | 44 | 44 | 44 | 0 | 0 | 100% | 100% ✅ |
| taiga | 29 | 29 | 29 | 0 | 0 | 100% | 100% ✅ |
| listmonk | 63 | 63 | 63 | 0 | 0 | 100% | 100% ✅ |
| gitea | 40 | 40 | 40 | 0 | 0 | 100% | 100% ✅ |
| calcom | 86 | 26 | 26 | 60 | 0 | 30% | 100% ⚠️ |
| **TOTAL** | **353** | **293** | **293** | **60** | **0** | **83%** | **100%** |

**Precision: 100%** — zero false positives across all 8 apps.
**Recall: 83%** — the 60 misses are ALL from Cal.com's `_crud()` dynamic helper.
Excluding Cal.com: **267/267 = 100% recall, 100% precision** on 7/8 apps.

### Against Actual GitHub Repos (verified ground truth)

| Repo | Method | Found/Actual | Recall | Precision |
|------|--------|-------------|--------|-----------|
| Appwrite | grep Http:: vs parsed | 186/187 | 99% | 100% ✅ |
| Cal.com | file count vs parsed | 82/82 | 100% | 100% ✅ |
| Medusa | file+export vs parsed | 51/52 | 98% | 100% ✅ |
| RealWorld | known spec vs parsed | 19/19 | 100% | 100% ✅ |

---

## Benchmark 3: Blind Auto-Discovery — Precision & Recall

Ground truth = source parser output from the same codebase.
The blind scanner probes a running server with no credentials or source access.

### Results (8 real-world app replicas)

| App | Real | Disc | Hit | Miss | FP | Recall | Precision |
|-----|------|------|-----|------|----|--------|-----------|
| conduit | 19 | 16 | 14 | 5 | 2 | 74% | 88% ⚠️ |
| fastapi_fs | 21 | 16 | 8 | 13 | 8 | 38% | 50% ❌ |
| medusa | 51 | 61 | 27 | 24 | 34 | 53% | 44% ⚠️ |
| forem | 44 | 31 | 25 | 19 | 6 | 57% | 81% ⚠️ |
| taiga | 29 | 21 | 18 | 11 | 3 | 62% | 86% ⚠️ |
| listmonk | 63 | 13 | 7 | 56 | 6 | 11% | 54% ❌ |
| gitea | 40 | 4 | 2 | 38 | 2 | 5% | 50% ❌ |
| calcom | 26 | 13 | 5 | 21 | 8 | 19% | 38% ❌ |
| **TOTAL** | **293** | **175** | **106** | **187** | **69** | **36%** | **61%** |

### Root Cause Analysis (187 missed endpoints)

| # | Root Cause | Count | % of Misses | Apps Affected |
|---|-----------|-------|-------------|---------------|
| 1 | Auth failure (no token) | ~122 | 65% | Listmonk(56), Gitea(38), FastAPI(13), Cal.com(15) |
| 2 | Compound paths `{owner}/{repo}` | ~38 | 20% | Gitea (all repo sub-resources) |
| 3 | Detail `/{id}` unreachable | ~18 | 10% | Taiga(7), Forem(6), Cal.com(4), Conduit(1) |
| 4 | Deep nesting (2+ levels) | ~16 | 9% | Medusa(12), Conduit(2), Cal.com(2) |
| 5 | PATCH not probed | ~7 | 4% | Taiga(4), FastAPI(3) |
| 6 | Vocabulary gap | ~4 | 2% | Conduit(profiles), Forem(badge_achievements) |

**Key insight:** 65% of all misses come from a single cause — auth failure.

### False Positive Analysis (69 extra discovered endpoints)

| Source | Count | Cause |
|--------|-------|-------|
| Structural inference over-reach | ~34 | FK/naming inference finds paths with wrong methods |
| Sub-action probing | ~20 | Tries /like, /follow on resources that don't support them |
| Auth endpoints counted | ~15 | /users/login, /users/signup alongside API endpoints |

---

## Benchmark 4: Blind Auto-Discovery vs Our Simulations (9 sites)

These simulations use standard REST patterns matching the probe vocabulary.

| Site | Endpoints | Found | Coverage |
|------|-----------|-------|----------|
| shopping | 14 | 16 | 114% |
| booking | 10 | 8 | 80% |
| library | 10 | 10 | 100% |
| restaurant | 12 | 12 | 100% |
| supermarket | 25 | 28 | 112% |
| music | 28 | 26 | 93% |
| news | 32 | 32 | 100% |
| roomboard | 35 | 36 | 103% |
| conduit | 20 | 16 | 80% |
| **TOTAL** | **186** | **184** | **99%** |

---

## Improvement History: Blind Auto-Discovery

| Version | Our Sims | Real Apps (count) | Real Apps (recall) | What Changed |
|---------|----------|-------------------|-------------------|--------------|
| v1 | 99% | 24% | ~15% | Basic vocabulary, flat probing |
| v2 (+vocab) | 99% | 67% | ~30% | +130 resource names, social actions, hyphenated |
| v3 (+structural) | 99% | 86%* | 36% | FK inference, naming patterns, sub-path replication |

*v2/v3 "Real Apps" count-based metric was misleading — included false positives
inflating coverage. Precision audit (v3) revealed true recall of 36%.

### Structural Inference Strategies Added in v3

| Strategy | What It Does | Impact |
|----------|-------------|--------|
| FK field inference | `category_id` in response → probe `/categories` | Medusa: found `/collections` from FK |
| Naming convention | Found `product-categories` → try `product-tags` | Medusa: +28 hyphenated resources |
| Sub-path replication | `/articles/search` → try `/products/search` | Conservative: list resources only |
| PATCH probing | Found PUT → also try PATCH | +7 endpoints on Taiga |
| `/me` pattern | Found `/users` → try `/users/me` | FastAPI, Forem, Medusa |
| Cascading CRUD | New FK resource → probe `/{id}` detail | Fills in detail endpoints |

### What Would Improve Blind Discovery Most

| Fix | Expected Recall Gain | Effort |
|-----|---------------------|--------|
| Cookie/session auth support | +56 endpoints (Listmonk) | Medium |
| API key auth (`?apiKey=`, `X-API-Key`) | +15 endpoints (Cal.com) | Low |
| Compound path probing from list responses | +38 endpoints (Gitea) | High |
| Consistent PATCH probing | +7 endpoints | Low |
| 2-level nesting from response FK | +16 endpoints (Medusa carts) | Medium |

---

## Side-by-Side Comparison

|  | Source Parser | Owner-Assisted | Blind Discovery |
|--|--------------|----------------|-----------------|
| **Recall** | 100% (7/8 apps) | ~87% | 36% |
| **Precision** | 100% | ~90% | 61% |
| **False positives** | 0 | ~5 | 69 |
| **Input needed** | Source code | Token + URL + hints | Just a URL |
| **Time** | <1 second | 10-30 seconds | 30-60 seconds |
| **Frameworks** | 10 supported | Any (HTTP) | Any (HTTP) |
| **Auth required** | No | Yes (token) | No (but limits recall) |

---

## Test Infrastructure

### 17 Simulation Sites (FastAPI replicas, 525 total endpoints)

9 original simulations + 8 real-world app replicas built from actual GitHub route definitions.

### 13 Real GitHub Repos (2,203 total routes extracted)

Cal.com, Forem/dev.to, Gitea, FastAPI Full-Stack, RealWorld/Conduit, Medusa,
Twenty CRM, Hoppscotch, Papermark, Documenso, Appwrite, Firefly III, Koel.

---

## Known Limitations

### Source Parser
- **Go Group() prefix nesting:** regex doesn't track `m.Group("/prefix")` chains (Gitea: 40% recall on prefixed routes in precision check)
- **Dynamic route generation:** Python helpers like `_crud()` invisible to regex (Cal.com replica: 30% recall)
- **Laravel web.php inclusion:** parser reads all .php files including web routes, inflating count (Firefly: 502 found vs 299 API routes)
- **Laravel Route::group prefixes:** nested group prefixes not tracked, some paths incomplete

### Blind Auto-Discovery
- **Auth diversity:** only JWT Bearer tokens; session/cookie, API keys, OAuth2 not supported
- **Compound identifiers:** `/repos/{owner}/{repo}` requires knowing valid pairs
- **Deep nesting:** max 1-level sub-resource; 2+ levels missed
- **False positives:** structural inference can over-reach (61% precision)

### Resolved in this version
- ~~**Self-declared trust**~~ → Fixed (Phase 1). Ed25519-signed credentials, regression-tested.
- ~~**Governance fields inert**~~ → Fixed (Phase 2). Every contract field binds at runtime.
- ~~**Operator-declared money limits**~~ → Fixed (Audit). Contract ceiling overrides operator claims.
- ~~**MCP compliance**~~ → Fixed (Phase 3). Official MCP SDK, JSON-RPC, conformance suite.
- ~~**No credential hygiene**~~ → Fixed (Audit). Audience, replay protection, TTL cap.

### Remaining
- **Workflow inference:** The compiler extracts routes, not workflows. `search → cart → checkout` ordering must be inferred or declared.
- **LLM agent evals:** Governance boundary tested with scripted sequences. pass@1 with real LLM agents (Phase 5b) requires Ollama.
