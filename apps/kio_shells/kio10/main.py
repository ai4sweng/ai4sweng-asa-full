"""KIO10 — Energy-Efficiency with Model-Driven TinyML and LLMs

Optimises models for energy-constrained deployment using TinyML techniques
(quantisation, pruning, knowledge distillation) guided by LLMs. Produces
energy-efficiency reports and deployment-ready compact model artifacts.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

import uvicorn
from kio_base import make_kio_app, placeholder_handler
from shared.config import get_settings

KIO_ID = "kio10"
TITLE = "Energy-Efficiency with Model-Driven TinyML and LLMs"

app = make_kio_app(
    KIO_ID,
    TITLE,
    placeholder_handler(KIO_ID, "tinyml_energy_efficiency"),
)

if __name__ == "__main__":
    cfg = get_settings()
    port = cfg.kio_port_map.get(KIO_ID, 8020)
    uvicorn.run("main:app", host=cfg.api_host, port=port, reload=False)
