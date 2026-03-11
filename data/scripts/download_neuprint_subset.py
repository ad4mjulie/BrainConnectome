#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

import numpy as np
import pandas as pd


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Download a hemibrain connectome subset from neuPrint and save as Parquet."
    )
    p.add_argument("--server", default="https://neuprint.janelia.org", help="neuPrint server URL")
    p.add_argument("--dataset", default="hemibrain:v1.2.1", help="neuPrint dataset name")
    p.add_argument("--token", default=os.getenv("NEUPRINT_TOKEN"), help="neuPrint API token (or set NEUPRINT_TOKEN)")
    p.add_argument("--max-neurons", type=int, default=3000, help="Number of neurons to include")
    p.add_argument("--max-edges", type=int, default=40000, help="Number of edges to include")
    p.add_argument("--min-weight", type=float, default=2.0, help="Minimum total synapse weight to keep")
    p.add_argument(
        "--roi",
        action="append",
        default=[],
        help="Restrict neurons to those intersecting this ROI (repeatable), e.g. --roi EB",
    )
    p.add_argument(
        "--roi-req",
        choices=["any", "all"],
        default="any",
        help="When multiple --roi are provided, require any vs all",
    )
    p.add_argument("--min-pre", type=int, default=0, help="Minimum total output synapses (pre)")
    p.add_argument("--min-post", type=int, default=0, help="Minimum total input synapses (post)")
    p.add_argument(
        "--out",
        default="data/connectome_subset.parquet",
        help="Output Parquet path (edge table)",
    )
    p.add_argument("--base-delay-ms", type=float, default=0.8, help="Base synaptic delay (ms)")
    p.add_argument(
        "--velocity-um-per-ms",
        type=float,
        default=400.0,
        help="Conduction velocity used for distance->delay (µm/ms)",
    )
    return p.parse_args()

def _maybe_load_dotenv(repo_root: Path) -> None:
    """
    Minimal .env loader to avoid passing tokens on the command line.
    Supports KEY=VALUE lines; ignores comments and blank lines.
    """
    for candidate in (repo_root / "backend" / ".env", repo_root / ".env"):
        if not candidate.exists():
            continue
        try:
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = val
        except Exception:
            # If .env exists but can't be read/parsed, fail later with missing token.
            pass


