FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy shared library first (layer cache friendly)
COPY shared/ ./shared/
COPY apps/orchestrator/ ./apps/orchestrator/
COPY apps/kio_shells/kio_base.py ./apps/kio_shells/kio_base.py

# Install shared + orchestrator deps
RUN uv pip install --system --no-cache \
    ./shared \
    ./apps/orchestrator

ENV PYTHONPATH=/app
WORKDIR /app/apps/orchestrator

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
