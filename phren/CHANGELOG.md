# Changelog

## [0.2.0] - 2026-10-01

### Added
- **Terminal Compiler**: `compile_terminal()` compiles API routes into governed PhrenContract
- **CLI**: `phren info|verify|compile|serve|eval|demo` commands
- **Screen-based navigation**: agents navigate constrained screens, not flat tool lists
- **Path trace system**: breadcrumb trail with flow hints guides agent navigation
- **Ed25519 trust model**: signed credentials, derived authority, operator registry
- **Fail-closed validation**: contracts without `max_transaction_amount` are rejected
- **Three simulation archetypes**: shopping (e-commerce), booking (hospitality), library (government)
- **Agent evaluation suite**: 7 tasks × 6 models (0.8B–8B), 126/126 pass rate
- **Docker support**: Dockerfile + docker-compose.yml for self-hosting
- **GitHub Actions CI**: test + build + publish workflows
- **PyPI package**: `pip install phren`

### Architecture
- Terminal = governance + flow + context between agent and API
- Screen-based constrains agent choice space (vs flat terminal: 0% → 100%)
- Generic governance: system prompt references roles, never specific action names
- MCP SDK 2.2.0 compliance with streamable HTTP transport

## [0.1.0] - 2026-09-15

### Added
- Initial contract schema and enforcement engine
- Source route parser (10 frameworks, 13 repos, 2,203 routes)
- MCP server with stdio and HTTP transports
- Basic test suite
