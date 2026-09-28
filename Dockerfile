FROM python:3.12-slim

LABEL maintainer="Phren" \
      description="Phren — governed terminals for AI agents" \
      version="0.2.0"

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (cache layer)
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    fastapi>=0.100 \
    uvicorn>=0.20 \
    PyJWT>=2.6 \
    pydantic>=2.0 \
    cryptography>=41.0 \
    requests>=2.28 \
    mcp>=1.0.0

# Copy project
COPY phren/ phren/
COPY simulations/ simulations/
COPY evals/ evals/
COPY conformance/ conformance/
COPY docs/ docs/
COPY README.md RESULTS.md SPEC.md LICENSE ./

# Install phren
RUN pip install --no-cache-dir -e .

# Verify install
RUN phren --help > /dev/null 2>&1

# Default: run the demo
EXPOSE 8000
ENTRYPOINT ["phren"]
CMD ["demo"]
