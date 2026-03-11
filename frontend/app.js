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
};

const renderer = new ConnectomeRenderer(el.container);
const controls = createOrbitControls(renderer.camera, renderer.getDomElement());

let selectedIndex = null;

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
