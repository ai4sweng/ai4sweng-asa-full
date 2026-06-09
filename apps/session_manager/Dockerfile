FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY shared/ ./shared/
COPY apps/session_manager/ ./apps/session_manager/
COPY alembic.ini ./

RUN uv pip install --system --no-cache \
    ./shared \
    ./apps/session_manager

ENV PYTHONPATH=/app
WORKDIR /app/apps/session_manager

EXPOSE 8002
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002"]
