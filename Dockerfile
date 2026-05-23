FROM node:20-alpine AS ui-builder

WORKDIR /ui

COPY ui/package.json ./
RUN npm install

COPY ui/ ./
RUN npm run build

FROM python:3.12-slim AS builder

WORKDIR /app

# Install poetry
RUN pip install --no-cache-dir poetry==1.8.3

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install production dependencies only
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --only main --no-root

# Copy source code
COPY condense/ ./condense/
COPY condense.default.yaml ./

# Install the package
RUN poetry install --no-interaction --no-ansi --only main

# --- Production image ---
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages and source
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/condense ./condense
COPY --from=builder /app/condense.default.yaml ./
COPY --from=ui-builder /ui/dist ./ui/dist

# Create non-root user
RUN useradd --create-home --shell /bin/bash condense
USER condense

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=3)" || exit 1

ENTRYPOINT ["python", "-m", "condense", "start"]
