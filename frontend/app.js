import { ConnectomeRenderer } from "/static/renderer.js";
import { createOrbitControls } from "/static/controls.js";

const el = {
  container: document.getElementById("canvasContainer"),
  hint: document.getElementById("hint"),
  simTime: document.getElementById("simTime"),
  simSpikes: document.getElementById("simSpikes"),
  simNeurons: document.getElementById("simNeurons"),
  simEdges: document.getElementById("simEdges"),
  selIndex: document.getElementById("selIndex"),
  selId: document.getElementById("selId"),
  selType: document.getElementById("selType"),
  selOut: document.getElementById("selOut"),
  selIn: document.getElementById("selIn"),
  stimBtn: document.getElementById("stimBtn"),
  pulseSpeed: document.getElementById("pulseSpeed"),
  pulseStrength: document.getElementById("pulseStrength"),
  hopSelect: document.getElementById("hopSelect"),
  toggleSynapses: document.getElementById("toggleSynapses"),
  fpsLabel: document.getElementById("fpsLabel"),
  screenshotBtn: document.getElementById("screenshotBtn"),
  tooltip: document.getElementById("tooltip"),
};

const renderer = new ConnectomeRenderer(el.container);
const controls = createOrbitControls(renderer.camera, renderer.getDomElement());

let selectedIndex = null;
let lastFrameTime = performance.now();
let hideSynapses = false;
let lastTooltipTime = 0;

function setHint(text) {
  el.hint.textContent = text;
}

function setSelected(meta) {
  selectedIndex = meta?.index ?? null;
  el.selIndex.textContent = meta ? String(meta.index) : "—";
  el.selId.textContent = meta ? String(meta.neuronId) : "—";
  el.selType.textContent = meta?.type ?? "—";
  el.selOut.textContent = meta ? String(meta.outCount) : "—";
  el.selIn.textContent = meta ? String(meta.inCount) : "—";
  el.stimBtn.disabled = selectedIndex == null;
}

async function loadConnectome() {
  await renderer.loadShaders();
  const [neurons, connections] = await Promise.all([
    fetch("/neurons").then((r) => r.json()),
    fetch("/connections").then((r) => r.json()),
  ]);

  renderer.setConnectome(neurons, connections);
  el.simNeurons.textContent = String(neurons.neuron_ids.length);
  el.simEdges.textContent = String(connections.pre.length);
  setHint("Loaded. Click a neuron to inspect; Shift+Click to stimulate.");
}

async function stimulate(index) {
  const res = await fetch("/stimulate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // These values are tuned to reliably elicit a spike in the default LIF params.
    body: JSON.stringify({ neuron_index: index, amplitude_pA: 800, duration_ms: 20 }),
  });
  if (!res.ok) {
    const msg = await res.text().catch(() => "");
    throw new Error(msg || `Stimulate failed (HTTP ${res.status})`);
  }
}

function attachInteraction() {
  const dom = renderer.getDomElement();
  dom.addEventListener("pointerdown", async (ev) => {
    if (ev.button !== 0) return;
    const idx = renderer.pick(ev.clientX, ev.clientY);
    if (idx == null) return;
    const meta = renderer.selectNeuron(idx);
    setSelected(meta);
    if (ev.shiftKey) {
      try {
        await stimulate(idx);
        renderer.stimulateVisual(idx);
      } catch (e) {
        setHint(`Stimulate error: ${e.message}`);
      }
    }
  });

  el.stimBtn.addEventListener("click", async () => {
    if (selectedIndex == null) return;
    try {
      await stimulate(selectedIndex);
      renderer.stimulateVisual(selectedIndex);
    } catch (e) {
      setHint(`Stimulate error: ${e.message}`);
    }
  });

  window.addEventListener("keydown", (ev) => {
    if (ev.key === "n" || ev.key === "N") {
      if (selectedIndex == null) return;
      const alreadyIsolated = renderer.isolatedIndex === selectedIndex;
      renderer.setIsolation(alreadyIsolated ? null : selectedIndex);
    }
  });

  dom.addEventListener("mousemove", (ev) => {
    const now = performance.now();
    if (now - lastTooltipTime < 80) return;
    lastTooltipTime = now;
    const idx = renderer.pick(ev.clientX, ev.clientY);
    if (idx == null) {
      el.tooltip.classList.add("hidden");
      return;
    }
    const meta = renderer.peekMetadata(idx);
    el.tooltip.innerHTML = `Index ${meta.index}<br/>ID ${meta.neuronId}<br/>Type ${meta.type ?? "—"}<br/>Out ${meta.outCount} / In ${meta.inCount}`;
    el.tooltip.style.left = `${ev.clientX + 12}px`;
    el.tooltip.style.top = `${ev.clientY + 12}px`;
    el.tooltip.classList.remove("hidden");
  });

  el.pulseSpeed.addEventListener("input", (e) => {
    renderer.pulseSpeed = parseFloat(e.target.value);
  });
  el.pulseStrength.addEventListener("input", (e) => {
    renderer.setPulseStrength(parseFloat(e.target.value));
  });
  el.hopSelect.addEventListener("change", (e) => {
    renderer.setHopIsolation(parseInt(e.target.value, 10));
  });
  el.toggleSynapses.addEventListener("click", () => {
    hideSynapses = !hideSynapses;
    renderer.toggleSynapses(!hideSynapses);
    el.toggleSynapses.textContent = hideSynapses ? "Show" : "Hide";
  });
  el.screenshotBtn.addEventListener("click", () => {
    renderer.captureScreenshot();
  });
}

async function pollActivityLoop() {
  while (true) {
    try {
      const a = await fetch("/activity").then((r) => r.json());
      el.simTime.textContent = `${a.t_ms.toFixed(1)} ms`;
      el.simSpikes.textContent = String(a.spikes.length);
      renderer.updateActivity(a.activity, a.spikes);
    } catch {
      setHint("Waiting for backend…");
    }
    // ~12 Hz update keeps JSON traffic manageable for 1k–5k neurons.
    await new Promise((r) => setTimeout(r, 85));
  }
}

function animate() {
  requestAnimationFrame(animate);
  const now = performance.now();
  const dt = now - lastFrameTime;
  if (dt > 0) {
    const fps = 1000 / dt;
    el.fpsLabel.textContent = fps.toFixed(0);
    if (fps < 40 && !hideSynapses) {
      renderer.toggleSynapses(false);
      hideSynapses = true;
    } else if (fps > 55 && hideSynapses) {
      renderer.toggleSynapses(true);
      hideSynapses = false;
    }
  }
  lastFrameTime = now;
  renderer.render(controls);
}

async function main() {
  try {
    console.log("Starting load...");
    await loadConnectome();
    console.log("Connectome loaded, attaching interaction...");
    attachInteraction();
    animate();
    pollActivityLoop();
  } catch (e) {
    console.error("Initialization Error:", e); // This will show in the console
    setHint(`Error: ${e.message}`);
  }
}

main();
