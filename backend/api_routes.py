from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from pathlib import Path
import asyncio
import json

router = APIRouter()


class StimulateRequest(BaseModel):
    neuron_id: int | None = Field(default=None)
    neuron_index: int | None = Field(default=None)
    amplitude_pA: float = Field(default=800.0)
    duration_ms: float = Field(default=20.0)

    @model_validator(mode="after")
    def _require_target(self) -> "StimulateRequest":
        if self.neuron_id is None and self.neuron_index is None:
            raise ValueError("Provide neuron_id or neuron_index")
        return self


@router.get("/neurons")
def get_neurons(request: Request) -> dict:
    connectome = getattr(request.app.state, "connectome", None)
    if connectome is None:
        raise HTTPException(status_code=503, detail="Connectome not loaded yet")
    return connectome.neurons_payload


@router.get("/connections")
def get_connections(request: Request) -> dict:
    connectome = getattr(request.app.state, "connectome", None)
    if connectome is None:
        raise HTTPException(status_code=503, detail="Connectome not loaded yet")
    return connectome.connections_payload


@router.get("/activity")
def get_activity(request: Request) -> dict:
    sim = getattr(request.app.state, "sim", None)
    if sim is None:
        raise HTTPException(status_code=503, detail="Simulation not started yet")
    snap = sim.snapshot()
    return {
        "t_ms": float(snap.t_ms),
        "activity": snap.activity.tolist(),
        "spikes": snap.spikes.tolist(),
    }


@router.post("/stimulate")
def stimulate(req: StimulateRequest, request: Request) -> dict:
    connectome = getattr(request.app.state, "connectome", None)
    sim = getattr(request.app.state, "sim", None)
    if connectome is None or sim is None:
        raise HTTPException(status_code=503, detail="Connectome or simulation not ready yet")

    idx = (
        int(req.neuron_index)
        if req.neuron_index is not None
        else connectome.id_to_index.get(int(req.neuron_id))
    )
    if idx is None:
        raise HTTPException(status_code=404, detail="Neuron not found")
    sim.stimulate(neuron_index=idx, amplitude_pA=req.amplitude_pA, duration_ms=req.duration_ms)
    return {"ok": True, "neuron_index": idx}


@router.get("/params")
def get_params(request: Request) -> dict:
    sim = getattr(request.app.state, "sim", None)
    if sim is None:
        raise HTTPException(status_code=503, detail="Simulation not ready")
    from dataclasses import asdict
    return asdict(sim.params)


@router.post("/params")
async def update_params(params: dict, request: Request) -> dict:
    sim = getattr(request.app.state, "sim", None)
    if sim is None:
        raise HTTPException(status_code=503, detail="Simulation not ready")
    sim.update_params(params)
    return {"ok": True}


@router.websocket("/ws/activity")
async def websocket_activity(websocket: WebSocket) -> None:
    sim = getattr(websocket.app.state, "sim", None)
    if sim is None:
        await websocket.close(code=1011)
        return

    await websocket.accept()
    q = sim.subscribe()
    try:
        while True:
            # Simple way to get from queue without blocking the event loop
            while not q.empty():
                snap = q.get_nowait()
                await websocket.send_json({
                    "t_ms": float(snap.t_ms),
                    "activity": snap.activity.tolist(),
                    "spikes": snap.spikes.tolist(),
                })
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    finally:
        sim.unsubscribe(q)


@router.get("/mesh/{roi}")
def get_mesh(roi: str, request: Request) -> FileResponse:
    mesh_path = Path(__file__).resolve().parents[1] / "frontend" / "meshes" / f"{roi}.obj"
    if not mesh_path.exists():
        raise HTTPException(status_code=404, detail=f"Mesh for {roi} not found")
    return FileResponse(mesh_path)
