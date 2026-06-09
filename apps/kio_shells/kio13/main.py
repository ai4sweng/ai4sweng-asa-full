"""KIO13 — Developer Training for Using AI-Powered Tools

Generates personalised training materials, interactive tutorials, and
skill-gap assessments to onboard developers onto AI-powered toolchains.
Adapts content to the developer's existing knowledge level and the
specific AI tools deployed in the project.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import uvicorn
from kio_base import make_kio_app, placeholder_handler
from shared.config import get_settings

KIO_ID = "kio13"
TITLE = "Developer Training for Using AI-Powered Tools"

app = make_kio_app(
    KIO_ID,
    TITLE,
    placeholder_handler(KIO_ID, "developer_training"),
)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8023)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
