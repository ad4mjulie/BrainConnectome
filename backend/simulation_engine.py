from __future__ import annotations
import brian2 as b2  # type: ignore
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .connectome_loader import Connectome
from .neuron_model import LIFParams

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivitySnapshot:
    t_ms: float
    activity: np.ndarray  # (N,) float32
    spikes: np.ndarray  # (K,) int32 neuron indices that spiked since last snapshot


@dataclass(frozen=True)
class StimulusRequest:
    neuron_index: int
    amplitude_pA: float
    duration_ms: float


class SimulationEngine:
    """
    Runs a Brian2 spiking simulation continuously in a background thread and
    exposes lightweight activity snapshots for visualization.
    """

    def __init__(
        self,
        connectome: Connectome,
        *,
        params: LIFParams | None = None,
        dt_ms: float = 0.2,
        chunk_ms: float = 10.0,
        brian_codegen_target: str = "numpy",
    ) -> None:
        self._connectome = connectome
        self._params = params or LIFParams()
        self._dt_ms = float(dt_ms)
        self._chunk_ms = float(chunk_ms)
        self._brian_codegen_target = brian_codegen_target

        self._lock = threading.Lock()
        self._snapshot = ActivitySnapshot(
            t_ms=0.0,
            activity=np.zeros(len(connectome.neuron_ids), dtype=np.float32),
            spikes=np.zeros(0, dtype=np.int32),
        )

        self._stim_queue: "queue.SimpleQueue[StimulusRequest]" = queue.SimpleQueue()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

        self._subscribers: set[queue.SimpleQueue[ActivitySnapshot]] = set()
        self._sub_lock = threading.Lock()

        self._b2: Any | None = None
        self._net: Any | None = None
        self._group: Any | None = None
        self._spike_mon: Any | None = None
        self._last_spike_idx = 0
        self._active_stims: list[tuple[float, int, float]] = []  # (end_ms, idx, amp_pA)

    @property
    def params(self) -> LIFParams:
        return self._params

    @property
    def dt_ms(self) -> float:
        return self._dt_ms

    @property
    def chunk_ms(self) -> float:
        return self._chunk_ms

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run_loop, name="brian2-sim", daemon=True)
        self._thread.start()

    def stop(self, *, timeout_s: float = 2.0) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=timeout_s)

    def snapshot(self) -> ActivitySnapshot:
        with self._lock:
            # Copy arrays so callers never observe mid-update state.
            return ActivitySnapshot(
                t_ms=self._snapshot.t_ms,
                activity=self._snapshot.activity.copy(),
                spikes=self._snapshot.spikes.copy(),
            )

    def update_params(self, new_params: dict[str, Any]) -> None:
        """Update simulation parameters in real-time."""
        with self._lock:
            for k, v in new_params.items():
                if hasattr(self._params, k):
                    setattr(self._params, k, v)
                else:
                    logger.warning("LIFParams has no attribute %s", k)
            
            # Some parameters might need re-syncing with the Brian2 objects
            # but for LIF models, most can be updated by just updating the
            # namespace or shared variables if they were set up that way.
            # Here we just update the local self._params. 
            # Note: v_th, v_reset, etc. in G are already variables in the Brian namespace
            # but they were passed as constants during build.
            # To make them truly dynamic, we'd need to map them to G variables.
            # For now, we'll focus on weights and rates which are easier.
            if "background_rate_hz" in new_params and hasattr(self, "_bg_group"):
                bg = getattr(self, "_bg_group", None)
                if bg:
                    bg.rates = new_params["background_rate_hz"] * b2.Hz

    def subscribe(self) -> queue.SimpleQueue[ActivitySnapshot]:
        q: queue.SimpleQueue[ActivitySnapshot] = queue.SimpleQueue()
        with self._sub_lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.SimpleQueue[ActivitySnapshot]) -> None:
        with self._sub_lock:
            self._subscribers.discard(q)

    def stimulate(
        self,
        *,
        neuron_index: int,
        amplitude_pA: float = 250.0,
        duration_ms: float = 6.0,
    ) -> None:
        self._stim_queue.put(
            StimulusRequest(
                neuron_index=int(neuron_index),
                amplitude_pA=float(amplitude_pA),
                duration_ms=float(duration_ms),
            )
        )

    def _build_brian_network(self) -> None:
 

        b2.prefs.codegen.target = self._brian_codegen_target
        b2.start_scope()
        b2.defaultclock.dt = self._dt_ms * b2.ms

        p = self._params
        N = len(self._connectome.neuron_ids)

        v_rest = p.v_rest_mV * b2.mV
        v_reset = p.v_reset_mV * b2.mV
        v_th = p.v_th_mV * b2.mV
        tau_m = p.tau_m_ms * b2.ms
        tau_syn = p.tau_syn_ms * b2.ms
        C = p.capacitance_pF * b2.pfarad

        eqs = """
        dv/dt = (v_rest - v)/tau_m + (I_syn + I_ext)/C : volt
        dI_syn/dt = -I_syn/tau_syn : amp
        I_ext : amp
        """

        G = b2.NeuronGroup(
            N,
            eqs,
            threshold="v > v_th",
            reset="v = v_reset",
            refractory=p.refractory_ms * b2.ms,
            method="euler",
            namespace={
                "v_rest": v_rest,
                "v_reset": v_reset,
                "v_th": v_th,
                "tau_m": tau_m,
                "tau_syn": tau_syn,
                "C": C,
            },
        )
        G.v = v_rest
        G.I_ext = 0.0 * b2.pA

        # Synapses from connectome.
        S = b2.Synapses(G, G, model="w: amp", on_pre="I_syn_post += w")
        pre = self._connectome.edges_pre.astype(np.int32, copy=False)
        post = self._connectome.edges_post.astype(np.int32, copy=False)
        S.connect(i=pre, j=post)

        w_pA = (self._connectome.weights * p.weight_scale_pA).astype(np.float32, copy=False)
        if p.max_abs_weight_pA > 0:
            w_pA = np.clip(w_pA, -p.max_abs_weight_pA, p.max_abs_weight_pA)
        S.w = w_pA * b2.pA

        d_ms = np.maximum(self._connectome.delays_ms, 0.0).astype(np.float32, copy=False)
        S.delay = d_ms * b2.ms

        # Background Poisson drive (helps keep dynamics visible without constant manual stimulation).
        objs: list[Any] = [G, S]
        if p.background_rate_hz > 0 and p.background_weight_pA != 0:
            BG = b2.PoissonGroup(N, rates=p.background_rate_hz * b2.Hz)
            self._bg_group = BG
            BG_S = b2.Synapses(BG, G, model="w: amp", on_pre="I_syn_post += w")
            BG_S.connect(j="i")  # one-to-one
            BG_S.w = p.background_weight_pA * b2.pA
            BG_S.delay = 0.0 * b2.ms
            objs.extend([BG, BG_S])

        spike_mon = b2.SpikeMonitor(G)
        objs.append(spike_mon)

        net = b2.Network(*objs)

        self._b2 = b2
        self._net = net
        self._group = G
        self._spike_mon = spike_mon
        self._last_spike_idx = 0
        self._active_stims.clear()

        logger.info(
            "Brian2 network built: N=%d neurons, M=%d synapses (dt=%.3fms, chunk=%.1fms, codegen=%s)",
            N,
            len(pre),
            self._dt_ms,
            self._chunk_ms,
            self._brian_codegen_target,
        )

    def _run_loop(self) -> None:
        try:
            self._build_brian_network()
        except Exception:
            logger.exception("Failed to initialize Brian2 simulation network.")
            return

        assert self._b2 is not None and self._net is not None and self._group is not None and self._spike_mon is not None
        b2 = self._b2
        net = self._net
        G = self._group
        spike_mon = self._spike_mon

        activity = np.zeros(len(self._connectome.neuron_ids), dtype=np.float32)
        decay = float(np.exp(-self._chunk_ms / max(self._params.activity_tau_ms, 1e-6)))

        while not self._stop_evt.is_set():
            t0 = time.perf_counter()

            now_ms = float(net.t / b2.ms)
            self._apply_stimuli(now_ms, G, b2)

            net.run(self._chunk_ms * b2.ms, report=None)

            # Read spikes from monitor incrementally.
            new_spike_i = np.asarray(spike_mon.i[self._last_spike_idx :], dtype=np.int32)
            self._last_spike_idx = len(spike_mon.i)

            # Update visualization activity (exponential decay + spike refresh).
            activity *= decay
            if new_spike_i.size:
                activity[new_spike_i] = 1.0

            snap = ActivitySnapshot(
                t_ms=float(net.t / b2.ms),
                activity=activity.astype(np.float32, copy=False),
                spikes=np.unique(new_spike_i),
            )
            with self._lock:
                self._snapshot = ActivitySnapshot(
                    t_ms=snap.t_ms,
                    activity=snap.activity.copy(),
                    spikes=snap.spikes.copy(),
                )

            # Broadcast to WebSocket subscribers
            with self._sub_lock:
                for q in self._subscribers:
                    q.put(self._snapshot)

            # Pace the loop to roughly real-time.
            elapsed = time.perf_counter() - t0
            target = self._chunk_ms / 1000.0
            if elapsed < target:
                time.sleep(target - elapsed)

    def _apply_stimuli(self, now_ms: float, G: Any, b2: Any) -> None:
        # Apply queued stimulation requests.
        while True:
            try:
                req = self._stim_queue.get_nowait()
            except Exception:
                break
            idx = int(req.neuron_index)
            if idx < 0 or idx >= len(self._connectome.neuron_ids):
                continue
            amp = float(req.amplitude_pA)
            dur = float(req.duration_ms)
            if dur <= 0:
                continue
            # Additive external current injection.
            G.I_ext[idx] += amp * b2.pA
            self._active_stims.append((now_ms + dur, idx, amp))

        # End expired stimuli.
        if not self._active_stims:
            return
        still_active: list[tuple[float, int, float]] = []
        for end_ms, idx, amp in self._active_stims:
            if now_ms >= end_ms:
                G.I_ext[idx] -= amp * b2.pA
            else:
                still_active.append((end_ms, idx, amp))
        self._active_stims = still_active

