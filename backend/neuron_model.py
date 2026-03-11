from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LIFParams:
    # Membrane
    v_rest_mV: float = -65.0
    v_reset_mV: float = -65.0
    v_th_mV: float = -50.0
    tau_m_ms: float = 20.0
    capacitance_pF: float = 200.0
    refractory_ms: float = 5.0

    # Synapses
    tau_syn_ms: float = 5.0
    weight_scale_pA: float = 6.0  # dataset "weight" -> pA conversion factor
    max_abs_weight_pA: float = 800.0

    # Background drive (keeps network from being completely silent)
    background_rate_hz: float = 0.5
    background_weight_pA: float = 20.0

    # Visualization-friendly activity (not a biophysical state)
    activity_tau_ms: float = 120.0

