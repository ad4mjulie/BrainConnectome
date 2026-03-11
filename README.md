# Drosophila Connectome Simulator (hemibrain / neuPrint)

interactive 3D visualization and spiking simulation for a *subset* of the **Drosophila melanogaster** connectome.

## What this project does

- Loads real connectome edges + neuron coordinates (neuPrint hemibrain recommended).
- Reconstructs a directed synapse graph (NetworkX).
- Runs a biologically plausible spiking simulation (Brian2 LIF).
- Streams activity to a Three.js WebGL viewer (instanced neurons + line synapses).

## Quickstart

### 1) Backend setup

```bash
cd brain_connectome_sim
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

### 2) Download a real connectome subset (neuPrint hemibrain)

1. Create a neuPrint account and generate an API token (neuPrint UI → account/settings).
2. Export `NEUPRINT_TOKEN` in your shell.
3. Run:

```bash
python data/scripts/download_neuprint_subset.py \
  --dataset hemibrain:v1.2.1 \
  --max-neurons 3000 \
  --max-edges 40000 \
  --out data/connectome_subset.parquet
```

Tip: to focus on a neuropil, add an ROI filter, e.g.:

```bash
python data/scripts/download_neuprint_subset.py --roi EB --max-neurons 2000 --out data/connectome_subset.parquet
```

If you skip this step, the backend will generate a **synthetic placeholder** connectome (useful for UI smoke-testing only).

### 3) Run the app

```bash
uvicorn backend.server:app --reload --port 8000
```

Open:
- `http://localhost:8000`

## Configuration (env vars)

- `CONNECTOME_PATH` (default `data/connectome_subset.parquet`)
- `MAX_NEURONS` (default `3000`)
- `MAX_EDGES` (default `40000`)
- `DT_MS` (default `0.2`)
- `CHUNK_MS` (default `10.0`)
- `BRIAN_CODEGEN` (`numpy` default; try `cython` for speed if you have a compiler)

## Architecture

- Backend: FastAPI app that loads the connectome, runs Brian2 in a background thread, and serves JSON to the UI.
- Simulation: Leaky integrate-and-fire neurons with current-based synapses and per-edge delays.
- Frontend: Three.js renderer with instanced neuron meshes and line segments for synapses.

Key files:

- `backend/server.py`: app startup, connectome load, simulation start/stop, static file mount
- `backend/connectome_loader.py`: Parquet parsing, graph building, payload packing, synthetic fallback
- `backend/simulation_engine.py`: Brian2 network, background run loop, snapshots
- `frontend/app.js`: fetches data, handles selection/stimulation, updates HUD
- `frontend/renderer.js`: GPU scene, neuron coloring, pulses, picking

## REST API

- `GET /neurons`: neuron ids, positions, types
- `GET /connections`: pre/post arrays, weights, delays
- `GET /activity`: simulation time, per-neuron activity, spike indices
- `POST /stimulate`: stimulate by `neuron_index` or `neuron_id`

Example payload:

```json
{ "neuron_index": 42, "amplitude_pA": 800, "duration_ms": 20 }
```

## Frontend controls

- Click: select a neuron
- Shift+Click: stimulate the neuron under the cursor
- Stimulate button: stimulate the currently selected neuron

## Visualization notes

- Neurons are rendered as instanced spheres, sized by degree.
- Spikes are shown by color changes and traveling pulses.
- Activity is downsampled to keep updates smooth for a few thousand neurons.

## Troubleshooting

- If `t` stays at `0 ms`, the simulation thread is not running. Check backend logs and set `BRIAN_CODEGEN=numpy`.
- If you see no data, confirm `CONNECTOME_PATH` points to a valid Parquet file.
- If neuPrint download fails, check `NEUPRINT_TOKEN` and dataset name.

## Alternative data sources (notes)

- **hemibrain**: easiest path via neuPrint API + this repo’s download script.
- **FlyWire**: public access is possible but often requires additional auth + dataset-specific tooling; add a second downloader once you decide your target region / cell types.

## Data format (Parquet)

`data/connectome_subset.parquet` is an *edge table* with columns:

- `pre_id` (int) – presynaptic neuron ID
- `post_id` (int) – postsynaptic neuron ID
- `weight` (float) – connection strength (e.g., synapse count)
- `delay_ms` (float) – synaptic delay in ms
- `pre_x`, `pre_y`, `pre_z` (float) – pre neuron coordinates
- `post_x`, `post_y`, `post_z` (float) – post neuron coordinates

## Notes on scientific plausibility

- Neurons are simulated as leaky integrate-and-fire units with synaptic current decay.
- Edge weights from the dataset are mapped to synaptic current increments via a scale factor.
- Synaptic delays (ms) are applied directly in the Brian2 `Synapses.delay` field.

## Frontend dependency note

The viewer loads Three.js from a CDN via `importmap` in `frontend/index.html`. For offline use, download Three.js locally and update the import map to point at local files.

## neuPrint + Python version note

If you use Python 3.10/3.11, keep `neuprint-python` pinned to `<0.6` (this repo’s `backend/requirements.txt` already does). Newer `neuprint-python` releases use Python 3.12+ f-string parsing features.
