FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY shared/ ./shared/
COPY apps/lm_engine/ ./apps/lm_engine/

RUN uv pip install --system --no-cache \
    ./shared \
    ./apps/lm_engine

ENV PYTHONPATH=/app
WORKDIR /app/apps/lm_engine

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
