FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CLAIMS_DATA_DIR=/app/data \
    TRACE_DIR=/app/var/traces \
    AUDIT_LOG=/app/var/audit/decisions.jsonl

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .
COPY data ./data

# Run as a non-root user; only /app/var is writable (traces and the audit log).
RUN useradd --uid 10001 --no-create-home app && mkdir -p /app/var && chown app /app/var
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "claims_assistant.api:app", "--host", "0.0.0.0", "--port", "8000"]
