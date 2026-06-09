# Single Dockerfile for all 12 KIO shells.
# Build arg KIO_ID selects which shell runs (e.g. kio3, kio5).
#
# Build:  docker build --build-arg KIO_ID=kio3 -t kio3 -f apps/kio_shells/Dockerfile .
# Run:    docker run -e KIO_ID=kio3 -p 8013:8013 kio3

ARG KIO_ID=kio3

FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY shared/ ./shared/
COPY apps/kio_shells/ ./apps/kio_shells/

RUN uv pip install --system --no-cache ./shared && \
    uv pip install --system --no-cache \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "loguru>=0.7" \
        "pytest>=7.4" \
        "httpx>=0.24" \
        "sqlalchemy>=2.0" \
        "psycopg2-binary>=2.9" \
        "bcrypt>=4.0" \
        "passlib[bcrypt]>=1.7" \
        "python-jose[cryptography]>=3.3" \
        "pyjwt>=2.8" \
        "cryptography>=41.0" \
        "aiosqlite>=0.19" \
        "email-validator>=2.0" \
        "python-multipart>=0.0.9"

ENV PYTHONPATH=/app:/app/apps/kio_shells

ARG KIO_ID
ENV KIO_ID=${KIO_ID}

# Port is set by docker-compose via KIO_PORT env; default to 8013
EXPOSE 8013

# Dynamically select the KIO shell to run
CMD uvicorn apps.kio_shells.${KIO_ID}.main:app --host 0.0.0.0 --port ${KIO_PORT:-8013}
