from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CACHE_VERSION = 2


@dataclass(frozen=True)
class Connectome:
    graph: nx.DiGraph
    neuron_ids: np.ndarray  # (N,) int64
    positions: np.ndarray  # (N, 3) float32
    types: list[str | None]  # len N

    edges_pre: np.ndarray  # (M,) int32 indices into [0..N)
    edges_post: np.ndarray  # (M,) int32
    weights: np.ndarray  # (M,) float32 (dataset units, typically synapse counts)
    delays_ms: np.ndarray  # (M,) float32

    id_to_index: dict[int, int]

    neurons_payload: dict[str, Any]
    connections_payload: dict[str, Any]


def _default_cache_path(parquet_path: Path) -> Path:
    return parquet_path.with_suffix(".nx.pkl")


def load_connectome(
    parquet_path: str | Path,
    *,
    max_neurons: int = 3000,
    max_edges: int = 40000,
    cache: bool = True,
    cache_path: str | Path | None = None,
    synthetic_if_missing: bool = True,
    seed: int = 7,
) -> Connectome:
    """
    Load a Drosophila connectome subset from Parquet and build:
      - NetworkX DiGraph (nodes=neurons, edges=synapses)
      - compact arrays for simulation + visualization

    Expected Parquet schema (edge table):
      pre_id, post_id, weight, delay_ms,
      pre_x, pre_y, pre_z, post_x, post_y, post_z
    """
    parquet_path = Path(parquet_path)
    if cache_path is None:
        cache_path = _default_cache_path(parquet_path)
    cache_path = Path(cache_path)

    if not parquet_path.exists():
        if not synthetic_if_missing:
            raise FileNotFoundError(
                f"Connectome Parquet not found: {parquet_path}. "
                "Run data/scripts/download_neuprint_subset.py to create it."
            )
        logger.warning(
            "Connectome Parquet missing (%s). Generating synthetic placeholder.",
            parquet_path,
        )
        return generate_synthetic_connectome(
            n_neurons=max_neurons,
            n_edges=max_edges,
            seed=seed,
        )

    if cache and cache_path.exists():
        try:
            cache_newer = cache_path.stat().st_mtime >= parquet_path.stat().st_mtime
        except OSError:
            cache_newer = False
        if cache_newer:
            try:
                with cache_path.open("rb") as f:
                    cached = pickle.load(f)
                if (
                    isinstance(cached, dict)
                    and cached.get("version") == _CACHE_VERSION
                    and cached.get("max_neurons") == max_neurons
                    and cached.get("max_edges") == max_edges
                ):
                    return cached["connectome"]
            except Exception:
                logger.exception("Failed to load cache: %s (rebuilding)", cache_path)

    edges_df = pd.read_parquet(parquet_path)
    connectome = _build_from_edge_table(
        edges_df,
        max_neurons=max_neurons,
        max_edges=max_edges,
    )

    if cache:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open("wb") as f:
                pickle.dump(
                    {
                        "version": _CACHE_VERSION,
                        "max_neurons": max_neurons,
                        "max_edges": max_edges,
                        "connectome": connectome,
                    },
                    f,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
        except Exception:
            logger.exception("Failed to write cache: %s", cache_path)

    return connectome


def _build_from_edge_table(
    edges_df: pd.DataFrame,
    *,
    max_neurons: int,
    max_edges: int,
) -> Connectome:
    df = edges_df.copy()

    required = {"pre_id", "post_id"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Edge table missing required columns: {sorted(missing)}")

    if "weight" not in df.columns:
        df["weight"] = 1.0
    if "delay_ms" not in df.columns:
        df["delay_ms"] = 1.0

    df["weight"] = df["weight"].astype("float32")
    df["delay_ms"] = df["delay_ms"].astype("float32")

    # Edge downsampling first (keeps memory predictable).
    if len(df) > max_edges:
        df = df.nlargest(max_edges, "weight", keep="all")

    # Node selection: keep a dense, interesting subgraph (by degree within edge table).
    node_counts = pd.concat([df["pre_id"], df["post_id"]], ignore_index=True).value_counts()
    if len(node_counts) > max_neurons:
        keep_ids = set(node_counts.head(max_neurons).index.astype("int64").tolist())
        df = df[df["pre_id"].isin(keep_ids) & df["post_id"].isin(keep_ids)]

    # Final edge cap after node filtering.
    if len(df) > max_edges:
        df = df.nlargest(max_edges, "weight", keep="all")

    # Build node table from coords if provided (pre_* and post_*).
    node_rows: list[pd.DataFrame] = []
    coord_sets = [
        ("pre_id", "pre_x", "pre_y", "pre_z", "pre_type"),
        ("post_id", "post_x", "post_y", "post_z", "post_type"),
    ]
    for id_col, x_col, y_col, z_col, type_col in coord_sets:
        if {x_col, y_col, z_col}.issubset(df.columns):
            cols = [id_col, x_col, y_col, z_col]
            if type_col in df.columns:
                cols.append(type_col)
            chunk = df[cols].rename(
                columns={
                    id_col: "neuron_id",
                    x_col: "x",
                    y_col: "y",
                    z_col: "z",
                    type_col: "type",
                }
            )
            node_rows.append(chunk.dropna(subset=["x", "y", "z"]))

    if node_rows:
        nodes_df = (
            pd.concat(node_rows, ignore_index=True)
            .drop_duplicates(subset=["neuron_id"], keep="first")
            .reset_index(drop=True)
        )
    else:
        # No coordinates in the edge table: put neurons on a synthetic ellipsoid.
        logger.warning("No coordinate columns found; generating synthetic positions.")
        all_ids = pd.concat([df["pre_id"], df["post_id"]]).drop_duplicates().astype("int64")
        nodes_df = pd.DataFrame({"neuron_id": all_ids})
        positions = _synthetic_positions(len(nodes_df), seed=13)
        nodes_df["x"] = positions[:, 0]
        nodes_df["y"] = positions[:, 1]
        nodes_df["z"] = positions[:, 2]

    nodes_df["neuron_id"] = nodes_df["neuron_id"].astype("int64")
    nodes_df = nodes_df.sort_values("neuron_id", kind="stable").reset_index(drop=True)

    neuron_ids = nodes_df["neuron_id"].to_numpy(dtype=np.int64)
    positions = nodes_df[["x", "y", "z"]].to_numpy(dtype=np.float32)
    types: list[str | None]
    if "type" in nodes_df.columns:
        raw_types = nodes_df["type"].tolist()
        types = []
        for t in raw_types:
            if t is None:
                types.append(None)
                continue
            try:
                if pd.isna(t):
                    types.append(None)
                else:
                    types.append(str(t))
            except Exception:
                types.append(str(t))
    else:
        types = [None] * len(nodes_df)

    id_to_index = {int(nid): int(i) for i, nid in enumerate(neuron_ids.tolist())}

    pre_idx = df["pre_id"].map(id_to_index)
    post_idx = df["post_id"].map(id_to_index)
    valid = pre_idx.notna() & post_idx.notna()
    df = df[valid].reset_index(drop=True)
    pre_idx = pre_idx[valid].astype("int32").to_numpy()
    post_idx = post_idx[valid].astype("int32").to_numpy()

    weights = df["weight"].to_numpy(dtype=np.float32)
    delays_ms = df["delay_ms"].to_numpy(dtype=np.float32)

    g = nx.DiGraph()
    for i, nid in enumerate(neuron_ids.tolist()):
        x, y, z = positions[i].tolist()
        g.add_node(
            int(nid),
            neuron_id=int(nid),
            index=int(i),
            x=float(x),
            y=float(y),
            z=float(z),
            type=types[i],
        )
    # Add edges using original IDs (attributes in dataset units).
    for pre_id, post_id, w, d in zip(
        df["pre_id"].astype("int64").tolist(),
        df["post_id"].astype("int64").tolist(),
        weights.tolist(),
        delays_ms.tolist(),
        strict=True,
    ):
        g.add_edge(int(pre_id), int(post_id), weight=float(w), delay_ms=float(d))

    neurons_payload = {
        "neuron_ids": neuron_ids.tolist(),
        "positions": positions.tolist(),
        "types": types,
    }
    connections_payload = {
        "pre": pre_idx.tolist(),
        "post": post_idx.tolist(),
        "weight": weights.tolist(),
        "delay_ms": delays_ms.tolist(),
    }

    return Connectome(
        graph=g,
        neuron_ids=neuron_ids,
        positions=positions,
        types=types,
        edges_pre=pre_idx,
        edges_post=post_idx,
        weights=weights,
        delays_ms=delays_ms,
        id_to_index=id_to_index,
        neurons_payload=neurons_payload,
        connections_payload=connections_payload,
    )


def generate_synthetic_connectome(
    *,
    n_neurons: int,
    n_edges: int,
    seed: int = 7,
) -> Connectome:
    """
    Synthetic placeholder graph used only when real Parquet data is unavailable.
    Creates distance-biased random wiring on an ellipsoid "brain" volume.
    """
    rng = np.random.default_rng(seed)
    neuron_ids = np.arange(1, n_neurons + 1, dtype=np.int64)
    positions = _synthetic_positions(n_neurons, seed=seed).astype(np.float32)

    # Distance-biased outgoing edges (cheap approximation).
    pre = rng.integers(0, n_neurons, size=n_edges, endpoint=False)
    post = rng.integers(0, n_neurons, size=n_edges, endpoint=False)
    mask = pre != post
    pre = pre[mask]
    post = post[mask]
    if len(pre) > n_edges:
        pre = pre[:n_edges]
        post = post[:n_edges]

    diffs = positions[pre] - positions[post]
    dist = np.linalg.norm(diffs, axis=1)
    # Prefer short-range connections; keep a subset accordingly.
    keep_prob = np.exp(-dist / (dist.mean() + 1e-6))
    keep = rng.random(len(dist)) < keep_prob
    pre = pre[keep]
    post = post[keep]

    weights = rng.integers(1, 8, size=len(pre)).astype(np.float32)
    delays_ms = rng.uniform(1.0, 8.0, size=len(pre)).astype(np.float32)

    g = nx.DiGraph()
    types = [None] * n_neurons
    id_to_index = {int(nid): int(i) for i, nid in enumerate(neuron_ids.tolist())}
    for i, nid in enumerate(neuron_ids.tolist()):
        x, y, z = positions[i].tolist()
        g.add_node(int(nid), neuron_id=int(nid), index=int(i), x=float(x), y=float(y), z=float(z))
    for pre_i, post_i, w, d in zip(pre.tolist(), post.tolist(), weights.tolist(), delays_ms.tolist(), strict=True):
        pre_id = int(neuron_ids[pre_i])
        post_id = int(neuron_ids[post_i])
        g.add_edge(pre_id, post_id, weight=float(w), delay_ms=float(d))

    neurons_payload = {
        "neuron_ids": neuron_ids.tolist(),
        "positions": positions.tolist(),
        "types": types,
    }
    connections_payload = {
        "pre": pre.astype(np.int32).tolist(),
        "post": post.astype(np.int32).tolist(),
        "weight": weights.tolist(),
        "delay_ms": delays_ms.tolist(),
    }

    return Connectome(
        graph=g,
        neuron_ids=neuron_ids,
        positions=positions,
        types=types,
        edges_pre=pre.astype(np.int32),
        edges_post=post.astype(np.int32),
        weights=weights,
        delays_ms=delays_ms,
        id_to_index=id_to_index,
        neurons_payload=neurons_payload,
        connections_payload=connections_payload,
    )


def _synthetic_positions(n: int, *, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # Ellipsoid-ish cloud with slight anisotropy, roughly "brain-shaped".
    pts = rng.normal(size=(n, 3)).astype(np.float32)
    pts[:, 0] *= 120.0
    pts[:, 1] *= 70.0
    pts[:, 2] *= 55.0
    return pts