def _retry(label: str, fn, *, attempts: int = 4):
    """
    neuPrint calls can occasionally fail with transient network issues
    (e.g. chunked transfer incomplete reads). Retry with backoff.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if i >= attempts - 1:
                break
            sleep_s = 1.5 * (2**i)
            print(f"{label} failed ({type(e).__name__}: {e}); retrying in {sleep_s:.1f}s…", flush=True)
            time.sleep(sleep_s)
    raise SystemExit(
        f"{label} failed after {attempts} attempts ({type(last_exc).__name__}: {last_exc}).\n"
        "If you see an auth error, you likely need a token:\n"
        "  export NEUPRINT_TOKEN=...   (or set it in brain_connectome_sim/backend/.env)\n"
        "Then rerun this script."
    )


def main() -> None:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if not args.token:
        _maybe_load_dotenv(repo_root)
        args.token = os.getenv("NEUPRINT_TOKEN")

    # neuprint-python
    try:
        from neuprint import Client  # type: ignore
        from neuprint import NeuronCriteria as NC  # type: ignore
        from neuprint import fetch_adjacencies, fetch_neurons  # type: ignore
    except SyntaxError as e:
        raise SystemExit(
            "Your installed neuprint-python is not compatible with this Python version.\n"
            "If you're on Python 3.10/3.11, install: neuprint-python<0.6\n"
            "Example:\n"
            "  python -m pip install 'neuprint-python<0.6' --upgrade --force-reinstall"
        ) from e

    if not args.token:
        print(
            "NEUPRINT_TOKEN not set; attempting anonymous neuPrint access. "
            "If this fails, create a token and set NEUPRINT_TOKEN or pass --token."
        )
    else:
        print(f"NEUPRINT_TOKEN detected (len={len(args.token)})", flush=True)

    client = Client(args.server, dataset=args.dataset, token=args.token)

    # 1) Fetch neurons (keep this lightweight; omit ROI tables).
    criteria = NC(
        status="Traced",
        cropped=False,
        rois=args.roi or None,
        roi_req=args.roi_req,
        min_pre=args.min_pre,
        min_post=args.min_post,
    )
    def _fetch_neurons_once():
        res = fetch_neurons(criteria, client=client, omit_rois=True)
        return res[0] if isinstance(res, tuple) else res

    neuron_df = _retry("fetch_neurons", _fetch_neurons_once, attempts=4)
    if "somaLocation" not in neuron_df.columns:
        raise SystemExit("neuPrint response missing somaLocation; cannot build 3D positions.")

    neuron_df = neuron_df[neuron_df["somaLocation"].notna()].copy()
    soma = neuron_df["somaLocation"].to_list()
    coords = np.array([v if isinstance(v, (list, tuple, np.ndarray)) and len(v) == 3 else [np.nan, np.nan, np.nan] for v in soma])
    neuron_df["x"] = coords[:, 0]
    neuron_df["y"] = coords[:, 1]
    neuron_df["z"] = coords[:, 2]
    neuron_df = neuron_df.dropna(subset=["x", "y", "z"])

    # Prefer neurons with lots of connectivity (dense subgraph).
    if "pre" in neuron_df.columns and "post" in neuron_df.columns:
        neuron_df["score"] = neuron_df["pre"].astype("float32") + neuron_df["post"].astype("float32")
        subset = neuron_df.nlargest(args.max_neurons, "score", keep="all").head(args.max_neurons).copy()
    else:
        subset = neuron_df.sample(n=min(args.max_neurons, len(neuron_df)), random_state=7).copy()

    subset["bodyId"] = subset["bodyId"].astype("int64")
    body_ids = subset["bodyId"].tolist()

    # 2) Fetch adjacencies among selected bodies.
    def _fetch_adjacencies_once():
        return fetch_adjacencies(body_ids, body_ids, client=client, min_total_weight=args.min_weight)

    _neurons2, conn_df = _retry("fetch_adjacencies", _fetch_adjacencies_once, attempts=4)
    if conn_df.empty:
        raise SystemExit("No connections returned. Try lowering --min-weight or increasing --max-neurons.")

    # fetch_adjacencies returns per-ROI rows; aggregate to total weight.
    needed_cols = {"bodyId_pre", "bodyId_post", "weight"}
    if not needed_cols.issubset(conn_df.columns):
        raise SystemExit(f"Unexpected conn_df columns; expected at least {sorted(needed_cols)}")

    edges = (
        conn_df[["bodyId_pre", "bodyId_post", "weight"]]
        .groupby(["bodyId_pre", "bodyId_post"], as_index=False)["weight"]
        .sum()
        .rename(columns={"bodyId_pre": "pre_id", "bodyId_post": "post_id"})
    )

    # Cap edges by strength.
    if len(edges) > args.max_edges:
        edges = edges.nlargest(args.max_edges, "weight", keep="all").head(args.max_edges)

    # 3) Attach soma coords + type metadata to both endpoints.
    nodes = subset[["bodyId", "type", "x", "y", "z"]].rename(columns={"bodyId": "neuron_id"}).copy()
    nodes["neuron_id"] = nodes["neuron_id"].astype("int64")

    pre_nodes = nodes.rename(
        columns={
            "neuron_id": "pre_id",
            "type": "pre_type",
            "x": "pre_x",
            "y": "pre_y",
            "z": "pre_z",
        }
    )
    post_nodes = nodes.rename(
        columns={
            "neuron_id": "post_id",
            "type": "post_type",
            "x": "post_x",
            "y": "post_y",
            "z": "post_z",
        }
    )

    edges = edges.merge(pre_nodes, on="pre_id", how="left").merge(post_nodes, on="post_id", how="left")
    edges = edges.dropna(subset=["pre_x", "pre_y", "pre_z", "post_x", "post_y", "post_z"]).copy()

    # 4) Estimate synaptic delays from soma-to-soma distances (coords are in nm in hemibrain).
    pre_xyz = edges[["pre_x", "pre_y", "pre_z"]].to_numpy(dtype=np.float32)
    post_xyz = edges[["post_x", "post_y", "post_z"]].to_numpy(dtype=np.float32)
    dist_um = np.linalg.norm(pre_xyz - post_xyz, axis=1) / 1000.0
    delay_ms = args.base_delay_ms + (dist_um / max(args.velocity_um_per_ms, 1e-6))
    edges["delay_ms"] = np.clip(delay_ms, 0.5, 8.0).astype("float32")

    # Final column order (expected by backend loader).
    out_df = edges[
        [
            "pre_id",
            "post_id",
            "weight",
            "delay_ms",
            "pre_x",
            "pre_y",
            "pre_z",
            "post_x",
            "post_y",
            "post_z",
            "pre_type",
            "post_type",
        ]
    ].copy()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_parquet(out_path, index=False)

    print(f"Wrote {out_path} ({len(out_df)} edges, {len(nodes)} nodes candidate pool)")


if __name__ == "__main__":
    main()
