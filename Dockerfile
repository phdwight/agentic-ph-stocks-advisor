# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder

WORKDIR /app

# Install build-time OS deps (for psycopg2-binary wheels, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install runtime deps from the pip-compile-locked requirements.txt
# for fully reproducible builds. The postgres extra (psycopg2-binary) is
# not in the lock file (pip-compile is run without extras), so it's
# installed separately using the constraint declared in pyproject.toml.
COPY pyproject.toml requirements.txt ./
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt && \
    /opt/venv/bin/pip install --no-cache-dir "psycopg2-binary>=2.9"

# Install the project itself last (deps are already locked & installed)
COPY . .
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps .

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.14-slim

WORKDIR /app

# Runtime libs: libpq5 for psycopg2, curl for health checks
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Bring the pre-built venv from the builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Default: run the CLI advisor. docker-compose overrides entrypoint
# for the web and worker services.
ENTRYPOINT ["ph-advisor"]
