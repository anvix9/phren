# Contributing to Phren

Thank you for your interest in contributing to Phren.

## Development Setup

```bash
git clone https://github.com/phren-agent/phren.git
cd phren
pip install -e ".[dev]"
pytest
```

## Project Structure

```
phren/              # Python package (Apache-2.0)
├── compiler/         # Route discovery tools
├── contract/         # Schema + permission resolver
├── terminal/         # Engine, agents, registry
└── mcp/              # MCP server
simulations/          # Test sites (not part of the package)
evals/                # Test harness
```

## Running Tests

```bash
pytest evals/ -v
```

## Code Style

- Python 3.11+
- Type hints on public interfaces
- Docstrings on modules and public classes

## Boundary Rule

Phren is open-core. The boundary is structural:

- **`phren/`** is Apache-2.0, fully functional standalone. It never imports from any commercial layer.
- Commercial features (registry, analytics, audit) live in a separate repository and consume `phren` as a dependency — never the inverse.

If your contribution adds a dependency on anything outside `phren/`, it belongs in the commercial repo, not here.

## Submitting Changes

1. Fork the repository
2. Create a branch (`git checkout -b fix/description`)
3. Make your changes with tests
4. Run `pytest` — all tests must pass
5. Submit a pull request

## Reporting Security Issues

See [SECURITY.md](SECURITY.md).
