from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api_routes import router
from .connectome_loader import load_connectome
from .simulation_engine import SimulationEngine


def _setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def create_app() -> FastAPI:
    _setup_logging()

    root = Path(__file__).resolve().parents[1]
    frontend_dir = root / "frontend"

    app = FastAPI(title="Drosophila Connectome Simulator")
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(frontend_dir / "index.html")

    @app.on_event("startup")
    def _startup() -> None:
        connectome_path = os.getenv("CONNECTOME_PATH", str(root / "data" / "connectome_subset.parquet"))
        max_neurons = int(os.getenv("MAX_NEURONS", "3000"))
        max_edges = int(os.getenv("MAX_EDGES", "40000"))

        dt_ms = float(os.getenv("DT_MS", "0.2"))
        chunk_ms = float(os.getenv("CHUNK_MS", "10.0"))
        codegen = os.getenv("BRIAN_CODEGEN", "numpy")

        connectome = load_connectome(
            connectome_path,
            max_neurons=max_neurons,
            max_edges=max_edges,
            cache=True,
            synthetic_if_missing=True,
        )
        app.state.connectome = connectome

        sim = SimulationEngine(connectome, dt_ms=dt_ms, chunk_ms=chunk_ms, brian_codegen_target=codegen)
        sim.start()
        app.state.sim = sim

        logging.getLogger(__name__).info(
            "Ready: connectome=%s (N=%d, M=%d)",
            connectome_path,
            len(connectome.neuron_ids),
            len(connectome.weights),
        )

    @app.on_event("shutdown")
    def _shutdown() -> None:
        sim = getattr(app.state, "sim", None)
        if sim is not None:
            sim.stop()

    return app


app = create_app()

